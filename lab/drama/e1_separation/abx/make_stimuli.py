# -*- coding: utf-8 -*-
"""票07 · E1 三臂 ABX 试听包（分离对照臂，F1-corrected-separation-arm）——加工片制备。

run in .venv-lab (工作目录任意，路径以本文件位置锚定):
    ../../../.venv-lab/Scripts/python make_stimuli.py            # 选段 + 制备 18 对 A/B 加工片
    ../../../.venv-lab/Scripts/python make_stimuli.py --dry-run  # 只选段打印，不写音频

输入（票05 产物，只读）:
    media/separated/separation_index.json   段 → orig/vocals/bgB/bgA 路径映射（47 段已全部在位，
                                            含 17 段亮剑——本票无需现跑 GPU 分离，直接复用）
    media/clips/<clip_id>_44k1.wav          原片切片
    results/e1/separation_round1.json       票05 whisper 词数（选段避开无对白段的依据）

输出（media/abx/，gitignored）:
    A__orig__<lang>__<layer>__<clip_id>__w<ms>.wav                    原片窗口（A 臂刺激）
    B__F1-corrected-separation-arm__<lang>__<layer>__<clip_id>__w<ms>.wav  加工片（B 臂刺激）
    stimuli_index.json                      选段依据 + 逐段响度/增益/true peak 全套台账

设计口径（decision_refs: D7, F1, F9；本脚本头部即"note 里说明理由"的正式记录）:
  * 臂定义：A = 原片切片 20s 窗口；B = 背景A(票05 相减床) + 分离人声×g 的回混重建。
  * 【设计要点，为什么 g 不是 LUFS 匹配】票05 已证 背景A + 分离人声 = 原混（bit 级恒等）。
    因此"把 B0=背景A+人声 的 integrated LUFS 对齐原混"这一步数学上恒等于 0 dB（B0 就是原混），
    照字面执行 A/B 完全重建、ABX 不可判。票面 background 材料允许"发现更干净设计在 note 说明后采用"，
    故本实现把 g 定义为**对白再平衡固定 +2.0 dB**：真实换声链里"新对白"（克隆 TTS）按参考对白做
    LUFS 匹配落位后残留 ±1~3 dB 级的电平误差，+2.0 dB 是该工况的代表性取值（固定、全段一致、可复现）；
    随后保留票面①要求的**成片响度配平**（B 的 integrated LUFS 对齐 A，主增益级）与 true peak 校验。
    这样 A/B 差异 = 分离伪影 + 对白电平再平衡 + 母带级响度处理，与真实换声链的分离/回混部分同构。
  * true peak 防护目标 −1.2 dBTP（对验收线 −1.0 dBTP 留 0.2 dB 余量；ffmpeg ebur128 true peak 口径，
    verify 侧用 4× 过采样重算）。A、B 用**同一**防护增益，保持两者相对电平关系（ABX 公平性）。
  * 端部 10ms sin² 交叉渐变同样**同时**施加于 A 与 B——渐变不构成可辨线索，差异只来自分离/回混处理。
  * 选段分层（F9：只报告不判闸）：Sintel 对白干净 6 段(en) + 亮剑对白干净 6 段(zh)
    + 每素材音效压对白 3 段(blender 3 + bili 3) = 18 试次。
    Sintel 对白 107s 才开始：按票05 whisper 原混词数降序取，0 词段只用于补位（按人声轨 RMS 择优）。
  * 【 viability 闸 + 回填规则】分层候选若"最佳 20s 窗人声 RMS / 原混窗 RMS < 0.10"（分离模型在该段
    几乎没分出人声 → A/B 听感不可分 → 强制瞎猜的废试次），剔除并在台账记录；配额缺口从同来源同语言、
    过闸且未选中的候选按层邻接序（音效压对白 > 音乐重 > 双人重叠）×人声窗 RMS 降序回填 1 名，
    stratum 标注 *_backfill。今晚触发：bili_liangjian_ep02_g0087_g0088（ratio=0.016，该段分离人声
    近无声——其"对白干净"标注本就 provisional，晨间听审可一并核实）→ 由亮剑 ep01_g0000（音效压对白，
    ratio=0.573）回填，zh 对白干净层实为 5+1 回填。
  * 亮剑分离：media/separated/ 已含全部 17 段亮剑三轨产物（separation_index.json 校验通过），
    本票**不新跑分离**（票面"需现分离"针对缺产物的新切片，现无缺失）。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent          # e1_separation/abx/
DRAMA = HERE.parent.parent                      # lab/drama/
MEDIA = DRAMA / "media"
SEP_DIR = MEDIA / "separated"
INDEX_PATH = SEP_DIR / "separation_index.json"
MANIFEST = DRAMA / "clips_manifest.yaml"
ROUND1_JSON = DRAMA / "results" / "e1" / "separation_round1.json"
OUT_DIR = MEDIA / "abx"
TMP_DIR = OUT_DIR / "_tmp"

RATE = 44100
WINDOW_S = 20.0                 # 每试次刺激时长（18 试次 × 3 段 × 20s ≈ 18 分钟纯播放）
WINDOW_HOP_S = 1.0              # 窗口搜索步长
FADE_MS = 10                    # 端部渐变时长（A/B 同施）
G_DIALOG_DB = 2.0               # 对白再平衡固定增益（设计要点见文件头）
MASTER_ALIGN = True             # 成片 integrated LUFS 对齐 A（票面①响度配平）
TP_GUARD_TARGET_DBTP = -1.2     # 生成侧防护目标（验收线 −1.0 + 0.2 余量）
ARM_TAG = "F1-corrected-separation-arm"
LAYER_SLUG = {"对白干净": "dlgclean", "音效压对白": "sfxdlg", "音乐重": "musichevy", "双人重叠": "ovlap"}
VIABILITY_RATIO_MIN = 0.10      # 最佳窗人声/原混 RMS 比，低于此判"分离人声近无声"剔除
BACKFILL_LAYER_ORDER = ["音效压对白", "音乐重", "双人重叠"]  # 回填层邻接序（对白可闻度由近到远）

# 选段配额（票面）：stratum → (来源, 语言, 分层, 条数)
STRAATA = [
    ("en_dlg_clean", "blender", "en", "对白干净", 6),
    ("zh_dlg_clean", "bili", "zh", "对白干净", 6),
    ("en_sfx", "blender", "en", "音效压对白", 3),
    ("zh_sfx", "bili", "zh", "音效压对白", 3),
]


def log(msg: str) -> None:
    print(f"[make_stimuli {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_manifest() -> dict[str, dict]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    return {c["clip_id"]: c for c in data["clips"] if c.get("status") == "cut"}


def load_sep_index() -> dict:
    idx = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    missing = []
    for cid, e in idx["clips"].items():
        for k in ("orig", "vocals", "bgA"):
            if k not in e or not Path(e[k]).exists():
                missing.append(f"{cid}:{k}")
    if missing:
        raise RuntimeError(f"separation_index 有缺产物段（如需新分离请协调者跑 separate.py）: {missing}")
    return idx


def load_round1_words() -> dict[str, int]:
    if not ROUND1_JSON.exists():
        return {}
    d = json.loads(ROUND1_JSON.read_text(encoding="utf-8"))
    return {p["clip_id"]: int(p["bleed"]["orig"]["words"]) for p in d.get("per_clip", [])}


def read_stereo(path: str | Path) -> np.ndarray:
    x, sr = sf.read(str(path), dtype="float32", always_2d=True)
    if sr != RATE or x.shape[1] != 2:
        raise RuntimeError(f"采样率/声道不符 {path}: sr={sr} ch={x.shape[1]}")
    return x


def best_window(x: np.ndarray, win_n: int, hop_n: int) -> tuple[float, int]:
    """人声轨能量最大的 20s 窗口（RMS, 起点样本）。对白存在的窗 = 分离伪影最可闻的窗。"""
    mono = x.mean(axis=1).astype(np.float64)
    n = len(mono)
    if n <= win_n:
        return float(np.sqrt(np.mean(mono**2))) if n else 0.0, 0
    csum = np.concatenate([[0.0], np.cumsum(mono**2)])
    starts = np.arange(0, n - win_n + 1, hop_n)
    rms = np.sqrt((csum[starts + win_n] - csum[starts]) / win_n)
    i = int(np.argmax(rms))
    return float(rms[i]), int(starts[i])


def viability(entry: dict, win_n: int, hop_n: int) -> dict:
    """分离人声存在性：最佳 20s 窗的人声 RMS / 原混 RMS。过低 → A/B 听感不可分（废试次）。"""
    voc = read_stereo(entry["vocals"])
    orig = read_stereo(entry["orig"])
    n = min(len(voc), len(orig))
    voc_rms, _ = best_window(voc[:n], win_n, hop_n)
    orig_rms, _ = best_window(orig[:n], win_n, hop_n)
    ratio = voc_rms / max(orig_rms, 1e-9)
    return {"voc_win_rms": round(voc_rms, 6), "orig_win_rms": round(orig_rms, 6),
            "ratio": round(ratio, 3), "viable": ratio >= VIABILITY_RATIO_MIN}


def measure_lufs_ffmpeg(x: np.ndarray, probe: Path) -> tuple[float, float]:
    """写临时 wav 后用 ffmpeg ebur128 测 integrated LUFS 与 true peak（dBFS 口径）。"""
    sf.write(str(probe), x, RATE, subtype="FLOAT")
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(probe),
           "-filter_complex", "ebur128=peak=true", "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg ebur128 失败: {proc.stderr[-500:]}")
    tail = proc.stderr.split("Summary:")[-1]
    mi = re.search(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", tail)
    mp = re.search(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", tail)
    if not mi:
        raise RuntimeError(f"ebur128 摘要解析失败: {tail[-300:]}")
    return float(mi.group(1)), (float(mp.group(1)) if mp else float("nan"))


def true_peak_dbtp_scipy(x: np.ndarray) -> float:
    """4× 过采样 true peak 估计（verify 侧免 ffmpeg 口径，ITU-R BS.1770 风格）。"""
    from scipy.signal import resample_poly
    y = resample_poly(x, 4, 1, axis=0)
    return float(20.0 * np.log10(max(float(np.abs(y).max()), 1e-12)))


class Selector:
    """确定性选段 + viability 闸 + 回填。所有中间量进 selection_audit 台账。"""

    def __init__(self, manifest: dict, sep_index: dict, words: dict):
        self.manifest = manifest
        self.sep_index = sep_index
        self.words = words
        self.stats: dict[str, dict] = {}
        self.selected_ids: set[str] = set()

    def ensure_stats(self, cids: list[str]) -> None:
        win_n, hop_n = int(WINDOW_S * RATE), int(WINDOW_HOP_S * RATE)
        for cid in cids:
            if cid not in self.stats:
                self.stats[cid] = viability(self.sep_index["clips"][cid], win_n, hop_n)
                s = self.stats[cid]
                log(f"  viability {cid}: ratio={s['ratio']:.3f} "
                    f"{'PASS' if s['viable'] else 'FAIL(<%.2f 剔除)' % VIABILITY_RATIO_MIN}")

    def sort_key(self, c: dict):
        cid = c["clip_id"]
        return (-int(self.words.get(cid) or 0),
                -round(self.stats[cid]["voc_win_rms"], 6), cid)

    def pool(self, source: str, lang: str, layer: str) -> list[dict]:
        return [c for c in self.manifest.values()
                if c["source"] == source and c["language"] == lang and c["layer"] == layer
                and c["clip_id"] not in self.selected_ids]

    def pick(self) -> tuple[list[dict], dict]:
        audit: dict = {}
        picked: list[dict] = []
        for stratum, source, lang, layer, k in STRAATA:
            cands = self.pool(source, lang, layer)
            self.ensure_stats([c["clip_id"] for c in cands])
            viable = [c for c in cands if self.stats[c["clip_id"]]["viable"]]
            excluded = [c["clip_id"] for c in cands if not self.stats[c["clip_id"]]["viable"]]
            chosen = sorted(viable, key=self.sort_key)[:k]
            backfill: list[dict] = []
            if len(chosen) < k:
                need = k - len(chosen)
                for adj in BACKFILL_LAYER_ORDER:
                    if adj == layer:
                        continue
                    bp = self.pool(source, lang, adj)
                    self.ensure_stats([c["clip_id"] for c in bp])
                    bp_v = [c for c in bp if self.stats[c["clip_id"]]["viable"]]
                    take = sorted(bp_v, key=lambda c: (-round(self.stats[c["clip_id"]]["voc_win_rms"], 6),
                                                       c["clip_id"]))[:need]
                    if take:
                        backfill = take
                        log(f"  {stratum}: viable {len(viable)}/{len(cands)} 不足配额 {k}，"
                            f"从 {adj} 回填 {[c['clip_id'] for c in take]}")
                        break
            entry = {
                "quota": k, "source": source, "language": lang, "layer": layer,
                "candidates": {c["clip_id"]: dict(self.stats[c["clip_id"]],
                                  words=self.words.get(c["clip_id"]))
                               for c in sorted(cands, key=lambda c: c["clip_id"])},
                "excluded_not_viable": excluded,
                "chosen": [c["clip_id"] for c in chosen],
                "backfill": {c["clip_id"]: self.stats[c["clip_id"]]["ratio"] for c in backfill},
            }
            audit[stratum] = entry
            for c in chosen:
                picked.append({"clip_id": c["clip_id"], "stratum": stratum, "source": source,
                               "language": lang, "layer": layer, "backfill_from": None})
                self.selected_ids.add(c["clip_id"])
            for c in backfill:
                picked.append({"clip_id": c["clip_id"], "stratum": f"{stratum}_backfill",
                               "source": source, "language": lang, "layer": c["layer"],
                               "backfill_from": f"{stratum}:{layer}缺口"})
                self.selected_ids.add(c["clip_id"])
        return picked, audit


def process_one(item: dict, entry: dict, stats: dict, probe: Path) -> dict:
    """单段制备：切窗 → 对白再平衡 → 母带响度对齐 → true peak 联合防护 → 端部渐变 → 落盘。"""
    cid = item["clip_id"]
    orig = read_stereo(entry["orig"])
    voc = read_stereo(entry["vocals"])
    bga = read_stereo(entry["bgA"])
    n = min(len(orig), len(voc), len(bga))
    trimmed = [len(orig), len(voc), len(bga)] != [n, n, n]
    orig, voc, bga = orig[:n], voc[:n], bga[:n]

    win_n = int(WINDOW_S * RATE)
    voc_rms, ws = best_window(voc, win_n, int(WINDOW_HOP_S * RATE))
    orig_rms, _ = best_window(orig, win_n, int(WINDOW_HOP_S * RATE))
    A = orig[ws:ws + win_n].copy()
    Voc = voc[ws:ws + win_n].copy()
    Bed = bga[ws:ws + win_n].copy()

    # 恒等性核对：B0 = Bed + Voc 应≈ A（票05 bit 级恒等的现场复核）
    b0 = Bed + Voc
    ident_max = float(np.abs(b0 - A).max())

    # 1) 对白再平衡（设计要点见文件头：固定 +2 dB，模拟换声链"新对白"落位残差）
    g = 10.0 ** (G_DIALOG_DB / 20.0)
    b1 = Bed + Voc * g
    lufs_a, tp_a_ff = measure_lufs_ffmpeg(A, probe)
    lufs_b0, _ = measure_lufs_ffmpeg(b0, probe)
    lufs_b1, _ = measure_lufs_ffmpeg(b1, probe)

    # 2) 成片响度配平：B 的 integrated LUFS 对齐 A（主增益级）
    master_db = round(lufs_a - lufs_b1, 3) if MASTER_ALIGN else 0.0
    B = b1 * (10.0 ** (master_db / 20.0))

    # 3) true peak 联合防护：A、B 同一增益，保持相对电平（目标 −1.2 dBTP，验收线 −1.0 留余量）
    tp_a = true_peak_dbtp_scipy(A)
    tp_b = true_peak_dbtp_scipy(B)
    guard_db = round(min(0.0, TP_GUARD_TARGET_DBTP - tp_a, TP_GUARD_TARGET_DBTP - tp_b), 3)
    if guard_db < 0:
        f = 10.0 ** (guard_db / 20.0)
        A *= f
        B *= f
        tp_a += guard_db
        tp_b += guard_db

    # 4) 端部 10ms sin² 渐变（A/B 同施，不构成线索）
    nf = int(RATE * FADE_MS / 1000)
    ramp = np.sin(np.pi / 2 * np.arange(nf) / nf).astype(np.float32) ** 2
    for x in (A, B):
        x[:nf] *= ramp[:, None]
        x[-nf:] *= ramp[::-1][:, None]

    # 5) 终态响度留档
    lufs_a_f, _ = measure_lufs_ffmpeg(A, probe)
    lufs_b_f, _ = measure_lufs_ffmpeg(B, probe)

    # 6) A≠B 数值核验（排除端部渐变的中段差异）
    m = nf * 2
    d = (A[m:-m] - B[m:-m]).astype(np.float64)
    diff_max = float(np.abs(d).max())
    diff_rms_ratio = float(np.sqrt(np.mean(d**2)) / max(np.sqrt(np.mean(A[m:-m].astype(np.float64)**2)), 1e-12))
    if diff_max <= 1e-4:
        raise RuntimeError(f"{cid}: A/B 中段无差异（max={diff_max:.2e}），违反 A≠B 设计底线")

    slug = LAYER_SLUG[item["layer"]]
    wtag = f"w{int(ws / RATE * 1000):05d}ms"
    name_a = f"A__orig__{item['language']}__{slug}__{cid}__{wtag}.wav"
    name_b = f"B__{ARM_TAG}__{item['language']}__{slug}__{cid}__{wtag}.wav"
    sf.write(str(OUT_DIR / name_a), A, RATE, subtype="FLOAT")
    sf.write(str(OUT_DIR / name_b), B, RATE, subtype="FLOAT")

    # 近透明标记：分离床几乎空（对白主导混音，voc/orig 比→1）时，+2dB 再平衡会被母带对齐
    # 数学上抵消，重建≈原混，听不出差异属链路"该段透明"的正常结果，不是试次缺陷。
    near_transparent = diff_rms_ratio < 0.02

    rec = {
        "clip_id": cid, "stratum": item["stratum"], "source": item["source"],
        "language": item["language"], "layer": item["layer"], "layer_slug": slug,
        "backfill_from": item["backfill_from"],
        "A_file": name_a, "B_file": name_b,
        "duration_s": round(win_n / RATE, 3), "window_start_s": round(ws / RATE, 3),
        "length_trimmed_to_min": trimmed,
        "voc_win_rms": round(voc_rms, 6), "orig_win_rms": round(orig_rms, 6),
        "voc_orig_ratio": round(voc_rms / max(orig_rms, 1e-9), 3),
        "identity_check_max_abs_diff": ident_max,
        "g_dialog_db": G_DIALOG_DB,
        "lufs_A": round(lufs_a, 2), "lufs_B0_identity": round(lufs_b0, 2),
        "lufs_B_before_master": round(lufs_b1, 2), "master_gain_db": master_db,
        "lufs_A_final": round(lufs_a_f, 2), "lufs_B_final": round(lufs_b_f, 2),
        "tp_A_dbtp": round(tp_a, 2), "tp_B_dbtp": round(tp_b, 2),
        "tp_ffmpeg_A_at_measure": round(tp_a_ff, 2),
        "tp_guard_db": guard_db, "tp_guard_target_dbtp": TP_GUARD_TARGET_DBTP,
        "fade_ms": FADE_MS,
        "expected_audibility": "near_transparent" if near_transparent else "normal",
        "ab_diff_mid_max_abs": diff_max, "ab_diff_mid_rms_ratio": round(diff_rms_ratio, 6),
    }
    log(f"  {cid} [{item['stratum']}] win@{rec['window_start_s']}s g=+{G_DIALOG_DB}dB "
        f"master={master_db:+.2f}dB guard={guard_db:+.2f}dB TP A/B={tp_a:.2f}/{tp_b:.2f} "
        f"LUFS差最终={lufs_b_f - lufs_a_f:+.2f} A≠B rms比={diff_rms_ratio:.4f}"
        f"{' [near_transparent]' if near_transparent else ''}")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser(description="票07 ABX 加工片制备（分离对照臂）")
    ap.add_argument("--dry-run", action="store_true", help="只选段打印，不写音频")
    a = ap.parse_args()

    manifest = load_manifest()
    sep_index = load_sep_index()
    words = load_round1_words()
    log(f"manifest cut={len(manifest)} 分离索引={len(sep_index['clips'])} 票05词数表={len(words)}段")

    sel = Selector(manifest, sep_index, words)
    picked, audit = sel.pick()
    by_id = {p["clip_id"]: p for p in picked}
    log("选段结果（目标 18 段）:")
    for p in picked:
        tag = f" ←{p['backfill_from']}" if p["backfill_from"] else ""
        log(f"  {p['stratum']:24s} {p['clip_id']}{tag}")
    if len(by_id) != len(picked):
        raise RuntimeError("选段出现重复 clip_id")
    if len(picked) != 18:
        raise RuntimeError(f"选段总数 {len(picked)} != 18（含回填应恰好 18）")

    if a.dry_run:
        return 0

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)
    TMP_DIR.mkdir(parents=True)
    probe = TMP_DIR / "lufs_probe.wav"

    t0 = time.perf_counter()
    records = []
    for i, item in enumerate(picked, 1):
        log(f"({i}/{len(picked)}) 制备 {item['clip_id']} …")
        records.append(process_one(item, sep_index["clips"][item["clip_id"]],
                                   sel.stats[item["clip_id"]], probe))

    index_out = {
        "arm": ARM_TAG,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rate": RATE, "window_s": WINDOW_S, "window_hop_s": WINDOW_HOP_S,
        "g_dialog_db": G_DIALOG_DB,
        "tp_guard_target_dbtp": TP_GUARD_TARGET_DBTP,
        "viability_ratio_min": VIABILITY_RATIO_MIN,
        "fade_ms": FADE_MS,
        "design_note": (
            "票05 已证 背景A+分离人声=原混（bit 级），故'B0 对齐原混 LUFS'恒等于 0 dB、照做即完全重建。"
            "本实现按票面允许的'更干净设计'条款：g=对白再平衡固定 +2.0 dB（真实换声链新对白 LUFS 落位残差量级），"
            "再做成片 integrated LUFS 对齐 A（主增益级）与 true peak 联合防护（A/B 同增益保相对电平）。"
            "A/B 差异=分离伪影+对白电平再平衡+母带响度处理，与真实换声链分离/回混部分同构。"
        ),
        "substitute_note": (
            "今晚无 CosyVoice：主臂（分离床+真克隆回混）与克隆对照臂缺席，克隆版次日补；"
            "本包只制备分离对照臂（F1 复审修正版），归因执行留 F1 确认后（R1）。"
        ),
        "separation_note": (
            "亮剑 17 段与 Sintel 30 段的三轨产物（orig/vocals/bgA）在 media/separated/ 全部在位，"
            "本票未新跑 GPU 分离。"
        ),
        "viability_note": (
            "选段 viability 闸：最佳 20s 窗 人声RMS/原混RMS < 0.10 剔除（分离人声近无声 → A/B 不可分 → 废试次）。"
            "触发：bili_liangjian_ep02_g0087_g0088 ratio=0.016（该段分离人声近无声；其'对白干净'为 provisional "
            "标注，晨间听审可一并核实）→ 缺口按层邻接序由亮剑 ep01 音效压对白回填，zh 对白干净层=5+1 回填。"
        ),
        "near_transparent_note": (
            "expected_audibility=near_transparent 的试次（A/B 中段差异 rms 比 < 0.02）：分离床几乎空的对白主导段，"
            "+2dB 对白再平衡被母带响度对齐数学抵消，重建≈原混——听不出差异是'该段链路透明'的正确感知，"
            "晨间分析对这类试次的 50% 命中应按'不可判'解读，不计入链路缺陷。"
        ),
        "selection_audit": audit,
        "stimuli": records,
    }
    (OUT_DIR / "stimuli_index.json").write_text(
        json.dumps(index_out, ensure_ascii=False, indent=1), encoding="utf-8")
    shutil.rmtree(TMP_DIR, ignore_errors=True)
    log(f"完成：{len(records)} 对 A/B 落盘 media/abx/，耗时 {time.perf_counter() - t0:.0f}s；"
        f"台账 stimuli_index.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
