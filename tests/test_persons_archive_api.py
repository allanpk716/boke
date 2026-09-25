# -*- coding: utf-8 -*-
"""票07:人物库详情页声纹档案列表(API + F11 失效过滤 + UI smoke)合约测试。

组合方式沿用 tests/test_archive.py:真实 Library/PersonLibrary/Orchestrator +
随机端口起服务;档案条目直接经 VoiceArchive/persons.add_ref 造(不走提交链)。

覆盖验收:
- GET /api/persons/<name>/archive:按人物×语言分组,条目带
  person/lang/ref_path/bvid/part/spk/ts/model/valid/audio_url(不外露 vec);
  组计数只算有效条目,失效条目单列(计数提示,票面 F11);人物不存在 404。
- DELETE /api/persons/<name>/archive {ref_path}:refs 条目 + persons_emb 向量
  记录 + 自动样本文件三者级联清理(单测 tmp 目录断言),clone 条目与其原文件
  不受影响;半途失败的重试(向量/文件残留)继续清干净;条目已不存在 → 404;
  删除后阈值重算被调用(D7 口径,mock 校准器)。
- F11 读侧过滤:ref_path 文件缺失的条目不参与识别打分(均分不再被失效条目
  拉低)——落点为 web_server._voiceid_compute 装载处(票02 VoiceArchive 读侧
  无注入点,评审票面已预留该落点)。
- UI smoke:review.html 人物卡含档案区(列表渲染/试听元素/删除调用),沿用
  静态页断言先例(test_review_html_voiceid_ui_smoke)。
"""
import http.client
import json
import sys
import threading
from pathlib import Path
from urllib.parse import quote

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "work" / "tools"))

import web_server                    # noqa: E402  (被测模块)
from boke import voiceid             # noqa: E402
from boke.common import SpkSeg, write_rttm   # noqa: E402
from boke.library import (           # noqa: E402
    EV_ANALYSIS_DONE, EV_DOWNLOAD_DONE, Library,
)
from boke.orchestrator import Orchestrator   # noqa: E402
from boke.persons import PersonLibrary       # noqa: E402

BV = "BV1arch0007"
VID = f"{BV}_P1"


# ---------- 环境与服务(同 test_archive.py 先例) ----------

@pytest.fixture
def env(tmp_path):
    """起好服务的测试环境;yield (app, port, lib, persons, orch, tmp)。"""
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


# ---------- 造料 ----------

def _mk_sample(app, name):
    """在 web 音频目录落一个真实样本文件(存在性校验/级联删文件要用)。"""
    f = app.web_audio_dir / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"fake archive sample wav")
    return f.resolve()


def _seed_persons_emb(app, entries):
    """按 (person, lang, vec, ref_path, bvid, part, spk) 元组灌档案。"""
    arch = voiceid.VoiceArchive(path=app.persons_emb_path)
    for person, lang, vec, ref_path, bvid, part, spk in entries:
        arch.add_entry(person, lang, vec, ref_path, bvid=bvid, part=part,
                       spk=spk, model="test-model")
    return arch


def _mk_reviewable(lib, tmp_path, speakers=("SPK_00",)):
    """造一条待核对记录(带 tagged.srt + langid.json,zh);供 /api/episode 走识别。"""
    media = tmp_path / "media" / f"{BV}_P1.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"fake mp4")
    lib.add(BV, 1, title="档案列表测试期", media_path=str(media), duration=94.0)
    lib.transition(BV, 1, EV_DOWNLOAD_DONE, media_path=str(media))
    lib.transition(BV, 1, EV_ANALYSIS_DONE)
    wdir = tmp_path / "work" / VID
    wdir.mkdir(parents=True, exist_ok=True)
    segs = [SpkSeg(0.5 + i * 3.0, 4.0 + i * 3.0, s)
            for i, s in enumerate(speakers)]
    write_rttm(wdir / "diar.rttm", segs)
    tagged = "".join(
        f"{i + 1}\n00:00:{i * 3:02d},500 --> 00:00:{i * 3 + 4:02d},000\n"
        f"[{s}] line{i}\n\n" for i, s in enumerate(speakers))
    (wdir / "tagged.srt").write_text(tagged, encoding="utf-8")
    (wdir / "langid.json").write_text(json.dumps(
        {"verdict": "dub_需要配音",
         "speakers": {s: {"lang": "zh"} for s in speakers}},
        ensure_ascii=False), encoding="utf-8")
    lib.update(BV, 1, artifacts={
        "rttm": str(wdir / "diar.rttm"), "tagged": str(wdir / "tagged.srt"),
        "audio": str(wdir / "audio.wav"), "langid": str(wdir / "langid.json"),
        "langid_suggest": "dub"})
    return wdir


def _write_emb(wdir, vectors, labels):
    """票01 产物:spk_emb.npy(num×dim)+ spk_emb.json(labels 原序)。"""
    wdir.mkdir(parents=True, exist_ok=True)
    np.save(wdir / "spk_emb.npy", np.array(vectors, dtype=np.float32))
    (wdir / "spk_emb.json").write_text(
        json.dumps({"labels": list(labels), "dim": len(vectors[0])}),
        encoding="utf-8")


def _write_thr(tmp_path):
    """zh 档阈值:score 1.0 过双门+主持人门(suggest)。"""
    voiceid.save_thresholds(
        {"version": 1, "langs": {"zh": {
            "calibrated": True, "genuine_n": 9, "impostor_n": 9,
            "cross_impostor_n": 9, "margin_n": 9,
            "thr": 0.50, "margin": 0.10, "thr_host": 0.90, "thr_cross": 0.95,
            "genuine_p5": 0.40, "genuine_p25": 0.60, "eer": 0.02}}},
        tmp_path / "work" / "voiceid_thresholds.json")


# ---------- GET /api/persons/<name>/archive ----------

def test_archive_api_grouped_entries_and_validity(env):
    """分组结构:按语言分组;条目字段齐、不外露 vec;valid=文件存在性;
    组计数只算有效条目,失效单列;audio_url 经 /audio/ 静态服务。"""
    app, port, lib, persons, orch, tmp_path = env
    s1 = _mk_sample(app, "voiceid_BVaa_P1_SPK_00.wav")
    s2 = _mk_sample(app, "voiceid_BVbb_P2_SPK_01.wav")
    persons.add_ref("圆脸", str(s1), None, "zh",
                    {"kind": "episode", "bvid": "BVaa", "part": 1,
                     "spk": "SPK_00"}, use="voiceid")
    persons.add_ref("圆脸", str(s2), None, "zh",
                    {"kind": "episode", "bvid": "BVbb", "part": 2,
                     "spk": "SPK_01"}, use="voiceid")
    ghost = tmp_path / "wa" / "ghost.wav"          # 不创建文件 → 失效
    _seed_persons_emb(app, [
        ("圆脸", "zh", [1, 0, 0], str(s1), "BVaa", 1, "SPK_00"),
        ("圆脸", "zh", [0.9, 0.1, 0], str(s2), "BVbb", 2, "SPK_01"),
        ("圆脸", "en", [0, 1, 0], str(ghost), "BVcc", 1, "SPK_09"),
    ])
    st, d = _req(port, "GET",
                 "/api/persons/" + quote("圆脸") + "/archive")
    assert st == 200 and d["ok"] is True and d["person"] == "圆脸"
    gs = {g["lang"]: g for g in d["groups"]}
    assert set(gs) == {"zh", "en"}
    zh, en = gs["zh"], gs["en"]
    assert zh["count"] == 2 and zh["invalid"] == 0
    assert en["count"] == 0 and en["invalid"] == 1   # 失效条目不进计数
    e1 = zh["entries"][0]
    for k in ("person", "lang", "ref_path", "bvid", "part", "spk", "ts",
              "model", "valid", "audio_url"):
        assert k in e1, k
    assert "vec" not in e1                           # 向量不外露给页面
    assert e1["person"] == "圆脸" and e1["lang"] == "zh"
    assert e1["bvid"] == "BVaa" and e1["part"] == 1 and e1["spk"] == "SPK_00"
    assert e1["model"] == "test-model" and e1["ts"]
    assert e1["valid"] is True
    assert e1["audio_url"] == "/audio/voiceid_BVaa_P1_SPK_00.wav"
    ge = en["entries"][0]
    assert ge["valid"] is False and ge["audio_url"] is None
    assert ge["ref_path"] == str(ghost)
    assert d["total"] == 2 and d["invalid_total"] == 1


def test_archive_api_unknown_person_404(env):
    app, port, *_ = env
    st, d = _req(port, "GET",
                 "/api/persons/" + quote("不存在") + "/archive")
    assert st == 404 and d["ok"] is False


def test_archive_api_empty_archive(env):
    """人物在库但无档案条目 → groups 空列表(ok,页面显示空态)。"""
    app, port, lib, persons, orch, tmp_path = env
    st, d = _req(port, "GET", "/api/persons/" + quote("圆脸") + "/archive")
    assert st == 200 and d["ok"] is True
    assert d["groups"] == [] and d["total"] == 0 and d["invalid_total"] == 0


# ---------- DELETE:三者级联清理 ----------

def test_archive_delete_cascades_refs_cache_and_sample_file(env):
    """删除一条:refs 条目 + persons_emb 向量记录 + 样本文件三者齐清;
    clone 用途条目与其原文件不受影响;删后档案列表不再含该条。"""
    app, port, lib, persons, orch, tmp_path = env
    s1 = _mk_sample(app, "voiceid_BVaa_P1_SPK_00.wav")
    clone = _mk_sample(app, "clone_manual.wav")
    persons.add_ref("圆脸", str(s1), None, "zh",
                    {"kind": "episode", "bvid": "BVaa", "part": 1,
                     "spk": "SPK_00"}, use="voiceid")
    persons.add_ref("圆脸", str(clone), None, "zh", None, use="clone")
    _seed_persons_emb(app, [
        ("圆脸", "zh", [1, 0, 0], str(s1), "BVaa", 1, "SPK_00"),
    ])
    st, d = _req(port, "DELETE",
                 "/api/persons/" + quote("圆脸") + "/archive",
                 {"ref_path": str(s1)})
    assert st == 200 and d["ok"] is True
    assert d["person"] == "圆脸" and d["ref_path"] == str(s1)
    assert d["ref_entries_removed"] == 1              # refs 条目已删
    assert d["cache_removed"] == 1                    # 向量记录已删
    assert d["sample_files_deleted"] == [str(s1)]     # 自动样本文件已删
    assert not s1.exists()
    assert persons.list_refs("圆脸", use="voiceid") == []
    assert len(persons.list_refs("圆脸", use="clone")) == 3   # 种子2+手动1
    assert clone.exists()                             # clone 原文件不动
    arch = voiceid.VoiceArchive(path=app.persons_emb_path)
    assert arch.entries(person="圆脸") == []
    st, d2 = _req(port, "GET",
                  "/api/persons/" + quote("圆脸") + "/archive")
    assert st == 200 and d2["groups"] == []           # 列表即时反映


def test_archive_delete_partial_failure_retry(env):
    """重试路径:refs 条目已不在(上次删除在清向量/文件前中断),但向量记录
    与样本文件有残留 → DELETE 继续清干净(ref_entries_removed=0)。"""
    app, port, lib, persons, orch, tmp_path = env
    s1 = _mk_sample(app, "voiceid_BVaa_P1_SPK_00.wav")
    persons.add_ref("圆脸", str(s1), None, "zh",
                    {"kind": "episode", "bvid": "BVaa", "part": 1,
                     "spk": "SPK_00"}, use="voiceid")
    _seed_persons_emb(app, [
        ("圆脸", "zh", [1, 0, 0], str(s1), "BVaa", 1, "SPK_00"),
    ])
    persons.remove_ref("圆脸", str(s1))               # 模拟 refs 已删的半途状态
    st, d = _req(port, "DELETE",
                 "/api/persons/" + quote("圆脸") + "/archive",
                 {"ref_path": str(s1)})
    assert st == 200 and d["ok"] is True
    assert d["ref_entries_removed"] == 0 and d["cache_removed"] == 1
    assert d["sample_files_deleted"] == [str(s1)] and not s1.exists()
    assert voiceid.VoiceArchive(
        path=app.persons_emb_path).entries(person="圆脸") == []


def test_archive_delete_twice_second_404(env):
    """删完再删同一条 → 404(条目已不存在),三处状态原样、不虚报清理。"""
    app, port, lib, persons, orch, tmp_path = env
    s1 = _mk_sample(app, "voiceid_BVaa_P1_SPK_00.wav")
    persons.add_ref("圆脸", str(s1), None, "zh",
                    {"kind": "episode", "bvid": "BVaa", "part": 1,
                     "spk": "SPK_00"}, use="voiceid")
    _seed_persons_emb(app, [
        ("圆脸", "zh", [1, 0, 0], str(s1), "BVaa", 1, "SPK_00"),
    ])
    st, d = _req(port, "DELETE",
                 "/api/persons/" + quote("圆脸") + "/archive",
                 {"ref_path": str(s1)})
    assert st == 200 and d["ok"] is True
    st, d2 = _req(port, "DELETE",
                  "/api/persons/" + quote("圆脸") + "/archive",
                  {"ref_path": str(s1)})
    assert st == 404 and d2["ok"] is False
    assert not s1.exists()
    assert persons.list_refs("圆脸", use="voiceid") == []
    assert voiceid.VoiceArchive(
        path=app.persons_emb_path).entries(person="圆脸") == []


def test_archive_delete_unknown_ref_404_no_side_effect(env):
    """删从未存在的 ref_path → 404,档案与文件原样。"""
    app, port, lib, persons, orch, tmp_path = env
    s1 = _mk_sample(app, "voiceid_BVaa_P1_SPK_00.wav")
    persons.add_ref("圆脸", str(s1), None, "zh",
                    {"kind": "episode", "bvid": "BVaa", "part": 1,
                     "spk": "SPK_00"}, use="voiceid")
    _seed_persons_emb(app, [
        ("圆脸", "zh", [1, 0, 0], str(s1), "BVaa", 1, "SPK_00"),
    ])
    st, d = _req(port, "DELETE",
                 "/api/persons/" + quote("圆脸") + "/archive",
                 {"ref_path": str(tmp_path / "wa" / "nope.wav")})
    assert st == 404 and d["ok"] is False
    assert s1.exists()                                # 误删防护
    assert len(persons.list_refs("圆脸", use="voiceid")) == 1
    arch = voiceid.VoiceArchive(path=app.persons_emb_path)
    assert len(arch.entries(person="圆脸")) == 1


def test_archive_delete_missing_body_400(env):
    app, port, *_ = env
    st, d = _req(port, "DELETE",
                 "/api/persons/" + quote("圆脸") + "/archive", {})
    assert st == 400 and d["ok"] is False
    st, d2 = _req(port, "DELETE",
                  "/api/persons/" + quote("不存在") + "/archive",
                  {"ref_path": "x.wav"})
    assert st == 404 and d2["ok"] is False


def test_archive_delete_recalibrates_thresholds(env, monkeypatch):
    """删除改变档案 → 阈值重算被调用(D7 口径;mock 校准器记调用)。"""
    app, port, lib, persons, orch, tmp_path = env
    s1 = _mk_sample(app, "voiceid_BVaa_P1_SPK_00.wav")
    persons.add_ref("圆脸", str(s1), None, "zh",
                    {"kind": "episode", "bvid": "BVaa", "part": 1,
                     "spk": "SPK_00"}, use="voiceid")
    _seed_persons_emb(app, [
        ("圆脸", "zh", [1, 0, 0], str(s1), "BVaa", 1, "SPK_00"),
    ])
    calls = []
    monkeypatch.setattr(app, "_recalibrate_thresholds",
                        lambda: calls.append(1))
    st, d = _req(port, "DELETE",
                 "/api/persons/" + quote("圆脸") + "/archive",
                 {"ref_path": str(s1)})
    assert st == 200 and d["ok"] is True
    assert calls == [1] and d["recalibrated"] is True


# ---------- F11:失效条目不参与识别打分(读侧过滤) ----------

def test_invalid_entries_excluded_from_identify(env):
    """ref_path 文件缺失的档案条目在 _voiceid_compute 装载处被滤掉:
    打分均分不再被失效条目拉低(有效条 1.0 + 失效条 0 分 → 仍 1.0 非 0.5)。"""
    app, port, lib, persons, orch, tmp_path = env
    good = _mk_sample(app, "voiceid_good_P1_SPK_00.wav")
    ghost = tmp_path / "wa" / "ghost.wav"              # 不创建 → 失效
    _seed_persons_emb(app, [
        ("圆脸", "zh", [1, 0, 0], str(good), "BVaa", 1, "SPK_00"),
        ("圆脸", "zh", [0, 1, 0], str(ghost), "BVbb", 1, "SPK_01"),
    ])
    _write_thr(tmp_path)
    wdir = _mk_reviewable(lib, tmp_path)
    _write_emb(wdir, [[1, 0, 0]], ["SPK_00"])
    st, d = _req(port, "GET", f"/api/episode/{BV}")
    assert st == 200 and d["ok"] is True
    sug = d["parts"][0]["speakers"][0]["suggest"]
    assert sug["available"] is True
    assert sug["person"] == "圆脸"
    assert sug["score"] == pytest.approx(1.0)          # 失效条(0 分)未参与
    # 同口径:档案列表里失效条标记 valid=false
    st, a = _req(port, "GET", "/api/persons/" + quote("圆脸") + "/archive")
    zh = next(g for g in a["groups"] if g["lang"] == "zh")
    assert zh["count"] == 1 and zh["invalid"] == 1


def test_valid_entries_still_identify(env):
    """反例护栏:文件在场的条目照常参与识别(过滤不误杀)。"""
    app, port, lib, persons, orch, tmp_path = env
    good = _mk_sample(app, "voiceid_good_P1_SPK_00.wav")
    _seed_persons_emb(app, [
        ("圆脸", "zh", [1, 0, 0], str(good), "BVaa", 1, "SPK_00"),
    ])
    _write_thr(tmp_path)
    wdir = _mk_reviewable(lib, tmp_path)
    _write_emb(wdir, [[1, 0, 0]], ["SPK_00"])
    st, d = _req(port, "GET", f"/api/episode/{BV}")
    sug = d["parts"][0]["speakers"][0]["suggest"]
    assert sug["available"] is True and sug["verdict"] == "suggest"
    assert sug["person"] == "圆脸" and sug["score"] == pytest.approx(1.0)


# ---------- UI smoke(沿用静态页断言先例) ----------

def test_review_html_archive_ui_smoke(env):
    """人物卡档案区:列表渲染 + 试听元素 + 删除调用 + 失效标记(静态页含接线)。"""
    _, port, *_ = env
    st, body = _req(port, "GET", "/review.html")
    assert st == 200
    frags = [
        "声纹档案",                          # 人物卡档案区开关
        "toggleArchive",                     # 展开/收起
        "renderArchive",                     # 分组列表渲染
        "langGroup",                         # 组头"中文组 N 条"
        "文件缺失(失效)",                   # F11 失效标记
        "audio",                             # 条目行试听元素
        "delArchive",                        # 删除调用
        "method:'DELETE'",                   # DELETE 端点调用
        "/api/persons/",                     # 档案 API 路径
        "/archive",                          # 端点后缀
        "级联清理",                          # 删除确认文案(三者级联)
        "条失效",                            # 组头失效计数提示
    ]
    for frag in frags:
        assert frag in body, frag
