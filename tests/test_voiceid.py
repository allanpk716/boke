# -*- coding: utf-8 -*-
"""voiceid 核心逻辑单测(票02):路由三分支/双门/互斥 compete/冷启动 F6/THR_host/
一致性 F5/FIFO 封顶/校准统计量(p99/余量/MARGIN 下限/跨语言档/解锁双条件/EER)/纯标准库。

全部用合成向量(tmp 内建档案文件),不 import pyannote/torch。
"""
import ast
import json
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boke import voiceid  # noqa: E402
from boke.voiceid import (  # noqa: E402
    VoiceArchive, calibrate, check_consistency, cosine, equal_error_rate,
    identify, load_thresholds, percentile, route, save_thresholds, score,
)

SQ = math.sqrt(0.5)          # cos 45°


def _ang(deg):
    """单位向量 [cos, sin]:对 e1=[1,0] 的 cosine 恰为 cos(deg)。"""
    t = math.radians(deg)
    return [math.cos(t), math.sin(t)]


def _entry(person, lang, vec, ref):
    return {"person": person, "lang": lang, "vec": list(vec), "ref_path": ref}


def _thr(**over):
    """zh 档手造阈值(默认 calibrated=True);over 可覆盖任意字段。"""
    tier = {"calibrated": True, "genuine_n": 99, "impostor_n": 99,
            "cross_impostor_n": 99, "margin_n": 99,
            "thr": 0.80, "margin": 0.10, "thr_host": 0.90, "thr_cross": 0.85,
            "genuine_p5": 0.70, "genuine_p25": 0.75, "eer": 0.01}
    tier.update(over)
    return {"version": 1, "langs": {"zh": tier}}


# 常用档案:圆脸 zh 两条同向,小外 en 一条正交
def _std_entries():
    return [
        _entry("圆脸", "zh", [1, 0], "y1.wav"),
        _entry("圆脸", "zh", [1, 0], "y2.wav"),
        _entry("小外", "en", [0, 1], "x1.wav"),
    ]


# ---------- 基础统计 ----------

def test_cosine_basics():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([3, 0], [1, 0]) == pytest.approx(1.0)   # 未归一输入可用
    assert cosine([0, 0], [1, 0]) == 0.0                  # 零向量不给分
    assert cosine([-1, 0], [1, 0]) == pytest.approx(-1.0)


def test_percentile_linear_interp():
    assert percentile([1, 2], 50) == pytest.approx(1.5)
    assert percentile([1, 2, 3, 4], 25) == pytest.approx(1.75)
    assert percentile([0, 0.5], 99) == pytest.approx(0.495)   # rank 0.99
    assert percentile([7], 99) == 7.0
    assert percentile([], 99) is None


# ---------- 打分 ----------

def test_score_mean_and_per_entry():
    entries = [_entry("甲", "zh", [1, 0], "a.wav"), _entry("甲", "zh", [0, 1], "b.wav")]
    r = score([1, 0], entries)
    assert r["score"] == pytest.approx(0.5)                # (1.0 + 0.0)/2
    assert [p["score"] for p in r["per_entry"]] == pytest.approx([1.0, 0.0])
    assert r["top_ref"] == "a.wav"                          # 最像的条目
    empty = score([1, 0], [])
    assert empty["score"] is None and empty["top_ref"] is None


# ---------- 语言路由三分支 ----------

def test_route_same_lang_pref():
    entries = [_entry("甲", "zh", [1, 0], "z1"), _entry("甲", "zh", [1, 0], "z2"),
               _entry("甲", "en", [0, 1], "e1")]
    used, cross = route([1, 0], "zh", entries)
    assert [e["ref_path"] for e in used] == ["z1", "z2"]    # 只用同语言组
    assert cross is False


def test_route_single_group_cross():
    entries = [_entry("乙", "en", [0, 1], "e1"), _entry("乙", "en", [0, 1], "e2")]
    used, cross = route([1, 0], "zh", entries)              # 乙无 zh 组,仅一组
    assert len(used) == 2 and cross is True
    # spk 语言未知(None)同样走单组跨语言
    used, cross = route([0, 1], None, entries)
    assert len(used) == 2 and cross is True


def test_route_multi_group_max_mean():
    entries = [_entry("丙", "en", [1, 0], "e1"), _entry("丙", "en", [1, 0], "e2"),
               _entry("丙", "ja", [0, 1], "j1")]
    used, cross = route([1, 0], "zh", entries)              # 无 zh 组,两组 → 取最高组均值
    assert [e["ref_path"] for e in used] == ["e1", "e2"] and cross is True
    # 并列时取语言名排序最前(determinism)
    tie = [_entry("丁", "en", [1, 0], "e1"), _entry("丁", "ja", [0, 1], "j1")]
    used, cross = route([1, 1], "zh", tie)                  # 两组均值同为 0.7071
    assert used[0]["ref_path"] == "e1" and cross is True


# ---------- identify:双门 / 互斥 / 冷启动 / 主持人 ----------

def test_identify_suggest_and_structure():
    res = identify([{"spk": "SPK_00", "vec": [1, 0], "lang": "zh"}],
                   _std_entries(), _thr(), host_person="圆脸")
    row = res["results"][0]
    assert set(row.keys()) == {"spk", "person", "score", "margin", "verdict",
                               "calibrated", "top_ref", "host_candidate"}
    assert row["spk"] == "SPK_00" and row["person"] == "圆脸"
    assert row["score"] == pytest.approx(1.0)
    assert row["margin"] == pytest.approx(1.0)              # 1.0 − 0.0(小外跨语言)
    assert row["verdict"] == "suggest" and row["calibrated"] is True
    assert row["top_ref"] == "y1.wav"
    assert row["host_candidate"] is True                    # 1.0 ≥ thr_host 0.90
    assert res["host_candidates"] == ["SPK_00"]


def test_identify_thr_gate_rejects():
    # score 0.85 < THR 0.90,但 margin 0.323 ≥ 0.10 → 只栽分数门
    t = _thr(thr=0.90, margin=0.10)
    res = identify([{"spk": "SPK_00", "vec": _ang(31.788), "lang": "zh"}],
                   _std_entries(), t, host_person="圆脸")
    row = res["results"][0]
    assert row["verdict"] == "unknown"
    assert row["person"] == "圆脸" and row["score"] == pytest.approx(0.85, abs=1e-3)
    assert res["host_candidates"] == []


def test_identify_margin_gate_rejects():
    # 3D:score 0.8 ≥ THR 0.5,但 top1−top2 = 0.2 < MARGIN 0.5 → 只栽余量门
    t = _thr(thr=0.50, margin=0.50)
    entries = [_entry("圆脸", "zh", [1, 0, 0], "y1"),
               _entry("小外", "en", [0, 1, 0], "x1")]
    res = identify([{"spk": "SPK_00", "vec": [0.8, 0.6, 0], "lang": "zh"}],
                   entries, t, host_person="圆脸")
    row = res["results"][0]
    assert row["verdict"] == "unknown"
    assert row["score"] == pytest.approx(0.8) and row["margin"] == pytest.approx(0.2)
    assert row["host_candidate"] is False


def test_identify_mutex_greedy_and_compete():
    res = identify([
        {"spk": "SPK_00", "vec": [1, 0], "lang": "zh"},          # 圆脸 1.0
        {"spk": "SPK_01", "vec": _ang(6), "lang": "zh"},         # 圆脸 0.9945
        {"spk": "SPK_02", "vec": _ang(41.4), "lang": "zh"},      # 圆脸 0.75 < THR → 栽门
    ], _std_entries(), _thr(), host_person="圆脸")
    by = {r["spk"]: r for r in res["results"]}
    assert by["SPK_00"]["verdict"] == "suggest"             # 最高分独占圆脸
    assert by["SPK_01"]["verdict"] == "compete"             # 双门都过但落选 → compete
    assert by["SPK_01"]["calibrated"] is True
    assert by["SPK_02"]["verdict"] == "unknown"             # 栽门的落选者不是 compete
    assert res["host_candidates"] == ["SPK_00"]


def test_identify_uncalibrated_suppresses_prefill():
    """F6:冷启动显示开、预填关——suggest 压成 uncalibrated,compete 保留。"""
    t = _thr(calibrated=False)
    res = identify([
        {"spk": "SPK_00", "vec": [1, 0], "lang": "zh"},
        {"spk": "SPK_01", "vec": _ang(6), "lang": "zh"},
    ], _std_entries(), t, host_person="圆脸")
    by = {r["spk"]: r for r in res["results"]}
    assert by["SPK_00"]["verdict"] == "uncalibrated"
    assert by["SPK_00"]["calibrated"] is False
    assert by["SPK_00"]["host_candidate"] is True           # 候选显示保留,预填由调用方结合 calibrated 关
    assert by["SPK_01"]["verdict"] == "compete"             # compete+uncalibrated 并存
    assert by["SPK_01"]["calibrated"] is False


def test_identify_cross_tier_threshold():
    """无同语言组走跨语言档:比对用 thr_cross,不用同语言档 thr。"""
    entries = [_entry("圆脸", "zh", [1, 0], "y1"),
               _entry("小外", "en", [0, 1], "x1")]
    spk = {"spk": "SPK_00", "vec": _ang(64.16), "lang": "zh"}    # vs 小外 0.9,vs 圆脸 0.436
    row = identify([spk], entries, _thr(thr=0.50, thr_cross=0.92))["results"][0]
    assert row["person"] == "小外" and row["score"] == pytest.approx(0.9, abs=1e-3)
    assert row["verdict"] == "unknown"                      # 0.9 < thr_cross 0.92(同语言档 0.5 会误放行)
    row = identify([spk], entries, _thr(thr=0.50, thr_cross=0.85))["results"][0]
    assert row["verdict"] == "suggest" and row["person"] == "小外"


def test_identify_no_thresholds_show_only():
    """阈值缺失(语言档不存在/传 None):只显示分数,不 suggest。"""
    for t in (None, {"version": 1, "langs": {}}):
        res = identify([{"spk": "SPK_00", "vec": [1, 0], "lang": "zh"}],
                       _std_entries(), t, host_person="圆脸")
        row = res["results"][0]
        assert row["verdict"] == "unknown" and row["calibrated"] is False
        assert row["person"] == "圆脸" and row["score"] == pytest.approx(1.0)


def test_identify_single_candidate_margin_none():
    """仅一个人物有档案:无第二名,余量门视为通过,margin=None;含边界相等。"""
    entries = [_entry("圆脸", "zh", [1, 0], "y1")]
    t = _thr(thr=1.0, thr_host=1.0)
    row = identify([{"spk": "SPK_00", "vec": [3, 0], "lang": "zh"}],   # cosine 恰 1.0
                   entries, t, host_person="圆脸")["results"][0]
    assert row["verdict"] == "suggest"                      # score==thr、host==thr_host 均含边界
    assert row["margin"] is None and row["host_candidate"] is True


def test_identify_layer_aggregation():
    """D5/打分层平均:同人物全部条目参与(含合并分身多 SPK 来源),不是取最好一条。"""
    entries = [_entry("圆脸", "zh", [1, 0], "y1"),
               _entry("圆脸", "zh", [0.6, 0.8], "y2")]     # y2 来自历史另一 SPK
    row = identify([{"spk": "SPK_00", "vec": [1, 0], "lang": "zh"}],
                   entries, _thr(thr=0.75, thr_host=1.0))["results"][0]
    assert row["score"] == pytest.approx(0.8)               # (1.0+0.6)/2
    assert row["verdict"] == "suggest"
    assert row["top_ref"] == "y1"


def test_identify_host_gate():
    """host_candidate 恰以 THR_host 为门(过 thr 不等于过 thr_host)。"""
    t = _thr(thr=0.80, thr_host=0.95)
    hi = identify([{"spk": "SPK_00", "vec": [1, 0], "lang": "zh"}],
                  _std_entries(), t, host_person="圆脸")["results"][0]
    lo = identify([{"spk": "SPK_01", "vec": _ang(26), "lang": "zh"}],   # 0.899
                  _std_entries(), t, host_person="圆脸")["results"][0]
    assert hi["host_candidate"] is True
    assert lo["verdict"] == "suggest" and lo["host_candidate"] is False


# ---------- 校准统计量 ----------

def test_calibrate_small_known_pools():
    """甲乙各 2 条(同人间全 1.0,冒认全 0.0):全统计量手算对表。"""
    entries = ([_entry("甲", "zh", [1, 0], f"a{i}") for i in (1, 2)] +
               [_entry("乙", "zh", [0, 1], f"b{i}") for i in (1, 2)])
    t = calibrate(entries)["langs"]["zh"]
    assert t["genuine_n"] == 4 and t["impostor_n"] == 4      # 留一法 4 样本;2×2 冒认对
    assert t["cross_impostor_n"] == 0
    assert t["thr"] == pytest.approx(0.02)                   # p99(0)=0,冒认<20 → max(0, 0+0.02)
    assert t["margin"] == pytest.approx(1.0)                 # p5(同人 top1−top2 全 1.0)
    assert t["genuine_p5"] == pytest.approx(1.0) and t["genuine_p25"] == pytest.approx(1.0)
    assert t["thr_host"] == pytest.approx(1.0)               # max(0.02+0.05, p25=1.0)
    assert t["thr_cross"] == pytest.approx(0.07)             # 跨语言对 0 条 <10 → THR+0.05
    assert t["calibrated"] is False                          # 4<5 且 4<10
    assert t["eer"] == pytest.approx(0.0)                    # 完全可分


def test_calibrate_p99_and_small_sample_bump():
    # 三人各一条:冒认池 = [0, 0.7071, 0.7071] → p99≈0.7071;<20 → +0.02 余量
    entries = [_entry("甲", "zh", [1, 0], "a"), _entry("乙", "zh", [0, 1], "b"),
               _entry("丙", "zh", [1, 1], "c")]
    t = calibrate(entries)["langs"]["zh"]
    c = cosine([1, 0], [1, 1])
    assert t["impostor_n"] == 3
    assert t["thr"] == pytest.approx(c + 0.02)               # p99 = c(前两名并列),bump 抬过最大值


def test_calibrate_bump_boundary_20_and_unlock_all_met():
    """冒认恰 20 对 → 不加余量(THR=0);同人 12 样本+冒认 20 → 解锁。"""
    entries = ([_entry("甲", "zh", [1, 0], f"a{i}") for i in range(2)] +
               [_entry("乙", "zh", [0, 1], f"b{i}") for i in range(10)])
    t = calibrate(entries)["langs"]["zh"]
    assert t["impostor_n"] == 20 and t["genuine_n"] == 12
    assert t["thr"] == pytest.approx(0.0)                    # 恰 20 对:无 bump
    assert t["calibrated"] is True


def test_calibrate_unlock_conditions():
    # A:冒认够(5 个单人人物 C(5,2)=10 对)但同人样本 0(无任何人物≥2 条)→ 不解锁
    vecs = [[1, 0], [0, 1], [1, 1], [1, -1], [-1, 1]]
    entries = [_entry(f"P{i}", "zh", v, f"p{i}") for i, v in enumerate(vecs)]
    t = calibrate(entries)["langs"]["zh"]
    assert t["impostor_n"] == 10 and t["genuine_n"] == 0
    assert t["calibrated"] is False
    # B:同人够(3+3=6)但冒认 3×3=9 <10 → 不解锁
    entries = ([_entry("甲", "zh", [1, 0], f"a{i}") for i in range(3)] +
               [_entry("乙", "zh", [0, 1], f"b{i}") for i in range(3)])
    t = calibrate(entries)["langs"]["zh"]
    assert t["genuine_n"] == 6 and t["impostor_n"] == 9
    assert t["calibrated"] is False


def test_calibrate_cross_language_pools():
    """跨语言冒认对双侧各记一次:zh/en 两档各 impostor_n=4、cross=4,THR 用 bump。"""
    entries = ([_entry("甲", "zh", [1, 0], f"a{i}") for i in (1, 2)] +
               [_entry("乙", "en", [0, 1], f"b{i}") for i in (1, 2)])
    t = calibrate(entries)
    for lang in ("zh", "en"):
        tier = t["langs"][lang]
        assert tier["genuine_n"] == 2                        # 只有本语言那人的留一法
        assert tier["impostor_n"] == 4 and tier["cross_impostor_n"] == 4
        assert tier["thr"] == pytest.approx(0.02)
        assert tier["thr_cross"] == pytest.approx(0.07)      # 4 <10 → fallback
    assert set(t["langs"]) == {"zh", "en"}


def test_calibrate_cross_tier_dedicated_p99():
    """zh 方向跨语言冒认对 ≥10 → 专属分布 p99,而非 THR+0.05 fallback。"""
    entries = ([_entry("甲", "zh", [1, 0], f"a{i}") for i in (1, 2)] +
               [_entry("乙", "en", [0, 1], f"e{i}") for i in (1, 2, 3)] +
               [_entry("乙", "en", [1, 0], f"e{i}") for i in (4, 5)])
    t = calibrate(entries)["langs"]["zh"]
    assert t["cross_impostor_n"] == 10                       # 2×5,值 [0]×6 + [1]×4
    assert t["thr_cross"] == pytest.approx(1.0)              # 专属 p99 = 1.0(fallback 会是 1.02+0.05)
    assert t["thr"] == pytest.approx(1.02)                   # 主池同 10 对,p99=1.0,bump→1.02


def test_calibrate_margin_floor():
    """同人 top1−top2 差极小(≈0.00005)→ MARGIN 落下限 0.03。"""
    near = [1, 0.01]
    entries = ([_entry("甲", "zh", [1, 0], f"a{i}") for i in (1, 2)] +
               [_entry("乙", "zh", near, f"b{i}") for i in (1, 2)])
    t = calibrate(entries)["langs"]["zh"]
    assert t["margin"] == pytest.approx(0.03)


def test_eer_values():
    assert equal_error_rate([1.0, 1.0], [0.0, 0.0]) == pytest.approx(0.0)   # 完全可分
    assert equal_error_rate([1.0], [0.0, 1.0]) == pytest.approx(0.25)       # 半重叠
    assert equal_error_rate([1.0], [1.0]) == pytest.approx(0.5)             # 分布重合
    assert equal_error_rate([], [0.5]) is None
    assert equal_error_rate([0.5], []) is None


def test_thresholds_save_load_roundtrip(tmp_path):
    path = tmp_path / "voiceid_thresholds.json"
    t = calibrate([_entry("甲", "zh", [1, 0], "a1"), _entry("乙", "zh", [0, 1], "b1")])
    save_thresholds(t, path)
    assert load_thresholds(path) == t
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data["langs"], dict) and "zh" in data["langs"]
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert load_thresholds(tmp_path / "none.json") is None


# ---------- 一致性检查(F5/D6) ----------

def test_check_consistency_block_and_pass():
    group = [_entry("圆脸", "zh", [1, 0], "y1"), _entry("圆脸", "zh", [1, 0], "y2")]
    t = _thr(genuine_p5=0.90)
    ok, s, reason = check_consistency([0, 1], "zh", group, t)
    assert ok is False and s == pytest.approx(0.0) and "p5" in reason
    ok, s, _ = check_consistency([1, 0], "zh", group, t)
    assert ok is True and s == pytest.approx(1.0)
    # 恰等于 p5 不拦(拦截条件是"低于";cosine 全整数运算无浮点噪声)
    exact = [_entry("圆脸", "zh", [3, 0], "y1"), _entry("圆脸", "zh", [3, 0], "y2")]
    ok, s, _ = check_consistency([3, 0], "zh", exact, _thr(genuine_p5=1.0))
    assert ok is True and s == pytest.approx(1.0)


def test_check_consistency_skip_lt2_and_no_p5():
    one = [_entry("圆脸", "zh", [1, 0], "y1")]
    ok, s, reason = check_consistency([0, 1], "zh", one, _thr())
    assert ok is True and s is None and reason                # <2 条跳过(F5)
    ok, *_ = check_consistency([0, 1], "zh", [], _thr())
    assert ok is True
    # 组内 ≥2 但该语言无同人分布(无 genuine_p5)→ 无法判定,放行
    two = one + [_entry("圆脸", "zh", [1, 0], "y2")]
    t = {"version": 1, "langs": {"zh": {"genuine_p5": None}}}
    ok, s, reason = check_consistency([0, 1], "zh", two, t)
    assert ok is True and "p5" in reason


# ---------- 档案读写(persons_emb.json) ----------

def _archive(tmp_path):
    return VoiceArchive(path=tmp_path / "work" / "persons_emb.json")


def test_archive_add_persist_filter(tmp_path):
    arc = _archive(tmp_path)
    e1 = arc.add_entry("圆脸", "zh", [1, 0], "refs/a.wav", bvid="BV1", part=2,
                       spk="SPK_00", ts="2026-09-25T07:00:00", model="wespeaker")
    assert set(e1) == {"person", "lang", "vec", "ref_path", "bvid", "part",
                       "spk", "ts", "model"}
    arc.add_entry("圆脸", "en", [0, 1], "refs/b.wav", ts="2026-09-25T07:01:00")
    arc.add_entry("小外", "en", [0, 1], "refs/c.wav", ts="2026-09-25T07:02:00")
    # 持久化:重开实例可见
    arc2 = _archive(tmp_path)
    assert len(arc2.entries()) == 3
    assert [e["ref_path"] for e in arc2.entries(person="圆脸", lang="zh")] == ["refs/a.wav"]
    assert len(arc2.entries(person="圆脸")) == 2
    assert len(arc2.entries(lang="en")) == 2
    # 原子写:无 .tmp 残留
    assert not [p for p in (tmp_path / "work").iterdir() if p.suffix == ".tmp"]
    # ts 缺省自动补
    e = arc2.add_entry("圆脸", "zh", [1, 0], "refs/d.wav")
    assert e["ts"]


def test_archive_fifo_cap(tmp_path):
    """每人每语言封顶 10 条 FIFO;不同语言各自计。"""
    arc = _archive(tmp_path)
    for i in range(12):
        arc.add_entry("圆脸", "zh", [1, 0], f"z{i}.wav", ts=f"2026-09-25T07:{i:02d}")
    for i in range(3):
        arc.add_entry("圆脸", "en", [0, 1], f"e{i}.wav", ts=f"2026-09-25T08:{i:02d}")
    arc.add_entry("小外", "zh", [1, 0], "x0.wav", ts="2026-09-25T09:00")
    zh = [e["ref_path"] for e in arc.entries(person="圆脸", lang="zh")]
    assert len(zh) == 10 and zh[0] == "z2.wav" and zh[-1] == "z11.wav"   # 最旧两条被挤掉
    assert len(arc.entries(person="圆脸", lang="en")) == 3               # en 不受 zh 挤占
    assert len(arc.entries(person="小外", lang="zh")) == 1               # 按人分别计


def test_archive_remove_cascade(tmp_path):
    arc = _archive(tmp_path)
    arc.add_entry("圆脸", "zh", [1, 0], "refs/a.wav")
    arc.add_entry("圆脸", "en", [0, 1], "refs/b.wav")
    arc.add_entry("小外", "en", [0, 1], "refs/c.wav")
    assert arc.remove_by_ref_path("refs/b.wav") == 1       # 删 refs 条目 → 级联删向量
    assert arc.remove_by_ref_path("refs/b.wav") == 0
    assert len(arc.entries(person="圆脸")) == 1
    assert arc.remove_person("圆脸") == 1
    assert arc.entries(person="圆脸") == []
    assert len(arc.entries()) == 1                          # 只剩小外


def test_archive_corrupt(tmp_path):
    path = tmp_path / "work" / "persons_emb.json"
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(RuntimeError):
        VoiceArchive(path=path)
    path.write_text(json.dumps({"no_entries": []}), encoding="utf-8")
    with pytest.raises(RuntimeError):
        VoiceArchive(path=path)


# ---------- 纯逻辑纪律 ----------

def test_pure_stdlib_only():
    """不 import pyannote/torch/numpy 等重依赖:AST 层面拦截。"""
    src = (Path(__file__).resolve().parents[1] / "src" / "boke" / "voiceid.py"
           ).read_text(encoding="utf-8")
    mods = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            mods.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module.split(".")[0])
    allowed = {"json", "os", "math", "copy", "threading", "datetime", "pathlib",
               "itertools", "collections", "statistics", "time", "functools"}
    assert mods <= allowed, f"voiceid.py 出现非标准库导入:{mods - allowed}"
    assert voiceid.MAX_ENTRIES_PER_LANG == 10
