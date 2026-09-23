# -*- coding: utf-8 -*-
"""stage D【核心自研】: 时间窗归人。

算法(01 §4.1): 每条字幕 × 每个说话人段求重叠时长 → 重叠最大者获胜;
置信度 = 获胜重叠 / 字幕时长;conf < 0.6 进 review.csv。
产物: tagged.srt / per-speaker.md / review.csv
"""
import csv
from pathlib import Path

from .common import SubLine, parse_rttm, parse_srt, write_srt

REVIEW_CONF = 0.6


def overlap(a0, a1, b0, b1) -> float:
    return max(0.0, min(a1, b1) - max(a0, b0))


def attribute_line(t0, t1, spksegs):
    """返回 (winner, conf, 次优, 次优重叠)。spksegs 已合并相邻同说话人段。"""
    per = {}
    for s in spksegs:
        per[s.spk] = per.get(s.spk, 0.0) + overlap(t0, t1, s.t0, s.t1)
    if not per:
        return None, 0.0, None, 0.0
    ranked = sorted(per.items(), key=lambda kv: kv[1], reverse=True)
    dur = max(1e-6, t1 - t0)
    winner, wov = ranked[0]
    runner, rov = (ranked[1] if len(ranked) > 1 else (None, 0.0))
    return winner, wov / dur, runner, rov / max(1e-6, dur)


def attribute(subs: list, spksegs: list, review_conf=REVIEW_CONF) -> list:
    """subs: [SubLine];spksegs: [SpkSeg](会先做相邻合并)。
    返回 [dict(idx,t0,t1,text,spk,conf,runner,runner_conf,review)] 按原顺序。"""
    from .common import merge_adjacent
    segs = merge_adjacent(spksegs)
    out = []
    for s in subs:
        winner, conf, runner, rconf = attribute_line(s.t0, s.t1, segs)
        if winner is None:
            # 无任何重叠:归给时间上最近的段(低置信度,必然进 review)
            winner, conf = _nearest(spksegs, s.t0, s.t1), 0.0
            runner, rconf = None, 0.0
        out.append({
            "idx": s.idx, "t0": s.t0, "t1": s.t1, "text": s.text,
            "spk": winner, "conf": round(conf, 4),
            "runner": runner, "runner_conf": round(rconf, 4),
            "review": conf < review_conf,
        })
    return out


def _nearest(segs, t0, t1):
    if not segs:
        return "SPK_00"
    best, bd = None, float("inf")
    for s in sorted(segs, key=lambda x: x.t0):
        d = min(abs(t0 - s.t1), abs(s.t0 - t1), abs((t0 + t1) / 2 - (s.t0 + s.t1) / 2))
        if d < bd:
            best, bd = s.spk, d
    return best


# ---------------- 产物写出 ----------------

def _mmss(t: float) -> str:
    m, s = divmod(int(t), 60)
    return f"{m:02d}:{s:02d}"


def write_outputs(rows: list, wdir: Path):
    wdir.mkdir(parents=True, exist_ok=True)

    tagged = [SubLine(idx=r["idx"], t0=r["t0"], t1=r["t1"],
                      text=f"[{r['spk']}] {r['text']}") for r in rows]
    write_srt(wdir / "tagged.srt", tagged)

    speakers = sorted({r["spk"] for r in rows})
    md = ["# 按说话人分稿", ""]
    for spk in speakers:
        lines = [r for r in rows if r["spk"] == spk]
        md.append(f"## {spk} ({len(lines)} 句)")
        md.append("")
        md.extend(f"- `{_mmss(r['t0'])}` {r['text']}" for r in lines)
        md.append("")
    (wdir / "per-speaker.md").write_text("\n".join(md), encoding="utf-8")

    with open(wdir / "review.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["idx", "t0", "t1", "text", "winner", "conf",
                    "runner_up", "runner_conf"])
        for r in rows:
            if r["review"]:
                w.writerow([r["idx"], f"{r['t0']:.2f}", f"{r['t1']:.2f}",
                            r["text"], r["spk"], r["conf"],
                            r["runner"] or "", r["runner_conf"]])


def run(video, work_dir="work", srt=None, rttm=None, force=False) -> dict:
    vid = Path(video).stem
    wdir = Path(work_dir) / vid
    tagged = wdir / "tagged.srt"
    if tagged.exists() and not force:
        return {"tagged": str(tagged), "skipped": True}
    subs = parse_srt(srt or wdir / "ocr.srt")
    segs = parse_rttm(rttm or wdir / "diar.rttm")
    rows = attribute(subs, segs)
    write_outputs(rows, wdir)
    n_review = sum(1 for r in rows if r["review"])
    stats = {"lines": len(rows), "review": n_review,
             "review_ratio": round(n_review / max(1, len(rows)), 3),
             "conf_mean": round(sum(r["conf"] for r in rows) / max(1, len(rows)), 3),
             "speakers": sorted({r["spk"] for r in rows})}
    import json
    (wdir / "attribute_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[attr] {stats}")
    return {"tagged": str(tagged), "review_csv": str(wdir / "review.csv"), **stats}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    a = ap.parse_args()
    print(run(a.video))
