# -*- coding: utf-8 -*-
"""票05:核对门声纹建议接入(API+UI)合约测试。

组合方式沿用 tests/test_web_api.py:真实 Library/PersonLibrary/Orchestrator +
后台动作同步桩(App.sync_bg=True,响应返回时链已跑完);声纹侧全真——
voiceid.identify / VoiceArchive / save_thresholds 直接跑,合成向量,
spk_emb.npy 用 numpy 落(不依赖 pyannote/.venv-diar)。

覆盖:
- 分人完成钩子:下载→分析(桩)产出 spk_emb 两件 → 自动识别落
  work/<stem>/voiceid.json(结构=票02 identify 输出);桩/mock 分 P 无两件
  产物 → 落 {"available": false, "reason": "stub-diarization"};重跑分人
  (rediarize)完成后同样刷新。
- GET /api/episode 说话人 suggest 四态(suggest/uncalibrated/compete/桩不可用);
  compete 附 compete_with(胜者 spk+score);语言缺失带 lang_missing 标记。
- 档案(persons_emb.json)/阈值(voiceid_thresholds.json)比 voiceid.json 新
  → GET 时用 identify 重算一次再返回并落盘(mtime 触发)。
- api_review 提交路径不变(预填只是 UI 默认值,决策 JSON 仍人工提交)。
- /api/persons refs 全量返回且带 use 字段(F9:API 不过滤,展示层过滤)。
- UI smoke:review.html 含四态徽章文案/克隆过滤/预填逻辑(静态页断言先例)。
"""
import http.client
import json
import math
import os
import sys
import threading
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "work" / "tools"))

import web_server                    # noqa: E402  (被测模块)
from boke import attribute, diarize  # noqa: E402
from boke import orchestrator as orch_mod   # noqa: E402
from boke import preview as preview_mod     # noqa: E402
from boke import review_apply, voiceid      # noqa: E402
from boke.common import SpkSeg, write_rttm  # noqa: E402
from boke.library import (           # noqa: E402
    EV_ANALYSIS_DONE, EV_DOWNLOAD_DONE, ST_PREVIEW, Library,
)
from boke.orchestrator import Orchestrator   # noqa: E402
from boke.persons import PersonLibrary       # noqa: E402

BV = "BV1vid00005"
VID = f"{BV}_P1"

COS6, SIN6 = math.cos(math.radians(6)), math.sin(math.radians(6))


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


# ---------- 声纹侧造料 ----------

def _seed_archive(tmp_path):
    """声纹档案:圆脸 zh 两条同向 + 小外 en 一条正交(3 维合成向量)。"""
    arch = voiceid.VoiceArchive(path=tmp_path / "work" / "persons_emb.json")
    arch.add_entry("圆脸", "zh", [1, 0, 0], "y1.wav")
    arch.add_entry("圆脸", "zh", [1, 0, 0], "y2.wav")
    arch.add_entry("小外", "en", [0, 1, 0], "x1.wav")
    return arch


def _write_thr(tmp_path, **over):
    """zh 档阈值:SPK_00→suggest(过门+主持人门),SPK_02→unknown(栽跨语言门)。"""
    tier = {"calibrated": True, "genuine_n": 9, "impostor_n": 9,
            "cross_impostor_n": 9, "margin_n": 9,
            "thr": 0.50, "margin": 0.10, "thr_host": 0.90, "thr_cross": 0.95,
            "genuine_p5": 0.40, "genuine_p25": 0.60, "eer": 0.02}
    tier.update(over)
    voiceid.save_thresholds(
        {"version": 1, "langs": {"zh": tier}},
        tmp_path / "work" / "voiceid_thresholds.json")


def _write_emb(wdir, vectors, labels):
    """票01 产物:spk_emb.npy(num×dim)+ spk_emb.json(labels 原序)。"""
    wdir.mkdir(parents=True, exist_ok=True)
    np.save(wdir / "spk_emb.npy", np.array(vectors, dtype=np.float32))
    (wdir / "spk_emb.json").write_text(
        json.dumps({"labels": list(labels), "dim": len(vectors[0])}),
        encoding="utf-8")


def _mk_reviewable(lib, tmp_path, speakers=("SPK_00", "SPK_01"),
                   langs=None, bv=BV, part=1):
    """造一条待核对记录:媒体+tagged.srt+langid.json 产物齐(langs={spk: lang|None})。"""
    langs = langs or {s: "zh" for s in speakers}
    media = tmp_path / "media" / f"{bv}_P{part}.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"fake mp4")
    lib.add(bv, part, title="声纹建议测试期", media_path=str(media), duration=94.0)
    lib.transition(bv, part, EV_DOWNLOAD_DONE, media_path=str(media))
    lib.transition(bv, part, EV_ANALYSIS_DONE)
    wdir = tmp_path / "work" / f"{bv}_P{part}"
    wdir.mkdir(parents=True, exist_ok=True)
    segs = [SpkSeg(0.5 + i * 3.0, 2.0 + i * 3.0, s)
            for i, s in enumerate(speakers)]
    write_rttm(wdir / "diar.rttm", segs)
    tagged = "".join(
        f"{i + 1}\n00:00:{i * 3:02d},500 --> 00:00:{i * 3 + 2:02d},000\n"
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


def _suggest_by_id(port, bv=BV):
    st, d = _req(port, "GET", f"/api/episode/{bv}")
    assert st == 200 and d["ok"] is True
    return {s["id"]: s["suggest"] for s in d["parts"][0]["speakers"]}


# ---------- 导入链桩(同 test_web_api 先例) ----------

def _ok_cookie(monkeypatch):
    monkeypatch.setattr(orch_mod, "check_cookie",
                        lambda path=None: (True, "cookie 有效"))


def _fake_view(monkeypatch):
    view = {"title": "声纹导入期", "pubdate": 1758240000, "duration": 2817,
            "pages": [{"page": 1, "duration": 1400}]}
    monkeypatch.setattr(orch_mod, "_fetch_view", lambda bv: view)


def _stub_ytdlp(monkeypatch):
    def fake(cmd, **kw):
        o = cmd[cmd.index("-o") + 1]
        out = Path(o.replace("%(ext)s", "mp4"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake mp4")
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(orch_mod, "_subprocess", fake)


def _stub_analyze(monkeypatch, emb_vectors, labels, langid_speakers):
    """分析链桩:推进 analysis_done,落 langid.json 与(可选)spk_emb 两件。"""
    def fake(self, bvid, part):
        rec = self.lib.get(bvid, part)
        assert rec["status"] == "待分析"
        stem = Path(rec["media_path"]).stem
        wdir = Path(self.work_dir) / stem
        wdir.mkdir(parents=True, exist_ok=True)
        (wdir / "langid.json").write_text(json.dumps(
            {"verdict": "dub_需要配音", "speakers": langid_speakers},
            ensure_ascii=False), encoding="utf-8")
        if emb_vectors:
            _write_emb(wdir, emb_vectors, labels)
        self.lib.update(bvid, part, artifacts={
            "langid": str(wdir / "langid.json"), "langid_suggest": "dub"})
        return {"ok": True,
                "record": self.lib.transition(bvid, part, EV_ANALYSIS_DONE)}
    monkeypatch.setattr(Orchestrator, "analyze", fake)


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


def _bump(path, ref_mtime=None):
    """把文件 mtime 顶到 ref 之后(默认 now+10),模拟档案/阈值更新。"""
    t = (ref_mtime + 5) if ref_mtime is not None else (os.path.getmtime(path) + 10)
    os.utime(path, (t, t))


# ---------- 分人完成钩子 ----------

def test_analyze_hook_identifies_and_writes_voiceid(env, monkeypatch):
    app, port, lib, persons, orch, tmp_path = env
    _seed_archive(tmp_path)
    _write_thr(tmp_path)
    _ok_cookie(monkeypatch)
    _fake_view(monkeypatch)
    _stub_ytdlp(monkeypatch)
    _stub_analyze(monkeypatch, emb_vectors=[[1, 0, 0], [0, 1, 0]],
                  labels=["SPK_00", "SPK_01"],
                  langid_speakers={"SPK_00": {"lang": "zh"},
                                   "SPK_01": {"lang": "zh"}})
    st, d = _req(port, "POST", "/api/ingest", {"bv": BV})
    assert st == 200 and d["ok"] is True
    vj = tmp_path / "work" / VID / "voiceid.json"
    assert vj.exists()                                 # 钩子自动落识别结果
    data = json.loads(vj.read_text(encoding="utf-8"))
    assert data["available"] is True
    by = {r["spk"]: r for r in data["results"]}
    assert by["SPK_00"]["person"] == "圆脸"
    assert by["SPK_00"]["verdict"] == "suggest"
    assert by["SPK_00"]["host_candidate"] is True      # 1.0 ≥ thr_host 0.90
    assert data["host_candidates"] == ["SPK_00"]
    # SPK_01 走跨语言档(thr_cross 0.95):小外 1.0 过门 → suggest
    assert by["SPK_01"]["person"] == "小外" and by["SPK_01"]["verdict"] == "suggest"


def test_analyze_hook_stub_part_writes_unavailable(env, monkeypatch):
    app, port, lib, persons, orch, tmp_path = env
    _ok_cookie(monkeypatch)
    _fake_view(monkeypatch)
    _stub_ytdlp(monkeypatch)
    _stub_analyze(monkeypatch, emb_vectors=None, labels=[],
                  langid_speakers={"SPK_00": {"lang": "zh"}})
    st, d = _req(port, "POST", "/api/ingest", {"bv": BV})
    assert st == 200 and d["ok"] is True
    vj = tmp_path / "work" / VID / "voiceid.json"
    assert vj.exists()
    assert json.loads(vj.read_text(encoding="utf-8")) == \
        {"available": False, "reason": "stub-diarization"}


def test_rediarize_reruns_voiceid(env, monkeypatch):
    app, port, lib, persons, orch, tmp_path = env
    _seed_archive(tmp_path)
    _write_thr(tmp_path)
    _mk_reviewable(lib, tmp_path)                      # 旧分 P:2 人,无 emb
    vj = tmp_path / "work" / VID / "voiceid.json"

    def fake_diar(video, work_dir="work", **kw):
        w = Path(work_dir) / Path(video).stem
        w.mkdir(parents=True, exist_ok=True)
        write_rttm(w / "diar.rttm", [SpkSeg(0.5, 2.0, "SPK_00"),
                                     SpkSeg(3.0, 5.5, "SPK_01"),
                                     SpkSeg(6.0, 8.0, "SPK_02")])
        _write_emb(w, [[1, 0, 0], [COS6, SIN6, 0], [0.6, 0.8, 0]],
                   ["SPK_00", "SPK_01", "SPK_02"])
        return {"rttm": str(w / "diar.rttm")}

    def fake_attr(video, work_dir="work", **kw):
        w = Path(work_dir) / Path(video).stem
        tagged = ("1\n00:00:00,500 --> 00:00:02,000\n[SPK_00] a\n\n"
                  "2\n00:00:03,000 --> 00:00:05,500\n[SPK_01] b\n\n"
                  "3\n00:00:06,000 --> 00:00:08,000\n[SPK_02] c\n")
        (w / "tagged.srt").write_text(tagged, encoding="utf-8")
        return {"tagged": str(w / "tagged.srt")}

    def fake_langid(self, stem):
        out = Path(self.work_dir) / stem / "langid.json"
        out.write_text(json.dumps({"verdict": "dub_需要配音",
                                   "speakers": {s: {"lang": "zh"}
                                                for s in ("SPK_00", "SPK_01",
                                                          "SPK_02")}},
                                  ensure_ascii=False), encoding="utf-8")
        return {"langid": str(out), "suggest": "dub"}

    monkeypatch.setattr(diarize, "run", fake_diar)
    monkeypatch.setattr(attribute, "run", fake_attr)
    monkeypatch.setattr(Orchestrator, "_run_langid", fake_langid)

    dec = {"verdict": "dub", "speakers": [], "rediarize": {"max_speakers": 3}}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True and d["voided"] is True
    assert vj.exists()                                 # 重跑分人后钩子刷新
    data = json.loads(vj.read_text(encoding="utf-8"))
    assert data["available"] is True
    by = {r["spk"]: r for r in data["results"]}
    assert set(by) == {"SPK_00", "SPK_01", "SPK_02"}
    assert by["SPK_00"]["verdict"] == "suggest"
    assert by["SPK_01"]["verdict"] == "compete"        # 与 SPK_00 争圆脸落选


# ---------- GET /api/episode:suggest 四态 ----------

def test_episode_suggest_suggest_compete_unknown(env):
    app, port, lib, persons, orch, tmp_path = env
    _seed_archive(tmp_path)
    _write_thr(tmp_path)
    _mk_reviewable(lib, tmp_path, speakers=("SPK_00", "SPK_01", "SPK_02"),
                   langs={"SPK_00": "zh", "SPK_01": "zh", "SPK_02": None})
    wdir = tmp_path / "work" / VID
    _write_emb(wdir, [[1, 0, 0], [COS6, SIN6, 0], [0.6, 0.8, 0]],
               ["SPK_00", "SPK_01", "SPK_02"])

    by = _suggest_by_id(port)
    s0 = by["SPK_00"]                                  # suggest 态
    assert s0["available"] is True and s0["verdict"] == "suggest"
    assert s0["person"] == "圆脸" and s0["score"] == pytest.approx(1.0)
    assert s0["calibrated"] is True and s0["host_candidate"] is True
    s1 = by["SPK_01"]                                  # compete 态
    assert s1["verdict"] == "compete" and s1["person"] == "圆脸"
    assert s1["compete_with"] == {"spk": "SPK_00", "score": pytest.approx(1.0)}
    s2 = by["SPK_02"]                                  # unknown 态(栽跨语言门)
    assert s2["verdict"] == "unknown" and s2["person"] == "小外"
    assert s2["score"] == pytest.approx(0.8, abs=1e-6)
    assert s2.get("lang_missing") is True              # 语言缺失带标记
    assert (wdir / "voiceid.json").exists()            # 懒算结果已落盘


def test_episode_suggest_uncalibrated(env):
    app, port, lib, persons, orch, tmp_path = env
    _seed_archive(tmp_path)
    _write_thr(tmp_path, calibrated=False)             # 冷启动:未校准
    _mk_reviewable(lib, tmp_path)
    _write_emb(tmp_path / "work" / VID, [[1, 0, 0], [COS6, SIN6, 0]],
               ["SPK_00", "SPK_01"])
    by = _suggest_by_id(port)
    s0 = by["SPK_00"]
    assert s0["available"] is True and s0["verdict"] == "uncalibrated"
    assert s0["calibrated"] is False
    assert s0["host_candidate"] is True                # 候选显示保留,预填由 UI 关
    assert by["SPK_01"]["verdict"] == "compete"        # compete+uncalibrated 并存


def test_episode_stub_part_suggest_unavailable(env):
    """桩/mock 分 P(无 spk_emb 两件):返回 available:false;GET 不代写桩标记。"""
    app, port, lib, persons, orch, tmp_path = env
    _mk_reviewable(lib, tmp_path)                      # 不落 emb 产物
    by = _suggest_by_id(port)
    assert by == {"SPK_00": {"available": False, "reason": "stub-diarization"},
                  "SPK_01": {"available": False, "reason": "stub-diarization"}}
    assert not (tmp_path / "work" / VID / "voiceid.json").exists()


# ---------- 档案/阈值更新触发重算 ----------

def test_persons_emb_newer_triggers_recompute(env):
    app, port, lib, persons, orch, tmp_path = env
    _seed_archive(tmp_path)
    _write_thr(tmp_path)
    _mk_reviewable(lib, tmp_path)
    _write_emb(tmp_path / "work" / VID, [[1, 0, 0], [0, 1, 0]],
               ["SPK_00", "SPK_01"])
    vj = tmp_path / "work" / VID / "voiceid.json"
    emb = tmp_path / "work" / "persons_emb.json"

    by = _suggest_by_id(port)
    assert by["SPK_00"]["verdict"] == "suggest"        # 首次懒算落盘
    assert vj.exists()

    arch = voiceid.VoiceArchive(path=emb)              # 档案更新(圆脸被删)
    arch.remove_person("圆脸")
    _bump(emb, vj.stat().st_mtime)                     # mtime 顶到结果之后

    by = _suggest_by_id(port)
    assert by["SPK_00"]["verdict"] == "unknown"        # 重算:圆脸没了
    assert by["SPK_00"]["person"] == "小外"
    data = json.loads(vj.read_text(encoding="utf-8"))  # 重算结果落盘
    row = next(r for r in data["results"] if r["spk"] == "SPK_00")
    assert row["verdict"] == "unknown" and row["person"] == "小外"


def test_thresholds_newer_triggers_recompute(env):
    app, port, lib, persons, orch, tmp_path = env
    _seed_archive(tmp_path)
    _write_thr(tmp_path)
    _mk_reviewable(lib, tmp_path)
    _write_emb(tmp_path / "work" / VID, [[1, 0, 0], [0, 1, 0]],
               ["SPK_00", "SPK_01"])
    vj = tmp_path / "work" / VID / "voiceid.json"
    thr = tmp_path / "work" / "voiceid_thresholds.json"

    by = _suggest_by_id(port)
    assert by["SPK_00"]["verdict"] == "suggest"
    _write_thr(tmp_path, calibrated=False)             # 阈值文件重写
    _bump(thr, vj.stat().st_mtime)

    by = _suggest_by_id(port)
    assert by["SPK_00"]["verdict"] == "uncalibrated"   # 重算吃新阈值


# ---------- api_review 不变 + /api/persons use 字段 ----------

def test_review_submission_unchanged_with_suggest(env, monkeypatch):
    """预填只是 UI 默认值:api_review 收到的决策原样落账,链路照旧。"""
    app, port, lib, persons, orch, tmp_path = env
    _seed_archive(tmp_path)
    _write_thr(tmp_path)
    _mk_reviewable(lib, tmp_path)
    _write_emb(tmp_path / "work" / VID, [[1, 0, 0], [0, 1, 0]],
               ["SPK_00", "SPK_01"])
    by = _suggest_by_id(port)
    assert by["SPK_00"]["verdict"] == "suggest"        # 建议在场
    calls = []
    _stub_preview(monkeypatch, calls)
    dec = {"verdict": "dub", "speakers": [
        {"id": "SPK_00", "note": "", "host": False, "host_person": None,
         "merge_into": None, "voice": "preset",
         "preset_voice": "zh-CN-YunjianNeural", "clone_ref": None},
        {"id": "SPK_01", "note": "", "host": False, "host_person": None,
         "merge_into": None, "voice": "preset",
         "preset_voice": "zh-CN-YunxiNeural", "clone_ref": None}]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True and d["status"] == ST_PREVIEW
    assert calls == [(BV, 1)]                          # 小样照常触发
    rec = lib.get(BV, 1)
    assert rec["review"] == dec                        # 决策原样,服务端不掺建议


def test_persons_api_returns_full_refs_with_use(env):
    """F9(API 侧决策):/api/persons 全量返回 refs 并带 use 字段,不在 API 过滤;
    展示层(核对台人物卡)自行过滤。"""
    app, port, lib, persons, orch, tmp_path = env
    persons.add_ref("圆脸", "work/web/audio/auto_spk.wav", None, "zh",
                    {"kind": "episode", "bvid": BV, "part": 1, "spk": "SPK_00"},
                    use="voiceid")
    st, d = _req(port, "GET", "/api/persons")
    assert st == 200 and d["ok"] is True
    yuan = next(p for p in d["persons"] if p["name"] == "圆脸")
    uses = [r.get("use") for r in yuan["refs"]]
    assert uses[:2] == ["clone", "clone"]              # 种子两条(clone)
    assert uses[-1] == "voiceid"                       # 新自动归档样本也在(API 不藏)


# ---------- UI smoke(review.html 四态渲染/预填/克隆过滤) ----------

def test_review_html_voiceid_ui_smoke(env):
    _, port, *_ = env
    st, body = _req(port, "GET", "/review.html")
    assert st == 200
    frags = [
        "声纹建议不可用(分人为桩数据)",   # stub 态小字
        "声纹建议:",                        # suggest 态徽章
        "未校准,仅参考",                    # uncalibrated 态徽章
        ",未建议",                           # compete 态徽章
        "自动归档声纹样本",                  # 克隆过滤后的计数提示
        "host_candidate",                    # 主持人预填双真条件
        "calibrated",
        "/api/review",                       # 决策仍人工提交
    ]
    for frag in frags:
        assert frag in body, frag
