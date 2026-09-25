#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""build_pairs.py — 票06 E2 伪重叠构造集：solo 段两两混合（纯 python，wavio 风格 wave/array）。

run in .venv-lab:
    ../../../.venv-lab/Scripts/python build_pairs.py               # 构造 18 条 + 抽 3 条对账 + 落 stats/manifest
    ../../../.venv-lab/Scripts/python build_pairs.py --audit-only  # 只对账已有构造产物（不重写音频、不改 manifest/stats）

做法（票06 规格）：
  - 原料 = clips_manifest.yaml 中 source=bili / language=zh / solo_candidate: true 的段（path_16k）。
  - 两两组合 C(4,2)=6 对 × SNR {0,+3,-3}dB = 18 条；性别分层全部 unknown/unknown
    （solo 段没有性别标注——无人听审，待听审后补分层）。
  - 混音 = 样本级线性加权和：两轨各按 RMS 归一到 1.0 → B 乘 10^(-SNR/20)
    （SNR = 20·lg(rmsA/rmsB)，A=配对第一源、B=第二源）→ 求共同缩放 s 使 |mix| 峰值 ≤0.98
    （防 int16 削波，保证量化后 mix[i]=gtA[i]+gtB[i] 逐样本成立、无钳位）。
    16k 副档实测是立体声（票面背景写 mono，与实际不符）——先 wavio.to_mono 下混再混合。
  - 时长 = 两段较短者；两轨窗口起点各自随机（固定 seed，可复现）→ 两声源在混音时间轴全程共现。
  - 产物：media/constructed/ 下 mix + 真值两轨（gt_a/gt_b）各 18 条，均 16k mono s16；
    clips_manifest.yaml 追加 constructed 条目（幂等：重跑先删旧 constructed 条目再追加）。
  - 对账：抽 3 条读回 mix 与两真值轨，验证逐样本 mix==gtA+gtB 且实测 SNR 偏差 ≤0.1dB。
"""
from __future__ import annotations

import argparse
import array
import itertools
import math
import random
import re
import sys
from datetime import datetime
from pathlib import Path

try:  # Windows 控制台默认 cp936，统一 UTF-8 防乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
DRAMA = HERE.parent
sys.path.insert(0, str(DRAMA))

import yaml  # noqa: E402  .venv-lab 已装 PyYAML
from common import wavio  # noqa: E402

MANIFEST = DRAMA / "clips_manifest.yaml"
CONSTRUCTED_DIR = DRAMA / "media" / "constructed"
STATS_MD = DRAMA / "results" / "e2" / "pairs_stats.md"

SEED = 20260925
SNRS_DB = [0, 3, -3]
PEAK_CEILING = 0.98        # 混音峰值上限（float 域，×32767 后仍留钳位余量）
AUDIT_TOL_SNR_DB = 0.1     # 对账时实测 SNR 与目标允许偏差
N_AUDIT = 3                # 抽检条数（验收标准：抽 3 条）
CONSTRUCTED_PREFIX = "constructed_"


# ---------------------------------------------------------------- 基础工具

def short_id(cid: str) -> str:
    """bili_liangjian_ep01_g0036_g0037 → ep01_g36_g37（文件名/clip_id 用短名）。"""
    s = re.sub(r"^bili_liangjian_", "", cid)
    return re.sub(r"g0*(\d+)", lambda m: "g" + m.group(1), s)


def pick_solo(manifest: dict) -> list[dict]:
    """取 source=bili / language=zh / solo_candidate: true 的条目（票06 原料）。"""
    out = []
    for c in manifest.get("clips", []):
        if c.get("source") == "bili" and c.get("language") == "zh" and c.get("solo_candidate") is True:
            out.append(c)
    return out


def rms_f(x: list[float]) -> float:
    return math.sqrt(sum(v * v for v in x) / len(x))


def quantize(x: float) -> int:
    return max(-32768, min(32767, round(x)))


# ---------------------------------------------------------------- 混音核心

def build_pair(ent_a: dict, ent_b: dict, snr_db: int) -> tuple[dict, dict]:
    """混一对 → (manifest 条目 dict, 对账用记录)。音频落 CONSTRUCTED_DIR。"""
    cid_a, cid_b = ent_a["clip_id"], ent_b["clip_id"]
    path_a = DRAMA / ent_a["path_16k"]
    path_b = DRAMA / ent_b["path_16k"]

    raw_a, rate_a, ch_a = wavio.read_wav(path_a)
    raw_b, rate_b, ch_b = wavio.read_wav(path_b)
    if rate_a != 16000 or rate_b != 16000:
        raise ValueError(f"16k 副档采样率异常: {rate_a}/{rate_b}")
    mono_a = [s / 32768.0 for s in wavio.to_mono(raw_a, ch_a)]  # 16k 副档实测 stereo → 下混
    mono_b = [s / 32768.0 for s in wavio.to_mono(raw_b, ch_b)]

    n = min(len(mono_a), len(mono_b))  # 时长取两段较短者
    rng = random.Random(f"{SEED}|{cid_a}|{cid_b}|{snr_db}")
    off_a = rng.randrange(0, len(mono_a) - n + 1)  # 窗口起点各自随机 → 可复现
    off_b = rng.randrange(0, len(mono_b) - n + 1)
    wa, wb = mono_a[off_a:off_a + n], mono_b[off_b:off_b + n]

    # 能量归一：两轨 RMS → 1.0，再按目标 SNR 压 B（SNR=A/B）
    rms_a, rms_b = rms_f(wa), rms_f(wb)
    if rms_a < 1e-6 or rms_b < 1e-6:
        raise ValueError(f"源段近静音，无法按 RMS 归一: {cid_a} rms={rms_a:.2e} / {cid_b} rms={rms_b:.2e}")
    fa = [v / rms_a for v in wa]
    fb = [v / rms_b * (10.0 ** (-snr_db / 20.0)) for v in wb]

    # 共同缩放防削波（等比缩放不破坏 SNR；量化后 mix=gtA+gtB 严格成立）
    peak = max(max(abs(v) for v in fa), max(abs(v) for v in fb),
               max(abs(x + y) for x, y in zip(fa, fb)))
    s = PEAK_CEILING / peak if peak > PEAK_CEILING else 1.0

    a16 = array.array("h", [quantize(v * s * 32767.0) for v in fa])
    b16 = array.array("h", [quantize(v * s * 32767.0) for v in fb])
    m16 = array.array("h", (a16[i] + b16[i] for i in range(n)))  # 峰值受控，无钳位

    tag = f"snr{'+' if snr_db > 0 else ''}{snr_db}"
    sid_a, sid_b = short_id(cid_a), short_id(cid_b)
    stem = f"{CONSTRUCTED_PREFIX}{sid_a}__{sid_b}_{tag}"
    p_mix = CONSTRUCTED_DIR / f"{stem}_16k.wav"
    p_ga = CONSTRUCTED_DIR / f"{stem}_gtA_16k.wav"
    p_gb = CONSTRUCTED_DIR / f"{stem}_gtB_16k.wav"
    CONSTRUCTED_DIR.mkdir(parents=True, exist_ok=True)
    wavio.write_wav(p_mix, m16, 16000, channels=1)
    wavio.write_wav(p_ga, a16, 16000, channels=1)
    wavio.write_wav(p_gb, b16, 16000, channels=1)

    rel = lambda p: p.relative_to(DRAMA).as_posix()  # noqa: E731
    gender = "unknown/unknown"  # solo 段无性别标注（无人听审），待听审后补分层
    entry = {
        "clip_id": stem,
        "source": "constructed",
        "source_file": rel(p_mix),
        "language": "zh",
        "layer": "双人重叠",
        "provisional": True,
        "start": 0.0,
        "end": round(n / 16000, 2),
        "duration": round(n / 16000, 2),
        "path": "",
        "path_16k": rel(p_mix),
        "status": "cut",
        "note": (f"票06 伪重叠构造：源A={cid_a} 源B={cid_b} SNR={snr_db:+d}dB(目标,A/B) "
                 f"gender={gender}(待听审后补分层) 真值轨见 gt_a/gt_b 字段 "
                 f"窗偏移A={off_a / 16000:.3f}s B={off_b / 16000:.3f}s seed={SEED} "
                 f"mix=gtA+gtB逐样本 R=1.0 归一 峰值≤{PEAK_CEILING}"),
        "source_a": cid_a,
        "source_b": cid_b,
        "snr_db": snr_db,
        "gender_pair": gender,
        "gt_a": rel(p_ga),
        "gt_b": rel(p_gb),
        "offset_a_s": round(off_a / 16000, 3),
        "offset_b_s": round(off_b / 16000, 3),
        "mix_seed": f"{SEED}",
    }
    audit_rec = {"entry": entry, "n": n}
    return entry, audit_rec


# ---------------------------------------------------------------- 对账（验收：抽 3 条能量关系复核）

def audit_pair(rec: dict) -> dict:
    """读回 mix 与两真值轨：逐样本 mix==gtA+gtB（读回值即两轨之和）；SNR 实测偏差 ≤0.1dB。"""
    e = rec["entry"]
    m, r_m, ch_m = wavio.read_wav(DRAMA / e["path_16k"])
    a, r_a, ch_a = wavio.read_wav(DRAMA / e["gt_a"])
    b, r_b, ch_b = wavio.read_wav(DRAMA / e["gt_b"])
    assert r_m == r_a == r_b == 16000 and ch_m == ch_a == ch_b == 1, "采样率/声道不符"
    assert len(m) == len(a) == len(b) == rec["n"], f"时长不符 mix={len(m)} a={len(a)} b={len(b)} n={rec['n']}"

    bad = sum(1 for i in range(len(m)) if m[i] != a[i] + b[i])  # 严格逐样本（构造时即相加，无钳位）
    snr_meas = 20.0 * math.log10(rms_f([v / 32768.0 for v in a]) / rms_f([v / 32768.0 for v in b]))
    dev = abs(snr_meas - e["snr_db"])
    ok = bad == 0 and dev <= AUDIT_TOL_SNR_DB
    return {"clip_id": e["clip_id"], "sample_mismatch": bad, "snr_target_db": e["snr_db"],
            "snr_measured_db": round(snr_meas, 3), "snr_dev_db": round(dev, 3), "ok": ok}


# ---------------------------------------------------------------- manifest 追加（幂等，纯文本插行，保留原注释）

def entry_yaml_text(e: dict) -> str:
    g = lambda k: e[k]  # noqa: E731
    note = e["note"]
    lines = [
        f"  - clip_id: {g('clip_id')}",
        f"    source: {g('source')}",
        f"    source_file: {g('source_file')}",
        f"    language: {g('language')}",
        f"    layer: {g('layer')}",
        f"    provisional: {'true' if g('provisional') else 'false'}",
        f"    start: {g('start')}",
        f"    end: {g('end')}",
        f"    duration: {g('duration')}",
        f'    path: "{g("path")}"',
        f"    path_16k: {g('path_16k')}",
        f"    status: {g('status')}",
        f'    note: "{note}"',
        f"    source_a: {g('source_a')}",
        f"    source_b: {g('source_b')}",
        f"    snr_db: {g('snr_db')}",
        f'    gender_pair: "{g("gender_pair")}"',
        f"    gt_a: {g('gt_a')}",
        f"    gt_b: {g('gt_b')}",
        f"    offset_a_s: {g('offset_a_s')}",
        f"    offset_b_s: {g('offset_b_s')}",
        f'    mix_seed: "{g("mix_seed")}"',
    ]
    return "\n".join(lines)


def append_manifest(entries: list[dict]) -> tuple[int, int]:
    """幂等追加：先剔除旧 constructed 条目行，再把新条目插在 quota_progress: 之前。返回 (追加数, 总条目数)。"""
    text = MANIFEST.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    # 状态机：跳过既有 constructed 条目（从 "  - clip_id: constructed_…" 到下一个条目/顶层键之前）
    cleaned, skipping = [], False
    for ln in lines:
        starts_entry = ln.startswith("  - clip_id:")
        if starts_entry:
            skipping = ln.split("clip_id:", 1)[1].strip().startswith(CONSTRUCTED_PREFIX)
        elif skipping and ln and not ln[0].isspace():
            skipping = False  # 顶层键（如 quota_progress:）→ 停止跳过
        if not skipping:
            cleaned.append(ln)

    block = "\n".join(entry_yaml_text(e) for e in entries) + "\n"
    out, inserted = [], False
    for ln in cleaned:
        if not inserted and ln.startswith("quota_progress:"):
            out.append(block)
            inserted = True
        out.append(ln)
    if not inserted:
        raise RuntimeError("manifest 里没找到 quota_progress: 顶层键，插入位置未定，拒绝改写")

    MANIFEST.write_text("".join(out), encoding="utf-8", newline="\n")
    check = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    ids = [c["clip_id"] for c in check["clips"]]
    if len(ids) != len(set(ids)):
        raise RuntimeError("manifest 出现重复 clip_id，拒绝交付")
    return len(entries), len(ids)


# ---------------------------------------------------------------- stats 落盘

def render_stats(entries: list[dict], audits: list[dict], n_total_clips: int) -> str:
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    L = []
    L.append("# results/e2/pairs_stats — E2 伪重叠构造集规模与统计（票06）")
    L.append("")
    L.append(f"> 生成时间 {now}；生成脚本 `e2_overlap/build_pairs.py`（seed={SEED}，可复现）。")
    L.append(f"> 混音定义：两轨 RMS 归一到 1.0 → B 乘 10^(-SNR/20)（**SNR=A/B，A=配对第一源**）→")
    L.append(f"> 共同缩放峰值≤{PEAK_CEILING} → int16；**mix = gtA + gtB 逐样本成立**（读回可验证）。")
    L.append(f"> 16k 副档实测为立体声（票面背景按 mono 估计，与实际不符）——先 to_mono 下混再混合。")
    L.append("")
    L.append("## 1. 规模（F10：后续比率指标一律以本表分母为准）")
    L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| solo 原料段（bili/zh, solo_candidate=true） | {len(set(e['source_a'] for e in entries) | set(e['source_b'] for e in entries))} |")
    L.append(f"| 两两配对数 C(4,2) | {len(set((e['source_a'], e['source_b']) for e in entries))} |")
    L.append(f"| SNR 档 | {', '.join(f'{s:+d}dB' if s else '0dB' for s in SNRS_DB)} |")
    L.append(f"| **构造集总数（F10 分母）** | **{len(entries)}** |")
    L.append(f"| manifest 总条目（含 Sintel 30 + 亮剑 17 + constructed） | {n_total_clips} |")
    L.append("")
    snr_count = {s: sum(1 for e in entries if e["snr_db"] == s) for s in SNRS_DB}
    dur = [e["duration"] for e in entries]
    L.append(f"SNR 分布：0dB={snr_count[0]} 条，+3dB={snr_count[3]} 条，−3dB={snr_count[-3]} 条；"
             f"时长 {min(dur):.0f}~{max(dur):.0f}s（=两源较短者）。")
    L.append("")
    L.append("## 2. 性别分层现状（票06 注意项）")
    L.append("")
    L.append("**全部 unknown/unknown**：solo 段没有性别标注（无人听审），无法做同性别/不同性别分层；")
    L.append("**待听审后补分层**，届时按 manifest `gender_pair` 字段重算本节。当前分层统计如实记：")
    L.append("")
    L.append("| gender_pair | 条数 | 占比 |")
    L.append("|---|---|---|")
    gcounts: dict[str, int] = {}
    for e in entries:
        gcounts[e["gender_pair"]] = gcounts.get(e["gender_pair"], 0) + 1
    for gp, n in sorted(gcounts.items()):
        L.append(f"| {gp} | {n} | {n / len(entries):.0%} |")
    L.append("")
    L.append("## 3. 逐条清单")
    L.append("")
    L.append("| clip_id | 源A | 源B | SNR | 时长s | 偏移A/B s |")
    L.append("|---|---|---|---|---|---|")
    for e in entries:
        L.append(f"| {e['clip_id']} | {e['source_a']} | {e['source_b']} | {e['snr_db']:+d}dB "
                 f"| {e['duration']:.0f} | {e['offset_a_s']:.2f}/{e['offset_b_s']:.2f} |")
    L.append("")
    L.append("注：偏移=各源内随机窗起点（seed 固定可复现）；等长对（如 60s+60s）窗长=源长，"
             "偏移无自由度故为 0.00/0.00——两声源在混音时间轴仍全程共现。")
    L.append("## 4. 混音对账（验收：抽 3 条读回复核）")
    L.append("")
    L.append("| clip_id | 逐样本 mix==gtA+gtB | 实测SNR dB | 目标 dB | 偏差 dB | 结论 |")
    L.append("|---|---|---|---|---|---|")
    for a in audits:
        mismatch_txt = "PASS(0 样本不符)" if a["sample_mismatch"] == 0 else f"FAIL({a['sample_mismatch']} 样本不符)"
        L.append(f"| {a['clip_id']} | {mismatch_txt} "
                 f"| {a['snr_measured_db']:.3f} | {a['snr_target_db']:+d} | {a['snr_dev_db']:.3f} "
                 f"| {'PASS' if a['ok'] else 'FAIL'} |")
    n_ok = sum(1 for a in audits if a["ok"])
    L.append("")
    L.append(f"对账结论：{n_ok}/{len(audits)} 通过（逐样本和严格相等 + SNR 偏差≤{AUDIT_TOL_SNR_DB}dB）。")
    L.append("")
    L.append("## 5. 后续接口（票06 交付的下游）")
    L.append("")
    L.append("- TSE 冒烟：`e2_overlap/tse_smoke.py`（就绪检查+调用骨架；输入=mix，配对真值=gt_a/gt_b，指标 SI-SDR）。")
    L.append("- 声纹门：`e2_overlap/voice_gate.py`（ECAPA cosine+阈值拦截/放行，输出带 F10 样本量字段）。")
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--audit-only", action="store_true", help="只对账已有构造产物（读 manifest constructed 条目）")
    args = ap.parse_args()

    manifest = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))

    if args.audit_only:
        olds = [c for c in manifest["clips"] if c.get("source") == "constructed"]
        if not olds:
            print("[FAIL] --audit-only：manifest 中无 constructed 条目，先跑一次全量构造")
            return 1
        audits = [audit_pair({"entry": e, "n": int(e["duration"] * 16000)}) for e in olds]
        for a in audits:
            print(f"[{'PASS' if a['ok'] else 'FAIL'}] {a['clip_id']}  mismatch={a['sample_mismatch']} "
                  f"snr={a['snr_measured_db']:.3f}dB (target {a['snr_target_db']:+d}, dev {a['snr_dev_db']:.3f})")
        n_ok = sum(1 for a in audits if a["ok"])
        print(f"--- audit {n_ok}/{len(audits)} passed")
        return 0 if n_ok == len(audits) else 1

    solo = pick_solo(manifest)
    if len(solo) != 4:
        print(f"[FAIL] 预期 4 条 solo_candidate 原料，实际 {len(solo)} 条（名单："
              f"{[c['clip_id'] for c in solo]}）——对照票03 提交核对 manifest")
        return 1
    print(f"[ok] solo 原料 4 段：{[c['clip_id'] for c in solo]}")

    entries, audit_recs = [], []
    for ea, eb in itertools.combinations(solo, 2):  # 4 段两两 = 6 对
        for snr in SNRS_DB:  # ×3 SNR = 18 条
            entry, rec = build_pair(ea, eb, snr)
            entries.append(entry)
            audit_recs.append(rec)
            print(f"[build] {entry['clip_id']}  dur={entry['duration']:.0f}s "
                  f"off={entry['offset_a_s']:.2f}/{entry['offset_b_s']:.2f}")
    assert len(entries) == 18, len(entries)

    rng = random.Random(f"{SEED}|audit")
    audits = [audit_pair(r) for r in rng.sample(audit_recs, N_AUDIT)]
    for a in audits:
        print(f"[audit] {'PASS' if a['ok'] else 'FAIL'} {a['clip_id']}  mismatch={a['sample_mismatch']} "
              f"snr={a['snr_measured_db']:.3f}dB (target {a['snr_target_db']:+d}, dev {a['snr_dev_db']:.3f})")
    if not all(a["ok"] for a in audits):
        print("[FAIL] 对账未全过，不落 manifest/stats")
        return 1

    n_added, n_total = append_manifest(entries)
    print(f"[manifest] 追加 {n_added} 条 constructed 条目，总条目 {n_total}（无重复 clip_id，注释未动）")

    STATS_MD.parent.mkdir(parents=True, exist_ok=True)
    STATS_MD.write_text(render_stats(entries, audits, n_total), encoding="utf-8", newline="\n")
    print(f"[stats] {STATS_MD.relative_to(DRAMA)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
