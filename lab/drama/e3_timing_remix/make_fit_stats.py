# -*- coding: utf-8 -*-
"""E3 步骤5:fit_stats.md 生成(机器指标;R3 听感拐点单独补)。
run(任意 python): python make_fit_stats.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rows = json.loads((HERE / "e3_synth_result.json").read_text(encoding="utf-8"))
    lines = ["# E3 时长三段式 · fit_stats (机器指标)\n",
             f"句子数: " + ", ".join(
                 f"{d} {sum(1 for r in rows if r['dir']==d)}"
                 for d in ("en2zh", "zh2en")),
             "口径: dur = 静音裁剪后语音跨度; 进窗 = |dur−win|/win ≤ 10%; "
             "C 臂(atempo)按构造必进窗,只报变速幅度分布。\n"]
    for d in ("en2zh", "zh2en"):
        rs = [r for r in rows if r["dir"] == d]
        if not rs:
            continue
        n = len(rs)
        lines.append(f"## {d} (n={n})\n")
        lines.append("| 臂 | 进窗 | 超窗 | 中位 |Δ|/win |")
        lines.append("|---|---|---|---|")
        for k in ("A", "B"):
            fit = sum(r[f"fit_{k}"] for r in rs)
            over = sum(1 for r in rs if r[f"dur_{k}"] > r["win_dur"] * 1.10)
            devs = sorted(abs(r[f"dur_{k}"] - r["win_dur"]) / r["win_dur"]
                          for r in rs)
            med = devs[n // 2] if n % 2 else (devs[n//2-1]+devs[n//2])/2
            lines.append(f"| {'A 自由译' if k=='A' else 'B 预算+speed'} | "
                         f"{fit}/{n} ({100*fit/n:.0f}%) | {over} | {med:.0%} |")
        at = sorted(r["atempo_C"] for r in rs)
        spd = [r["speed_used"] for r in rs if r["speed_used"] != 1.0]
        rew = sum(1 for r in rs if r["events"])
        cand_swap = sum(1 for r in rs
                        if any("try2" in e for e in r["events"]))
        lines.append(f"- B 回炉(任一事件): {rew}/{n}; 换候选: {cand_swap}/{n}; "
                     f"speed≠1: {len(spd)}/{n}"
                     f" (>1.15: {sum(1 for s in spd if s>1.15)}, "
                     f"范围 {min(spd):.2f}~{max(spd):.2f})" if spd else
                     f"- B 回炉: {rew}/{n}; speed≠1: 0")
        lines.append(f"- C atempo: 中位 {at[n//2]:.2f}, 均值 "
                     f"{sum(at)/n:.2f}; >1.15 {sum(1 for a in at if a>1.15)}/{n}, "
                     f">1.3 {sum(1 for a in at if a>1.3)}/{n}, "
                     f">1.5 {sum(1 for a in at if a>1.5)}/{n}, "
                     f"<0.87(拖慢) {sum(1 for a in at if a<0.87)}/{n}\n")
    lines.append("## 逐句明细\n")
    lines.append("| id | 向 | win | A dur | A | B dur | B | speed | atempo |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        lines.append(
            f"| {r['id']} | {r['dir']} | {r['win_dur']} | {r['dur_A']} | "
            f"{'✓' if r['fit_A'] else '✗'} | {r['dur_B']} | "
            f"{'✓' if r['fit_B'] else '✗'} | {r['speed_used']} | "
            f"{r['atempo_C']} |")
    (HERE / "fit_stats.md").write_text("\n".join(lines) + "\n",
                                       encoding="utf-8")
    print("fit_stats.md written,", len(rows), "rows")


if __name__ == "__main__":
    main()
