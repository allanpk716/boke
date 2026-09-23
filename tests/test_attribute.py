# -*- coding: utf-8 -*-
"""attribute 归人算法单测:构造已知归属假数据验证。任务书验收:≥95%。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boke.attribute import attribute, attribute_line, overlap  # noqa: E402
from boke.common import SpkSeg, SubLine, merge_adjacent, parse_srt, write_srt, \
    parse_rttm, write_rttm  # noqa: E402


# ---------- 纯函数 ----------

def test_overlap():
    assert overlap(0, 10, 5, 20) == 5
    assert overlap(0, 5, 10, 20) == 0
    assert overlap(3, 7, 3, 7) == 4


def test_attribute_line_basic():
    segs = [SpkSeg(0, 10, "SPK_00"), SpkSeg(10, 20, "SPK_01")]
    w, c, r, rc = attribute_line(2, 8, segs)
    assert w == "SPK_00" and c == 1.0 and rc == 0.0


def test_attribute_line_partial():
    segs = [SpkSeg(0, 10, "SPK_00"), SpkSeg(10, 20, "SPK_01")]
    w, c, r, rc = attribute_line(6, 14, segs)  # 各半
    assert 0.45 < c < 0.55 and r is not None and abs(c - rc) < 0.11


def test_merge_adjacent_fragments():
    """01 §4.2:同一说话人被切碎 → 合并后再投票。"""
    segs = [SpkSeg(0, 5, "SPK_00"), SpkSeg(5.1, 8, "SPK_00"),
            SpkSeg(9, 12, "SPK_01"), SpkSeg(12.2, 15, "SPK_00")]
    m = merge_adjacent(segs)
    assert len(m) == 3 and m[0].t1 == 8 and m[1].spk == "SPK_01"


# ---------- 构造数据 ----------

def _make_case(n_lines=200, noise_ratio=0.2, jitter=0.2, seed=42):
    """两人轮流说话,每段 1 条字幕居中。
    noise: 一半"温和跨界"(尾部伸进下段 0.5~1.5s,conf~0.8 仍归对),
           一半"硬骑线"(跨换人点近五五开,conf~0.5 → 应进 review)。"""
    import random
    rnd = random.Random(seed)
    subs, segs, truth = [], [], []
    t = 0.0
    for i in range(n_lines):
        seg_dur = rnd.uniform(4, 10)
        spk = f"SPK_{i % 2:02d}"
        segs.append(SpkSeg(t, t + seg_dur, spk))
        text = f"第{i}句话语内容"
        r = rnd.random()
        if r < noise_ratio / 2:                       # 温和跨界
            t0 = t + jitter
            t1 = t + seg_dur + rnd.uniform(0.5, 1.5)
        elif r < noise_ratio:                          # 硬骑线
            back = min(rnd.uniform(1.5, 3.0), seg_dur * 0.6)
            fwd = rnd.uniform(1.5, 3.0)
            t0 = t + seg_dur - back
            t1 = t + seg_dur + fwd
        else:                                          # 干净
            t0 = t + seg_dur * rnd.uniform(0.1, 0.25)
            t1 = t + seg_dur * rnd.uniform(0.75, 0.9)
        subs.append(SubLine(idx=i + 1, t0=round(t0, 3), t1=round(t1, 3), text=text))
        truth.append(spk)
        t += seg_dur + rnd.uniform(0.2, 1.0)  # 段间隙
    return subs, segs, truth


def test_accuracy_clean():
    subs, segs, truth = _make_case(noise_ratio=0.0)
    rows = attribute(subs, segs)
    acc = sum(r["spk"] == g for r, g in zip(rows, truth)) / len(truth)
    assert acc == 1.0, f"clean case accuracy {acc}"


def test_accuracy_noisy_ge95():
    """任务书验收口径:含 20% 抢话噪声仍 ≥95%。"""
    subs, segs, truth = _make_case(noise_ratio=0.2)
    rows = attribute(subs, segs)
    acc = sum(r["spk"] == g for r, g in zip(rows, truth)) / len(truth)
    assert acc >= 0.95, f"noisy case accuracy {acc}"
    # 横跨句应普遍低置信度
    low_conf = [r for r in rows if r["conf"] < 0.6]
    assert len(low_conf) > 0, "抢话句应进 review"


def test_gap_line_review():
    """字幕落在说话人段间隙 → conf=0 归最近者,必进 review。"""
    subs = [SubLine(1, 100.0, 102.0, "间隙里的一句")]
    segs = [SpkSeg(0, 90, "SPK_00"), SpkSeg(95, 99, "SPK_01")]
    rows = attribute(subs, segs)
    r = rows[0]
    assert r["conf"] == 0.0 and r["review"] and r["spk"] in ("SPK_00", "SPK_01")


# ---------- 产物格式 ----------

def test_srt_rttm_roundtrip(tmp_path):
    subs = [SubLine(1, 1.5, 3.25, "[SPK_00] 你好世界")]
    write_srt(tmp_path / "a.srt", subs)
    back = parse_srt(tmp_path / "a.srt")
    assert back[0].t0 == 1.5 and back[0].t1 == 3.25 and back[0].text == "[SPK_00] 你好世界"

    segs = [SpkSeg(1.2, 5.0, "SPK_00"), SpkSeg(5.4, 9.9, "SPK_01")]
    write_rttm(tmp_path / "a.rttm", segs)
    back = parse_rttm(tmp_path / "a.rttm")
    assert len(back) == 2 and abs(back[0].t0 - 1.2) < 1e-3 and back[1].spk == "SPK_01"
