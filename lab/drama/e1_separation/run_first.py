# -*- coding: utf-8 -*-
"""E1' 分离客观横评·首轮 —— 串起分离 + 客观指标 + 结果落盘。

run in .venv-lab:
    ../../../.venv-lab/Scripts/python run_first.py              # Sintel 30 段（source=blender）：分离 → 指标 → json+md
    ../../../.venv-lab/Scripts/python run_first.py --skip-separate   # 只重算指标（复用已有分离产物）
    ../../../.venv-lab/Scripts/python run_first.py --md-only      # 只从已有 json 重渲染 md
    ../../../.venv-lab/Scripts/python run_first.py --force      # 分离全部重跑; --source all 不过滤

产物:
    media/separated/**            三轨音频（gitignored）
    results/e1/separation_round1.json   全部数字（objective.py 写）
    results/e1/separation_round1.md     汇总报告（本脚本从 json 渲染）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import objective  # noqa: E402
import separate  # noqa: E402

RESULTS_DIR = HERE.parent / "results" / "e1"
OUT_JSON = RESULTS_DIR / "separation_round1.json"
OUT_MD = RESULTS_DIR / "separation_round1.md"
LAYER_ORDER = objective.LAYER_ORDER


def fmt(x, nd=2) -> str:
    return "—" if x is None else f"{x:.{nd}f}"


def ag(r: dict, key: str, field: str = "mean") -> str:
    """安全取分层聚合数字：该层无可比样本时 agg={'n':0}（如音乐重层原混 0 词→无 bleed 比），显示 —。"""
    v = r.get(key, {})
    if not isinstance(v, dict):
        return fmt(v)
    return fmt(v.get(field))


def render_md(out: dict) -> str:
    st = out["stratified"]
    total = st.get("总体", {})
    L = []
    L.append("# results/e1/separation_round1 — E1' 分离客观横评·首轮")
    L.append("")
    L.append(f"> 生成时间 {out['generated_at']}；分离模型 `{out['separation']['model']}`"
             f"（normalization_threshold={out['separation']['normalization_threshold']}，"
             f"保三轨同幅度尺度，见 json provenance）；"
             f"真值参照=sintel_center.wav FC 中置按时间窗取段；ASR=faster-whisper small cpu-int8 (en, vad)。")
    L.append(">")
    L.append(f"> {out['conclusions']['identity']}")
    L.append(f"> {out['conclusions']['subtract_vs_instrumental']}")
    L.append("")
    L.append("## 1. 分层汇总")
    L.append("")
    L.append("| 分层 | 段数 | SI-SDR(vs中置,best) median/mean | SI-SDR(零滞后) median | corr(best) median | bleed bgA mean | bleed bgB mean | A/B差异(vs A) dB mean | 恒等通过 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for layer in [k for k in st if k != "总体"]:
        r = st[layer]
        L.append(f"| {layer} | {r['n_clips']} | {ag(r, 'si_sdr_vs_center_best', 'median')} / {ag(r, 'si_sdr_vs_center_best')} "
                 f"| {ag(r, 'si_sdr_vs_center_zero_lag', 'median')} | {ag(r, 'corr_vs_center_best', 'median')} "
                 f"| {ag(r, 'bleed_bgA')} | {ag(r, 'bleed_bgB')} "
                 f"| {ag(r, 'ab_diff_vs_bgA_db')} | {r['identity_pass']}/{r['n_clips']} |")
    r = total
    L.append(f"| **总体** | {r.get('n_clips', 0)} | {ag(r, 'si_sdr_vs_center_best', 'median')} / {ag(r, 'si_sdr_vs_center_best')} "
             f"| {ag(r, 'si_sdr_vs_center_zero_lag', 'median')} | {ag(r, 'corr_vs_center_best', 'median')} "
             f"| {ag(r, 'bleed_bgA')} | {ag(r, 'bleed_bgB')} "
             f"| {ag(r, 'ab_diff_vs_bgA_db')} | {r.get('identity_pass', 0)}/{r.get('n_clips', 0)} |")
    L.append("")
    L.append("口径：SI-SDR/corr 对整体增益不敏感；best=±60 样本（±1.4ms）微对齐搜索后的最优口径，"
             "零滞后是保守口径；bleed=背景轨听写词数/原混词数（对白残留代理，越低越干净）。")
    L.append("")
    L.append("## 2. 逐段数字")
    L.append("")
    L.append("| clip | 分层 | SI-SDR best | lag | corr best | SI-SDR 零滞后 | 恒等 | 原混词 | bgA词 | bgB词 | bleed bgA | bleed bgB | A/B差异 dB(vsA) |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for p in out["per_clip"]:
        c, b, a = p["center"], p["bleed"], p["ab_diff"]
        L.append(f"| {p['clip_id']} | {p['layer']} | {fmt(c['best']['si_sdr'], 1)} | {c['best']['lag']} "
                 f"| {fmt(c['best']['corr'], 3)} | {fmt(c['zero_lag']['si_sdr'], 1)} "
                 f"| {'✓' if p['identity']['identical'] else '✗'} "
                 f"| {b['orig']['words']} | {b['bgA']['words']} | {b['bgB']['words']} "
                 f"| {fmt(b['bleed_bgA_ratio'])} | {fmt(b['bleed_bgB_ratio'])} | {fmt(a['diff_vs_bgA_db'], 1)} |")
    L.append("")
    L.append("## 3. 相减恒等性验证（工程验证）")
    L.append("")
    worst = max((p["identity"]["max_abs_diff"] for p in out["per_clip"]), default=0.0)
    L.append(f"- 结论：**{out['conclusions']['identity']}**（逐样本重算 原混−人声 与背景A文件比对，最大偏差 {worst:.2e}）。")
    L.append("- 含义：背景A 由纯 python 波形相减产生（原混−人声，float32 落盘），与公式定义严格一致；"
             "后续所有以背景A为对象的指标，其语义就是「原混减去模型人声」本身，不引入第二模型自由度。")
    L.append("")
    L.append("## 4. 相减纪律 vs 模型第二输出（背景A vs 背景B）")
    L.append("")
    L.append(f"- 差异能量：总体均值 {fmt(total.get('ab_diff_vs_bgA_db', {}).get('mean') if total.get('ab_diff_vs_bgA_db', {}).get('n') else None)} dB（相对背景A）"
             f"——模型 instrumental 输出与「原混−模型人声」不是同一轨，差异即模型对两 stem 的分配自由度。")
    L.append(f"- 对白残留（bleed 均值）：相减 bgA={fmt(total.get('bleed_bgA', {}).get('mean') if total.get('bleed_bgA', {}).get('n') else None)} vs "
             f"模型 instrumental bgB={fmt(total.get('bleed_bgB', {}).get('mean') if total.get('bleed_bgB', {}).get('n') else None)}（分层数字见 §1；越低越干净）。")
    L.append("")
    L.append("## 5. F4 sanity — 负例（无对白切片上的 Whisper 幻觉）")
    L.append("")
    L.append(f"- 取例说明：{out['f4_negatives']['note']}")
    L.append("")
    L.append("| clip | 分层 | 原混词(vad开) | bgA词(vad开) | bgB词(vad开) | bgA词(vad关) | bgB词(vad关) |")
    L.append("|---|---|---|---|---|---|---|")
    for n in out["f4_negatives"]["clips"]:
        if "error" in n:
            L.append(f"| {n['clip_id']} | {n['layer']} | {n['error']} | | | | |")
            continue
        b, pv = n["bleed"], n
        L.append(f"| {n['clip_id']} | {n['layer']} | {b['orig']['words']} | {b['bgA']['words']} | {b['bgB']['words']} "
                 f"| {pv['no_vad_probe_bgA']['words']} | {pv['no_vad_probe_bgB']['words']} |")
    L.append("")
    L.append("判读：理想负例 bgA/bgB 词数应≈0；vad 关掉后的残留词数即 Whisper 幻觉基线，"
             "衡量主口径（vad 开）bleed 数字的可信下限。")
    L.append("")
    if out.get("clips_skipped_missing_products"):
        L.append(f"## 备注")
        L.append("")
        L.append(f"- 缺产物跳过段：{out['clips_skipped_missing_products']}")
        L.append("")
    L.append("---")
    L.append("")
    L.append(f"全部数字可追溯：逐段明细与文件路径见 `separation_round1.json`（per_clip / stratified / f4_negatives）。"
             f"音频产物在 `media/separated/`（gitignored）：`stems/`=模型两轨原名，`<clip_id>_bgA_subtract.wav`=相减背景A，"
             f"`ref/`=中置参照段，`asr16k/`=whisper 输入转码缓存。")
    return "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description="E1' 首轮全流程")
    ap.add_argument("--clips", default="", help="逗号分隔 clip_id 子集；缺省=按 --source 过滤")
    ap.add_argument("--source", default="blender",
                    help="按 manifest source 字段过滤（默认 blender=Sintel 30 段；'all'=不过滤）")
    ap.add_argument("--force", action="store_true", help="分离产物全部重跑")
    ap.add_argument("--skip-separate", action="store_true", help="跳过分离，直接算指标")
    ap.add_argument("--skip-objective", action="store_true", help="只跑分离")
    ap.add_argument("--md-only", action="store_true", help="不跑分离/指标，从已有 json 重渲染 md")
    a = ap.parse_args()
    only = [x.strip() for x in a.clips.split(",") if x.strip()] or None
    if only is None and a.source != "all":
        # manifest 是共享文件（票03 会追加 B 站段）；本票口径=Sintel 30 段，按 source 字段锁定
        only = [c["clip_id"] for c in separate.load_manifest() if c.get("source") == a.source]
        separate.log(f"按 source={a.source} 选出 {len(only)} 段")

    t0 = time.perf_counter()
    if a.md_only:
        if not OUT_JSON.exists():
            raise SystemExit(f"--md-only 需要 {OUT_JSON}，先跑 objective")
        res = json.loads(OUT_JSON.read_text(encoding="utf-8"))
    else:
        if not a.skip_separate:
            out = separate.run(only=only, force=a.force)
            if out["stats"]["failed"]:
                separate.log("有分离失败段，仍继续对已完成段算指标")
        res = None
        if not a.skip_objective:
            res = objective.run(only=only)
        if res is None:
            separate.log("按 --skip-objective 结束（未产出指标）")
            return 0
    md = render_md(res)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(md, encoding="utf-8")
    print(md)
    separate.log(f"run_first 完成，总耗时 {time.perf_counter() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
