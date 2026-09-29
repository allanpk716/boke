# -*- coding: utf-8 -*-
"""E3 步骤5:fit_stats.md 生成(机器指标;R3 听感拐点单独补)。
run(任意 python): python make_fit_stats.py
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def stat_block(rows, d):
    rs = [r for r in rows if r["dir"] == d]
    if not rs:
        return None
    n = len(rs)
    out = {"n": n}
    for k in ("A", "B"):
        out[f"fit_{k}"] = sum(r[f"fit_{k}"] for r in rs)
        out[f"over_{k}"] = sum(1 for r in rs
                               if r[f"dur_{k}"] > r["win_dur"] * 1.10)
        devs = sorted(abs(r[f"dur_{k}"] - r["win_dur"]) / r["win_dur"]
                      for r in rs)
        out[f"meddev_{k}"] = devs[n // 2] if n % 2 else (devs[n//2-1]+devs[n//2])/2
    out["rework"] = sum(1 for r in rs if r["events"])
    out["cand_swap"] = sum(1 for r in rs if any("try2" in e for e in r["events"]))
    spd = [r["speed_used"] for r in rs if r["speed_used"] != 1.0]
    out["spd_n"] = len(spd)
    out["spd_gt115"] = sum(1 for s in spd if s > 1.15)
    at = sorted(r["atempo_C"] for r in rs)
    out["at_med"] = at[n // 2]
    out["at_mean"] = sum(at) / n
    for th in (1.15, 1.3, 1.5):
        out[f"at_gt{th}"] = sum(1 for a in at if a > th)
    out["at_slow"] = sum(1 for a in at if a < 0.87)
    for k in ("A", "B"):
        out[f"repeat_{k}"] = sum(1 for r in rs
                                 if r.get(f"bursts_{k}", 1) > 1)
    return out


def fmt_block(name, s):
    L = [f"### {name}\n"]
    L.append("| 臂 | 进窗 | 超窗 | 中位|Δ|/win | 复发段>1 |")
    L.append("|---|---|---|---|---|")
    for k, lab in (("A", "A 自由译"), ("B", "B 预算+speed")):
        L.append(f"| {lab} | {s[f'fit_{k}']}/{s['n']} "
                 f"({100*s[f'fit_{k}']/s['n']:.0f}%) | {s[f'over_{k}']} | "
                 f"{s[f'meddev_{k}']:.0%} | {s[f'repeat_{k}']} |")
    L.append(f"- B 回炉: {s['rework']}/{s['n']}; 换候选: {s['cand_swap']}; "
             f"speed≠1: {s['spd_n']} (其中>1.15: {s['spd_gt115']})")
    L.append(f"- C atempo: 中位 {s['at_med']:.2f}/均值 {s['at_mean']:.2f}; "
             f">1.15 {s['at_gt1.15']}/{s['n']}, >1.3 {s['at_gt1.3']}, "
             f">1.5 {s['at_gt1.5']}, <0.87 拖慢 {s['at_slow']}\n")
    return L


def detail(rows, title):
    L = [f"## 逐句明细 · {title}\n",
         "| id | 向 | win | A dur | A | burstA | B dur | B | burstB | speed | atempo |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(
            f"| {r['id']} | {r['dir']} | {r['win_dur']} | {r['dur_A']} | "
            f"{'✓' if r['fit_A'] else '✗'} | {r.get('bursts_A','')} | "
            f"{r['dur_B']} | {'✓' if r['fit_B'] else '✗'} | "
            f"{r.get('bursts_B','')} | {r['speed_used']} | {r['atempo_C']} |")
    return L + [""]


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rows = json.loads((HERE / "e3_synth_result.json").read_text(encoding="utf-8"))
    lines = ["# E3 时长三段式 · fit_stats (机器指标)\n",
             f"句子数: " + ", ".join(
                 f"{d} {sum(1 for r in rows if r['dir']==d)}"
                 for d in ("en2zh", "zh2en")),
             "口径: dur = 静音裁剪后语音跨度; 进窗 = |dur−win|/win ≤ 10%; "
             "burst = 响段计数(被>0.6s静音隔开),>1 提示复读/多次起声; "
             "C 臂(atempo)按构造必进窗,只报变速幅度分布。",
             "配置1 = 原生参考(en2zh→博主zh净25s; zh2en→Shaman en脏stem 6.5s)。\n"]
    for d in ("en2zh", "zh2en"):
        s = stat_block(rows, d)
        if s:
            lines.append(f"## 配置1 · {d}")
            lines.extend(fmt_block(f"{d} (n={s['n']})", s))
    lines.extend(detail(rows, "配置1"))
    fx = HERE / "e3_synth_result_x.json"
    if fx.is_file():
        rows_x = json.loads(fx.read_text(encoding="utf-8"))
        lines.append("\n---\n配置2 = zh4all(两方向都用博主 zh 参考跨语言合成)\n")
        for d in ("en2zh", "zh2en"):
            s = stat_block(rows_x, d)
            if s:
                lines.append(f"## 配置2 · {d}")
                lines.extend(fmt_block(f"{d} (n={s['n']})", s))
        lines.extend(detail(rows_x, "配置2"))
    (HERE / "fit_stats.md").write_text("\n".join(lines) + "\n",
                                       encoding="utf-8")
    print("fit_stats.md written,", len(rows), "rows")


if __name__ == "__main__":
    main()
