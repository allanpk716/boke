# -*- coding: utf-8 -*-
"""片库账本与状态机:work/library.json 唯一状态源(01 号票)。

每视频每分P一条记录。主链七态(待下载→待分析→待核对→已核对待合成→小样待听→
全片合成中→已交付)+ 旁路两态(已排除=终态/失败),转移规则照 proposal rev1
§2 M3(含小样不过回退边 F7、失败断点重试 F6、改人数重跑分人决策作废 D9)。

单写入者:同进程内按账本文件路径加锁串行(多线程/多实例共享同一把锁),
变更后整文件原子替换——临时文件写完 fsync 再 os.replace。

状态只能经 transition()(唯一入口)变更;busy 只能经 set_busy() 或带副作用的
转移变更(提交核对/点过即自动开工置位,完成边与 fail 清除);失败上下文
(failed_from/failed_stage/error)只能由 fail 事件写入,retry 回原主态并清空。

记录字段:
  bvid/part                    标识(分P为决策最小单元,D9)
  title/date/duration/is_charge/media_path   期名/日期/时长/充电专属/媒体路径
  status                       九态之一(只能经 transition 变更)
  busy                         进行中子标志(下载中/分析中/小样合成中/全片合成中;
                               是字段不是状态)
  artifacts                    各阶段产物路径 {audio/subs/diar/attr/...}
  review                       核对决策 JSON {verdict: dub|skip, speakers: [...],
                               rediarize?: {max_speakers}}
  dec_hash                     提交核对时的决策内容哈希(下游产物版本键,F12)
  preview_path/preview_stale   小样路径与作废标记(旧小样文件保留可回听)
  output_path                  全片成品路径
  failed_from/failed_stage/error   失败时所处主态/阶段/错误信息
  created_at/updated_at        时间戳(本地时间,自动维护)
"""
import copy
import json
import os
import tempfile
import threading
import time
from pathlib import Path

DEFAULT_PATH = Path("work") / "library.json"

# ---------------- 状态(与 spec/CONTEXT.md 用词一致) ----------------

ST_PENDING_DOWNLOAD = "待下载"
ST_PENDING_ANALYSIS = "待分析"
ST_PENDING_REVIEW = "待核对"
ST_REVIEWED = "已核对待合成"
ST_PREVIEW = "小样待听"
ST_SYNTHESIZING = "全片合成中"
ST_DELIVERED = "已交付"
ST_EXCLUDED = "已排除"      # 旁路终态(D7:用户确认跳过才进入)
ST_FAILED = "失败"           # 旁路,可 retry 断点重入

MAIN_STATES = (ST_PENDING_DOWNLOAD, ST_PENDING_ANALYSIS, ST_PENDING_REVIEW,
               ST_REVIEWED, ST_PREVIEW, ST_SYNTHESIZING, ST_DELIVERED)
# 有后台任务在跑、可失败进入旁路的主态(proposal M3 明示的四条失败边)
FAILABLE_STATES = (ST_PENDING_DOWNLOAD, ST_PENDING_ANALYSIS,
                   ST_REVIEWED, ST_SYNTHESIZING)
# 不允许置 busy 的状态(两个终态 + 失败态;失败态须先 retry 回主态)
BUSY_FORBIDDEN = (ST_EXCLUDED, ST_FAILED, ST_DELIVERED)

# ---------------- 事件与转移表 ----------------

EV_DOWNLOAD_DONE = "download_done"
EV_ANALYSIS_DONE = "analysis_done"
EV_REVIEW_SUBMIT = "review_submit"
EV_CONFIRM_SKIP = "confirm_skip"
EV_REDIARIZE = "rediarize"
EV_PREVIEW_READY = "preview_ready"
EV_PREVIEW_PASS = "preview_pass"
EV_PREVIEW_REJECT = "preview_reject"
EV_FULL_DONE = "full_done"
EV_FAIL = "fail"
EV_RETRY = "retry"

EVENTS = (EV_DOWNLOAD_DONE, EV_ANALYSIS_DONE, EV_REVIEW_SUBMIT, EV_CONFIRM_SKIP,
          EV_REDIARIZE, EV_PREVIEW_READY, EV_PREVIEW_PASS, EV_PREVIEW_REJECT,
          EV_FULL_DONE, EV_FAIL, EV_RETRY)

# (状态, 事件) -> 目标状态;"*" 表示回到 failed_from(失败断点重试)
TRANSITIONS = {
    (ST_PENDING_DOWNLOAD, EV_DOWNLOAD_DONE): ST_PENDING_ANALYSIS,
    (ST_PENDING_DOWNLOAD, EV_FAIL): ST_FAILED,
    (ST_PENDING_ANALYSIS, EV_ANALYSIS_DONE): ST_PENDING_REVIEW,
    (ST_PENDING_ANALYSIS, EV_FAIL): ST_FAILED,
    (ST_PENDING_REVIEW, EV_REVIEW_SUBMIT): ST_REVIEWED,
    (ST_PENDING_REVIEW, EV_CONFIRM_SKIP): ST_EXCLUDED,
    (ST_PENDING_REVIEW, EV_REDIARIZE): ST_PENDING_REVIEW,
    (ST_REVIEWED, EV_PREVIEW_READY): ST_PREVIEW,
    (ST_REVIEWED, EV_FAIL): ST_FAILED,
    (ST_PREVIEW, EV_PREVIEW_PASS): ST_SYNTHESIZING,
    (ST_PREVIEW, EV_PREVIEW_REJECT): ST_PENDING_REVIEW,   # F7 回退边:小样不过
    (ST_SYNTHESIZING, EV_FULL_DONE): ST_DELIVERED,
    (ST_SYNTHESIZING, EV_FAIL): ST_FAILED,
    (ST_FAILED, EV_RETRY): "*",
}
assert all(ev in EVENTS for (_, ev) in TRANSITIONS)

# 各事件允许携带的字段(fail 的 stage/error 单独必填校验)
_EVENT_FIELDS = {
    EV_DOWNLOAD_DONE: {"media_path"},
    EV_ANALYSIS_DONE: set(),
    EV_REVIEW_SUBMIT: {"review"},
    EV_CONFIRM_SKIP: {"review"},
    EV_REDIARIZE: set(),
    EV_PREVIEW_READY: {"preview_path"},
    EV_PREVIEW_PASS: set(),
    EV_PREVIEW_REJECT: set(),
    EV_FULL_DONE: {"output_path"},
    EV_FAIL: {"stage", "error"},
    EV_RETRY: set(),
}

# ---------------- 异常 ----------------


class LibraryError(Exception):
    """账本模块错误基类(文件损坏/格式不对)。"""


class RecordNotFound(LibraryError):
    """账本中无该 bvid+分P 记录。"""


class DuplicateRecord(LibraryError):
    """该 bvid+分P 已在片库中。"""


class IllegalTransition(LibraryError):
    """非法状态转移/busy 操作(状态机拒绝)。"""


# ---------------- 内部工具 ----------------

_locks = {}
_locks_guard = threading.Lock()


def _lock_for(path: Path) -> threading.Lock:
    """按账本文件路径取进程内锁:同进程多实例串行写同一文件。"""
    key = str(path.resolve())
    with _locks_guard:
        if key not in _locks:
            _locks[key] = threading.Lock()
        return _locks[key]


_META_FIELDS = ("title", "date", "duration", "is_charge", "media_path")
_CONTENT_FIELDS = ("artifacts", "review", "dec_hash",
                   "preview_path", "preview_stale", "output_path")
# 只能由状态机写的字段(update() 封锁)
_LOCKED_FIELDS = ("status", "busy", "failed_from", "failed_stage", "error")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


# ---------------- 账本 ----------------


class Library:
    """片库账本:读写 work/library.json 并守卫状态机。

    每次操作在路径锁内重新读盘→变更→原子写回,因此同进程多个 Library
    实例(页面/后台任务各持一个)看到同一份状态,并发写互不覆盖。
    """

    def __init__(self, path=None):
        self.path = Path(path) if path is not None else DEFAULT_PATH
        self._lock = _lock_for(self.path)

    # ---- 存储 ----

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "records": {}}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            raise LibraryError(f"账本文件损坏,无法读取: {self.path} ({e})") from e
        if not isinstance(data, dict) or not isinstance(data.get("records"), dict):
            raise LibraryError(f"账本格式不对(records 缺失): {self.path}")
        return data

    def _save(self, data: dict):
        """原子写:临时文件写完 fsync,再 os.replace 顶掉旧文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent),
                                   prefix=".library-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    @staticmethod
    def _key(bvid, part) -> str:
        return f"{bvid}|{int(part)}"

    def _rec(self, data: dict, bvid, part) -> dict:
        rec = data["records"].get(self._key(bvid, part))
        if rec is None:
            raise RecordNotFound(f"片库无 {bvid} P{part}")
        return rec

    # ---- 查询 ----

    def get(self, bvid, part):
        """按 bvid+分P 查单条记录(深拷贝,外部改不动账本);无则 None。"""
        with self._lock:
            rec = self._load()["records"].get(self._key(bvid, part))
            return copy.deepcopy(rec) if rec is not None else None

    def query(self, bvid=None, status=None, busy=None) -> list:
        """列记录(按 bvid/状态/busy 过滤,按 bvid+part 排序),返回深拷贝。"""
        with self._lock:
            recs = copy.deepcopy(list(self._load()["records"].values()))
        out = [r for r in recs
               if (bvid is None or r["bvid"] == bvid)
               and (status is None or r["status"] == status)
               and (busy is None or r["busy"] == busy)]
        out.sort(key=lambda r: (r["bvid"], r["part"]))
        return out

    # ---- 记录 CRUD ----

    def add(self, bvid, part, **meta) -> dict:
        """导入登记:新建一条待下载记录;重复导入报 DuplicateRecord。"""
        unknown = [f for f in meta if f not in _META_FIELDS]
        if unknown:
            raise ValueError(f"add 不接受字段: {unknown}")
        with self._lock:
            data = self._load()
            k = self._key(bvid, part)
            if k in data["records"]:
                raise DuplicateRecord(f"{k} 已在片库中")
            rec = {"bvid": bvid, "part": int(part),
                   "title": None, "date": None, "duration": None,
                   "is_charge": False, "media_path": None,
                   "status": ST_PENDING_DOWNLOAD, "busy": False,
                   "artifacts": {}, "review": None, "dec_hash": None,
                   "preview_path": None, "preview_stale": False,
                   "output_path": None,
                   "failed_from": None, "failed_stage": None, "error": None,
                   "created_at": _now(), "updated_at": _now()}
            rec.update(meta)
            data["records"][k] = rec
            self._save(data)
            return copy.deepcopy(rec)

    def update(self, bvid, part, **fields) -> dict:
        """改记录内容(元数据/产物路径/核对决策等);状态机字段封锁。"""
        locked = [f for f in fields if f in _LOCKED_FIELDS]
        if locked:
            raise ValueError(f"字段 {locked} 只能经 transition/set_busy 变更")
        unknown = [f for f in fields if f not in _META_FIELDS + _CONTENT_FIELDS]
        if unknown:
            raise ValueError(f"未知字段: {unknown}")
        if "review" in fields and fields["review"] is not None \
                and not isinstance(fields["review"], dict):
            raise ValueError("review 必须是 dict(核对决策 JSON)")
        with self._lock:
            data = self._load()
            rec = self._rec(data, bvid, part)
            for f, v in fields.items():
                rec[f] = copy.deepcopy(v) if isinstance(v, dict) else v
            rec["updated_at"] = _now()
            self._save(data)
            return copy.deepcopy(rec)

    # ---- busy 子标志 ----

    def set_busy(self, bvid, part, flag) -> dict:
        """置/清进行中标志;终态(已交付/已排除)与失败态不允许开 busy。"""
        with self._lock:
            data = self._load()
            rec = self._rec(data, bvid, part)
            if flag and rec["status"] in BUSY_FORBIDDEN:
                raise IllegalTransition(
                    f"{rec['status']} 态不允许置 busy({rec['bvid']} P{rec['part']})")
            if rec["busy"] != bool(flag):
                rec["busy"] = bool(flag)
                rec["updated_at"] = _now()
                self._save(data)
            return copy.deepcopy(rec)

    # ---- 状态转移(唯一入口) ----

    def transition(self, bvid, part, event, **kwargs) -> dict:
        """状态转移唯一入口:非法(状态,事件)组合抛 IllegalTransition。

        事件可携带字段:download_done(media_path)/review_submit(review)/
        confirm_skip(review)/preview_ready(preview_path)/full_done(output_path);
        fail 必填 stage 与 error。携带 review 时与转移同锁原子落账,并按其
        verdict 做门禁:review_submit 要求 verdict=dub,confirm_skip 要求
        verdict=skip(D7:排除需用户确认跳过)。

        转移副作用:review_submit/preview_pass/rediarize 置 busy(后台任务
        自动开工);preview_ready 清小样作废标记;preview_reject/rediarize
        把既有小样标作废(文件保留);rediarize 另清 review 与 dec_hash
        (改人数重跑分人,决策作废回待核对,D9);fail 记 failed_from/
        failed_stage/error 并清 busy;retry 回 failed_from 主态并清失败上下文。
        """
        with self._lock:
            data = self._load()
            rec = self._rec(data, bvid, part)
            src = rec["status"]
            dst = TRANSITIONS.get((src, event))
            if dst is None:
                raise IllegalTransition(f"非法转移: {src} --{event}--> (无此边)")

            unknown = set(kwargs) - _EVENT_FIELDS[event]
            if unknown:
                raise ValueError(f"事件 {event} 不接受参数: {sorted(unknown)}")
            writes = {}
            for f in _EVENT_FIELDS[event]:
                if f in ("stage", "error"):
                    continue    # fail 专用,单独必填校验,不入记录字段
                v = kwargs.get(f)
                if v is None:
                    continue
                if f == "review":
                    if not isinstance(v, dict):
                        raise ValueError("review 必须是 dict(核对决策 JSON)")
                    writes[f] = copy.deepcopy(v)
                elif isinstance(v, str) and v:
                    writes[f] = v
                else:
                    raise ValueError(f"{f} 必须是非空字符串")
            if event == EV_FAIL:
                stage, err = kwargs.get("stage"), kwargs.get("error")
                if not (isinstance(stage, str) and stage
                        and isinstance(err, str) and err):
                    raise ValueError("fail 事件必须带非空 stage 与 error")

            if event == EV_REVIEW_SUBMIT:
                rv = writes.get("review") or rec["review"]
                if not isinstance(rv, dict) or rv.get("verdict") != "dub":
                    raise IllegalTransition(
                        "提交核对要求判定配音(review.verdict=dub);"
                        "全中文跳过走 confirm_skip(D7)")
            elif event == EV_CONFIRM_SKIP:
                rv = writes.get("review") or rec["review"]
                if not isinstance(rv, dict) or rv.get("verdict") != "skip":
                    raise IllegalTransition(
                        "排除需用户确认跳过(review.verdict=skip,见 D7)")

            if dst == "*":  # 失败断点重试:回 failed_from 主态
                back = rec.get("failed_from")
                if back not in MAIN_STATES:
                    raise IllegalTransition("失败记录缺少有效 failed_from,无法重试")
                dst = back

            rec.update(writes)
            rec["status"] = dst
            if event == EV_FAIL:
                rec["failed_from"] = src
                rec["failed_stage"] = stage
                rec["error"] = err
            else:
                rec["failed_from"] = rec["failed_stage"] = rec["error"] = None
            rec["busy"] = False
            if event in (EV_REVIEW_SUBMIT, EV_PREVIEW_PASS, EV_REDIARIZE):
                rec["busy"] = True
            if event == EV_PREVIEW_READY:
                rec["preview_stale"] = False
            elif event == EV_PREVIEW_REJECT and rec["preview_path"]:
                rec["preview_stale"] = True
            elif event == EV_REDIARIZE:
                rec["review"] = None
                rec["dec_hash"] = None
                if rec["preview_path"]:
                    rec["preview_stale"] = True
            rec["updated_at"] = _now()
            self._save(data)
            return copy.deepcopy(rec)
