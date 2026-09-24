# -*- coding: utf-8 -*-
"""试听台 + 核对台 + 片库 HTTP 服务(07 号票:mock 页面接真数据)。

静态路由(既有,向后兼容):
  /            → work/web/index.html;  /library.html /review.html 同目录
  /audio/*     → work/web/audio;  /refs/* → 仓库根 refs/(人物库参考音试听)
  /media/*     → in/(本地视频大文件,LAN 流式播放)与 D:/boke_media
  /work/*      → work/ 下音频产物(dec_<hash>/preview.mp3、成品 m4a;
                 只放行音频扩展,账本/人物库 JSON 不外露)
  POST /save_picks /save_review → work/*.json(旧 mock 落盘,保留)

API 路由(全部 JSON;页面轮询 /api/library 每 2s 刷新):
  GET  /api/library                     账本全量+管道计数+待办+播主清单
  GET  /api/episode/<bvid>              期详情(parts/说话人样本/langid/小样/成品)
  GET  /api/persons                     人物库(参考音附可试听 URL)
  POST /api/ingest                      {bv} 或 {items:[...]} 导入;cookie 失效 423
  POST /api/review                      每分P决策 JSON → 03 应用器 → 05 小样(后台)
  POST /api/preview/<bvid>/<part>       {"verdict":"pass|fail"} → 06 全片(后台)/回退
  POST /api/retry/<bvid>/<part>         失败断点重试(按失败前主态路由 04/05/06)
  POST /api/persons/ref                 收录说话人候选到人物(episode 来源必须带 part)

后台长任务(下载→分析 / 小样 / 全片 / 重跑分人)经 threading.Thread 驱动
04/05/06;每分P同时至多一个后台任务(进程内 dict 防重入);线程异常必落
账本失败态(待核对等无失败边的主态只清 busy 并打日志,不裸吞)。

绑定 0.0.0.0:8765;测试用 make_server("127.0.0.1", 0, app) 起随机端口实例。
"""
import json
import re
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

WORK = Path(__file__).resolve().parents[1]
WEB = WORK / "web"
ROOT = WORK.parent
MEDIA_DIRS = [WORK.parent / "in", Path("D:/boke_media")]
_SRC = ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from boke import attribute, diarize, fullmix           # noqa: E402
from boke import orchestrator, preview as boke_preview   # noqa: E402
from boke import review_apply                            # noqa: E402
from boke.common import parse_srt                        # noqa: E402
from boke.library import (                               # noqa: E402
    EV_CONFIRM_SKIP, EV_FAIL, EV_PREVIEW_REJECT, EV_REDIARIZE, EV_REVIEW_SUBMIT,
    FAILABLE_STATES, IllegalTransition,
    ST_EXCLUDED, ST_FAILED, ST_PENDING_DOWNLOAD, ST_PENDING_REVIEW,
    ST_PREVIEW, ST_REVIEWED, ST_SYNTHESIZING, Library,
)
from boke.orchestrator import Orchestrator               # noqa: E402
from boke.persons import PersonLibrary                   # noqa: E402
from boke.review_apply import DecisionError              # noqa: E402

_EP_RE = re.compile(r"^/api/episode/([A-Za-z0-9]+)$")
_PREVIEW_RE = re.compile(r"^/api/preview/([^/]+)/(\d+)$")
_RETRY_RE = re.compile(r"^/api/retry/([^/]+)/(\d+)$")
_SPK_RE = re.compile(r"^\[(SPK_\d+)\]")
# /work 与 /refs 只放行音频扩展(小样 mp3/成品 m4a/说话人样本与参考音 wav)
_AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".flac"}
_VIDEO_EXTS = {".mp4", ".mkv", ".flv", ".webm"}


def _norm_part(p):
    """分P口径归一:1 / "1" / "P1" → 1;非法 → None。"""
    if p is None:
        return None
    s = str(p).strip().lower()
    if s.startswith("p"):
        s = s[1:]
    try:
        return int(s)
    except ValueError:
        return None


# ---------------- 应用层(路由处理器,可整体注入测试替身) ----------------


class App:
    """API 应用:持账本/人物库/编排器与后台任务调度,方法返回 (code, obj)。"""

    def __init__(self, lib=None, persons=None, orch=None, work_dir=None,
                 web_audio_dir=None, catalog_path=None):
        self.work_dir = Path(work_dir) if work_dir else WORK
        self.lib = lib if lib is not None else Library(self.work_dir / "library.json")
        self.persons = persons if persons is not None else PersonLibrary()
        self.orch = orch if orch is not None else \
            Orchestrator(lib=self.lib, work_dir=str(self.work_dir))
        self.web_audio_dir = Path(web_audio_dir) if web_audio_dir else WEB / "audio"
        self.catalog_path = Path(catalog_path) if catalog_path \
            else WORK / "space_videos.json"
        self.sync_bg = False          # 测试注入:后台任务同步执行
        self._bg = {}                 # (bvid, part) -> stage,防重入
        self._bg_lock = threading.Lock()

    # ---------- 后台任务调度 ----------

    def spawn(self, bvid, part, stage, fn):
        """每分P同时至多一个后台任务;异常经 _guarded 落账本,不裸吞。

        sync_bg=True 时同步执行(测试用,响应返回时链已跑完)。
        返回是否真的启动(已有任务在跑则 False)。
        """
        key = (bvid, int(part))
        with self._bg_lock:
            if key in self._bg:
                return False
            self._bg[key] = stage
        if self.sync_bg:
            self._guarded(key, stage, fn)
        else:
            threading.Thread(target=self._guarded, args=(key, stage, fn),
                             daemon=True,
                             name=f"bg-{stage}-{bvid}-P{part}").start()
        return True

    def _guarded(self, key, stage, fn):
        bvid, part = key
        try:
            fn()
        except Exception as e:
            err = f"{stage}后台任务异常:{e}"
            print(f"[bg] {err}", flush=True)
            self._fail_or_clear(bvid, part, stage, err)
        finally:
            with self._bg_lock:
                self._bg.pop(key, None)

    def _fail_or_clear(self, bvid, part, stage, err):
        """线程异常善后:可失败主态落账本失败态;无失败边的主态清 busy。"""
        try:
            rec = self.lib.get(bvid, part)
            if rec is None:
                return
            if rec["status"] in FAILABLE_STATES:
                self.lib.transition(bvid, part, EV_FAIL, stage=stage, error=err)
                return
            if rec["busy"]:
                self.lib.set_busy(bvid, part, False)
        except Exception as e2:
            print(f"[bg] 失败登记也失败:{e2}", flush=True)

    # ---------- 后台动作(04/05/06 驱动) ----------

    def _run_download_analyze(self, bvid, part):
        r = self.orch.download(bvid, part)
        if r.get("ok"):
            self.orch.analyze(bvid, part)

    def _run_preview(self, bvid, part):
        boke_preview.generate_preview(bvid, part, library=self.lib,
                                      work_root=str(self.work_dir))

    def _run_fullmix(self, bvid, part):
        fullmix.run_full_mix(bvid, part, lib=self.lib, work_dir=self.work_dir)

    def _run_retry(self, bvid, part):
        r = self.orch.retry(bvid, part)
        if not r.get("ok"):
            return
        resumed = r.get("resumed")
        if resumed == ST_REVIEWED:            # 小样阶段失败 → 重出小样
            self._run_preview(bvid, part)
        elif resumed == ST_SYNTHESIZING:      # 全片阶段失败 → 续全片
            self._run_fullmix(bvid, part)

    def _run_rediarize(self, bvid, part, max_speakers):
        """重跑分人:diarize(新人数)→ 重归人 → 重测语言 → 产物入账 → 清 busy。

        状态已由 EV_REDIARIZE 留在待核对(无失败边),异常经 _guarded 只能
        清 busy + 打日志——页面轮询看到 busy 归零即知重跑结束/失败。
        """
        rec = self.lib.get(bvid, part)
        if rec is None or not rec.get("media_path"):
            raise RuntimeError("记录缺媒体文件,无法重跑分人")
        video = rec["media_path"]
        stem = Path(video).stem
        wd = str(self.work_dir)
        d = diarize.run(video, wd, max_speakers=int(max_speakers or 6),
                        force=True)
        a = attribute.run(video, wd, force=True)
        (self.work_dir / stem / "langid.json").unlink(missing_ok=True)
        out = self.orch._run_langid(stem)     # 说话人标签变了,语言结论重测
        arts = dict((self.lib.get(bvid, part) or {}).get("artifacts") or {})
        arts.update({"rttm": d.get("rttm") or str(self.work_dir / stem / "diar.rttm"),
                     "tagged": a.get("tagged") or str(self.work_dir / stem / "tagged.srt"),
                     "langid": out["langid"], "langid_suggest": out["suggest"]})
        self.lib.update(bvid, part, artifacts=arts)
        self.lib.set_busy(bvid, part, False)

    # ---------- URL 映射 ----------

    def _file_url(self, path):
        """账本/人物库文件路径 → 页面可播 URL;放不进已知目录返回 None。"""
        if not path:
            return None
        p = Path(str(path))
        if not p.is_absolute():
            p = ROOT / p
        try:
            p = p.resolve()
        except OSError:
            pass
        for base, prefix in ((WEB, "/"), (ROOT / "refs", "/refs/"),
                             (self.work_dir, "/work/")):
            try:
                return prefix + p.relative_to(base).as_posix()
            except ValueError:
                continue
        for d in list(MEDIA_DIRS) + [getattr(self.orch, "media_dir", None)]:
            if d is None:
                continue
            try:
                if p.parent == Path(d).resolve():
                    return "/media/" + p.name
            except OSError:
                continue
        if p.suffix.lower() in _VIDEO_EXTS:
            return "/media/" + p.name
        return None

    def _catalog(self):
        """播主清单(work/space_videos.json,清单勾选导入的数据源)。"""
        try:
            data = json.loads(self.catalog_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    # ---------- GET /api/library ----------

    def api_library(self):
        recs = self.lib.query()
        counts = {}
        for r in recs:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        out = []
        for r in recs:
            r = dict(r)
            r["media_url"] = self._file_url(r.get("media_path"))
            r["preview_url"] = self._file_url(r.get("preview_path"))
            r["output_url"] = self._file_url(r.get("output_path"))
            out.append(r)
        todo = [{"bvid": r["bvid"], "part": r["part"], "title": r.get("title"),
                 "status": r["status"]}
                for r in recs if r["status"] == ST_PENDING_REVIEW]
        return 200, {"ok": True, "records": out, "counts": counts,
                     "todo": todo, "catalog": self._catalog()}

    # ---------- GET /api/episode/<bvid> ----------

    def api_episode(self, bvid):
        recs = self.lib.query(bvid=bvid)
        if not recs:
            return 404, {"ok": False, "error": f"片库无 {bvid} 的记录"}
        title = next((r.get("title") for r in recs if r.get("title")), bvid)
        return 200, {"ok": True, "bvid": bvid, "title": title,
                     "parts": [self._part_view(r) for r in recs]}

    def _part_view(self, rec):
        arts = rec.get("artifacts") or {}
        tagged = arts.get("tagged")
        stem = None
        if tagged:
            stem = Path(tagged).parent.name
        elif rec.get("media_path"):
            stem = Path(rec["media_path"]).stem
        else:
            stem = f"{rec['bvid']}_P{rec['part']}"
        langid = None
        if arts.get("langid") and Path(arts["langid"]).exists():
            try:
                langid = json.loads(
                    Path(arts["langid"]).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                langid = None
        return {
            "part": rec["part"], "status": rec["status"], "busy": rec["busy"],
            "title": rec.get("title"), "date": rec.get("date"),
            "duration": rec.get("duration"), "is_charge": rec.get("is_charge"),
            "media_url": self._file_url(rec.get("media_path")),
            "suggest": arts.get("langid_suggest"),
            "langid": langid,
            "speakers": self._speakers_view(arts, stem),
            "preview_url": self._file_url(rec.get("preview_path")),
            "preview_stale": rec.get("preview_stale"),
            "output_url": self._file_url(rec.get("output_path")),
            "review": rec.get("review"), "dec_hash": rec.get("dec_hash"),
            "error": rec.get("error"), "failed_stage": rec.get("failed_stage"),
            "failed_from": rec.get("failed_from"),
        }

    def _speakers_view(self, arts, stem):
        """说话人卡数据真源:tagged.srt 归人结论 + langid 语言 + 切好的样本。"""
        talk, order = {}, []
        tagged = arts.get("tagged")
        if tagged and Path(tagged).exists():
            for s in parse_srt(tagged):
                m = _SPK_RE.match(s.text.strip())
                spk = m.group(1) if m else "SPK_00"
                if spk not in talk:
                    talk[spk] = 0.0
                    order.append(spk)
                talk[spk] += s.t1 - s.t0
        langs = {}
        if arts.get("langid") and Path(arts["langid"]).exists():
            try:
                langs = (json.loads(
                    Path(arts["langid"]).read_text(encoding="utf-8"))
                    .get("speakers")) or {}
            except (OSError, json.JSONDecodeError):
                langs = {}
        out = []
        for spk in sorted(order):
            sample = f"speaker_{stem}_{spk}_sample.wav"
            cands = [f"cand_{stem}_{spk}_{i}.wav" for i in (1, 2)]
            out.append({
                "id": spk,
                "lang": (langs.get(spk) or {}).get("lang"),
                "talk_sec": round(talk[spk], 1),
                "sample": ("/audio/" + sample)
                if (self.web_audio_dir / sample).exists() else None,
                "cands": ["/audio/" + c for c in cands
                          if (self.web_audio_dir / c).exists()],
            })
        return out

    # ---------- POST /api/ingest ----------

    def api_ingest(self, body):
        bv = str((body or {}).get("bv") or "").strip()
        items = (body or {}).get("items")
        if not bv and not items:
            return 400, {"ok": False,
                         "error": "body 要么 {\"bv\": ...} 要么 {\"items\": [...]}"}
        ok, reason = orchestrator.check_cookie(self.orch.cookies_path)
        if not ok:
            return 423, {"ok": False, "error": reason, "cookie": False}
        if bv:
            res = self.orch.ingest_bv(bv)
        else:
            res = self.orch.ingest_catalog(items)
        if not res.get("ok"):
            return 502, {"ok": False,
                         "error": res.get("reason") or "导入失败"}
        for t in res.get("tasks") or []:
            if t.get("status") == ST_PENDING_DOWNLOAD:
                self.spawn(t["bvid"], t["part"], "download",
                           lambda b=t["bvid"], p=t["part"]:
                           self._run_download_analyze(b, p))
        return 200, {"ok": True, "added": res["added"], "skipped": res["skipped"],
                     "tasks": [{"bvid": t["bvid"], "part": t["part"],
                                "status": t["status"]}
                               for t in res.get("tasks") or []]}

    # ---------- POST /api/review ----------

    def api_review(self, body):
        body = body or {}
        bvid = str(body.get("bvid") or body.get("ep") or "").strip()
        part = _norm_part(body.get("part"))
        if not bvid or part is None:
            return 400, {"ok": False, "error": "缺 bvid / part"}
        decision = body.get("decision")
        if not isinstance(decision, dict):
            decision = {k: body[k] for k in ("verdict", "speakers", "rediarize")
                        if k in body}
        rec = self.lib.get(bvid, part)
        if rec is None:
            return 404, {"ok": False, "error": f"片库无 {bvid} P{part}"}
        if rec["status"] != ST_PENDING_REVIEW:
            return 409, {"ok": False,
                         "error": f"{bvid} P{part} 当前是「{rec['status']}」,"
                                  "只有「待核对」能提交核对决策。"}
        if decision.get("rediarize"):
            # 调 03 应用器 → 决策作废信号 → 账本 rediarize(回待核对,决策清空)
            core = review_apply.apply_decision(
                decision, rec.get("media_path") or "", str(self.work_dir),
                part=part, persons=self.persons)
            if core.get("status") != review_apply.STATUS_VOIDED:
                return 500, {"ok": False, "error": "应用器未按 rediarize 作废决策"}
            self.lib.transition(bvid, part, EV_REDIARIZE)
            mx = (decision.get("rediarize") or {}).get("max_speakers") or 6
            self.spawn(bvid, part, "rediarize",
                       lambda: self._run_rediarize(bvid, part, mx))
            return 200, {"ok": True, "voided": True,
                         "status": ST_PENDING_REVIEW, "max_speakers": mx}
        verdict = decision.get("verdict")
        if verdict == "skip":                      # D7:用户确认跳过才排除
            self.lib.transition(bvid, part, EV_CONFIRM_SKIP, review=decision)
            return 200, {"ok": True, "excluded": True, "status": ST_EXCLUDED}
        if verdict != "dub":
            return 400, {"ok": False,
                         "error": "verdict 必须是 dub 或 skip(重跑分人走 rediarize)"}
        if not rec.get("media_path"):
            return 400, {"ok": False, "error": "记录缺媒体文件,无法应用决策"}
        try:
            r = review_apply.apply_decision(
                decision, rec["media_path"], str(self.work_dir), part=part,
                persons=self.persons)
        except DecisionError as e:
            return 400, {"ok": False, "error": str(e)}
        self.lib.transition(bvid, part, EV_REVIEW_SUBMIT, review=decision)
        self.lib.update(bvid, part, dec_hash=r["dec_hash"])
        self.spawn(bvid, part, "preview",
                   lambda: self._run_preview(bvid, part))
        return 200, {"ok": True,
                     "status": (self.lib.get(bvid, part) or {}).get("status"),
                     "dec_hash": r["dec_hash"], "dec_dir": r.get("dec_dir"),
                     "apply": r.get("status")}

    # ---------- POST /api/preview/<bvid>/<part> ----------

    def api_preview(self, bvid, part, body):
        verdict = str((body or {}).get("verdict") or "").strip()
        rec = self.lib.get(bvid, part)
        if rec is None:
            return 404, {"ok": False, "error": f"片库无 {bvid} P{part}"}
        if verdict == "fail":
            try:
                self.lib.transition(bvid, part, EV_PREVIEW_REJECT)
            except IllegalTransition as e:
                return 409, {"ok": False, "error": str(e)}
            return 200, {"ok": True, "status": ST_PENDING_REVIEW,
                         "preview_stale": True}
        if verdict == "pass":
            if rec["status"] not in (ST_PREVIEW, ST_SYNTHESIZING):
                return 409, {"ok": False,
                             "error": f"{bvid} P{part} 当前是「{rec['status']}」,"
                                      "只有「小样待听」(或失败重入的全片合成中)"
                                      "能点过跑全片。"}
            started = self.spawn(bvid, part, "fullmix",
                                 lambda: self._run_fullmix(bvid, part))
            return 200, {"ok": True, "started": started,
                         "status": (self.lib.get(bvid, part) or {}).get("status")}
        return 400, {"ok": False, "error": 'verdict 必须是 "pass" 或 "fail"'}

    # ---------- POST /api/retry/<bvid>/<part> ----------

    def api_retry(self, bvid, part):
        rec = self.lib.get(bvid, part)
        if rec is None:
            return 404, {"ok": False, "error": f"片库无 {bvid} P{part}"}
        if rec["status"] != ST_FAILED:
            return 409, {"ok": False,
                         "error": f"{bvid} P{part} 当前是「{rec['status']}」,"
                                  "不是失败态,无需重试。"}
        started = self.spawn(bvid, part, "retry",
                             lambda: self._run_retry(bvid, part))
        return 200, {"ok": True, "started": started,
                     "resumed": rec.get("failed_from")}

    # ---------- /api/persons ----------

    def api_persons(self):
        persons = self.persons.list_persons()
        for p in persons:
            for ref in p.get("refs") or []:
                ref["audio_url"] = self._file_url(ref.get("audio"))
        return 200, {"ok": True, "persons": persons}

    @staticmethod
    def _audio_rel(a):
        """参考音 URL/路径 → 仓库相对路径(persons 存储口径)。"""
        if a is None:
            return None
        s = str(a).strip().replace("\\", "/")
        if not s:
            return None
        for prefix, rel in (("/audio/", "work/web/audio/"),
                            ("/refs/", "refs/"), ("/work/", "work/")):
            if s.startswith(prefix):
                return rel + s[len(prefix):]
        return s

    def api_persons_ref(self, body):
        body = body or {}
        source = body.get("source")
        source = source if isinstance(source, dict) else {"kind": "manual"}
        if source.get("kind") == "episode":
            part = _norm_part(source.get("part"))
            bvid = str(source.get("bvid") or "").strip()
            spk = str(source.get("spk") or "").strip()
            if part is None:
                return 400, {"ok": False,
                             "error": "episode 来源必须带 part(来源分P溯源,"
                                      "评审 R1)——说话人参考音要能追到当期分P。"}
            if not bvid or not spk:
                return 400, {"ok": False,
                             "error": "episode 来源需带 bvid 与 spk"}
            source = {"kind": "episode", "bvid": bvid, "part": part, "spk": spk}
        else:
            source = {"kind": "manual"}
        audio = self._audio_rel(body.get("audio"))
        if not audio:
            return 400, {"ok": False, "error": "缺参考音 audio(可试听候选的路径)"}
        lang = body.get("lang") or None
        new_name = str(body.get("new_name") or "").strip()
        person = str(body.get("person") or "").strip()
        note = str(body.get("note") or "")
        try:
            if new_name:
                if self.persons.get(new_name):
                    return 400, {"ok": False, "error": f"人物已存在:{new_name}"}
                self.persons.add_person(new_name, body.get("main_lang") or lang
                                        or "zh", note=note)
                person = new_name
            elif not person or self.persons.get(person) is None:
                return 400, {"ok": False, "error": f"人物不存在:{person or '(空)'}"}
            ref = self.persons.add_ref(person, audio,
                                       body.get("transcript") or None,
                                       lang, source)
        except (ValueError, KeyError) as e:
            return 400, {"ok": False, "error": str(e)}
        return 200, {"ok": True, "ref": ref, "person": self.persons.get(person)}


# ---------------- HTTP 层 ----------------


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    # ---- 静态翻译:/media /work /refs ----

    def _under(self, base: Path, rel: str, exts=None):
        """rel 限死在 base 内;exts 给定时只放行这些扩展,否则给不存在路径→404。"""
        target = (base / rel).resolve()
        inside = target == base or base in target.parents
        if inside and (exts is None or target.suffix.lower() in exts):
            return str(target)
        return str(base / ".forbidden")

    def translate_path(self, path):
        p = urlsplit(path).path
        m = re.match(r"^/media/(.+)$", p)
        if m:
            name = m.group(1)
            for d in MEDIA_DIRS:
                target = (d / name).resolve()
                if target.exists():
                    return str(target)
        app = getattr(self.server, "app", None)
        m = re.match(r"^/work/(.+)$", p)
        if m:
            base = app.work_dir if app is not None else WORK
            return self._under(Path(base), unquote(m.group(1)), _AUDIO_EXTS)
        m = re.match(r"^/refs/(.+)$", p)
        if m:
            return self._under(ROOT / "refs", unquote(m.group(1)), _AUDIO_EXTS)
        return super().translate_path(path)

    # ---- JSON 小工具 ----

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    # ---- 路由 ----

    def do_GET(self):
        app = getattr(self.server, "app", None)
        path = urlsplit(self.path).path
        if path.startswith("/api/"):
            if app is None:
                return self._json({"ok": False, "error": "API 未初始化"}, 500)
            try:
                code, obj = self._api_get(app, path)
            except Exception as e:
                code, obj = 500, {"ok": False, "error": f"服务器内部错误:{e}"}
            return self._json(obj, code)
        return super().do_GET()

    def _api_get(self, app, path):
        if path == "/api/library":
            return app.api_library()
        m = _EP_RE.match(path)
        if m:
            return app.api_episode(m.group(1))
        if path == "/api/persons":
            return app.api_persons()
        return 404, {"ok": False, "error": f"无此接口:{path}"}

    def _save(self, fname):
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n).decode("utf-8"))
        out = WORK / fname
        prev = []
        if out.exists():
            try:
                prev = json.loads(out.read_text(encoding="utf-8"))
                if not isinstance(prev, list):
                    prev = [prev]
            except Exception:
                prev = []
        prev.append(data)
        out.write_text(json.dumps(prev, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_POST(self):
        path = urlsplit(self.path).path
        if path == "/save_picks":
            return self._save("voice_picks.json")
        if path == "/save_review":
            return self._save("review_decisions_mock.json")
        if not path.startswith("/api/"):
            return self.send_error(404)
        app = getattr(self.server, "app", None)
        if app is None:
            return self._json({"ok": False, "error": "API 未初始化"}, 500)
        try:
            body = self._body()
        except ValueError as e:
            return self._json({"ok": False,
                               "error": f"body 不是合法 JSON:{e}"}, 400)
        try:
            code, obj = self._api_post(app, path, body)
        except Exception as e:
            code, obj = 500, {"ok": False, "error": f"服务器内部错误:{e}"}
        return self._json(obj, code)

    def _api_post(self, app, path, body):
        if path == "/api/ingest":
            return app.api_ingest(body)
        if path == "/api/review":
            return app.api_review(body)
        if path == "/api/persons/ref":
            return app.api_persons_ref(body)
        m = _PREVIEW_RE.match(path)
        if m:
            return app.api_preview(m.group(1), int(m.group(2)), body)
        m = _RETRY_RE.match(path)
        if m:
            return app.api_retry(m.group(1), int(m.group(2)))
        return 404, {"ok": False, "error": f"无此接口:{path}"}

    def log_message(self, fmt, *args):
        print("[http]", self.address_string(), fmt % args, flush=True)


def make_server(host="0.0.0.0", port=8765, app=None):
    """起服务实例(测试传 app 与随机端口;main 用默认 App 与 8765)。"""
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.app = app if app is not None else App()
    return srv


def main():
    srv = make_server("0.0.0.0", 8765)
    print(f"[web] serving 0.0.0.0:8765 → {WEB} (+/media /work /refs /api/*)",
          flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
