# -*- coding: utf-8 -*-
"""Web API 路由单测(07 号票):真实模块组合 + 后台动作同步桩。

组合方式:Library/PersonLibrary/Orchestrator/review_apply 全用真实现,
下载→分析、小样、全片、重跑分人四类后台动作按票面 monkeypatch 成同步桩,
App.sync_bg=True 让 spawn 内联执行——HTTP 响应返回时后台链已跑完可直接断言。
服务实例:make_server 绑 127.0.0.1:0(随机端口,不占 8765,副作用声明)。

覆盖验收:/api/library 全量+管道计数+待办+清单;/api/episode 期详情
(说话人样本/langid 结论/状态/小样/成品);/api/ingest BV 与清单两入口 +
cookie 失效 423;/api/review 三分支(dub→03 应用器+触发 05 小样 /
skip→confirm_skip / rediarize→决策作废信号+回退);/api/preview 过与不过;
/api/retry 按失败前主态路由;/api/persons 读与收录(episode 来源必须带 part,
评审 R1);既有静态路由(/ /library.html /work 音频)不回归。
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

import web_server                      # noqa: E402  (被测模块)
from boke import attribute, diarize    # noqa: E402
from boke import fullmix, orchestrator as orch_mod   # noqa: E402
from boke import preview as preview_mod   # noqa: E402
from boke import review_apply          # noqa: E402
from boke.common import SpkSeg, write_rttm   # noqa: E402
from boke.library import (             # noqa: E402
    EV_ANALYSIS_DONE, EV_DOWNLOAD_DONE, EV_FAIL, EV_FULL_DONE, EV_PREVIEW_PASS,
    EV_PREVIEW_READY, EV_REVIEW_SUBMIT, ST_DELIVERED, ST_EXCLUDED,
    ST_FAILED, ST_PENDING_ANALYSIS, ST_PENDING_DOWNLOAD, ST_PENDING_REVIEW,
    ST_PREVIEW, ST_REVIEWED, ST_SYNTHESIZING, Library,
)
from boke.orchestrator import Orchestrator   # noqa: E402
from boke.persons import PersonLibrary   # noqa: E402

BV = "BV1web00001"
VID = f"{BV}_P1"

OCR_SRT = ("1\n00:00:00,500 --> 00:00:02,000\nhello world\n\n"
           "2\n00:00:03,000 --> 00:00:05,500\nhi there\n")
TAGGED_SRT = ("1\n00:00:00,500 --> 00:00:02,000\n[SPK_00] hello world\n\n"
              "2\n00:00:03,000 --> 00:00:05,500\n[SPK_01] hi there\n")


# ---------- 环境与服务 ----------

@pytest.fixture
def env(tmp_path):
    """起好服务的测试环境(后台同步执行);yield (app, port, lib, persons, orch)。"""
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
    """最小 HTTP 客户端:JSON 接口返回 dict,静态页返回文本。"""
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


# ---------- 记录与决策构造 ----------

def _mk_reviewable(lib, tmp_path, bv=BV, part=1, title="北爱威尔士脱英"):
    """造一条待核对记录:媒体在盘,分析产物(ocr/diar/tagged/langid)齐。"""
    media = tmp_path / "media" / f"{bv}_P{part}.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"fake mp4")
    lib.add(bv, part, title=title, media_path=str(media), duration=94.0)
    lib.transition(bv, part, EV_DOWNLOAD_DONE, media_path=str(media))
    lib.transition(bv, part, EV_ANALYSIS_DONE)
    wdir = tmp_path / "work" / f"{bv}_P{part}"
    wdir.mkdir(parents=True, exist_ok=True)
    (wdir / "ocr.srt").write_text(OCR_SRT, encoding="utf-8")
    write_rttm(wdir / "diar.rttm", [SpkSeg(0.5, 2.0, "SPK_00"),
                                    SpkSeg(3.0, 5.5, "SPK_01")])
    (wdir / "tagged.srt").write_text(TAGGED_SRT, encoding="utf-8")
    (wdir / "langid.json").write_text(json.dumps({
        "verdict": "dub_需要配音",
        "speakers": {"SPK_00": {"lang": "en"}, "SPK_01": {"lang": "en"}}},
        ensure_ascii=False), encoding="utf-8")
    lib.update(bv, part, artifacts={
        "srt": str(wdir / "ocr.srt"), "rttm": str(wdir / "diar.rttm"),
        "tagged": str(wdir / "tagged.srt"), "audio": str(wdir / "audio.wav"),
        "langid": str(wdir / "langid.json"), "langid_suggest": "dub"})
    return media


def _decision_dub():
    return {"verdict": "dub", "speakers": [
        {"id": "SPK_00", "note": "主持", "host": False, "host_person": None,
         "merge_into": None, "voice": "preset",
         "preset_voice": "zh-CN-YunjianNeural", "clone_ref": None},
        {"id": "SPK_01", "note": "", "host": False, "host_person": None,
         "merge_into": None, "voice": "preset",
         "preset_voice": "zh-CN-YunxiNeural", "clone_ref": None}]}


def _mk_reviewed(lib, tmp_path, decision=None, bv=BV, part=1):
    """推进到已核对待合成(+dec 目录产物与 dec_hash)。"""
    decision = decision or _decision_dub()
    media = _mk_reviewable(lib, tmp_path, bv=bv, part=part)
    review_apply.apply_decision(decision, str(media), str(tmp_path / "work"),
                                part=part)
    lib.transition(bv, part, EV_REVIEW_SUBMIT, review=decision)
    lib.update(bv, part, dec_hash=review_apply.dec_hash(decision))
    return decision, media


def _mk_preview_ready(lib, tmp_path, decision=None, bv=BV, part=1):
    """推进到小样待听:提交决策→dec 目录产物→小样文件→preview_ready。"""
    decision, media = _mk_reviewed(lib, tmp_path, decision=decision, bv=bv,
                                   part=part)
    h = review_apply.dec_hash(decision)
    mp3 = tmp_path / "work" / f"{bv}_P{part}" / f"dec_{h}" / "preview.mp3"
    mp3.parent.mkdir(parents=True, exist_ok=True)
    mp3.write_bytes(b"fake preview mp3")
    lib.transition(bv, part, EV_PREVIEW_READY, preview_path=str(mp3))
    return decision, mp3


def _stub_preview(monkeypatch, calls):
    """05 小样桩:记录调用,按真模块同款落 preview_ready(小样待听)。"""
    def fake(bvid, part, decisions=None, **kw):
        calls.append((bvid, part))
        lib = kw["library"]
        rec = lib.get(bvid, part)
        assert rec["status"] == ST_REVIEWED
        h = rec["dec_hash"] or review_apply.dec_hash(rec["review"])
        vid = Path(rec["media_path"]).stem
        mp3 = Path(kw["work_root"]) / vid / f"dec_{h}" / "preview.mp3"
        mp3.parent.mkdir(parents=True, exist_ok=True)
        mp3.write_bytes(b"fake preview mp3")
        lib.transition(bvid, part, EV_PREVIEW_READY, preview_path=str(mp3))
        return {"status": "applied", "preview": str(mp3)}
    monkeypatch.setattr(preview_mod, "generate_preview", fake)


def _stub_fullmix(monkeypatch, calls):
    """06 全片桩:记录调用,过门(仅小样待听时,镜像真实现)→成品→已交付。"""
    def fake(bvid, part, **kw):
        calls.append((bvid, part, Path(kw["work_dir"])))
        lib = kw["lib"]
        if lib.get(bvid, part)["status"] == ST_PREVIEW:
            lib.transition(bvid, part, EV_PREVIEW_PASS)
        rec = lib.get(bvid, part)
        h = rec["dec_hash"]
        vid = Path(rec["media_path"]).stem
        out = Path(kw["work_dir"]) / vid / f"dec_{h}" / vid / f"{vid}.m4a"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake m4a")
        lib.transition(bvid, part, EV_FULL_DONE, output_path=str(out))
        return {"ok": True, "record": lib.get(bvid, part), "output_path": str(out)}
    monkeypatch.setattr(fullmix, "run_full_mix", fake)


def _ok_cookie(monkeypatch):
    monkeypatch.setattr(orch_mod, "check_cookie",
                        lambda path=None: (True, "cookie 有效"))


def _fake_view(monkeypatch, pages=(1,)):
    view = {"title": "导入期", "pubdate": 1758240000, "duration": 2817,
            "is_charging_arc": 1,
            "pages": [{"page": p, "duration": 1400 + p} for p in pages]}
    monkeypatch.setattr(orch_mod, "_fetch_view", lambda bv: view)


def _stub_ytdlp(monkeypatch, orch):
    """yt-dlp 桩:按 -o 模板在 media_dir 造出假 mp4。"""
    def fake(cmd, **kw):
        o = cmd[cmd.index("-o") + 1]
        out = Path(o.replace("%(ext)s", "mp4"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake mp4")
        import subprocess
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(orch_mod, "_subprocess", fake)


def _stub_analyze(monkeypatch):
    """分析链桩:直接推进 analysis_done(链本身 04 已测)。"""
    def fake(self, bvid, part):
        assert self.lib.get(bvid, part)["status"] == ST_PENDING_ANALYSIS
        return {"ok": True,
                "record": self.lib.transition(bvid, part, EV_ANALYSIS_DONE)}
    monkeypatch.setattr(Orchestrator, "analyze", fake)


# ---------- 静态路由不回归 ----------

def test_static_pages_served(env):
    _, port, *_ = env
    st, body = _req(port, "GET", "/")
    assert st == 200 and "剥壳配音" in body
    st, body = _req(port, "GET", "/library.html")
    assert st == 200 and "/api/library" in body     # 页面已接真数据源
    st, body = _req(port, "GET", "/review.html")
    assert st == 200 and "/api/episode" in body


def test_work_route_serves_audio_and_blocks_others(env):
    app, port, _, _, _, tmp_path = env
    mp3 = tmp_path / "work" / VID / "dec_ab12" / "preview.mp3"
    mp3.parent.mkdir(parents=True)
    mp3.write_bytes(b"fake mp3")
    st = _req(port, "GET", f"/work/{VID}/dec_ab12/preview.mp3")[0]
    assert st == 200
    # 非音频扩展与越界路径一律 404(不泄露账本/人物库 JSON)
    assert _req(port, "GET", "/work/library.json")[0] == 404
    assert _req(port, "GET", "/work/../persons.json")[0] == 404


# ---------- GET /api/library ----------

def test_api_library_counts_todo_catalog_and_urls(env):
    app, port, lib, _, _, tmp_path = env
    _mk_reviewable(lib, tmp_path)                      # 待核对
    lib.add("BV1web00002", 1, title="已交付期")
    out = tmp_path / "work" / "BV1web00002_P1" / "dec_x" / "v.m4a"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"m4a")
    lib.transition("BV1web00002", 1, EV_DOWNLOAD_DONE, media_path="m.mp4")
    lib.transition("BV1web00002", 1, EV_ANALYSIS_DONE)
    lib.transition("BV1web00002", 1, EV_REVIEW_SUBMIT,
                   review={"verdict": "dub", "speakers": []})
    lib.transition("BV1web00002", 1, EV_PREVIEW_READY, preview_path="p")
    lib.transition("BV1web00002", 1, EV_PREVIEW_PASS)
    lib.transition("BV1web00002", 1, EV_FULL_DONE, output_path=str(out))
    app.catalog_path.write_text(json.dumps([
        {"bvid": "BV1cat00003", "title": "清单期", "length": "14:27"}],
        ensure_ascii=False), encoding="utf-8")

    st, d = _req(port, "GET", "/api/library")
    assert st == 200 and d["ok"] is True
    assert d["counts"] == {"待核对": 1, "已交付": 1}
    assert d["todo"] == [{"bvid": BV, "part": 1, "title": "北爱威尔士脱英",
                          "status": "待核对"}]
    assert d["catalog"][0]["bvid"] == "BV1cat00003"
    by_key = {(r["bvid"], r["part"]): r for r in d["records"]}
    assert by_key[(BV, 1)]["status"] == "待核对"
    delivered = by_key[("BV1web00002", 1)]
    assert delivered["output_url"] == "/work/BV1web00002_P1/dec_x/v.m4a"
    assert by_key[(BV, 1)]["media_url"] == f"/media/{VID}.mp4"


# ---------- GET /api/episode ----------

def test_api_episode_detail(env):
    app, port, lib, _, _, tmp_path = env
    _mk_preview_ready(lib, tmp_path)
    # 说话人样本与克隆候选放进 web 音频目录
    for name in (f"speaker_{VID}_SPK_00_sample.wav", f"cand_{VID}_SPK_01_1.wav",
                 f"cand_{VID}_SPK_01_2.wav"):
        (app.web_audio_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (app.web_audio_dir / name).write_bytes(b"wav")

    st, d = _req(port, "GET", f"/api/episode/{BV}")
    assert st == 200 and d["ok"] is True and d["bvid"] == BV
    assert d["title"] == "北爱威尔士脱英"
    p = d["parts"][0]
    assert p["part"] == 1 and p["status"] == ST_PREVIEW and p["busy"] is False
    assert p["media_url"] == f"/media/{VID}.mp4"
    assert p["suggest"] == "dub"
    assert p["langid"]["verdict"] == "dub_需要配音"
    spk0, spk1 = p["speakers"]
    assert spk0["id"] == "SPK_00" and spk0["lang"] == "en"
    assert spk0["talk_sec"] == 1.5 and spk1["talk_sec"] == 2.5
    assert spk0["sample"] == f"/audio/speaker_{VID}_SPK_00_sample.wav"
    assert spk1["cands"] == [f"/audio/cand_{VID}_SPK_01_1.wav",
                             f"/audio/cand_{VID}_SPK_01_2.wav"]
    assert p["preview_url"].startswith("/work/") and \
        p["preview_url"].endswith("preview.mp3")
    assert p["preview_stale"] is False
    assert p["dec_hash"] == review_apply.dec_hash(_decision_dub())


def test_api_episode_unknown_404(env):
    _, port, *_ = env
    st, d = _req(port, "GET", "/api/episode/BV1nope0000")
    assert st == 404 and d["ok"] is False


# ---------- POST /api/ingest ----------

def test_ingest_cookie_invalid_423(env, monkeypatch):
    _, port, lib, _, _, _ = env
    reason = "B 站说登录已失效(nav 返回 -101),请重新导出 cookie 后再导入。"
    monkeypatch.setattr(orch_mod, "check_cookie", lambda path=None: (False, reason))
    st, d = _req(port, "POST", "/api/ingest", {"bv": BV})
    assert st == 423
    assert d["ok"] is False and reason in d["error"]
    assert lib.query() == []                        # cookie 门拦住,账本不动


def test_ingest_bv_registers_and_drives_download_then_analyze(
        env, monkeypatch):
    app, port, lib, _, orch, _ = env
    _ok_cookie(monkeypatch)
    _fake_view(monkeypatch)
    _stub_ytdlp(monkeypatch, orch)
    _stub_analyze(monkeypatch)

    st, d = _req(port, "POST", "/api/ingest", {"bv": BV})
    assert st == 200 and d["ok"] is True and d["added"] == 1
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_PENDING_REVIEW       # 下载+分析同步桩已跑完
    assert rec["busy"] is False
    assert rec["media_path"].endswith(f"{VID}.mp4")
    assert rec["title"] == "导入期" and rec["is_charge"] is True


def test_ingest_catalog_items(env, monkeypatch):
    _, port, lib, _, orch, _ = env
    _ok_cookie(monkeypatch)
    _stub_ytdlp(monkeypatch, orch)
    _stub_analyze(monkeypatch)
    items = [{"bvid": "BV1cat00002", "title": "清单期", "length": "14:27",
              "created": 1758000000, "is_charging_arc": 1}]
    st, d = _req(port, "POST", "/api/ingest", {"items": items})
    assert st == 200 and d["ok"] is True and d["added"] == 1
    rec = lib.get("BV1cat00002", 1)
    assert rec["duration"] == 867 and rec["is_charge"] is True
    # 清单勾选与 BV 粘贴同待遇:登记即后台下载+分析,响应时已推进到待核对
    assert rec["status"] == ST_PENDING_REVIEW and rec["busy"] is False


def test_ingest_bad_body_400(env, monkeypatch):
    _, port, lib, _, _, _ = env
    _ok_cookie(monkeypatch)
    st, d = _req(port, "POST", "/api/ingest", {})
    assert st == 400 and d["ok"] is False
    st, d = _req(port, "POST", "/api/ingest", {"bv": " ", "items": []})
    assert st == 400


# ---------- POST /api/review ----------

def test_review_dub_applies_decision_and_triggers_preview(env, monkeypatch):
    _, port, lib, _, _, tmp_path = env
    _mk_reviewable(lib, tmp_path)
    calls = []
    _stub_preview(monkeypatch, calls)
    dec = _decision_dub()

    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True
    assert d["status"] == ST_PREVIEW                # 小样桩同步跑完
    assert calls == [(BV, 1)]
    rec = lib.get(BV, 1)
    assert rec["review"] == dec
    assert rec["dec_hash"] == review_apply.dec_hash(dec)
    # 决策目录真产物(03 应用器真跑):配置+哨兵在,提交即落 dec 目录
    dec_dir = tmp_path / "work" / VID / f"dec_{rec['dec_hash']}"
    assert (dec_dir / "voices.generated.yaml").exists()
    assert (dec_dir / "voices.generated.yaml.done").exists()
    assert d["dec_dir"] == str(dec_dir)


def test_review_flat_body_shape_accepted(env, monkeypatch):
    """旧核对台 flat 提交形状({verdict,speakers} 顶层级)同样受理。"""
    _, port, lib, _, _, tmp_path = env
    _mk_reviewable(lib, tmp_path)
    calls = []
    _stub_preview(monkeypatch, calls)
    dec = _decision_dub()
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, **dec})
    assert st == 200 and d["ok"] is True and calls == [(BV, 1)]


def test_review_decision_error_400_no_ledger_change(env, monkeypatch):
    _, port, lib, _, _, tmp_path = env
    _mk_reviewable(lib, tmp_path)
    calls = []
    _stub_preview(monkeypatch, calls)
    bad = {"verdict": "dub", "speakers": [
        {"id": "SPK_00", "note": "", "host": True, "host_person": "不存在的",
         "merge_into": None, "voice": "clone", "clone_ref": None}]}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": bad})
    assert st == 400 and d["ok"] is False and "人物库" in d["error"]
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_PENDING_REVIEW and rec["review"] is None
    assert calls == []


def test_review_skip_confirms_exclusion(env, monkeypatch):
    _, port, lib, _, _, tmp_path = env
    _mk_reviewable(lib, tmp_path)
    calls = []
    _stub_preview(monkeypatch, calls)
    dec = {"verdict": "skip", "speakers": [], "note": "全中文,用户确认跳过"}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True and d["status"] == ST_EXCLUDED
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_EXCLUDED and rec["review"]["verdict"] == "skip"
    assert calls == []                              # 排除不触发小样


def test_review_rediarize_voids_decision_and_reruns(env, monkeypatch):
    _, port, lib, persons, orch, tmp_path = env
    _mk_reviewable(lib, tmp_path)
    calls = []

    def fake_diar(video, work_dir="work", **kw):
        calls.append(("diarize", kw.get("max_speakers"), kw.get("force")))
        w = Path(work_dir) / Path(video).stem
        w.mkdir(parents=True, exist_ok=True)
        write_rttm(w / "diar.rttm", [SpkSeg(0.5, 2.0, "SPK_00"),
                                     SpkSeg(3.0, 5.5, "SPK_01"),
                                     SpkSeg(6.0, 8.0, "SPK_02")])
        return {"rttm": str(w / "diar.rttm")}

    def fake_attr(video, work_dir="work", **kw):
        calls.append(("attribute", kw.get("force")))
        w = Path(work_dir) / Path(video).stem
        tagged = ("1\n00:00:00,500 --> 00:00:02,000\n[SPK_00] a\n\n"
                  "2\n00:00:03,000 --> 00:00:05,500\n[SPK_01] b\n\n"
                  "3\n00:00:06,000 --> 00:00:08,000\n[SPK_02] c\n")
        (w / "tagged.srt").write_text(tagged, encoding="utf-8")
        return {"tagged": str(w / "tagged.srt")}

    def fake_langid(self, stem):
        calls.append("langid")
        out = Path(self.work_dir) / stem / "langid.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"verdict": "dub_需要配音",
                                   "speakers": {"SPK_02": {"lang": "ja"}}},
                                  ensure_ascii=False), encoding="utf-8")
        return {"langid": str(out), "suggest": "dub"}

    monkeypatch.setattr(diarize, "run", fake_diar)
    monkeypatch.setattr(attribute, "run", fake_attr)
    monkeypatch.setattr(Orchestrator, "_run_langid", fake_langid)

    dec = {"verdict": "dub", "speakers": [], "rediarize": {"max_speakers": 3}}
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True and d["voided"] is True
    assert d["status"] == ST_PENDING_REVIEW         # 前端跳回核对态
    rec = lib.get(BV, 1)
    assert rec["review"] is None and rec["dec_hash"] is None
    assert rec["busy"] is False                     # 重跑同步完成后已清
    # 重跑链真被驱动:diarize 带新人数,artifacts 更新,说话人换新
    assert ("diarize", 3, True) in calls
    spks = {s["id"] for s in _req(port, "GET", f"/api/episode/{BV}")[1]
            ["parts"][0]["speakers"]}
    assert spks == {"SPK_00", "SPK_01", "SPK_02"}


def test_review_rejects_when_not_pending_review(env, monkeypatch):
    _, port, lib, _, _, tmp_path = env
    _mk_preview_ready(lib, tmp_path)                # 已在小样待听
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": _decision_dub()})
    assert st == 409 and "小样待听" in d["error"]


def test_review_unknown_record_404(env):
    _, port, *_ = env
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": "BV1nope0000", "part": 1, "decision": _decision_dub()})
    assert st == 404


# ---------- POST /api/preview/<bvid>/<part> ----------

def test_preview_fail_returns_to_review_and_stales(env):
    _, port, lib, _, _, tmp_path = env
    _, mp3 = _mk_preview_ready(lib, tmp_path)
    st, d = _req(port, "POST", f"/api/preview/{BV}/1", {"verdict": "fail"})
    assert st == 200 and d["ok"] is True
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_PENDING_REVIEW       # F7 回退边
    assert rec["preview_stale"] is True             # 旧小样标作废
    assert rec["preview_path"] == str(mp3)          # 文件保留可回听


def test_preview_pass_triggers_fullmix_background(env, monkeypatch):
    _, port, lib, _, _, tmp_path = env
    _mk_preview_ready(lib, tmp_path)
    calls = []
    _stub_fullmix(monkeypatch, calls)
    st, d = _req(port, "POST", f"/api/preview/{BV}/1", {"verdict": "pass"})
    assert st == 200 and d["ok"] is True and d["started"] is True
    assert calls and calls[0][:2] == (BV, 1)
    assert calls[0][2] == tmp_path / "work"
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_DELIVERED            # 全片桩同步跑完
    assert rec["output_path"].endswith(f"{VID}.m4a")


def test_preview_pass_wrong_state_409(env):
    _, port, lib, _, _, tmp_path = env
    _mk_reviewable(lib, tmp_path)                   # 待核对,门未到
    st, d = _req(port, "POST", f"/api/preview/{BV}/1", {"verdict": "pass"})
    assert st == 409


def test_preview_bad_verdict_400(env):
    _, port, lib, _, _, tmp_path = env
    _mk_preview_ready(lib, tmp_path)
    st, d = _req(port, "POST", f"/api/preview/{BV}/1", {"verdict": "meh"})
    assert st == 400
    assert lib.get(BV, 1)["status"] == ST_PREVIEW   # 状态没动


# ---------- POST /api/retry/<bvid>/<part> ----------

def test_retry_analysis_failure_resumes_via_orchestrator(env, monkeypatch):
    _, port, lib, _, orch, tmp_path = env
    media = tmp_path / "media" / f"{VID}.mp4"          # 停在待分析再失败
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"fake mp4")
    lib.add(BV, 1, title="分析期", media_path=str(media))
    lib.transition(BV, 1, EV_DOWNLOAD_DONE, media_path=str(media))
    lib.transition(BV, 1, EV_FAIL, stage="ocr", error="ocr 崩了")
    seen = []

    def fake_retry(self, bvid, part):
        seen.append(bvid)
        self.lib.transition(bvid, part, "retry")
        return {"ok": True, "resumed": ST_PENDING_ANALYSIS}

    monkeypatch.setattr(Orchestrator, "retry", fake_retry)
    st, d = _req(port, "POST", f"/api/retry/{BV}/1")
    assert st == 200 and d["ok"] is True and d["resumed"] == ST_PENDING_ANALYSIS
    assert seen == [BV]
    assert lib.get(BV, 1)["status"] == ST_PENDING_ANALYSIS


def test_retry_preview_and_fullmix_resume_routed(env, monkeypatch):
    _, port, lib, _, _, tmp_path = env
    # 记录一:小样失败(failed_from=已核对待合成)→ 重试驱动 05
    _mk_reviewed(lib, tmp_path)
    lib.transition(BV, 1, EV_FAIL, stage="preview", error="小样崩了")
    pv_calls, fm_calls = [], []
    _stub_preview(monkeypatch, pv_calls)

    def fake_retry_review(self, bvid, part):
        self.lib.transition(bvid, part, "retry")
        return {"ok": True, "resumed": ST_REVIEWED}

    monkeypatch.setattr(Orchestrator, "retry", fake_retry_review)
    st, d = _req(port, "POST", f"/api/retry/{BV}/1")
    assert st == 200 and pv_calls == [(BV, 1)]
    assert lib.get(BV, 1)["status"] == ST_PREVIEW

    # 记录二:全片失败(failed_from=全片合成中)→ 重试驱动 06
    _stub_fullmix(monkeypatch, fm_calls)
    lib.transition(BV, 1, EV_PREVIEW_PASS)          # 小样待听 → 全片合成中
    lib.transition(BV, 1, EV_FAIL, stage="mix", error="混音崩了")

    def fake_retry_synth(self, bvid, part):
        self.lib.transition(bvid, part, "retry")
        return {"ok": True, "resumed": ST_SYNTHESIZING}

    monkeypatch.setattr(Orchestrator, "retry", fake_retry_synth)
    st, d = _req(port, "POST", f"/api/retry/{BV}/1")
    assert st == 200 and fm_calls and fm_calls[0][:2] == (BV, 1)
    assert lib.get(BV, 1)["status"] == ST_DELIVERED


def test_retry_not_failed_409(env):
    _, port, lib, _, _, tmp_path = env
    _mk_reviewable(lib, tmp_path)
    st, d = _req(port, "POST", f"/api/retry/{BV}/1")
    assert st == 409 and "失败" in d["error"]


# ---------- /api/persons ----------

def test_persons_get_lists_library(env):
    _, port, _, persons, _, _ = env
    persons.add_ref("圆脸", "work/web/audio/x.wav", None, "zh",
                    {"kind": "manual"})
    st, d = _req(port, "GET", "/api/persons")
    assert st == 200 and d["ok"] is True
    names = {p["name"] for p in d["persons"]}
    assert {"圆脸", "小外"} <= names                 # 种子在
    yuan = next(p for p in d["persons"] if p["name"] == "圆脸")
    assert any(r["audio_url"] == "/audio/x.wav" for r in yuan["refs"])


def test_persons_ref_episode_requires_part(env):
    """评审 R1:episode 来源必须带 part,服务端校验拒绝。"""
    _, port, _, persons, _, _ = env
    body = {"person": "圆脸", "audio": "/audio/speaker_x_SPK_01_sample.wav",
            "lang": "en",
            "source": {"kind": "episode", "bvid": BV, "spk": "SPK_01"}}
    st, d = _req(port, "POST", "/api/persons/ref", body)
    assert st == 400 and "part" in d["error"]
    assert len(persons.get("圆脸")["refs"]) == 2     # 没写进去(种子两条)

    body["source"]["part"] = 1
    st, d = _req(port, "POST", "/api/persons/ref", body)
    assert st == 200 and d["ok"] is True
    ref = persons.get("圆脸")["refs"][-1]
    assert ref["source"] == {"kind": "episode", "bvid": BV, "part": 1,
                             "spk": "SPK_01"}
    assert ref["audio"] == "work/web/audio/speaker_x_SPK_01_sample.wav"
    assert ref["lang"] == "en"


def test_persons_ref_new_person_and_errors(env):
    _, port, _, persons, _, _ = env
    st, d = _req(port, "POST", "/api/persons/ref", {
        "new_name": "日本记者", "main_lang": "ja", "note": "常客",
        "audio": "/audio/cand_x_SPK_02_1.wav", "lang": "ja",
        "source": {"kind": "episode", "bvid": BV, "part": 2, "spk": "SPK_02"}})
    assert st == 200 and d["ok"] is True
    p = persons.get("日本记者")
    assert p["main_lang"] == "ja" and "常客" in p["note"]
    assert len(p["refs"]) == 1 and p["refs"][0]["source"]["part"] == 2

    st, d = _req(port, "POST", "/api/persons/ref", {
        "person": "不存在的人", "audio": "/audio/a.wav", "lang": "zh",
        "source": {"kind": "manual"}})
    assert st == 400 and "不存在" in d["error"]

    st, d = _req(port, "POST", "/api/persons/ref", {
        "person": "圆脸", "lang": "zh", "source": {"kind": "manual"}})
    assert st == 400                                 # 缺 audio


def test_review_busy_rejects_all_paths_and_keeps_ledger(env, monkeypatch):
    """重跑分人等后台在跑(busy=True)时提交核对决策必须 409,账本原样。

    否则 dub 会把状态推进已核对待合成而 spawn("preview") 因防重入返回
    False,小样永不调度,分P 卡死。
    """
    _, port, lib, _, _, tmp_path = env
    _mk_reviewable(lib, tmp_path)
    lib.set_busy(BV, 1, True)                       # 模拟后台任务在跑
    calls = []
    _stub_preview(monkeypatch, calls)
    bodies = [
        {"bvid": BV, "part": 1, "decision": _decision_dub()},      # dub
        {"bvid": BV, "part": 1, "decision": {"verdict": "skip",
                                             "speakers": []}},     # skip
        {"bvid": BV, "part": 1,
         "decision": {"verdict": "dub", "speakers": [],
                      "rediarize": {"max_speakers": 3}}},          # rediarize
    ]
    for body in bodies:
        st, d = _req(port, "POST", "/api/review", body)
        assert st == 409 and d["ok"] is False, body
        assert "还在跑" in d["error"]
        rec = lib.get(BV, 1)
        assert rec["status"] == ST_PENDING_REVIEW       # 状态没动
        assert rec["busy"] is True
        assert rec["review"] is None and rec["dec_hash"] is None
    assert calls == []                                  # 小样没被误触发
    # busy 清掉后同一决策照常受理(dub 桩同步跑完到小样待听)
    lib.set_busy(BV, 1, False)
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": _decision_dub()})
    assert st == 200 and d["ok"] is True and d["status"] == ST_PREVIEW


def test_bg_exception_lands_in_ledger_fail(env, monkeypatch):
    """后台线程异常不许裸吞:可失败主态必须落账本失败态。"""
    _, port, lib, _, _, tmp_path = env
    _mk_reviewable(lib, tmp_path)
    dec = _decision_dub()

    def boom(bvid, part, decisions=None, **kw):
        raise RuntimeError("小样引擎爆炸")

    monkeypatch.setattr(preview_mod, "generate_preview", boom)
    st, d = _req(port, "POST", "/api/review",
                 {"bvid": BV, "part": 1, "decision": dec})
    assert st == 200 and d["ok"] is True              # 提交本身成功
    assert d["status"] == ST_FAILED                   # 同步桩内已暴露失败
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_FAILED
    assert rec["failed_from"] == ST_REVIEWED and rec["busy"] is False
    assert "小样引擎爆炸" in rec["error"]


# ---------- /media 静态路由围栏 ----------

def test_media_route_fences_traversal_and_extension(env, monkeypatch):
    """/media 只放行 MEDIA_DIRS 内的视频扩展;path-as-is 穿越拿不到界外文件。

    回归位:cookie 文件放 media 外,穿越路径必须 404 不回传内容。
    """
    _, port, _, _, _, tmp_path = env
    media_dir = tmp_path / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(web_server, "MEDIA_DIRS", [media_dir])
    legit = media_dir / "BV1SGem6uEU9_P1.mp4"
    legit.write_bytes(b"fake mp4 bytes")
    secret = tmp_path / "work" / "cookies_full.txt"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("SESSDATA=leak-me", encoding="utf-8")

    st, body = _req(port, "GET", "/media/BV1SGem6uEU9_P1.mp4")
    assert st == 200 and "fake mp4 bytes" in body      # 正常视频不回归
    st, body = _req(port, "GET", "/media/../work/cookies_full.txt")
    assert st == 404                                    # 目录穿越被围栏拦下
    assert "SESSDATA" not in (body or "")
    (media_dir / "notes.txt").write_bytes(b"plain text")
    assert _req(port, "GET", "/media/notes.txt")[0] == 404   # 非视频扩展
    assert _req(port, "GET", "/media/no_such_file.mp4")[0] == 404
