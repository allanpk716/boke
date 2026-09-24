# -*- coding: utf-8 -*-
"""08 号票:端到端验收冒烟(spec 20260924-核对门正式版 验收 6 条)。

可重复执行(幂等):每轮在 work/tmp/acceptance_<模式>_<时间戳>/ 新建临时账本
副本目录,Library/PersonLibrary/Orchestrator/App 全部注入临时路径;既有真实
媒体与分析产物只读挂账本(artifacts 指真实路径)。ocr.srt/diar.rttm 另以副本
放进临时 work/<id>/:决策应用器(review_apply)按 work_dir 布局读取做合并归人
重算,不读账本 artifacts,这是唯一需要落盘的播种副本(几 KB 文本)。

真实引擎 vs 桩(逐条标注,详见报告头):
  1 账本/状态机/决策应用器/HTTP API=真实;小样:SKIP_GPU=1 → synthesize
    mock 静音桩(全程不调真实 TTS),默认 → cosyvoice3 GPU 真克隆合成;
  2 cookie nav 预检/B 站视频信息/yt-dlp 下载=真实(落临时 media_dir);
    分析链=桩(本条只验导入+下载);cookie 失效/断网 → 423 分支断言;
  3 失败注入=桩(小样 stage 抛一次错);失败落账与 /api/retry 断点续跑=真实;
  4 cookie 预检=真实(对置空的 cookie 文件)→ /api/ingest 423;
  5 决策变更→新 dec_hash→重出小样(引擎同第 1 条);旧 dec 目录保留在盘、
    账本活跃字段不再引用;
  6 /api/library、/api/episode 与账本文件三方对账(不开浏览器)。

输出:逐条 [PASS]/[FAIL] + 一句证据(stdout 与 work/acceptance_report.txt);
任一 FAIL 退出码非 0。用法:
  .venv/Scripts/python.exe work/tools/acceptance_check.py              # GPU 真小样
  SKIP_GPU=1 .venv/Scripts/python.exe work/tools/acceptance_check.py   # 静音桩小样
仅标准库 + 既有模块。
"""
import http.client
import json
import os
import shutil
import sys
import threading
import time
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORK = REPO / "work"
for _p in (str(REPO / "src"), str(WORK / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import web_server                                      # noqa: E402
from boke import preview as boke_preview               # noqa: E402
from boke import review_apply                          # noqa: E402
from boke.library import (                             # noqa: E402
    EV_ANALYSIS_DONE, EV_DOWNLOAD_DONE, Library,
    ST_EXCLUDED, ST_FAILED, ST_PENDING_ANALYSIS, ST_PENDING_DOWNLOAD,
    ST_PENDING_REVIEW, ST_PREVIEW, ST_REVIEWED,
)
from boke.orchestrator import Orchestrator             # noqa: E402
from boke.persons import PersonLibrary                 # noqa: E402

SKIP_GPU = os.environ.get("SKIP_GPU") == "1"
MODE_TAG = "SKIP_GPU=1(小样=静音桩,全程不调真实 TTS)" if SKIP_GPU \
    else "默认(小样=真实引擎 cosyvoice3 GPU 克隆合成)"

BV = "BV1gpeJ6fEAk"
VID = f"{BV}_P1"
PARTS = {1: WORK / "BV1gpeJ6fEAk_P1", 2: WORK / "BV1gpeJ6fEAk_P2"}
DURATION = {1: 2364.8, 2: 2107.52}          # 与 source_duration.txt 一致
TITLE = {1: "嘉宾中国行 P1", 2: "嘉宾中国行 P2"}
SUGGEST = {1: "dub", 2: "skip"}
WEB_AUDIO = WORK / "web" / "audio"
CATALOG = WORK / "space_videos.json"
COOKIES = WORK / "cookies_full.txt"
REPORT_PATH = WORK / "acceptance_report.txt"

PREFLIGHT = [
    REPO / "in" / "BV1gpeJ6fEAk_P1.mp4", REPO / "in" / "BV1gpeJ6fEAk_P2.mp4",
    *(PARTS[p] / n for p in (1, 2)
      for n in ("audio.wav", "ocr.srt", "diar.rttm", "tagged.srt", "langid.json")),
    WEB_AUDIO / f"cand_{VID}_SPK_00_1.wav", WEB_AUDIO / f"cand_{VID}_SPK_00_2.wav",
    WEB_AUDIO / f"cand_{VID}_SPK_02_1.wav",
    REPO / "refs" / "yuanlian_chinese_01.wav",
    REPO / "refs" / "ref01_transcript.txt",
    CATALOG,
]


# ---------------- 决策构造(真实说话人:SPK_00..03) ----------------

def _spk(sid, **kw):
    d = {"id": sid, "note": "", "host": False, "host_person": None,
         "merge_into": None, "voice": None, "preset_voice": None,
         "clone_ref": None}
    d.update(kw)
    return d


def p1_decision(variant=0):
    """P1 决策:SPK_03(疑似分身)并入 SPK_00;SPK_01 指认主持人挂人物库
    「圆脸」;两位嘉宾走克隆(clone_ref 指既有候选原声段)。
    variant=1 换 SPK_00 候选段 → 决策内容变化 → 新 dec_hash(第 5 条用)。"""
    a = "2" if variant else "1"
    return {"verdict": "dub", "speakers": [
        _spk("SPK_00", note="日本嘉宾A", voice="clone",
             clone_ref=str(WEB_AUDIO / f"cand_{VID}_SPK_00_{a}.wav")),
        _spk("SPK_01", note="主持人(圆脸)", host=True, host_person="圆脸"),
        _spk("SPK_02", note="日本嘉宾B", voice="clone",
             clone_ref=str(WEB_AUDIO / f"cand_{VID}_SPK_02_1.wav")),
        _spk("SPK_03", note="嘉宾A分身(合并)", merge_into="SPK_00"),
    ]}


def p2_decision():
    return {"verdict": "skip", "speakers": [],
            "note": "全中文场,用户确认跳过(D7)"}


# ---------------- 播种与环境 ----------------

def seed_reviewable(lib, work_dir, bvid, part, media, art, title):
    """把既有产物的分P记录置「待核对」:媒体/产物挂真实路径(只读);
    ocr.srt/diar.rttm 副本进临时 work/<媒体stem>/(决策应用器读取口径)。"""
    lib.add(bvid, part, title=title, duration=DURATION[part],
            media_path=str(media))
    lib.transition(bvid, part, EV_DOWNLOAD_DONE, media_path=str(media))
    lib.transition(bvid, part, EV_ANALYSIS_DONE)
    mirror = Path(work_dir) / Path(media).stem
    mirror.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(art / "ocr.srt", mirror / "ocr.srt")
    shutil.copyfile(art / "diar.rttm", mirror / "diar.rttm")
    lib.update(bvid, part, artifacts={
        "srt": str(art / "ocr.srt"), "rttm": str(art / "diar.rttm"),
        "tagged": str(art / "tagged.srt"), "audio": str(art / "audio.wav"),
        "langid": str(art / "langid.json"), "langid_suggest": SUGGEST[part]})


class Env:
    """临时账本 + 真 App + 随机端口 HTTP 服务(同 tests/test_web_api 范式)。"""

    def __init__(self, tmp_root, sync_bg=True, cookies=None):
        self.tmp = Path(tmp_root)
        self.work = self.tmp / "work"
        self.lib = Library(self.tmp / "library.json")
        self.persons = PersonLibrary(path=self.tmp / "persons.json")
        self.orch = Orchestrator(lib=self.lib, work_dir=str(self.work),
                                 media_dir=self.tmp / "media",
                                 cookies_path=cookies or COOKIES)
        self.app = web_server.App(lib=self.lib, persons=self.persons,
                                  orch=self.orch, work_dir=self.work,
                                  web_audio_dir=WEB_AUDIO,
                                  catalog_path=CATALOG)
        self.app.sync_bg = sync_bg
        if SKIP_GPU:   # 小样走静音桩:不调真实 TTS,其余链路不变
            def _silent_preview(bvid, part, _app=self.app):
                boke_preview.generate_preview(bvid, part, library=_app.lib,
                                              work_root=str(_app.work_dir),
                                              mock=True)
            self.app._run_preview = _silent_preview
        self.srv = web_server.make_server("127.0.0.1", 0, self.app)
        threading.Thread(target=self.srv.serve_forever, daemon=True,
                         name="acc-http").start()
        self.port = self.srv.server_address[1]

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def req(port, method, path, body=None, timeout=120):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers, payload = {}, None
    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    conn.request(method, path, payload, headers)
    r = conn.getresponse()
    raw = r.read()
    conn.close()
    if "json" in (r.getheader("Content-Type") or ""):
        return r.status, json.loads(raw.decode("utf-8"))
    return r.status, raw.decode("utf-8", "replace")


# ---------------- 断言小工具 ----------------

def _manifest_engines(dec_dir):
    """决策目录合成清单里的引擎用量(验证"真引擎/静音桩"口径)。"""
    mf = Path(dec_dir) / VID / "tts" / "manifest.json"
    assert mf.exists(), f"缺合成清单 {mf}"
    used = json.loads(mf.read_text(encoding="utf-8")).get("engine_used") or {}
    assert used, "manifest 无 engine_used"
    return used


def _assert_engines(used):
    if SKIP_GPU:
        assert set(used) <= {"silence"}, f"SKIP_GPU 模式出现真实引擎:{used}"
    else:
        assert "silence" not in used and \
            ("cosyvoice3" in used or "edge" in used), \
            f"默认模式未走真实引擎:{used}"


def _smallest_catalog_entries(n):
    def secs(s):
        try:
            p = [int(x) for x in str(s).split(":")]
        except ValueError:
            return 10 ** 9
        if len(p) == 3:
            return p[0] * 3600 + p[1] * 60 + p[2]
        if len(p) == 2:
            return p[0] * 60 + p[1]
        return 10 ** 9
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows = [it for it in data if isinstance(it, dict) and it.get("bvid")]
    rows.sort(key=lambda it: secs(it.get("length")))
    return rows[:n]


# ---------------- 六条冒烟 ----------------

def item1(env):
    """播种 P1/P2 → 提交决策 → 决策目录生成 + 小样落盘 + P2 已排除。"""
    st, d = req(env.port, "POST", "/api/review",
                {"bvid": BV, "part": 1, "decision": p1_decision(0)},
                timeout=3600)
    assert st == 200 and d.get("ok"), f"/api/review P1 → HTTP {st} {d}"
    assert d.get("status") == ST_PREVIEW, \
        f"P1 提交后状态 {d.get('status')!r} ≠ 小样待听"
    h1 = d.get("dec_hash")
    dec_dir = Path(d.get("dec_dir") or "")
    assert review_apply.is_complete(dec_dir / review_apply.VOICES_NAME), \
        "voices.generated.yaml 或 .done 哨兵缺失"
    assert review_apply.is_complete(dec_dir / review_apply.TAGGED_NAME), \
        "tagged.dec.srt 或 .done 哨兵缺失"
    rec = env.lib.get(BV, 1)
    assert rec["dec_hash"] == h1 and rec["preview_path"] \
        and Path(rec["preview_path"]).exists(), "小样文件未落盘/未登记"
    engines = _manifest_engines(dec_dir)
    _assert_engines(engines)

    st2, d2 = req(env.port, "POST", "/api/review",
                  {"bvid": BV, "part": 2, "decision": p2_decision()})
    assert st2 == 200 and d2.get("ok") and d2.get("excluded"), \
        f"/api/review P2 → {st2} {d2}"
    rec2 = env.lib.get(BV, 2)
    assert rec2["status"] == ST_EXCLUDED \
        and rec2["review"]["verdict"] == "skip" \
        and rec2["dec_hash"] is None, f"P2 状态不对:{rec2['status']}"
    p2dir = env.work / f"{BV}_P2"
    assert not p2dir.exists() or not any(
        p.name.startswith("dec_") for p in p2dir.iterdir()), "P2 不应有决策目录"
    return (f"P1 决策目录 dec_{h1} 产物齐(带哨兵)且小样落盘,引擎={engines};"
            f"P2→已排除(verdict=skip,无决策目录)")


def item2(env):
    """片库勾选导入未下载 BV:cookie 有效真下载一个最小分P;失效 423 分支。"""
    seen = []

    def _stub_analyze(bvid, part):     # 分析链桩:导入冒烟只验到「待分析」
        seen.append(bvid)
        return {"ok": True, "stubbed": True}

    env.orch.analyze = _stub_analyze
    chosen = None
    for entry in _smallest_catalog_entries(3):
        st, d = req(env.port, "POST", "/api/ingest", {"items": [entry]},
                    timeout=300)
        if st in (200, 423):
            chosen = entry
            break
    assert chosen is not None, f"清单前 3 小导入全失败(网络?):{st} {d}"
    if st == 423:
        assert d.get("ok") is False and d.get("cookie") is False, \
            f"423 响应形状不对:{d}"
        assert env.lib.query() == [], "cookie 门拦截后账本不应有记录"
        return (f"cookie 失效分支:HTTP 423 红条({(d.get('error') or '')[:40]}"
                f"…),账本未动")
    bv2 = chosen["bvid"]
    assert d.get("ok") and d.get("added") == 1, f"/api/ingest → {st} {d}"
    rec = env.lib.get(bv2, 1)
    assert rec is not None and rec["status"] == ST_PENDING_DOWNLOAD, \
        f"导入未登记待下载:{rec and rec['status']}"
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        rec = env.lib.get(bv2, 1)
        if rec["status"] == ST_PENDING_ANALYSIS and not rec["busy"]:
            break
        if rec["status"] == ST_FAILED:
            raise AssertionError(f"真下载落失败态:{rec['error']}")
        time.sleep(2)
    assert rec["status"] == ST_PENDING_ANALYSIS, \
        f"下载 600s 未完成,状态停在「{rec['status']}」"
    media = Path(rec["media_path"])
    assert media.exists() and media.stat().st_size > 0, "媒体文件未落盘"
    return (f"cookie 有效分支:勾选导入 {bv2}(《{(chosen.get('title') or '')[:14]}"
            f"》{chosen.get('length')})→ yt-dlp 真下载 "
            f"{media.stat().st_size // 1024} KB → 待分析(分析链桩)")


def item3(env):
    """桩 stage 抛错 → 失败态;retry 断点续跑 → 重出小样。"""
    bv3 = "BV1smoke0001"
    seed_reviewable(env.lib, env.work, bv3, 1, REPO / "in" / "BV1gpeJ6fEAk_P1.mp4",
                    PARTS[1], "冒烟·失败重试")
    orig = boke_preview.generate_preview

    def boom(bvid, part, decisions=None, **kw):
        raise RuntimeError("冒烟注入:小样引擎爆炸")

    boke_preview.generate_preview = boom
    try:
        st, d = req(env.port, "POST", "/api/review",
                    {"bvid": bv3, "part": 1, "decision": p1_decision(0)},
                    timeout=300)
    finally:
        boke_preview.generate_preview = orig
    assert st == 200 and d.get("ok"), f"提交本身应成功:{st} {d}"
    assert d.get("status") == ST_FAILED, \
        f"桩抛错后状态 {d.get('status')!r} ≠ 失败"
    rec = env.lib.get(bv3, 1)
    assert rec["failed_from"] == ST_REVIEWED \
        and rec["failed_stage"] == "preview", \
        f"失败上下文不对:{rec['failed_from']}/{rec['failed_stage']}"
    assert "小样引擎爆炸" in (rec["error"] or ""), "错误信息未落账"

    st, d = req(env.port, "POST", f"/api/retry/{bv3}/1", timeout=3600)
    assert st == 200 and d.get("ok") and d.get("started"), \
        f"/api/retry → {st} {d}"
    assert d.get("resumed") == ST_REVIEWED, f"断点 resumed={d.get('resumed')!r}"
    rec = env.lib.get(bv3, 1)
    assert rec["status"] == ST_PREVIEW, \
        f"重试后状态 {rec['status']!r} ≠ 小样待听"
    assert rec["failed_from"] is None and rec["failed_stage"] is None \
        and rec["error"] is None, "重试后失败上下文未清"
    assert rec["preview_path"] and Path(rec["preview_path"]).exists(), \
        "重试后小样未落盘"
    engines = _manifest_engines(Path(rec["preview_path"]).parent)
    _assert_engines(engines)
    return ("小样 stage 抛错→失败态(failed_from=已核对待合成,error 落账);"
            f"retry 断点续跑重出小样成功,引擎={engines}")


def item4(env):
    """cookie 置空 → /api/ingest 423。"""
    (env.tmp / "cookies_empty.txt").write_text("", encoding="utf-8")
    st, d = req(env.port, "POST", "/api/ingest", {"bv": "BV1smoke423x"})
    assert st == 423, f"置空 cookie 应 423,得到 {st}"
    assert d.get("ok") is False and d.get("cookie") is False, \
        f"响应缺 cookie 失效标记:{d}"
    assert env.lib.query() == [], "账本不应有记录"
    return f"cookie 置空 → /api/ingest 423({(d.get('error') or '')[:38]}…)"


def item5(env):
    """决策变更 → dec_hash 变化 → 旧小样/成品不再被引用。"""
    rec0 = env.lib.get(BV, 1)
    h1 = rec0["dec_hash"]
    old_dir = env.work / VID / f"dec_{h1}"
    assert h1 and old_dir.exists() and Path(rec0["preview_path"]).exists(), \
        "前置(第 1 条)的小样应在盘"

    st, d = req(env.port, "POST", f"/api/preview/{BV}/1", {"verdict": "fail"})
    assert st == 200 and d.get("ok"), f"小样不过回退 → {st} {d}"
    rec = env.lib.get(BV, 1)
    assert rec["status"] == ST_PENDING_REVIEW \
        and rec["preview_stale"] is True, "回退后应为待核对+小样作废标记"
    assert Path(rec["preview_path"]).exists(), "旧小样文件应保留可回听"

    st, d = req(env.port, "POST", "/api/review",
                {"bvid": BV, "part": 1, "decision": p1_decision(1)},
                timeout=3600)
    assert st == 200 and d.get("ok"), f"改决策重提 → {st} {d}"
    h2 = d["dec_hash"]
    assert h2 != h1, "决策内容变了但 dec_hash 没变"
    rec = env.lib.get(BV, 1)
    assert rec["dec_hash"] == h2, "账本 dec_hash 未指向新 hash"
    assert rec["preview_stale"] is False, "新小样不应带作废标记"
    assert rec["preview_path"] and Path(rec["preview_path"]).exists(), \
        "新小样未落盘"
    for ref in (rec["dec_hash"] or "", rec["preview_path"] or "",
                rec["output_path"] or ""):
        assert h1 not in str(ref), f"活跃引用仍指旧 hash:{ref}"
    assert old_dir.exists() and (old_dir / "preview.mp3").exists(), \
        "旧 dec 目录应保留在盘可查"
    engines = _manifest_engines(Path(rec["preview_path"]).parent)
    _assert_engines(engines)
    return (f"决策变更 dec_hash {h1}→{h2},账本小样路径指向新 dec 目录,"
            f"旧目录保留在盘但活跃字段不再引用,引擎={engines}")


def item6(env):
    """三个页面的 /api 数据一致性:期名/状态同源(不开浏览器)。"""
    st, lib_api = req(env.port, "GET", "/api/library")
    assert st == 200 and lib_api.get("ok"), "/api/library 不可用"
    st, ep = req(env.port, "GET", f"/api/episode/{BV}")
    assert st == 200 and ep.get("ok"), "/api/episode 不可用"
    disk = json.loads((env.tmp / "library.json").read_text(encoding="utf-8"))
    stats = []
    for part in (1, 2):
        lr = next((r for r in lib_api["records"]
                   if r["bvid"] == BV and r["part"] == part), None)
        pr = next((p for p in ep["parts"] if p["part"] == part), None)
        dr = disk["records"].get(f"{BV}|{part}")
        assert lr and pr and dr, f"P{part} 三方数据缺一方"
        assert lr["title"] == pr["title"] == dr["title"] == TITLE[part], \
            f"P{part} 期名不同源:{lr['title']}/{pr['title']}/{dr['title']}"
        assert lr["status"] == pr["status"] == dr["status"], \
            f"P{part} 状态不同源:{lr['status']}/{pr['status']}/{dr['status']}"
        stats.append(f"P{part}={dr['status']}")
    assert sum(lib_api["counts"].values()) == len(lib_api["records"]), \
        "counts 汇总与记录数不一致"
    assert ep["title"] == TITLE[1], "期详情期名与账本不一致"
    return (f"/api/library、/api/episode、账本文件三方同源(期名「{TITLE[1]}」,"
            f"{' '.join(stats)}),counts={lib_api['counts']}")


# ---------------- 主流程 ----------------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    t0 = time.time()
    ts = time.strftime("%Y%m%d_%H%M%S")
    tmp_root = WORK / "tmp" / f"acceptance_{'skipgpu' if SKIP_GPU else 'gpu'}_{ts}"
    tmp_root.mkdir(parents=True, exist_ok=True)
    lines = []

    def emit(s):
        print(s, flush=True)
        lines.append(s)

    emit("=" * 72)
    emit("端到端验收冒烟(08 号票 · spec 20260924-核对门正式版 验收 6 条)")
    emit(f"模式:{MODE_TAG}")
    emit(f"临时账本/工作目录:{tmp_root}(每轮新建,幂等;既有媒体与产物只读)")
    emit("真实/桩标注:1 账本+状态机+决策应用器+API=真实,小样=上列模式;"
         "2 cookie 预检+yt-dlp 下载=真实(分析链桩,只验到待分析;失效走 423);"
         "3 失败注入=桩,失败落账+retry 续跑=真实;4 cookie 预检=真实;"
         "5 决策变更+重出小样=真实(引擎同 1);6 三页数据源对账=真实")
    emit("=" * 72)

    missing = [str(p) for p in PREFLIGHT if not p.exists()]
    if missing or not shutil.which("ffmpeg"):
        emit(f"[0] 冒烟前置输入 [FAIL] — 缺文件:{missing};"
             f"ffmpeg 在 PATH:{bool(shutil.which('ffmpeg'))}")
        REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
        sys.exit(2)

    envs = []
    results = []
    try:
        main_env = Env(tmp_root / "main", sync_bg=True)
        dl_env = Env(tmp_root / "dl", sync_bg=False)
        fail_env = Env(tmp_root / "fail", sync_bg=True)
        nc_env = Env(tmp_root / "nc", sync_bg=True,
                     cookies=tmp_root / "nc" / "cookies_empty.txt")
        envs = [main_env, dl_env, fail_env, nc_env]

        seed_reviewable(main_env.lib, main_env.work, BV, 1,
                        REPO / "in" / "BV1gpeJ6fEAk_P1.mp4", PARTS[1], TITLE[1])
        seed_reviewable(main_env.lib, main_env.work, BV, 2,
                        REPO / "in" / "BV1gpeJ6fEAk_P2.mp4", PARTS[2], TITLE[2])

        for no, title, fn in (
                (1, "播种 P1/P2→提交决策→决策目录+小样+P2 排除",
                 lambda: item1(main_env)),
                (2, "片库勾选导入未下载 BV(真下载/423 分支)",
                 lambda: item2(dl_env)),
                (3, "注入失败→失败态+retry 断点续跑", lambda: item3(fail_env)),
                (4, "cookie 置空→/api/ingest 423", lambda: item4(nc_env)),
                (5, "决策变更→dec_hash 变化→旧产物不再引用",
                 lambda: item5(main_env)),
                (6, "三页 /api 数据一致性(期名/状态同源)",
                 lambda: item6(main_env))):
            try:
                ev = fn()
                line = f"[{no}] {title} [PASS] — {ev}"
                results.append((no, title, True))
            except AssertionError as e:
                line = f"[{no}] {title} [FAIL] — {e}"
                results.append((no, title, False))
            except Exception as e:      # 非断言异常也算 FAIL,但带定位
                tb = traceback.format_exc().strip().splitlines()
                line = (f"[{no}] {title} [FAIL] — 异常 {type(e).__name__}: {e}"
                        f" | {tb[-1] if tb else ''}")
                results.append((no, title, False))
            emit(line)
    finally:
        for e in envs:
            try:
                e.close()
            except Exception:
                pass

    ok = sum(1 for _, _, good in results if good)
    emit("-" * 72)
    emit(f"结果:{ok}/{len(results)} PASS;总耗时 {time.time() - t0:.0f}s;"
         f"模式:{MODE_TAG}")
    emit(f"临时目录保留待查:{tmp_root}")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[report] {REPORT_PATH}", flush=True)
    sys.exit(0 if ok == len(results) and results else 1)


if __name__ == "__main__":
    main()
