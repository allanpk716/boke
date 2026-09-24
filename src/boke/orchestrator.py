# -*- coding: utf-8 -*-
"""分析编排与导入入口(04 号票)。

职责(spec 20260924 "Implementation Decisions" 导入节与分析编排节):
- 导入:ingest_bv(粘贴 BV)/ ingest_catalog(播主清单勾选)两个入口,
  导入前 check_cookie() 预检——读 work/cookies_full.txt 的 SESSDATA,
  GET B 站 nav 接口验证;失效→返回 False+人话原因,不登记任何记录(D11 cookie 门)。
- 下载:yt-dlp 命令行参数照搬 work/tools/download_exclusive.py 模板,
  --playlist-items 精下单分P,产物落 <media_dir>/<BV>_P<序号>.mp4(默认 D:/boke_media);
  完成→media_path 入账→待分析;失败→账本失败态(stage=download)。
- 分析链:对每分P依次 extract→ocr→diarize(隔离 venv 由 boke.diarize.run
  内置 subprocess,直接调)→attribute→langid(work/tools/check_language.py
  是脚本,subprocess 调它=最小侵入);每步产物路径即入账;全完成→待核对
  并登记 langid 结论标记(建议跳过/建议配音;是否跳过由用户确认,D7);
  任一步异常→账本失败态+阶段+错误信息。
- 下载/分析均为同步函数,由调用方(服务端后台线程)驱动;
  账本所有变更只走 boke.library 的接口(单写入者)。
- 断点续跑(F6):artifacts 已登记且文件仍在盘上的步骤直接跳过;
  retry() 经账本 retry 事件回失败前主态,再按主态续跑下载/分析。

测试桩点(单测全桩化,不联网不写 D 盘):_http_get_json(B 站接口)、
_fetch_view(视频信息)、_subprocess(yt-dlp 与 langid 脚本统一出口)。
仅标准库 + boke 既有模块。
"""
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from . import attribute, diarize, extract_audio, ocr_subtitles
from .library import (
    EV_ANALYSIS_DONE, EV_DOWNLOAD_DONE, EV_FAIL, EV_RETRY,
    ST_FAILED, ST_PENDING_ANALYSIS, ST_PENDING_DOWNLOAD,
    DuplicateRecord, Library,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_COOKIES = ROOT / "work" / "cookies_full.txt"
DEFAULT_MEDIA_DIR = Path("D:/boke_media")
LANGID_SCRIPT = ROOT / "work" / "tools" / "check_language.py"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")
NAV_URL = "https://api.bilibili.com/x/web-interface/nav"
VIEW_URL = "https://api.bilibili.com/x/web-interface/view?bvid={bv}"
VIDEO_URL = "https://www.bilibili.com/video/{bv}"

STAGE_DOWNLOAD = "download"
# 分析链阶段 → 账本 artifacts 键(与各 stage 产物文件口径一致)
STAGE_KEY = {"extract": "audio", "ocr": "srt", "diarize": "rttm",
             "attribute": "tagged", "langid": "langid"}
ANALYZE_STAGES = ("extract", "ocr", "diarize", "attribute", "langid")


# ---------------- 网络与子进程(测试桩点) ----------------


def _http_get_json(url, headers=None):
    """B 站接口 GET→JSON;网络异常向上抛,由调用方转人话。"""
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode("utf-8"))


def _subprocess(cmd, **kw):
    """子进程统一出口:yt-dlp 下载与 langid 脚本都经此,便于测试拦截。"""
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def _fetch_view(bv):
    """拉视频信息(标题/发布日期/分P表);B 站拒绝或网络异常抛 RuntimeError。"""
    sessdata = read_sessdata(DEFAULT_COOKIES)
    headers = {"User-Agent": UA, "Referer": "https://www.bilibili.com/"}
    if sessdata:
        headers["Cookie"] = f"SESSDATA={sessdata}"
    try:
        data = _http_get_json(VIEW_URL.format(bv=bv), headers=headers)
    except Exception as e:
        raise RuntimeError(f"网络请求失败:{e}") from e
    if data.get("code") != 0:
        raise RuntimeError(f"B 站接口拒绝(code={data.get('code')}:"
                           f"{data.get('message', '')}),检查 BV 号或 cookie。")
    return data.get("data") or {}


# ---------------- cookie 预检 ----------------


def read_sessdata(cookies_path) -> str:
    """从 Netscape 格式 cookie 文件抓 SESSDATA 值;无文件/无该字段返回空串。"""
    p = Path(cookies_path)
    if not p.exists():
        return ""
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) < 7:
            fields = line.split()
        if len(fields) >= 7 and fields[5] == "SESSDATA":
            return fields[6].strip()
    return ""


def check_cookie(cookies_path=None):
    """导入前 B 站 cookie 预检。返回 (ok, 人话原因);ok=False 时不该导入。"""
    path = Path(cookies_path) if cookies_path else DEFAULT_COOKIES
    if not path.exists():
        return False, (f"cookie 文件不存在:{path}。"
                       "请先从浏览器导出 B 站登录 cookie 存到该路径。")
    sessdata = read_sessdata(path)
    if not sessdata:
        return False, (f"cookie 文件({path})里没有 SESSDATA 字段,"
                       "说明导出时没登录 B 站,请登录后重新导出。")
    try:
        data = _http_get_json(NAV_URL, headers={
            "User-Agent": UA, "Referer": "https://www.bilibili.com/",
            "Cookie": f"SESSDATA={sessdata}"})
    except Exception as e:
        return False, f"验证 cookie 时连不上 B 站({e}),导入暂停。"
    if data.get("code") == 0 and (data.get("data") or {}).get("isLogin"):
        return True, "cookie 有效"
    return False, (f"B 站说登录已失效(nav 返回 code={data.get('code')} "
                   f"{data.get('message', '')}),请重新导出 cookie 后再导入。")


# ---------------- 小工具 ----------------


def _ts_date(ts):
    """unix 时间戳 → "YYYY-MM-DD";给不了就 None。"""
    try:
        return time.strftime("%Y-%m-%d", time.localtime(int(ts)))
    except (TypeError, ValueError, OSError):
        return None


def _parse_length(s):
    """清单 length 字段 "14:27"/"1:02:03" → 秒;解析不了给 None。"""
    if not s:
        return None
    try:
        parts = [int(x) for x in str(s).split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    return None


# ---------------- 编排器 ----------------


class Orchestrator:
    """导入/下载/分析编排:账本变更全部经 boke.library 的接口落账。

    lib/work_dir/media_dir/cookies_path 均可注入(服务端用默认值,
    单测用 tmp_path)。work_dir 默认 "work"(相对仓库根,与各 stage
    产物落盘口径一致);langid 脚本按同一口径找 work/<stem>/langid.json。
    """

    def __init__(self, lib=None, work_dir="work", media_dir=DEFAULT_MEDIA_DIR,
                 cookies_path=None):
        self.lib = lib if lib is not None else Library()
        self.work_dir = Path(work_dir)
        self.media_dir = Path(media_dir)
        self.cookies_path = Path(cookies_path) if cookies_path else DEFAULT_COOKIES

    # ---------- 导入 ----------

    def ingest_bv(self, bv):
        """粘贴 BV 导入:cookie 门→拉分P信息→逐分P登记(待下载)。

        幂等:已在片库的分P跳过,不重复登记。
        返回 {ok, reason, tasks, added, skipped};ok=False 时 reason
        是给人看的原因(cookie 失效/拉信息失败),账本不动。
        """
        ok, reason = check_cookie(self.cookies_path)
        if not ok:
            return {"ok": False, "reason": reason,
                    "tasks": [], "added": 0, "skipped": 0}
        try:
            view = _fetch_view(bv)
        except Exception as e:
            return {"ok": False, "reason": f"拉取视频信息失败:{e}",
                    "tasks": [], "added": 0, "skipped": 0}
        pages = view.get("pages") or [{"page": 1}]
        date = _ts_date(view.get("pubdate"))
        added = skipped = 0
        tasks = []
        for pg in pages:
            part = int(pg.get("page") or len(tasks) + 1)
            meta = {
                "title": view.get("title"),
                "date": date,
                "duration": pg.get("duration") or view.get("duration"),
                "is_charge": bool(view.get("is_charging_arc")),
            }
            rec, is_new = self._add_or_skip(bv, part, meta)
            added += 1 if is_new else 0
            skipped += 0 if is_new else 1
            tasks.append(rec)
        return {"ok": True, "reason": "", "tasks": tasks,
                "added": added, "skipped": skipped}

    def ingest_catalog(self, items):
        """播主清单勾选批量导入(items=work/space_videos.json 的条目)。

        清单接口没有分P信息,每条按 P1 登记;cookie 门先行,失效整批不登记。
        幂等同 ingest_bv。
        """
        ok, reason = check_cookie(self.cookies_path)
        if not ok:
            return {"ok": False, "reason": reason,
                    "tasks": [], "added": 0, "skipped": 0}
        added = skipped = 0
        tasks = []
        for it in items or []:
            it = it or {}
            bv = it.get("bvid")
            if not bv:
                continue
            meta = {
                "title": it.get("title"),
                "date": _ts_date(it.get("created")),
                "duration": _parse_length(it.get("length")),
                "is_charge": bool(it.get("is_charging_arc") or it.get("is_pay")),
            }
            rec, is_new = self._add_or_skip(bv, 1, meta)
            added += 1 if is_new else 0
            skipped += 0 if is_new else 1
            tasks.append(rec)
        return {"ok": True, "reason": "", "tasks": tasks,
                "added": added, "skipped": skipped}

    def _add_or_skip(self, bv, part, meta):
        """登记一条待下载记录;已在片库则跳过(幂等),返回 (记录, 是否新登记)。"""
        try:
            return self.lib.add(bv, part, **meta), True
        except DuplicateRecord:
            return self.lib.get(bv, part), False

    # ---------- 下载 ----------

    def download(self, bvid, part):
        """下载单个分P(同步):yt-dlp 精下该 P→media_path 入账→待分析。"""
        rec = self.lib.get(bvid, part)
        if rec is None:
            return {"ok": False, "stage": STAGE_DOWNLOAD,
                    "error": f"片库里没有 {bvid} P{part},请先导入。"}
        if rec["status"] != ST_PENDING_DOWNLOAD:
            return {"ok": False, "stage": STAGE_DOWNLOAD,
                    "error": f"{bvid} P{part} 当前是「{rec['status']}」,"
                             "不是待下载,不用重复下载。"}
        self.lib.set_busy(bvid, part, True)
        try:
            path = self._yt_dlp_part(bvid, part)
        except Exception as e:
            return self._fail(bvid, part, STAGE_DOWNLOAD, f"下载失败:{e}")
        if path is None:
            return self._fail(bvid, part, STAGE_DOWNLOAD,
                              "yt-dlp 跑完但没找到产物文件(检查输出目录与磁盘)。")
        rec = self.lib.transition(bvid, part, EV_DOWNLOAD_DONE,
                                  media_path=str(path))
        return {"ok": True, "record": rec}

    def _yt_dlp_part(self, bvid, part):
        """yt-dlp 单分P下载:参数照搬 work/tools/download_exclusive.py,
        输出模板固定 <BV>_P<序号>.%(ext)s(单P视频也不会跑偏文件名)。"""
        self.media_dir.mkdir(parents=True, exist_ok=True)
        cmd = [
            "yt-dlp",
            "--cookies", str(self.cookies_path),
            "--user-agent", UA,
            "--add-headers", "Referer:https://www.bilibili.com/",
            "-f", "bv*[height<=1080]+ba/b",
            "--merge-output-format", "mp4",
            "-N", "4",
            "--retries", "3",
            "--playlist-items", str(part),
            "-o", str(self.media_dir / f"{bvid}_P{part}.%(ext)s"),
            VIDEO_URL.format(bv=bvid),
        ]
        r = _subprocess(cmd)
        if r.returncode != 0:
            tail = (((r.stderr or "").strip() or (r.stdout or "").strip())
                    or f"yt-dlp 退出码 {r.returncode}")[-400:]
            raise RuntimeError(tail)
        tmp_exts = {".part", ".ytdl", ".tmp"}
        hits = [p for p in sorted(self.media_dir.glob(f"{bvid}_P{part}.*"))
                if p.suffix.lower() not in tmp_exts]
        return hits[0] if hits else None

    # ---------- 分析链 ----------

    def analyze(self, bvid, part):
        """跑分析链(同步):extract→ocr→diarize→attribute→langid。

        每步产物路径即入账(崩了也能断点续跑);产物已登记且文件仍在的
        步骤直接跳过(F6)。全完成→待核对+langid 建议标记;
        任一步异常→账本失败态(阶段+错误信息)。
        """
        rec = self.lib.get(bvid, part)
        if rec is None:
            return {"ok": False, "stage": "",
                    "error": f"片库里没有 {bvid} P{part},请先导入。"}
        if rec["status"] != ST_PENDING_ANALYSIS:
            return {"ok": False, "stage": "",
                    "error": f"{bvid} P{part} 当前是「{rec['status']}」,"
                             "不是待分析。"}
        video = rec.get("media_path")
        if not video or not Path(video).exists():
            return self._fail(bvid, part, "extract",
                              f"媒体文件不存在:{video}(下载产物丢了,"
                              "请重试或重新导入)。")
        self.lib.set_busy(bvid, part, True)
        artifacts = dict(rec.get("artifacts") or {})
        stem = Path(video).stem
        stage = "extract"
        try:
            for stage in ANALYZE_STAGES:
                key = STAGE_KEY[stage]
                if stage != "langid" and artifacts.get(key) \
                        and Path(artifacts[key]).exists():
                    continue    # 断点续跑:产物在,跳过该步
                out = self._run_stage(stage, video, stem)
                if stage == "langid":
                    artifacts["langid"] = out["langid"]
                    artifacts["langid_suggest"] = out["suggest"]
                else:
                    artifacts[key] = out[key]
                self.lib.update(bvid, part, artifacts=dict(artifacts))
        except Exception as e:
            return self._fail(bvid, part, stage, str(e) or repr(e))
        rec = self.lib.transition(bvid, part, EV_ANALYSIS_DONE)
        return {"ok": True, "record": rec}

    def _run_stage(self, stage, video, stem):
        """调单个 stage;diarize 用 boke.diarize.run(内置隔离 venv subprocess)。"""
        if stage == "extract":
            return extract_audio.run(video, str(self.work_dir))
        if stage == "ocr":
            return ocr_subtitles.run(video, str(self.work_dir))
        if stage == "diarize":
            return diarize.run(video, str(self.work_dir))
        if stage == "attribute":
            return attribute.run(video, str(self.work_dir))
        return self._run_langid(stem)   # langid

    def _run_langid(self, stem):
        """语言检测:check_language.py 是脚本,subprocess 调它(最小侵入)。
        langid.json 已在则复用不重跑(断点续跑);结论只转成建议标记。"""
        out = self.work_dir / stem / "langid.json"
        if not out.exists():
            r = _subprocess([sys.executable, str(LANGID_SCRIPT), stem],
                            cwd=str(ROOT))
            if r.returncode != 0 or not out.exists():
                tail = ((r.stderr or "").strip() or (r.stdout or "").strip())
                raise RuntimeError("语言检测失败:"
                                   f"{tail[-300:] or '没有产出 langid.json'}")
        data = json.loads(out.read_text(encoding="utf-8"))
        verdict = str(data.get("verdict") or "")
        # D7:检测结论只是建议,"跳过"由用户在核对台确认后经 confirm_skip 落账
        return {"langid": str(out),
                "suggest": "skip" if verdict.startswith("skip") else "dub"}

    # ---------- 失败与重试 ----------

    def _fail(self, bvid, part, stage, error):
        rec = self.lib.transition(bvid, part, EV_FAIL, stage=stage, error=error)
        return {"ok": False, "stage": stage, "error": error, "record": rec}

    def retry(self, bvid, part):
        """失败断点重试(F6):账本 retry 回失败前主态,再按主态续跑——
        待下载→重下;待分析→续分析(产物在的步骤自动跳过)。
        待核对及之后阶段的失败归对应后台模块驱动,这里只恢复状态不越权。"""
        rec = self.lib.get(bvid, part)
        if rec is None:
            return {"ok": False, "error": f"片库里没有 {bvid} P{part}。"}
        if rec["status"] != ST_FAILED:
            return {"ok": False,
                    "error": f"{bvid} P{part} 当前是「{rec['status']}」,"
                             "不是失败态,无需重试。"}
        back = rec.get("failed_from")
        self.lib.transition(bvid, part, EV_RETRY)
        if back == ST_PENDING_DOWNLOAD:
            return self.download(bvid, part)
        if back == ST_PENDING_ANALYSIS:
            return self.analyze(bvid, part)
        return {"ok": True, "resumed": back,
                "record": self.lib.get(bvid, part),
                "note": f"已恢复到「{back}」,该阶段的任务由对应模块继续驱动。"}
