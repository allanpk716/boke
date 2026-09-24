# -*- coding: utf-8 -*-
"""分析编排与导入入口单测(04 号票)。

全桩化(tmp_path + monkeypatch):不联网、不写 D 盘。
桩点:orchestrator._http_get_json(B 站接口)/ orchestrator._fetch_view(视频信息)
/ orchestrator._subprocess(yt-dlp 与 check_language.py 子进程)/
boke.extract_audio 等四个 stage 模块的 run。
覆盖验收:cookie 预检四态、BV/清单导入幂等、下载登记与失败、
分析链顺序与产物入账、langid 建议标记、失败登记与断点重试续跑。
"""
import json
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import boke.attribute          # noqa: E402
import boke.diarize            # noqa: E402
import boke.extract_audio      # noqa: E402
import boke.ocr_subtitles      # noqa: E402
from boke import orchestrator as orch   # noqa: E402
from boke.library import (     # noqa: E402
    ST_FAILED, ST_PENDING_ANALYSIS, ST_PENDING_DOWNLOAD, ST_PENDING_REVIEW,
    ST_REVIEWED, Library,
)

BV = "BV1test01"


# ---------- 夹具 ----------

_COOKIE_LINE = ".bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tabc%2Cdef_123"

_VIEW = {"title": "26-09-19 北爱威尔士苏格兰脱英", "pubdate": 1758240000,
         "duration": 2817, "is_charging_arc": 1,
         "pages": [{"page": 1, "part": "上", "duration": 1500},
                   {"page": 2, "part": "下", "duration": 1317}]}


def _write_cookie_file(tmp_path, line=_COOKIE_LINE, name="cookies.txt"):
    p = tmp_path / name
    p.write_text("# Netscape HTTP Cookie File\n" + line + "\n", encoding="utf-8")
    return p


def _mk_orch(tmp_path, lib=None, with_cookies=True):
    lib = lib if lib is not None else Library(tmp_path / "library.json")
    cookies = _write_cookie_file(tmp_path) if with_cookies else \
        tmp_path / "no_such_cookies.txt"
    return orch.Orchestrator(lib=lib, work_dir=tmp_path / "work",
                             media_dir=tmp_path / "media",
                             cookies_path=cookies)


class StageSpy:
    """四个 pipeline stage 的桩:记录调用顺序,产物写假文件,可指定失败点。"""

    _KEYS = (("extract", boke.extract_audio, "audio"),
             ("ocr", boke.ocr_subtitles, "srt"),
             ("diarize", boke.diarize, "rttm"),
             ("attribute", boke.attribute, "tagged"))

    def __init__(self, work_dir, fail_at=None):
        self.calls = []
        self.work_dir = Path(work_dir)
        self.fail_at = fail_at

    def install(self, monkeypatch):
        for name, module, key in self._KEYS:
            monkeypatch.setattr(module, "run", self._make(name, key))

    def _make(self, name, key):
        def fake(video, work_dir, *a, **kw):
            self.calls.append(name)
            if self.fail_at == name:
                raise RuntimeError(f"{name} 崩了")
            p = self.work_dir / Path(video).stem / f"{name}.fake"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("fake 产物", encoding="utf-8")
            return {key: str(p)}
        return fake


def _allow_nav(monkeypatch):
    """cookie 预检的 nav 接口桩:免联网直接放行。"""
    monkeypatch.setattr(orch, "_http_get_json",
                        lambda url, headers=None: {"code": 0,
                                                   "data": {"isLogin": True}})


def _install_langid(monkeypatch, o, spy=None, verdict="skip_全中文无需配音",
                    rc=0):
    """langid 桩:拦 _subprocess 里对 check_language.py 的调用,
    在编排的 work_dir/<stem>/ 下写 langid.json,并把 langid 记入调用顺序。"""
    def fake(cmd, **kw):
        if not any("check_language.py" in str(c) for c in cmd):
            raise AssertionError(f"langid 桩收到意外子进程调用: {cmd}")
        if spy is not None:
            spy.calls.append("langid")
        stem = cmd[-1]
        d = Path(o.work_dir) / stem
        d.mkdir(parents=True, exist_ok=True)
        (d / "langid.json").write_text(
            json.dumps({"verdict": verdict, "speakers": {}},
                       ensure_ascii=False), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, rc, "", "")
    monkeypatch.setattr(orch, "_subprocess", fake)


def _install_download(monkeypatch, o, rc=0, stderr=""):
    """yt-dlp 桩:按 -o 模板造出 <BV>_P<n>.mp4 假产物,记录命令行。"""
    cmds = []

    def fake(cmd, **kw):
        cmds.append(cmd)
        if rc != 0:
            return subprocess.CompletedProcess(cmd, rc, "", stderr)
        tmpl = cmd[cmd.index("-o") + 1]
        target = Path(tmpl.replace(".%(ext)s", ".mp4"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake mp4")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(orch, "_subprocess", fake)
    return cmds


def _add_downloaded(o, bvid=BV, part=1):
    """造一条已下载(待分析)的记录:假媒体文件真实存在。"""
    media = Path(o.media_dir) / f"{bvid}_P{part}.mp4"
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(b"fake mp4")
    o.lib.add(bvid, part, title="测试期", date="2026-09-19", duration=1500.0)
    o.lib.transition(bvid, part, "download_done", media_path=str(media))
    return media


# ---------- check_cookie ----------

def test_cookie_file_missing(monkeypatch, tmp_path):
    def _boom(*a, **kw):
        raise AssertionError("文件缺失时不应发起网络请求")
    monkeypatch.setattr(orch, "_http_get_json", _boom)
    ok, reason = orch.check_cookie(tmp_path / "absent.txt")
    assert ok is False
    assert "不存在" in reason


def test_cookie_without_sessdata(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "_http_get_json",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("无 SESSDATA 时不应联网")))
    p = tmp_path / "cookies.txt"
    p.write_text(".bilibili.com\tTRUE\t/\tTRUE\t0\tOTHER\tx\n", encoding="utf-8")
    ok, reason = orch.check_cookie(p)
    assert ok is False
    assert "SESSDATA" in reason


def test_cookie_valid(monkeypatch, tmp_path):
    seen = {}

    def fake_http(url, headers=None):
        seen["url"], seen["headers"] = url, headers
        return {"code": 0, "data": {"isLogin": True}}

    monkeypatch.setattr(orch, "_http_get_json", fake_http)
    ok, reason = orch.check_cookie(_write_cookie_file(tmp_path))
    assert ok is True
    assert "nav" in seen["url"]
    assert "abc%2Cdef_123" in seen["headers"]["Cookie"]


def test_cookie_expired(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "_http_get_json",
                        lambda *a, **kw: {"code": -101, "message": "账号未登录"})
    ok, reason = orch.check_cookie(_write_cookie_file(tmp_path))
    assert ok is False
    assert reason  # 人话原因非空


def test_cookie_network_error(monkeypatch, tmp_path):
    def _net_down(*a, **kw):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(orch, "_http_get_json", _net_down)
    ok, reason = orch.check_cookie(_write_cookie_file(tmp_path))
    assert ok is False
    assert reason


# ---------- ingest_bv ----------

def test_ingest_blocked_by_cookie_gate(tmp_path):
    o = _mk_orch(tmp_path, with_cookies=False)
    r = o.ingest_bv(BV)
    assert r["ok"] is False and r["reason"]
    assert o.lib.query() == []          # 不登记


def test_ingest_bv_registers_all_parts(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    _allow_nav(monkeypatch)
    monkeypatch.setattr(orch, "_fetch_view", lambda bv: dict(_VIEW))
    r = o.ingest_bv(BV)
    assert r["ok"] is True and r["added"] == 2 and len(r["tasks"]) == 2
    recs = o.lib.query(bvid=BV)
    assert [x["part"] for x in recs] == [1, 2]
    for rec in recs:
        assert rec["status"] == ST_PENDING_DOWNLOAD
        assert rec["title"] == _VIEW["title"]
        assert rec["date"] == time.strftime("%Y-%m-%d",
                                            time.localtime(_VIEW["pubdate"]))
    assert recs[0]["duration"] == 1500 and recs[1]["duration"] == 1317
    assert recs[0]["is_charge"] is True


def test_ingest_bv_idempotent(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    _allow_nav(monkeypatch)
    monkeypatch.setattr(orch, "_fetch_view", lambda bv: dict(_VIEW))
    o.ingest_bv(BV)
    r2 = o.ingest_bv(BV)
    assert r2["ok"] is True and r2["added"] == 0
    assert r2["skipped"] == 2 and len(r2["tasks"]) == 2
    assert len(o.lib.query(bvid=BV)) == 2      # 不重复记录


def test_ingest_bv_view_fetch_failure(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    _allow_nav(monkeypatch)

    def _boom(bv):
        raise RuntimeError("网络断了")
    monkeypatch.setattr(orch, "_fetch_view", _boom)
    r = o.ingest_bv(BV)
    assert r["ok"] is False and "网络断了" in r["reason"]
    assert o.lib.query() == []


# ---------- ingest_catalog ----------

def _catalog_items():
    return [
        {"bvid": "BV1cat0001", "title": "高市早苗联合国首秀",
         "created": 1790167515, "length": "14:27", "is_charging_arc": 1},
        {"bvid": "BV1cat0002", "title": "另一期", "created": 1790000000,
         "length": "1:02:03", "is_charging_arc": 0},
    ]


def test_ingest_catalog_registers_and_parses(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    _allow_nav(monkeypatch)
    r = o.ingest_catalog(_catalog_items())
    assert r["ok"] is True and r["added"] == 2
    a, b = o.lib.query(bvid="BV1cat0001")[0], o.lib.query(bvid="BV1cat0002")[0]
    assert a["duration"] == 14 * 60 + 27
    assert b["duration"] == 3600 + 2 * 60 + 3
    assert a["is_charge"] is True and b["is_charge"] is False
    assert a["status"] == ST_PENDING_DOWNLOAD


def test_ingest_catalog_idempotent(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    _allow_nav(monkeypatch)
    o.ingest_catalog(_catalog_items())
    r2 = o.ingest_catalog(_catalog_items())
    assert r2["added"] == 0 and r2["skipped"] == 2
    assert len(o.lib.query()) == 2


# ---------- 下载 ----------

def test_download_success_registers_media(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    o.lib.add(BV, 1, title="测试期")
    cmds = _install_download(monkeypatch, o)
    r = o.download(BV, 1)
    assert r["ok"] is True
    cmd = cmds[0]
    assert cmd[0] == "yt-dlp"
    assert str(o.cookies_path) in cmd
    assert cmd[cmd.index("--playlist-items") + 1] == "1"
    assert any(f"{BV}_P1.%(ext)s" in c for c in cmd)
    assert cmd[-1] == f"https://www.bilibili.com/video/{BV}"
    rec = o.lib.get(BV, 1)
    assert rec["status"] == ST_PENDING_ANALYSIS
    assert rec["media_path"].endswith(f"{BV}_P1.mp4")
    assert rec["busy"] is False


def test_download_failure_marks_failed(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    o.lib.add(BV, 1, title="测试期")
    _install_download(monkeypatch, o, rc=1, stderr="HTTP Error 404")
    r = o.download(BV, 1)
    assert r["ok"] is False and r["stage"] == "download"
    rec = o.lib.get(BV, 1)
    assert rec["status"] == ST_FAILED
    assert rec["failed_stage"] == "download"
    assert "404" in rec["error"]
    assert rec["failed_from"] == ST_PENDING_DOWNLOAD
    assert rec["busy"] is False


def test_download_refuses_wrong_state(tmp_path):
    o = _mk_orch(tmp_path)
    _add_downloaded(o)                     # 已是待分析
    r = o.download(BV, 1)
    assert r["ok"] is False and "待下载" in r["error"]
    assert o.lib.get(BV, 1)["status"] == ST_PENDING_ANALYSIS


# ---------- 分析链 ----------

def test_analysis_chain_order_and_artifacts(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    _add_downloaded(o)
    spy = StageSpy(o.work_dir)
    spy.install(monkeypatch)
    _install_langid(monkeypatch, o, spy=spy, verdict="dub_需要配音")

    r = o.analyze(BV, 1)
    assert r["ok"] is True
    assert spy.calls == ["extract", "ocr", "diarize", "attribute", "langid"]
    rec = o.lib.get(BV, 1)
    assert rec["status"] == ST_PENDING_REVIEW
    assert rec["busy"] is False
    art = rec["artifacts"]
    assert art["audio"].endswith("extract.fake")
    assert art["srt"].endswith("ocr.fake")
    assert art["rttm"].endswith("diarize.fake")
    assert art["tagged"].endswith("attribute.fake")
    assert art["langid"].endswith("langid.json")
    assert art["langid_suggest"] == "dub"     # D7:只写建议标记


def test_analysis_chain_suggest_skip_mark(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    _add_downloaded(o)
    StageSpy(o.work_dir).install(monkeypatch)
    _install_langid(monkeypatch, o, verdict="skip_全中文无需配音")
    o.analyze(BV, 1)
    assert o.lib.get(BV, 1)["artifacts"]["langid_suggest"] == "skip"


def test_analysis_failure_registers_stage_and_error(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    _add_downloaded(o)
    spy = StageSpy(o.work_dir, fail_at="ocr")
    spy.install(monkeypatch)
    _install_langid(monkeypatch, o)

    r = o.analyze(BV, 1)
    assert r["ok"] is False and r["stage"] == "ocr"
    rec = o.lib.get(BV, 1)
    assert rec["status"] == ST_FAILED
    assert rec["failed_stage"] == "ocr"
    assert "ocr 崩了" in rec["error"]
    assert rec["failed_from"] == ST_PENDING_ANALYSIS
    assert rec["busy"] is False
    assert rec["artifacts"]["audio"].endswith("extract.fake")  # 已完成步入账
    assert "srt" not in rec["artifacts"]


def test_analysis_refuses_wrong_state(tmp_path):
    o = _mk_orch(tmp_path)
    o.lib.add(BV, 1, title="测试期")       # 待下载,还没下
    r = o.analyze(BV, 1)
    assert r["ok"] is False and "待分析" in r["error"]


# ---------- retry 断点续跑 ----------

def test_retry_resumes_from_failed_stage(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    _add_downloaded(o)
    spy = StageSpy(o.work_dir, fail_at="diarize")
    spy.install(monkeypatch)
    _install_langid(monkeypatch, o, spy=spy)
    assert o.analyze(BV, 1)["ok"] is False
    assert spy.calls == ["extract", "ocr", "diarize"]

    spy.fail_at = None                      # 修好,重试
    r = o.retry(BV, 1)
    assert r["ok"] is True
    # extract/ocr 产物已在盘上→不重跑(各只出现一次);
    # diarize 失败时无产物→重试时重跑该步,再继续 attribute/langid
    assert spy.calls == ["extract", "ocr", "diarize",
                         "diarize", "attribute", "langid"]
    rec = o.lib.get(BV, 1)
    assert rec["status"] == ST_PENDING_REVIEW
    assert rec["error"] is None and rec["failed_stage"] is None


def test_retry_after_download_failure_reruns_download(tmp_path, monkeypatch):
    o = _mk_orch(tmp_path)
    o.lib.add(BV, 1, title="测试期")
    _install_download(monkeypatch, o, rc=1, stderr="超时")
    assert o.download(BV, 1)["ok"] is False
    _install_download(monkeypatch, o, rc=0)   # 网络恢复
    r = o.retry(BV, 1)
    assert r["ok"] is True
    assert o.lib.get(BV, 1)["status"] == ST_PENDING_ANALYSIS


def test_retry_refuses_non_failed_state(tmp_path):
    o = _mk_orch(tmp_path)
    o.lib.add(BV, 1, title="测试期")
    r = o.retry(BV, 1)
    assert r["ok"] is False and "失败" in r["error"]


def test_retry_of_review_stage_only_restores_state(tmp_path):
    """待核对之后阶段的失败归其他模块驱动:retry 只恢复状态不越权驱动。"""
    o = _mk_orch(tmp_path)
    _add_downloaded(o)
    o.lib.transition(BV, 1, "analysis_done")
    o.lib.transition(BV, 1, "review_submit",
                     review={"verdict": "dub", "speakers": []})
    o.lib.transition(BV, 1, "fail", stage="preview", error="小样合成崩了")
    r = o.retry(BV, 1)
    assert r["ok"] is True and r["resumed"] == ST_REVIEWED
    assert o.lib.get(BV, 1)["status"] == ST_REVIEWED
