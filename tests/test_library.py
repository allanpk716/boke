# -*- coding: utf-8 -*-
"""片库账本与状态机单测(01 号票):合法/非法转移全枚举、busy 语义、
失败记录与断点重试、改人数重跑作废、并发写不丢。账本一律注入 tmp_path。"""
import json
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boke.library import (  # noqa: E402
    EVENTS, TRANSITIONS, MAIN_STATES,
    ST_PENDING_DOWNLOAD, ST_PENDING_ANALYSIS, ST_PENDING_REVIEW, ST_REVIEWED,
    ST_PREVIEW, ST_SYNTHESIZING, ST_DELIVERED, ST_EXCLUDED, ST_FAILED,
    Library, LibraryError, RecordNotFound, DuplicateRecord, IllegalTransition,
)


# ---------- 夹具与驱动 ----------

_DUB = {"verdict": "dub", "speakers": [{"id": "SPK_00", "voice": "clone"}]}
_SKIP = {"verdict": "skip", "speakers": []}

_REVIEW = ["download_done", "analysis_done"]
_SUBMIT = _REVIEW + [("review_submit", {"review": dict(_DUB)})]

# 从新建记录到各状态的合法驱动路径(steps: 事件名 或 (事件名, kwargs))
_PATHS = {
    ST_PENDING_DOWNLOAD: [],
    ST_PENDING_ANALYSIS: ["download_done"],
    ST_PENDING_REVIEW: _REVIEW,
    ST_REVIEWED: _SUBMIT,
    ST_PREVIEW: _SUBMIT + [("preview_ready", {"preview_path": "p.m4a"})],
    ST_SYNTHESIZING: _SUBMIT + [("preview_ready", {"preview_path": "p.m4a"}),
                                "preview_pass"],
    ST_DELIVERED: _SUBMIT + [("preview_ready", {"preview_path": "p.m4a"}),
                             "preview_pass", "full_done"],
    ST_EXCLUDED: _REVIEW + [("confirm_skip", {"review": dict(_SKIP)})],
    ST_FAILED: ["download_done", ("fail", {"stage": "分析", "error": "ocr 崩了"})],
}

# 测试侧锁定的转移表(与 spec M3 逐边对应;"*"=回 failed_from)
EXPECTED_TRANSITIONS = {
    (ST_PENDING_DOWNLOAD, "download_done"): ST_PENDING_ANALYSIS,
    (ST_PENDING_DOWNLOAD, "fail"): ST_FAILED,
    (ST_PENDING_ANALYSIS, "analysis_done"): ST_PENDING_REVIEW,
    (ST_PENDING_ANALYSIS, "fail"): ST_FAILED,
    (ST_PENDING_REVIEW, "review_submit"): ST_REVIEWED,
    (ST_PENDING_REVIEW, "confirm_skip"): ST_EXCLUDED,
    (ST_PENDING_REVIEW, "rediarize"): ST_PENDING_REVIEW,
    (ST_REVIEWED, "preview_ready"): ST_PREVIEW,
    (ST_REVIEWED, "fail"): ST_FAILED,
    (ST_PREVIEW, "preview_pass"): ST_SYNTHESIZING,
    (ST_PREVIEW, "preview_reject"): ST_PENDING_REVIEW,
    (ST_SYNTHESIZING, "full_done"): ST_DELIVERED,
    (ST_SYNTHESIZING, "fail"): ST_FAILED,
    (ST_FAILED, "retry"): "*",
}

# 各事件合法触发时需要的 kwargs(fail 必填项单测另盖)
_EDGE_KW = {
    "fail": {"stage": "测试阶段", "error": "boom"},
    "review_submit": {"review": dict(_DUB)},
    "confirm_skip": {"review": dict(_SKIP)},
    "preview_ready": {"preview_path": "p.m4a"},
}


def _lib(tmp_path):
    return Library(tmp_path / "library.json")


def _drive_to(lib, status, bvid="BV1test", part=1):
    """新建记录并沿合法路径驱动到指定状态。"""
    lib.add(bvid, part, title=f"{bvid} P{part}", date="2026-09-01", duration=600.0)
    for step in _PATHS[status]:
        if isinstance(step, tuple):
            lib.transition(bvid, part, step[0], **step[1])
        else:
            lib.transition(bvid, part, step)
    return bvid, part


# ---------- 转移表 ----------

def test_transition_table_matches_spec():
    assert TRANSITIONS == EXPECTED_TRANSITIONS


def test_event_set_consistent():
    assert {e for (_, e) in TRANSITIONS} == set(EVENTS)
    assert len(MAIN_STATES) == 7
    assert set(MAIN_STATES) | {ST_EXCLUDED, ST_FAILED} == set(_PATHS)


@pytest.mark.parametrize("src,ev,dst",
                         sorted((s, e, d) for (s, e), d in TRANSITIONS.items()
                                if e != "retry"))
def test_every_legal_edge(tmp_path, src, ev, dst):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, src, f"BV1edge{abs(hash((src, ev))) % 10000:04d}")
    rec = lib.transition(bv, p, ev, **_EDGE_KW.get(ev, {}))
    assert rec["status"] == dst


def test_retry_restores_failed_from(tmp_path):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, ST_FAILED, "BV1retry")
    rec = lib.transition(bv, p, "retry")
    assert rec["status"] == ST_PENDING_ANALYSIS          # 回失败前主态
    assert rec["failed_from"] is None                     # 失败上下文清空
    assert rec["failed_stage"] is None and rec["error"] is None
    assert rec["busy"] is False
    with pytest.raises(IllegalTransition):                # 已不在失败态,不可再 retry
        lib.transition(bv, p, "retry")


# ---------- 非法转移 ----------

def test_every_illegal_pair_rejected(tmp_path):
    lib = _lib(tmp_path)
    for i, src in enumerate(_PATHS):
        bv = f"BV1s{i:02d}"
        _drive_to(lib, src, bv)
        legal = {e for (s, e) in TRANSITIONS if s == src}
        for ev in EVENTS:
            if ev in legal:
                continue
            with pytest.raises(IllegalTransition):
                lib.transition(bv, 1, ev, **_EDGE_KW.get(ev, {}))
        assert lib.get(bv, 1)["status"] == src            # 非法调用不动状态


def test_unknown_event_and_missing_record(tmp_path):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, ST_PENDING_DOWNLOAD, "BV1u")
    with pytest.raises(IllegalTransition):
        lib.transition(bv, p, "bogus_event")
    with pytest.raises(ValueError):
        lib.transition(bv, p, "download_done", bogus=1)   # 未知参数
    with pytest.raises(RecordNotFound):
        lib.transition("BV1none", 1, "download_done")
    with pytest.raises(RecordNotFound):
        lib.set_busy("BV1none", 1, True)
    with pytest.raises(RecordNotFound):
        lib.update("BV1none", 1, title="t")


# ---------- 核对提交的 verdict 门(D7/D13 关联) ----------

def test_submit_and_skip_guards(tmp_path):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, ST_PENDING_REVIEW, "BV1g")
    with pytest.raises(IllegalTransition):
        lib.transition(bv, p, "review_submit")             # 无决策
    with pytest.raises(IllegalTransition):
        lib.transition(bv, p, "review_submit", review=dict(_SKIP))  # 判跳过不能进合成
    with pytest.raises(IllegalTransition):
        lib.transition(bv, p, "confirm_skip", review=dict(_DUB))    # 判配音不能排除
    lib.update(bv, p, review=dict(_DUB))                   # 两步式:先存决策再提交
    lib.transition(bv, p, "review_submit")
    assert lib.get(bv, p)["status"] == ST_REVIEWED


# ---------- busy 语义 ----------

def test_busy_flag_semantics(tmp_path):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, ST_PENDING_DOWNLOAD, "BV1b")
    assert lib.get(bv, p)["busy"] is False
    lib.set_busy(bv, p, True)
    assert lib.get(bv, p)["busy"] is True
    lib.set_busy(bv, p, True)                              # 幂等
    lib.set_busy(bv, p, False)
    lib.set_busy(bv, p, False)                             # 幂等
    assert lib.get(bv, p)["busy"] is False
    assert lib.query(status=ST_PENDING_DOWNLOAD, busy=False)[0]["bvid"] == bv

    # 转移自带的 busy 置位/清除
    lib.transition(bv, p, "download_done")                 # 完成边清 busy
    assert lib.get(bv, p)["busy"] is False
    lib.set_busy(bv, p, True)                              # 模拟分析中
    lib.transition(bv, p, "analysis_done")
    assert lib.get(bv, p)["busy"] is False
    lib.transition(bv, p, "review_submit", review=dict(_DUB))
    assert lib.get(bv, p)["busy"] is True                  # 小样合成自动开始
    lib.transition(bv, p, "preview_ready")
    assert lib.get(bv, p)["busy"] is False
    lib.transition(bv, p, "preview_pass")
    assert lib.get(bv, p)["busy"] is True                  # 全片合成自动开始
    lib.transition(bv, p, "full_done")
    assert lib.get(bv, p)["busy"] is False


def test_busy_forbidden_in_terminal_states(tmp_path):
    lib = _lib(tmp_path)
    for i, state in enumerate((ST_DELIVERED, ST_EXCLUDED, ST_FAILED)):
        bv = f"BV1t{i}"
        _drive_to(lib, state, bv)
        with pytest.raises(IllegalTransition):
            lib.set_busy(bv, 1, True)
        lib.set_busy(bv, 1, False)                         # 清除永远允许
        assert lib.get(bv, 1)["busy"] is False


# ---------- 失败记录与断点重试(F6) ----------

@pytest.mark.parametrize("src", [ST_PENDING_DOWNLOAD, ST_PENDING_ANALYSIS,
                                 ST_REVIEWED, ST_SYNTHESIZING])
def test_failure_records_context_then_retry(tmp_path, src):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, src, f"BV1f{src}")
    lib.set_busy(bv, p, True)
    rec = lib.transition(bv, p, "fail", stage="小样生成", error="GPU OOM")
    assert rec["status"] == ST_FAILED
    assert rec["failed_from"] == src
    assert rec["failed_stage"] == "小样生成"
    assert rec["error"] == "GPU OOM"
    assert rec["busy"] is False
    rec = lib.transition(bv, p, "retry")
    assert rec["status"] == src                            # 断点重入原主态
    assert rec["failed_from"] is None and rec["failed_stage"] is None
    assert rec["error"] is None and rec["busy"] is False


def test_fail_requires_stage_and_error(tmp_path):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, ST_PENDING_ANALYSIS, "BV1miss")
    with pytest.raises(ValueError):
        lib.transition(bv, p, "fail", error="e")
    with pytest.raises(ValueError):
        lib.transition(bv, p, "fail", stage="", error="e")
    with pytest.raises(ValueError):
        lib.transition(bv, p, "fail", stage="s")


def test_retry_without_failed_from_rejected(tmp_path):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, ST_FAILED, "BV1bad")
    path = tmp_path / "library.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["records"][f"{bv}|{p}"]["failed_from"] = None
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(IllegalTransition):
        lib.transition(bv, p, "retry")


# ---------- 小样作废标记与回退边(F7)、改人数重跑作废(D9) ----------

def test_preview_stale_lifecycle(tmp_path):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, ST_PENDING_REVIEW, "BV1p")
    lib.transition(bv, p, "review_submit", review=dict(_DUB))
    lib.transition(bv, p, "preview_ready", preview_path="v1.m4a")
    rec = lib.get(bv, p)
    assert rec["preview_path"] == "v1.m4a" and rec["preview_stale"] is False
    lib.transition(bv, p, "preview_reject")                # 回退边:不过
    rec = lib.get(bv, p)
    assert rec["status"] == ST_PENDING_REVIEW
    assert rec["preview_stale"] is True                    # 旧小样标作废(保留可回听)
    assert rec["review"]["verdict"] == "dub"               # 决策保留可改
    lib.transition(bv, p, "review_submit", review=dict(_DUB))
    lib.transition(bv, p, "preview_ready", preview_path="v2.m4a")
    rec = lib.get(bv, p)
    assert rec["preview_path"] == "v2.m4a" and rec["preview_stale"] is False


def test_rediarize_invalidates_decisions(tmp_path):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, ST_PENDING_REVIEW, "BV1r")
    lib.update(bv, p, review=dict(_DUB), dec_hash="h1")
    lib.transition(bv, p, "review_submit")
    lib.transition(bv, p, "preview_ready", preview_path="old.m4a")
    lib.transition(bv, p, "preview_reject")
    lib.update(bv, p, review=dict(_DUB), dec_hash="h2")
    rec = lib.transition(bv, p, "rediarize")
    assert rec["status"] == ST_PENDING_REVIEW              # 仍回待核对
    assert rec["review"] is None                           # 说话人级决策作废
    assert rec["dec_hash"] is None                         # 下游产物失效
    assert rec["preview_stale"] is True                    # 小样作废标记保留
    assert rec["busy"] is True                             # 重跑开始
    lib.set_busy(bv, p, False)                             # 重跑完成收尾
    bv2, p2 = _drive_to(lib, ST_PREVIEW, "BV1r2")
    with pytest.raises(IllegalTransition):                 # 只能从待核对触发
        lib.transition(bv2, p2, "rediarize")


# ---------- CRUD / 查询 / 字段封锁 ----------

def test_crud_and_query(tmp_path):
    lib = _lib(tmp_path)
    lib.add("BV1a", 1, title="上", date="2026-09-01", duration=60.0,
            is_charge=True, media_path="m1")
    lib.add("BV1a", 2, title="下", date="2026-09-02")
    with pytest.raises(DuplicateRecord):
        lib.add("BV1a", 1, title="重复")
    assert lib.get("BV1nope", 1) is None

    parts = lib.query(bvid="BV1a")
    assert [r["part"] for r in parts] == [1, 2]
    assert len(lib.query(bvid="BV1a", status=ST_PENDING_DOWNLOAD)) == 2
    r = lib.get("BV1a", 1)
    assert r["is_charge"] is True and r["status"] == ST_PENDING_DOWNLOAD
    assert r["busy"] is False and r["artifacts"] == {}

    lib.update("BV1a", 2, title="下(改)", artifacts={"audio": "a.wav"},
               review=dict(_DUB))
    r = lib.get("BV1a", 2)
    assert r["title"] == "下(改)" and r["artifacts"] == {"audio": "a.wav"}
    assert r["review"] == _DUB and r["duration"] is None

    got = lib.get("BV1a", 1)                               # 返回副本,外部改不动账本
    got["status"] = ST_DELIVERED
    assert lib.get("BV1a", 1)["status"] == ST_PENDING_DOWNLOAD


def test_machine_fields_locked_from_update(tmp_path):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, ST_PENDING_DOWNLOAD, "BV1k")
    for f in ("status", "busy", "failed_from", "failed_stage", "error"):
        with pytest.raises(ValueError):
            lib.update(bv, p, **{f: "x"})
    with pytest.raises(ValueError):
        lib.update(bv, p, no_such_field=1)


# ---------- 单一状态源与原子写 ----------

def test_single_source_across_instances(tmp_path):
    a = Library(tmp_path / "library.json")
    b = Library(tmp_path / "library.json")
    a.add("BV1x", 2, title="P2")
    a.transition("BV1x", 2, "download_done")
    assert b.get("BV1x", 2)["status"] == ST_PENDING_ANALYSIS
    b.transition("BV1x", 2, "analysis_done")
    assert a.get("BV1x", 2)["status"] == ST_PENDING_REVIEW


def test_atomic_write_leaves_no_tmp(tmp_path):
    lib = _lib(tmp_path)
    bv, p = _drive_to(lib, ST_PREVIEW, "BV1w")
    assert [f for f in tmp_path.iterdir() if f.suffix == ".tmp"] == []
    data = json.loads((tmp_path / "library.json").read_text(encoding="utf-8"))
    assert data["records"][f"{bv}|{p}"]["status"] == ST_PREVIEW


def test_corrupt_ledger_raises(tmp_path):
    p = tmp_path / "library.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(LibraryError):
        Library(p).query()


# ---------- 并发写不丢(线程 x100 交错) ----------

def test_concurrent_writes_no_loss(tmp_path):
    lib = _lib(tmp_path)
    lib.add("BV0shared", 1, title="shared")
    n = 100
    errs = []

    def work(i):
        try:
            bv = f"BV1c{i:03d}"
            lib.add(bv, 1, title=f"题{i}", is_charge=(i % 2 == 0))
            lib.transition(bv, 1, "download_done", media_path=f"D:/media/{bv}.mp4")
            lib.set_busy(bv, 1, True)
            lib.set_busy("BV0shared", 1, i % 2 == 0)       # 交错打同一条记录
            lib.set_busy(bv, 1, False)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errs == []
    recs = lib.query()
    assert len(recs) == n + 1
    mine = {r["bvid"]: r for r in recs if r["bvid"].startswith("BV1c")}
    assert len(mine) == n
    for i in range(n):
        r = mine[f"BV1c{i:03d}"]
        assert r["status"] == ST_PENDING_ANALYSIS
        assert r["busy"] is False
        assert r["media_path"] == f"D:/media/BV1c{i:03d}.mp4"
        assert r["is_charge"] == (i % 2 == 0)
    shared = lib.get("BV0shared", 1)
    assert shared["status"] == ST_PENDING_DOWNLOAD          # 没人动它的状态
    assert isinstance(shared["busy"], bool)
