# -*- coding: utf-8 -*-
"""票06:归档闭环(提交→自动沉淀→校准)合约测试。

组合方式沿用 tests/test_web_api.py:真实 Library/PersonLibrary/Orchestrator +
后台动作同步桩(App.sync_bg=True);样本切割(ffmpeg 两步拼接)与向量抽取器
(.venv-diar 子进程)按票面注入 stub——抽取 stub 返回按样本路径确定的向量,
专验 F2 裁定「档案向量 = 样本片段直接抽取,不是整期质心」。

覆盖验收:
- 逐 SPK 归档:merge 后同人物多 SPK 各成独立条(D5/F2);未分配人物
  (voice=skip/无 host_person、person)不归档;语言分组(spk 语言缺省继承
  人物 main_lang);refs[](use=voiceid、绝对路径、source kind=episode 带
  bvid/part/spk)+ persons_emb 该语言组 FIFO 封顶 10 驱逐最旧。
- 一致性(票02 判定):组内 <2 条跳过直接入库;低于同人分布 p5 默认拦
  (回执 blocked {spk,score,p5,sample_path});POST /api/review/archive_force
  「仍入库」成功且幂等(重复 force 409)。
- F2 验收断言:档案 vec == stub extract(样本路径) 的返回值;并直接复用
  work/tools/voiceid_bench.py 的 recompute_consistency(cos>=0.999)对
  【非空】档案跑通(票04 评审建议2:空档案会 0 checked 0 failed 空过)。
- 归档后阈值重算被调用(mock 校准器);归档失败(坏档案/抽取器炸)只降级
  记 archive_error/skipped,提交链与小样照常。
- UI smoke:review.html 提交成功 toast 回显/拦截提示块/仍入库按钮。
"""
import http.client
import json
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "work" / "tools"))

import web_server                    # noqa: E402  (被测模块)
import voiceid_bench                 # noqa: E402  (F2 比对函数直接复用)
from boke import preview as preview_mod   # noqa: E402
from boke import review_apply, voiceid    # noqa: E402
from boke.common import SpkSeg, write_rttm   # noqa: E402
from boke.library import (           # noqa: E402
    EV_ANALYSIS_DONE, EV_DOWNLOAD_DONE, ST_PREVIEW, Library,
)
from boke.orchestrator import Orchestrator   # noqa: E402
from boke.persons import PersonLibrary       # noqa: E402

BV = "BV1vid00006"
VID = f"{BV}_P1"


# ---------- 环境与服务(同 test_web_api 先例) ----------

@pytest.fixture
def env(tmp_path):
    """起好服务的测试环境(后台同步执行);yield (app, port, lib, persons, orch, tmp)。"""
    lib = Library(tmp_path / "library.json")
    persons = PersonLibrary(path=tmp_path / "persons.json",
                            refs_dir=tmp_path / "refs",
                            web_audio_dir=tmp_path / "wa")
    orch = Orchestrator(lib=lib, work_dir=tmp_path / "work",
                        media_dir=tmp_path / "media",
                        cookies_path=tmp_path / "cookies.txt")
    app = web_server.App(lib=lib, persons=persons, orch=orch,
                         work_dir=tmp_path / "work",
                         web_audio_dir=tmp_path / "wa",
                         catalog_path=tmp_path / "space_videos.json")
    app.sync_bg = True
    srv = web_server.make_server("127.0.0.1", 0, app)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield app, srv.server_address[1], lib, persons, orch, tmp_path
    finally:
        srv.shutdown()
        srv.server_close()


def _req(port, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=15)
    headers, payload = {}, None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, payload, headers)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    if not raw:
        return r.status, None
    if "json" in (r.getheader("Content-Type") or ""):
        return r.status, json.loads(raw.decode("utf-8"))
    return r.status, raw.decode("utf-8", "replace")


# ---------- 造料:待核对记录 / 桩(切割/抽取/小样) / 档案与阈值 ----------

def _mk_reviewable(lib, tmp_path, speakers=("SPK_00", "SPK_01"), langs=None,
                   bv=BV, part=1):
    """造一条待核对记录:媒体+tagged.srt+langid.json 产物齐(langs={spk: lang|None})。"""
    langs = langs if langs is not None else {s: "zh" for s in speakers}
    media = tmp_path / "media" / f"{bv}_P{part}.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"fake mp4")
    lib.add(bv, part, title="归档闭环测试期", media_path=str(media), duration=94.0)
    lib.transition(bv, part, EV_DOWNLOAD_DONE, media_path=str(media))
    lib.transition(bv, part, EV_ANALYSIS_DONE)
    wdir = tmp_path / "work" / f"{bv}_P{part}"
    wdir.mkdir(parents=True, exist_ok=True)
    segs = [SpkSeg(0.5 + i * 3.0, 4.0 + i * 3.0, s)
            for i, s in enumerate(speakers)]           # 3.5s 段,入选切样窗口
    write_rttm(wdir / "diar.rttm", segs)
    tagged = "".join(
        f"{i + 1}\n00:00:{i * 3:02d},500 --> 00:00:{i * 3 + 4:02d},000\n"
        f"[{s}] line{i}\n\n" for i, s in enumerate(speakers))
    (wdir / "tagged.srt").write_text(tagged, encoding="utf-8")
    (wdir / "ocr.srt").write_text(
        tagged.replace("[", "").replace("] ", ""), encoding="utf-8")
    (wdir / "langid.json").write_text(json.dumps(
        {"verdict": "dub_需要配音",
         "speakers": {s: {"lang": langs.get(s)} for s in speakers}},
        ensure_ascii=False), encoding="utf-8")
    lib.update(bv, part, artifacts={
        "srt": str(wdir / "ocr.srt"),
        "rttm": str(wdir / "diar.rttm"), "tagged": str(wdir / "tagged.srt"),
        "audio": str(wdir / "audio.wav"), "langid": str(wdir / "langid.json"),
        "langid_suggest": "dub"})
    return media


def _stub_cut(app):
    """样本切割桩(替 ffmpeg 两步拼接):落一个假 wav,返回绝对路径。"""
    def fake(bvid, part, spk, tagged, audio):
        out = app.web_audio_dir / f"voiceid_{bvid}_P{part}_{spk}.wav"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake archive sample wav")
        return out.resolve()
    app._archive_cut_sample = fake


def _mk_extract(vec_by_spk, fail_spks=()):
    """抽取器 stub:按样本文件名里的 SPK 返回确定性向量;fail_spks 抛错。"""
    def fake(path):
        name = Path(path).name
        for spk in fail_spks:
            if spk in name:
                raise RuntimeError(f"boom:{spk}")
        for spk, v in vec_by_spk.items():
            if spk in name:
                return list(v)
        raise AssertionError(f"stub 未登记的样本:{path}")
    return fake


def _seed_archive(tmp_path, person="圆脸", lang="zh", n=2, vec=None,
                  tag="y"):
    """预置档案条目(ref_path 用 y<i>.wav 标号,验 FIFO 驱逐顺序)。"""
    arch = voiceid.VoiceArchive(path=tmp_path / "work" / "persons_emb.json")
    for i in range(n):
        arch.add_entry(person, lang, list(vec or [1, 0, 0]), f"{tag}{i + 1}.wav")
    return arch


def _write_thr(tmp_path, **over):
    """zh 档阈值:genuine_p5 可调(一致性拦截线)。"""
    tier = {"calibrated": True, "genuine_n": 9, "impostor_n": 9,
            "cross_impostor_n": 9, "margin_n": 9,
            "thr": 0.50, "margin": 0.10, "thr_host": 0.90, "thr_cross": 0.95,
            "genuine_p5": 0.40, "genuine_p25": 0.60, "eer": 0.02}
    tier.update(over)
    voiceid.save_thresholds(
        {"version": 1, "langs": {"zh": tier}},
        tmp_path / "work" / "voiceid_thresholds.json")


def _stub_preview(monkeypatch, calls):
    def fake(bvid, part, decisions=None, **kw):
        calls.append((bvid, part))
        lib = kw["library"]
        rec = lib.get(bvid, part)
        h = rec["dec_hash"] or review_apply.dec_hash(rec["review"])
        vid = Path(rec["media_path"]).stem
        mp3 = Path(kw["work_root"]) / vid / f"dec_{h}" / "preview.mp3"
        mp3.parent.mkdir(parents=True, exist_ok=True)
        mp3.write_bytes(b"fake preview mp3")
        lib.transition(bvid, part, "preview_ready", preview_path=str(mp3))
        return {"status": "applied", "preview": str(mp3)}
    monkeypatch.setattr(preview_mod, "generate_preview", fake)


def _spk(sid, *, host=False, host_person=None, person=None, merge=None,
         voice="preset", preset="zh-CN-YunjianNeural"):
    """决策里的说话人条目(person=声纹归档目标:建议预填/人工指认,票06)。"""
    return {"id": sid, "note": "", "host": host,
            "host_person": host_person, "person": person,
            "merge_into": merge, "voice": voice,
            "preset_voice": preset if voice == "preset" else None,
            "clone_ref": None}


# ---------- 逐 SPK 归档 / merge / 语言分组 / refs+emb ----------

def test_archive_per_spk_merge_and_lang_groups(env, monkeypatch):
    """merge 后同人物多 SPK 各成独立条;逐 SPK 条数正确;语言分组正确;
    refs[](use=voiceid,绝对路径,episode 溯源)与 persons_emb 同步入库。"""
    app, port, lib, persons, orch, tmp_path = env
    _stub_cut(app)
    vecs = {"SPK_00": [1, 0, 0, 0], "SPK_01": [0.9, 0.1, 0, 0],
            "SPK_02": [0, 1, 0, 0]}
    app.extract_fn = _mk_extract(vecs)
    calls = []
    _stub_preview(monkeypatch, calls)
    _mk_reviewable(lib, tmp_path,
                   speakers=("SPK_00", "SPK_01", "SPK_02", "SPK_03"),
                   langs={"SPK_00": "zh", "SPK_01": "zh",
                          "SPK_02": "en", "SPK_03": "en"})
    dec = {"verdict": "dub", "speakers": [
        _spk("SPK_00", host=True, host_person="圆脸"),
        _spk("SPK_01", merge="SPK_00"),                     # 分身并入主持人
        _spk("SPK_02", person="小外"),                      # 建议预填的嘉宾
        _spk("SPK_03", voice="skip")]}                      # 未分配人物,不归档
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    assert calls == [(BV, 1)]                               # 小样照常触发
    a = d["archive"]
    assert "archive_error" not in a
    assert [x["spk"] for x in a["archived"]] == ["SPK_00", "SPK_01", "SPK_02"]
    assert len(a["skipped"]) == 1 and a["skipped"][0]["spk"] == "SPK_03"
    assert a["blocked"] == []
    # 回显文案数据:"已为圆脸归档 2 条(中文组现有 2 条)"/小外英文组 1 条
    assert a["summaries"] == [
        {"person": "圆脸", "lang": "zh", "archived": 2, "total": 2},
        {"person": "小外", "lang": "en", "archived": 1, "total": 1}]
    assert a["recalibrated"] is True

    arch = voiceid.VoiceArchive(path=tmp_path / "work" / "persons_emb.json")
    es = arch.entries()
    by_spk = {e["spk"]: e for e in es}
    assert set(by_spk) == {"SPK_00", "SPK_01", "SPK_02"}    # 逐 SPK 各成条(D5)
    assert by_spk["SPK_00"]["person"] == "圆脸" and by_spk["SPK_00"]["lang"] == "zh"
    assert by_spk["SPK_01"]["person"] == "圆脸" and by_spk["SPK_01"]["lang"] == "zh"
    assert by_spk["SPK_02"]["person"] == "小外" and by_spk["SPK_02"]["lang"] == "en"
    for spk in by_spk:                                      # 来源字段可追溯
        e = by_spk[spk]
        assert e["bvid"] == BV and e["part"] == 1
        assert e["ref_path"].endswith(f"voiceid_{BV}_P1_{spk}.wav")
        assert Path(e["ref_path"]).is_absolute()            # 票04 裁定:绝对路径
    # refs[] 同步:use=voiceid、绝对路径、episode 溯源带 bvid/part/spk(票03)
    yuan = persons.list_refs("圆脸", use="voiceid")
    assert len(yuan) == 2
    assert {r["source"]["spk"] for r in yuan} == {"SPK_00", "SPK_01"}
    for r in yuan:
        assert r["use"] == "voiceid" and r["transcript"] is None
        assert Path(r["audio"]).is_absolute()
        assert r["source"] == {"kind": "episode", "bvid": BV, "part": 1,
                               "spk": r["source"]["spk"]}
    xw = persons.list_refs("小外", use="voiceid")
    assert len(xw) == 1 and xw[0]["lang"] == "en"
    # 归档后阈值表已重算落盘
    thr = voiceid.load_thresholds(tmp_path / "work" / "voiceid_thresholds.json")
    assert set(thr["langs"]) == {"zh", "en"}


def test_archive_lang_fallback_to_person_main_lang(env, monkeypatch):
    """说话人语言未知(langid 缺)→ 条目落人物主要语言组,refs 与 emb 同 lang。"""
    app, port, lib, persons, orch, tmp_path = env
    _stub_cut(app)
    app.extract_fn = _mk_extract({"SPK_00": [1, 0, 0, 0]})
    _stub_preview(monkeypatch, [])
    _mk_reviewable(lib, tmp_path, speakers=("SPK_00",), langs={"SPK_00": None})
    dec = {"verdict": "dub", "speakers": [_spk("SPK_00", person="圆脸")]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    arch = voiceid.VoiceArchive(path=tmp_path / "work" / "persons_emb.json")
    es = arch.entries(person="圆脸")
    assert len(es) == 1 and es[0]["lang"] == "zh"           # 圆脸 main_lang
    assert persons.list_refs("圆脸", use="voiceid")[0]["lang"] == "zh"


def test_archive_fifo_evicts_oldest(env, monkeypatch):
    """每人每语言封顶 10 条 FIFO:9 条旧档 + 2 条新档 → 最旧被驱逐。"""
    app, port, lib, persons, orch, tmp_path = env
    _seed_archive(tmp_path, n=9, tag="y")                  # y1..y9(旧→新)
    _stub_cut(app)
    app.extract_fn = _mk_extract({"SPK_00": [1, 0, 0], "SPK_01": [1, 0, 0]})
    _stub_preview(monkeypatch, [])
    _mk_reviewable(lib, tmp_path, speakers=("SPK_00", "SPK_01"))
    dec = {"verdict": "dub", "speakers": [
        _spk("SPK_00", host=True, host_person="圆脸"),
        _spk("SPK_01", merge="SPK_00")]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    a = d["archive"]
    assert a["summaries"] == [{"person": "圆脸", "lang": "zh",
                               "archived": 2, "total": 10}]
    arch = voiceid.VoiceArchive(path=tmp_path / "work" / "persons_emb.json")
    es = arch.entries(person="圆脸", lang="zh")
    assert len(es) == 10                                    # 封顶不超
    refs = [Path(e["ref_path"]).name for e in es]
    assert refs[0] == "y2.wav"                              # 最旧 y1 被驱逐
    assert "y1.wav" not in refs and "y9.wav" in refs
    assert sum(1 for r in refs if r.startswith(f"voiceid_{BV}")) == 2


def test_archive_no_person_leaves_nothing(env, monkeypatch):
    """未分配人物的决策(既有测试先例形状)不归档、不写档案/阈值文件。"""
    app, port, lib, persons, orch, tmp_path = env
    _stub_preview(monkeypatch, [])
    _mk_reviewable(lib, tmp_path)
    dec = {"verdict": "dub", "speakers": [
        _spk("SPK_00"), _spk("SPK_01")]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    a = d["archive"]
    assert a["archived"] == [] and a["blocked"] == [] \
        and a["summaries"] == [] and a["recalibrated"] is False
    assert "archive_error" not in a
    assert [s["spk"] for s in a["skipped"]] == ["SPK_00", "SPK_01"]
    assert "未分配人物" in a["skipped"][0]["reason"]
    assert not (tmp_path / "work" / "persons_emb.json").exists()
    assert not (tmp_path / "work" / "voiceid_thresholds.json").exists()


# ---------- 一致性拦截(F5/D6)+ 仍入库 ----------

def test_archive_consistency_lt2_skips_check(env, monkeypatch):
    """组内 <2 条跳过一致性检查直接入库(F5):坏向量也放行。"""
    app, port, lib, persons, orch, tmp_path = env
    _seed_archive(tmp_path, n=1)                            # 仅 1 条
    _write_thr(tmp_path, genuine_p5=0.9)
    _stub_cut(app)
    app.extract_fn = _mk_extract({"SPK_00": [0, 1, 0]})     # 与旧档正交(0 分)
    _stub_preview(monkeypatch, [])
    _mk_reviewable(lib, tmp_path, speakers=("SPK_00",))
    dec = {"verdict": "dub", "speakers": [_spk("SPK_00", person="圆脸")]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    assert d["archive"]["blocked"] == []
    assert [x["spk"] for x in d["archive"]["archived"]] == ["SPK_00"]


def test_archive_consistency_blocks_and_force(env, monkeypatch):
    """低于同人分布 p5 默认拦(回执 blocked 四要素+试听 URL);
    「仍入库」force 成功且重复 force 幂等拒绝。"""
    app, port, lib, persons, orch, tmp_path = env
    _seed_archive(tmp_path, n=2)                            # 圆脸 zh 2 条 [1,0,0]
    _write_thr(tmp_path, genuine_p5=0.9)
    _stub_cut(app)
    app.extract_fn = _mk_extract({"SPK_00": [0, 1, 0]})     # 均分 0 < p5
    _stub_preview(monkeypatch, [])
    _mk_reviewable(lib, tmp_path, speakers=("SPK_00",))
    dec = {"verdict": "dub", "speakers": [_spk("SPK_00", person="圆脸")]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    a = d["archive"]
    assert a["archived"] == [] and a["recalibrated"] is False
    assert len(a["blocked"]) == 1
    b = a["blocked"][0]
    assert b["spk"] == "SPK_00" and b["person"] == "圆脸" and b["lang"] == "zh"
    assert b["score"] == pytest.approx(0.0)
    assert b["p5"] == pytest.approx(0.9)
    assert Path(b["sample_path"]).is_absolute() and Path(b["sample_path"]).exists()
    assert b["sample_url"] == "/audio/voiceid_%s_P1_SPK_00.wav" % BV
    arch = voiceid.VoiceArchive(path=tmp_path / "work" / "persons_emb.json")
    assert len(arch.entries(person="圆脸")) == 2            # 未入档
    assert persons.list_refs("圆脸", use="voiceid") == []

    # 仍入库(人工裁决):跳一致性检查,样本复用提交时已切好的文件
    st, f = _req(port, "POST", "/api/review/archive_force",
                 {"bvid": BV, "part": 1, "spk": "SPK_00"})
    assert st == 200 and f["ok"] is True
    assert f["person"] == "圆脸" and f["total_after"] == 3
    assert f["recalibrated"] is True
    arch = voiceid.VoiceArchive(path=tmp_path / "work" / "persons_emb.json")
    es = arch.entries(person="圆脸")
    assert len(es) == 3 and es[-1]["vec"] == [0, 1, 0]
    assert persons.list_refs("圆脸", use="voiceid")[0]["audio"] == b["sample_path"]

    st, f2 = _req(port, "POST", "/api/review/archive_force",
                  {"bvid": BV, "part": 1, "spk": "SPK_00"})
    assert st == 409 and f2["ok"] is False                  # 幂等:同样本不重复入
    st, f3 = _req(port, "POST", "/api/review/archive_force",
                  {"bvid": BV, "part": 1, "spk": "SPK_09"})
    assert st == 400                                        # 决策里没有的说话人


# ---------- F2 验收:档案向量 = 样本直抽 ----------

def test_archive_f2_sample_direct_extraction(env, monkeypatch):
    """① 档案 vec == stub extract(该样本路径) 的返回值(样本直抽路径,
    不是整期质心);② 复用 voiceid_bench.recompute_consistency 的
    cos>=0.999 比对,对非空档案 0 failed(票04 评审建议2)。"""
    app, port, lib, persons, orch, tmp_path = env
    _stub_cut(app)
    vecs = {"SPK_00": [1.0, 0.0, 0.0, 0.0], "SPK_01": [0.0, 1.0, 0.0, 0.0]}
    extract = _mk_extract(vecs)
    app.extract_fn = extract
    _stub_preview(monkeypatch, [])
    _mk_reviewable(lib, tmp_path, speakers=("SPK_00", "SPK_01"))
    dec = {"verdict": "dub", "speakers": [
        _spk("SPK_00", host=True, host_person="圆脸"),
        _spk("SPK_01", person="圆脸")]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    arch = voiceid.VoiceArchive(path=tmp_path / "work" / "persons_emb.json")
    es = arch.entries()
    assert len(es) == 2                                     # 档案非空(防空过)
    for e in es:                                            # ① vec == extract(样本)
        assert e["vec"] == extract(e["ref_path"])
        assert e["vec"] == vecs[e["spk"]]
    # ② 票04 bench 的核心比对函数直接复用(不重写)
    report, failed = voiceid_bench.recompute_consistency(
        es, extract, sample_n=10)
    assert len(report) == 2 and failed == []
    assert all(r["cos"] >= voiceid_bench.RECOMPUTE_MIN_COS for r in report)


# ---------- 阈值重算调用 / 归档失败降级 ----------

def test_archive_recalibrate_called_mock(env, monkeypatch):
    """归档后阈值重算被调用(mock 校准器记录调用)。"""
    app, port, lib, persons, orch, tmp_path = env
    calls = []
    monkeypatch.setattr(app, "_recalibrate_thresholds",
                        lambda: calls.append(1))
    _stub_cut(app)
    app.extract_fn = _mk_extract({"SPK_00": [1, 0, 0]})
    _stub_preview(monkeypatch, [])
    _mk_reviewable(lib, tmp_path, speakers=("SPK_00",))
    dec = {"verdict": "dub", "speakers": [_spk("SPK_00", person="圆脸")]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    assert calls == [1] and d["archive"]["recalibrated"] is True
    assert not (tmp_path / "work" / "voiceid_thresholds.json").exists()  # 被 mock


def test_archive_error_degrades_not_fails_submission(env, monkeypatch):
    """档案文件损坏 → archive_error 降级;提交与小样照常成功。"""
    app, port, lib, persons, orch, tmp_path = env
    emb = tmp_path / "work" / "persons_emb.json"
    emb.parent.mkdir(parents=True, exist_ok=True)
    emb.write_text("{坏了", encoding="utf-8")
    _stub_cut(app)
    app.extract_fn = _mk_extract({"SPK_00": [1, 0, 0]})
    calls = []
    _stub_preview(monkeypatch, calls)
    _mk_reviewable(lib, tmp_path, speakers=("SPK_00",))
    dec = {"verdict": "dub", "speakers": [_spk("SPK_00", person="圆脸")]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    assert d["status"] == ST_PREVIEW and calls == [(BV, 1)]  # 提交链不受影响
    assert "声纹档案文件损坏" in d["archive"]["archive_error"]


def test_archive_extractor_failure_skips_spk(env, monkeypatch):
    """单 SPK 抽取器炸 → 该 SPK 记 skipped(原因可见),不影响提交回执。"""
    app, port, lib, persons, orch, tmp_path = env
    _stub_cut(app)
    app.extract_fn = _mk_extract({"SPK_00": [1, 0, 0]}, fail_spks=("SPK_01",))
    _stub_preview(monkeypatch, [])
    _mk_reviewable(lib, tmp_path, speakers=("SPK_00", "SPK_01"))
    dec = {"verdict": "dub", "speakers": [
        _spk("SPK_00", person="圆脸"), _spk("SPK_01", person="圆脸")]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    a = d["archive"]
    assert "archive_error" not in a
    assert [x["spk"] for x in a["archived"]] == ["SPK_00"]
    assert [x["spk"] for x in a["skipped"]] == ["SPK_01"]
    assert "boom:SPK_01" in a["skipped"][0]["reason"]


# ---------- 样本选段纯函数(复用 cut_speaker_samples 切法) ----------

def test_pick_sample_segments_selection_rules():
    """3.5-8s 整段、彼此间距>60s、总长≤30s、至多 3 段(试听台切法)。"""
    segs = [(200.0, 204.0),            # 打乱顺序,函数内部排序
            (0.0, 4.0),                # 4s ✓ ①
            (30.0, 34.0),              # 4s 但距①仅 30s ✗
            (65.0, 73.0),              # 8s ✓ ②
            (70.0, 76.0),              # 距②仅 5s ✗
            (150.0, 153.0),            # 3s < 3.5 ✗
            (400.0, 404.0)]            # 4s ✓ ③
    picked = web_server._pick_sample_segments(segs)
    assert picked == [(0.0, 4.0), (65.0, 73.0), (200.0, 204.0)]
    # 总长封顶:4 段 8s(共 32s>30)→ 第 4 段不进
    segs8 = [(i * 100.0, i * 100.0 + 8.0) for i in range(4)]
    picked8 = web_server._pick_sample_segments(segs8)
    assert picked8 == [(0.0, 8.0), (100.0, 108.0), (200.0, 208.0)]
    assert sum(t1 - t0 for t0, t1 in picked8) <= 30.0
    assert web_server._pick_sample_segments([]) == []
    assert web_server._pick_sample_segments([(0, 1), (100, 103)]) == []  # 无合格段


# ---------- UI smoke(提交成功 toast 回显 + 拦截提示块) ----------

def test_review_html_archive_ui_smoke(env):
    _, port, *_ = env
    st, body = _req(port, "GET", "/review.html")
    assert st == 200
    frags = [
        "仍入库",                            # 拦截提示块的人工裁决按钮(D6)
        "声纹档案明显不符",                  # 拦截提示块标题
        "/api/review/archive_force",         # 仍入库端点
        "已为",                              # 提交成功 toast 回显(D4)
        "归档",                              # toast 文案:已为 X 归档 N 条
        "组现有",                            # toast 文案:(中文组现有 M 条)
        "archCard",                          # 拦截提示块容器
        "person:",                           # 决策附声纹归档目标(person 字段)
    ]
    for frag in frags:
        assert frag in body, frag
