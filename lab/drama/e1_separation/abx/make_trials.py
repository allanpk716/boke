# -*- coding: utf-8 -*-
"""票07 · E1 三臂 ABX 试听包（分离对照臂）——试次清单生成与自检。

run in .venv-lab (工作目录任意，路径以本文件位置锚定):
    ../../../.venv-lab/Scripts/python make_trials.py            # 从 media/abx/stimuli_index.json 生成试次清单
    ../../../.venv-lab/Scripts/python make_trials.py --verify   # 自检：文件一一对应/等长/true peak/A≠B/播放副本
    ../../../.venv-lab/Scripts/python make_trials.py --verify --trials <json路径>   # 指定清单（缺省取最新）

产物:
    results/abx/trials_<date>_F1-corrected-separation-arm.json   试次清单（入库；answer 字段留给听者回填）
    media/abx/play/T<nn>_1.wav / _2.wav / _X.wav                 中性命名的播放副本（盲听层，gitignored）
    终端自检报告（--verify）

随机化口径: 固定种子 seed=20260925（random.Random，Python 版本内可复现）。
    按 clip_id 升序定序后逐试次抽 X ∈ {A,B} 与播放顺序 play_order ∈ {AB,BA}，最后整体洗牌试次顺序，
    洗牌后编 T01..T18（文件里的顺序=建议听音顺序）。answer 字段置 null，听者判完回填。

盲听口径: 主刺激文件名带 A__orig__/B__F1-corrected 标注（溯源+验收要求），直接按名播放会破盲。
    因此另产一套中性命名播放副本 media/abx/play/T<nn>_1/_2/_X.wav：_1/_2 = A/B 按 play_order 摆位，
    _X = X 臂的副本。听者只听 play/ 下的文件，answer 回填 "1"/"2"（X 像 _1 还是 _2）；
    晨间分析按 answer_decode 规则解码对错。master 的 A/B 文件仅供溯源与客观指标，不做播放用。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent          # e1_separation/abx/
DRAMA = HERE.parent.parent                      # lab/drama/
STIMULI_INDEX = DRAMA / "media" / "abx" / "stimuli_index.json"
RESULTS_DIR = DRAMA / "results" / "abx"
ARM_TAG = "F1-corrected-separation-arm"
SEED = 20260925
EXPECTED_TOTAL = 18
TP_ACCEPT_DBTP = -1.0        # 验收线
DUR_TOL_S = 0.001
DIFF_MIN_ABS = 1e-4          # A≠B 中段最小差异（float32 台账同口径）


def log(msg: str) -> None:
    print(f"[make_trials {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def find_latest_trials() -> Path:
    files = sorted(RESULTS_DIR.glob(f"trials_*_{ARM_TAG}.json"))
    if not files:
        raise SystemExit(f"results/abx/ 下没有 trials_*_{ARM_TAG}.json，先跑不带 --verify 的生成模式")
    return files[-1]


def build() -> Path:
    import random
    idx = json.loads(STIMULI_INDEX.read_text(encoding="utf-8"))
    stims = idx["stimuli"]
    if len(stims) != EXPECTED_TOTAL:
        raise SystemExit(f"stimuli_index 有 {len(stims)} 段，期望 {EXPECTED_TOTAL}——先完整跑 make_stimuli.py")
    counts: dict[str, int] = {}
    for s in stims:
        counts[s["stratum"]] = counts.get(s["stratum"], 0) + 1
    log(f"分层计数（F9 只报告不判闸）: {counts}")

    rng = random.Random(SEED)
    # 可复现：固定顺序抽签，最后洗牌
    ordered = sorted(stims, key=lambda s: s["clip_id"])
    trials = []
    for s in ordered:
        trials.append({
            "clip_id": s["clip_id"],
            "stratum": s["stratum"],
            "layer": s["layer"],
            "language": s["language"],
            "source": s["source"],
            "A": f"media/abx/{s['A_file']}",
            "B": f"media/abx/{s['B_file']}",
            "X": "A" if rng.random() < 0.5 else "B",
            "play_order": "AB" if rng.random() < 0.5 else "BA",
            "answer": None,
            "duration_s": s["duration_s"],
            "window_start_s": s["window_start_s"],
            "lufs_A": s["lufs_A_final"],
            "lufs_B": s["lufs_B_final"],
            "tp_A_dbtp": s["tp_A_dbtp"],
            "tp_B_dbtp": s["tp_B_dbtp"],
            "g_dialog_db": s["g_dialog_db"],
            "master_gain_db": s["master_gain_db"],
            "tp_guard_db": s["tp_guard_db"],
            "expected_audibility": s["expected_audibility"],
            "ab_diff_mid_rms_ratio": s["ab_diff_mid_rms_ratio"],
        })
    rng.shuffle(trials)
    for i, t in enumerate(trials, 1):
        t["trial_id"] = f"T{i:02d}"

    # 盲听播放副本：play/T<nn>_1/_2 = A/B 按 play_order 摆位；_X = X 臂。中性命名，防文件名泄底。
    play_dir = DRAMA / "media" / "abx" / "play"
    if play_dir.exists():
        shutil.rmtree(play_dir)
    play_dir.mkdir(parents=True)
    for t in trials:
        seq_a, seq_b = ("1", "2") if t["play_order"] == "AB" else ("2", "1")
        copies = {
            f"{t['trial_id']}_{seq_a}.wav": t["A"],
            f"{t['trial_id']}_{seq_b}.wav": t["B"],
            f"{t['trial_id']}_X.wav": t["A"] if t["X"] == "A" else t["B"],
        }
        for dst, src in copies.items():
            shutil.copyfile(str(DRAMA / src), str(play_dir / dst))
        t["play_files"] = {
            "1": f"media/abx/play/{t['trial_id']}_1.wav",
            "2": f"media/abx/play/{t['trial_id']}_2.wav",
            "X": f"media/abx/play/{t['trial_id']}_X.wav",
        }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULTS_DIR / f"trials_{time.strftime('%Y%m%d')}_{ARM_TAG}.json"
    doc = {
        "arm": ARM_TAG,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seed": SEED,
        "n_trials": len(trials),
        "strata_counts": counts,
        "paths_root": "lab/drama",
        "instructions": "results/abx/README_listener.md",
        "note_substitute": (
            "【替代决策·显著】今晚无 CosyVoice（.venv-lab 安装过重留待次日）：主臂（分离床+真克隆回混）"
            "与克隆对照臂（克隆声 vs 原声直比）今晚缺席，克隆版次日补装环境后补制备——不静默降质。"
            "本清单只测分离对照臂（F1 复审修正版）：B=背景A(票05 相减床)+分离人声×(+2dB) 回混重建"
            "+母带响度对齐。"
        ),
        "note_design": (
            "设计要点：票05 已证 背景A+分离人声=原混（bit 级恒等），'重建后对齐原混 LUFS'恒为 0 dB，"
            "照字面做 A/B 完全相同、ABX 不可判。按票面'更干净设计在 note 说明后采用'条款："
            "B = 背景A + 分离人声×g，g=固定 +2.0 dB 对白再平衡（真实换声链中'新对白'按参考对白 LUFS "
            "落位后的代表性残差），再做 B 成片 integrated LUFS 对齐 A（主增益级）与 true peak 联合防护"
            "（A/B 同增益，目标 −1.2 dBTP，验收线 −1.0 留 0.2 dB 余量）。"
            "A/B 差异=分离伪影+对白电平再平衡+母带响度处理，与真实换声链的分离/回混部分同构。"
        ),
        "note_F9": "判据口径：分档(50/65%)优先于显著性检验；闸门判定用总体 18 试次，分层(stratum 字段)只报告不判闸。",
        "note_answer": (
            "answer 字段留给听者：盲听 play/ 副本后，X 像 _1 就填 \"1\"，像 _2 就填 \"2\"（null=未听）。"
            "晨间分析解码（answer_decode）：play_order=\"AB\" 时 _1=A/_2=B，\"BA\" 反之；"
            "答对 = (answer==\"1\" and X==\"A\") or (answer==\"2\" and X==\"B\")。听前别看 X 与 play_order 列。"
        ),
        "trials": trials,
    }
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"试次清单落盘 {out}（{len(trials)} 试次，seed={SEED}）")
    for t in trials:
        log(f"  {t['trial_id']} {t['stratum']:13s} X={t['X']} play={t['play_order']} {t['clip_id']}")
    return out


def true_peak_dbtp(x: np.ndarray) -> float:
    from scipy.signal import resample_poly
    y = resample_poly(x, 4, 1, axis=0)
    return float(20.0 * np.log10(max(float(np.abs(y).max()), 1e-12)))


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(trials_path: Path) -> int:
    doc = json.loads(trials_path.read_text(encoding="utf-8"))
    trials = doc["trials"]
    problems: list[str] = []
    rows = []

    if len(trials) != EXPECTED_TOTAL:
        problems.append(f"试次数 {len(trials)} != {EXPECTED_TOTAL}")
    counts: dict[str, int] = {}
    ids_seen = set()
    for t in trials:
        tid = t.get("trial_id", "?")
        counts[t["stratum"]] = counts.get(t["stratum"], 0) + 1
        if t["clip_id"] in ids_seen:
            problems.append(f"{tid}: clip_id 重复 {t['clip_id']}")
        ids_seen.add(t["clip_id"])
        if t.get("X") not in ("A", "B"):
            problems.append(f"{tid}: X 非法 {t.get('X')}")
        if t.get("answer") not in (None, "A", "B"):
            problems.append(f"{tid}: answer 非法 {t.get('answer')}")
        if t.get("play_order") not in ("AB", "BA"):
            problems.append(f"{tid}: play_order 非法 {t.get('play_order')}")

        pa, pb = DRAMA / t["A"], DRAMA / t["B"]
        if not pa.exists():
            problems.append(f"{tid}: 缺 A 文件 {t['A']}")
            continue
        if not pb.exists():
            problems.append(f"{tid}: 缺 B 文件 {t['B']}")
            continue
        ia, ib = sf.info(str(pa)), sf.info(str(pb))
        if (ia.frames, ia.samplerate, ia.channels) != (ib.frames, ib.samplerate, ib.channels):
            problems.append(f"{tid}: A/B 形状不等 A={ia.frames}/{ia.samplerate}/{ia.channels} "
                            f"B={ib.frames}/{ib.samplerate}/{ib.channels}")
            continue
        A, sr_a = sf.read(str(pa), dtype="float32", always_2d=True)
        B, sr_b = sf.read(str(pb), dtype="float32", always_2d=True)
        dur = ia.frames / ia.samplerate
        if abs(dur - float(t["duration_s"])) > DUR_TOL_S:
            problems.append(f"{tid}: 时长不符 file={dur:.3f}s json={t['duration_s']}s")

        tp_a, tp_b = true_peak_dbtp(A), true_peak_dbtp(B)
        if tp_a > TP_ACCEPT_DBTP + 1e-6:
            problems.append(f"{tid}: A true peak {tp_a:.2f} > {TP_ACCEPT_DBTP} dBTP")
        if tp_b > TP_ACCEPT_DBTP + 1e-6:
            problems.append(f"{tid}: B true peak {tp_b:.2f} > {TP_ACCEPT_DBTP} dBTP")

        nf = int(ia.samplerate * 0.02)  # 跳过端部渐变区
        d = (A[nf:-nf] - B[nf:-nf]).astype(np.float64)
        diff_max = float(np.abs(d).max())
        if diff_max <= DIFF_MIN_ABS:
            problems.append(f"{tid}: A/B 中段无差异 max={diff_max:.2e}（完全重建退化为不可判）")

        # 盲听播放副本：存在 + 与所映射的 master 音频字节一致（want 按播放位 1/2/X 键控）
        pf = t.get("play_files") or {}
        seq_a, seq_b = ("1", "2") if t.get("play_order") == "AB" else ("2", "1")
        want = {seq_a: t["A"], seq_b: t["B"], "X": t["A"] if t["X"] == "A" else t["B"]}
        master_hash = {k: _file_sha(DRAMA / v) for k, v in want.items()}
        for pos in ("1", "2", "X"):
            p = pf.get(pos)
            if not p or not (DRAMA / p).exists():
                problems.append(f"{tid}: 缺播放副本 _{pos} ({p})")
                continue
            if _file_sha(DRAMA / p) != master_hash[pos]:
                problems.append(f"{tid}: 播放副本 _{pos} 与 master 不一致")

        rows.append((tid, t["stratum"], dur, tp_a, tp_b, diff_max,
                     f"{t['lufs_A']:.1f}/{t['lufs_B']:.1f}", t["X"], t["answer"]))

    if counts.get("zh_dlg_clean", 0) + counts.get("zh_dlg_clean_backfill", 0) < 6:
        problems.append(f"zh 对白干净层（含回填）不足 6: {counts}")
    if counts.get("en_dlg_clean", 0) != 6:
        problems.append(f"en 对白干净层 != 6: {counts}")
    if counts.get("en_sfx", 0) != 3 or counts.get("zh_sfx", 0) != 3:
        problems.append(f"音效压对白层 != 各3: {counts}")

    print()
    print(f"自检报告 {trials_path.name}")
    print("| 试次 | 分层 | 时长s | TP_A dBTP | TP_B dBTP | A≠B max | LUFS A/B | X | answer |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r[0]} | {r[1]} | {r[2]:.1f} | {r[3]:.2f} | {r[4]:.2f} | {r[5]:.2e} | {r[6]} | {r[7]} | {r[8]} |")
    print()
    if problems:
        print(f"FAIL：{len(problems)} 项")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"PASS：{len(rows)} 试次；文件一一对应、A/B 等长、true peak ≤ {TP_ACCEPT_DBTP} dBTP、"
          f"A≠B、播放副本与 master 一致、分层配额 {counts}。answer 均为 null（待听者回填）。")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="票07 ABX 试次清单生成/自检")
    ap.add_argument("--verify", action="store_true", help="自检已有清单")
    ap.add_argument("--trials", default="", help="自检目标 json（缺省=results/abx/ 最新）")
    a = ap.parse_args()
    if a.verify:
        target = Path(a.trials) if a.trials else find_latest_trials()
        if not target.is_absolute():
            target = DRAMA / target
        return verify(target)
    build()
    return 0


if __name__ == "__main__":
    sys.exit(main())
