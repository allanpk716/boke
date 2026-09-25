# -*- coding: utf-8 -*-
"""E1' 分离客观横评·首轮 —— 客观指标：对 separate.py 产物逐段计算四组数字。

run in .venv-lab:
    ../../../.venv-lab/Scripts/python objective.py            # 全部（读 separation_index.json）
    ../../../.venv-lab/Scripts/python objective.py --clips blender_sintel_g0002

四组数字（验收口径）:
  ① 人声轨 vs 真值参照（sintel_center.wav FC 中置，按切片时间窗取段重采样 44.1k mono）
     的 SI-SDR 与 Pearson 相关系数（自实现；零滞后与 ±60 样本微对齐两口径）。
  ② 背景A 与 原混−人声 的逐样本恒等性验证（工程验证，读盘重算，期望 bit 级相等）。
  ③ 背景A（相减纪律） vs 背景B（模型 instrumental）差异能量（相对背景A/背景B 的 dB）。
  ④ bleed 指数：faster-whisper small(cpu,int8) 对 原混/背景A/背景B 各自听写词数比
     （对白残留代理）+ F4 sanity：音乐重切片作负例测 Whisper 幻觉词数（vad 开/关两口径）。

输出: results/e1/separation_round1.json（run_first.py 据此渲染 md）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

import separate as S

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

MEDIA = S.MEDIA
SEP_DIR = S.SEP_DIR
REF_DIR = SEP_DIR / "ref"
ASR_DIR = SEP_DIR / "asr16k"
CENTER_WAV = MEDIA / "blender" / "sintel_center.wav"
RESULTS_DIR = S.DRAMA / "results" / "e1"
OUT_JSON = RESULTS_DIR / "separation_round1.json"
LAG_SEARCH = 60          # ±60 样本 ≈ ±1.36ms 微对齐搜索窗
EPS = 1e-12
LAYER_ORDER = ["对白干净", "音效压对白", "音乐重", "待听审标注"]
NEGATIVE_LAYERS = ["音乐重"]   # F4 负例层；不足 3 段时从待听审标注补（结果里如实标注）
NEGATIVE_TARGET = 3


def log(msg: str) -> None:
    print(f"[objective {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _ffmpeg_run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {' '.join(cmd)}\n{proc.stderr.strip()}")


# ---------------------------------------------------------------- 指标自实现（不依赖 museval）

def si_sdr(est: np.ndarray, ref: np.ndarray) -> float:
    """scale-invariant SDR（标准定义，自实现）。est/ref 同长 float；对整体增益不敏感。"""
    est = est - est.mean()
    ref = ref - ref.mean()
    alpha = np.dot(est, ref) / max(np.dot(ref, ref), EPS)
    target = alpha * ref
    noise = est - target
    return float(10.0 * np.log10(max(np.dot(target, target), EPS) / max(np.dot(noise, noise), EPS)))


def pearson(a: np.ndarray, b: np.ndarray) -> float:
    a = a - a.mean()
    b = b - b.mean()
    return float(np.dot(a, b) / max(np.sqrt(np.dot(a, a) * np.dot(b, b)), EPS))


def best_lag_metrics(est: np.ndarray, ref: np.ndarray, span: int = LAG_SEARCH) -> dict:
    """在 ±span 样本内找 |相关| 最大的滞后；返回零滞后与最优滞后两口径的 SI-SDR/相关。"""
    zero = {"lag": 0, "si_sdr": round(si_sdr(est, ref), 4), "corr": round(pearson(est, ref), 4)}
    best, best_abs = dict(zero), abs(zero["corr"])
    for lag in range(-span, span + 1):
        if lag == 0:
            continue
        e, r = (est[lag:], ref[:-lag]) if lag > 0 else (est[:lag], ref[-lag:])
        c = pearson(e, r)
        if abs(c) > best_abs:
            best = {"lag": lag, "si_sdr": round(si_sdr(e, r), 4), "corr": round(c, 4)}
            best_abs = abs(c)
    return {"zero_lag": zero, "best": best}


# ---------------------------------------------------------------- 参照与轨道读取

def center_ref_44k(entry: dict) -> np.ndarray:
    """取切片时间窗的中置参照段：与切片段同一 ffmpeg 方法（-ss 前置快 seek + 重采样）切 sintel_center.wav → 44.1k mono。"""
    REF_DIR.mkdir(parents=True, exist_ok=True)
    ref_path = REF_DIR / f"{Path(entry['orig']).stem}_center_44k1.wav"
    if not ref_path.exists():
        dur = entry["end"] - entry["start"]
        _ffmpeg_run([
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{entry['start']:.3f}", "-i", str(CENTER_WAV), "-t", f"{dur:.3f}",
            "-vn", "-acodec", "pcm_s16le", "-ar", str(S.RATE), "-ac", "1", str(ref_path),
        ])
    ref, sr = sf.read(ref_path, dtype="float32", always_2d=True)
    assert sr == S.RATE, f"参照段采样率 {sr} != {S.RATE}"
    return ref[:, 0]


def read_stereo(path: str) -> np.ndarray:
    x, sr = sf.read(path, dtype="float32", always_2d=True)
    assert sr == S.RATE, f"{path} 采样率 {sr} != {S.RATE}"
    return x


# ---------------------------------------------------------------- ②③ 各组指标

def identity_check(entry: dict) -> dict:
    """背景A vs 原混−人声：读盘重算，逐样本比（期望 bit 级相等）。"""
    orig = read_stereo(entry["orig"])
    voc = read_stereo(entry["vocals"])
    bga = read_stereo(entry["bgA"])
    n = min(len(orig), len(voc), len(bga))
    diff = orig[:n] - voc[:n] - bga[:n]
    max_abs = float(np.abs(diff).max()) if n else 0.0
    return {"max_abs_diff": max_abs, "identical": bool(max_abs == 0.0), "samples_compared": int(n)}


def ab_diff_energy(entry: dict) -> dict:
    """背景A（相减） vs 背景B（模型 instrumental）差异能量。"""
    bga = read_stereo(entry["bgA"])
    bgb = read_stereo(entry["bgB"])
    n = min(len(bga), len(bgb))
    a = bga[:n].astype(np.float64)
    b = bgb[:n].astype(np.float64)
    d = a - b
    ea, eb, ed = float(np.sum(a * a)), float(np.sum(b * b)), float(np.sum(d * d))
    return {
        "samples": int(n),
        "diff_vs_bgA_db": round(10 * np.log10(max(ed, EPS) / max(ea, EPS)), 2),
        "diff_vs_bgB_db": round(10 * np.log10(max(ed, EPS) / max(eb, EPS)), 2),
        "bgA_rms_dbfs": round(10 * np.log10(max(ea / (2 * n), EPS)), 2),
        "bgB_rms_dbfs": round(10 * np.log10(max(eb / (2 * n), EPS)), 2),
    }


# ---------------------------------------------------------------- ④ bleed（whisper 词数）

_WN: dict = {}


def whisper_model():
    if "m" not in _WN:
        from faster_whisper import WhisperModel

        _WN["m"] = WhisperModel("small", device="cpu", compute_type="int8")
        log("whisper small (cpu,int8) 已加载")
    return _WN["m"]


def norm_text(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()).strip()


def to16k_mono(src: str, dst: Path) -> Path:
    """三轨统一转 16k mono 再喂 whisper：转码路径一致，消除解码差异。源更新才重转。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists() or Path(src).stat().st_mtime > dst.stat().st_mtime:
        _ffmpeg_run(["ffmpeg", "-y", "-v", "error", "-i", src, "-vn",
                     "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", str(dst)])
    return dst


def transcribe_words(src: str, tag: str, vad: bool = True) -> dict:
    dst = ASR_DIR / f"{tag}_16k.wav"
    to16k_mono(src, dst)
    segs, info = whisper_model().transcribe(str(dst), language="en", vad_filter=vad)
    text = norm_text(" ".join(s.text for s in segs))
    return {"words": len(text.split()), "vad": vad,
            "audio_sec": round(info.duration, 2) if info else None}


def bleed_for_clip(cid: str, entry: dict) -> dict:
    r = {}
    for key, tag in (("orig", "orig"), ("bgA", "bgA_subtract"), ("bgB", "bgB_instrumental")):
        r[key] = transcribe_words(entry[key], f"{cid}_{tag}", vad=True)
    ow = r["orig"]["words"]
    r["bleed_bgA_ratio"] = round(r["bgA"]["words"] / ow, 4) if ow else None
    r["bleed_bgB_ratio"] = round(r["bgB"]["words"] / ow, 4) if ow else None
    return r


# ---------------------------------------------------------------- 汇总

def agg(values: list) -> dict:
    v = sorted(x for x in values if x is not None)
    if not v:
        return {"n": 0}
    mid = len(v) // 2
    median = v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2
    return {"n": len(v), "mean": round(sum(v) / len(v), 4), "median": round(median, 4),
            "min": round(v[0], 4), "max": round(v[-1], 4)}


def stratify(per_clip: list[dict]) -> dict:
    out = {}
    layers = [l for l in LAYER_ORDER if any(p["layer"] == l for p in per_clip)]
    for layer in layers + ["总体"]:
        rows = per_clip if layer == "总体" else [p for p in per_clip if p["layer"] == layer]
        out[layer] = {
            "n_clips": len(rows),
            "si_sdr_vs_center_best": agg([p["center"]["best"]["si_sdr"] for p in rows]),
            "si_sdr_vs_center_zero_lag": agg([p["center"]["zero_lag"]["si_sdr"] for p in rows]),
            "corr_vs_center_best": agg([p["center"]["best"]["corr"] for p in rows]),
            "bleed_bgA": agg([p["bleed"]["bleed_bgA_ratio"] for p in rows]),
            "bleed_bgB": agg([p["bleed"]["bleed_bgB_ratio"] for p in rows]),
            "bgA_words": agg([float(p["bleed"]["bgA"]["words"]) for p in rows]),
            "bgB_words": agg([float(p["bleed"]["bgB"]["words"]) for p in rows]),
            "orig_words": agg([float(p["bleed"]["orig"]["words"]) for p in rows]),
            "ab_diff_vs_bgA_db": agg([p["ab_diff"]["diff_vs_bgA_db"] for p in rows]),
            "identity_pass": sum(p["identity"]["identical"] for p in rows),
        }
    return out


def pick_negatives(manifest_clips: list[dict]) -> tuple[list[dict], str]:
    """F4 负例：优先音乐重层；不足 NEGATIVE_TARGET 从待听审标注顺序补（粗标未听审，如实记录）。"""
    neg = [c for c in manifest_clips if c["layer"] in NEGATIVE_LAYERS]
    note = f"音乐重层共 {len(neg)} 段"
    extra = []
    if len(neg) < NEGATIVE_TARGET:
        extra = [c for c in manifest_clips if c["layer"] == "待听审标注"][: NEGATIVE_TARGET - len(neg)]
        neg += extra
        note += (f"，不足 {NEGATIVE_TARGET} 段，从待听审标注顺序补 {len(extra)} 段"
                 f"（{'+'.join(c['clip_id'] for c in extra)}；粗标未听审，负例结论按弱证据对待）")
    return neg[:NEGATIVE_TARGET], note


# ---------------------------------------------------------------- 主流程

def run(only: list[str] | None = None) -> dict:
    index = S.load_index()
    manifest_clips = S.load_manifest()
    if only:
        ids = {c["clip_id"] for c in manifest_clips} & set(only)
        manifest_clips = [c for c in manifest_clips if c["clip_id"] in ids]
    entries = [(c, index["clips"][c["clip_id"]]) for c in manifest_clips
               if S.products_exist(index["clips"].get(c["clip_id"], {}))]
    missing = sorted({c["clip_id"] for c in manifest_clips} - {c["clip_id"] for c, _ in entries})
    if missing:
        log(f"警告: {len(missing)} 段缺分离产物，跳过: {missing[:5]}{'…' if len(missing) > 5 else ''}")
    log(f"待评估 {len(entries)} 段")

    per_clip = []
    for i, (c, e) in enumerate(entries, 1):
        cid = c["clip_id"]
        t0 = time.perf_counter()
        voc = read_stereo(e["vocals"])
        ref = center_ref_44k(e)
        n = min(len(voc), len(ref))
        voc_mono = (voc[:n, 0] + voc[:n, 1]).astype(np.float32) / 2.0  # L+R 均值；SI-SDR/相关对增益不敏感
        row = {
            "clip_id": cid,
            "layer": c["layer"],
            "provisional": c.get("provisional", False),
            "start": e["start"], "end": e["end"],
            "files": {k: e[k] for k in ("orig", "vocals", "bgA", "bgB")},
            "vocals_peak": round(e.get("vocals_peak", float(np.abs(voc).max())), 4),
            "center": best_lag_metrics(voc_mono, ref[:n]),
            "identity": identity_check(e),
            "ab_diff": ab_diff_energy(e),
        }
        row["bleed"] = bleed_for_clip(cid, e)
        per_clip.append(row)
        log(f"({i}/{len(entries)}) {cid} [{row['layer']}]: si_sdr={row['center']['best']['si_sdr']} "
            f"corr={row['center']['best']['corr']} lag={row['center']['best']['lag']} "
            f"identity={'OK' if row['identity']['identical'] else 'FAIL'} "
            f"bleed bgA/bgB={row['bleed']['bleed_bgA_ratio']}/{row['bleed']['bleed_bgB_ratio']} "
            f"({time.perf_counter() - t0:.1f}s)")

    neg_clips, neg_note = pick_negatives(manifest_clips)
    negatives = []
    for c in neg_clips:
        e = index["clips"].get(c["clip_id"], {})
        if not S.products_exist(e):
            negatives.append({"clip_id": c["clip_id"], "layer": c["layer"], "error": "缺分离产物"})
            continue
        b = bleed_for_clip(c["clip_id"], e)
        probe = {k: transcribe_words(e[k], f"{c['clip_id']}_{k}_noVAD", vad=False) for k in ("bgA", "bgB")}
        negatives.append({"clip_id": c["clip_id"], "layer": c["layer"], "bleed": b,
                          "no_vad_probe_bgA": probe["bgA"], "no_vad_probe_bgB": probe["bgB"]})
        log(f"负例 {c['clip_id']} [{c['layer']}]: orig={b['orig']['words']} bgA={b['bgA']['words']} "
            f"bgB={b['bgB']['words']}（vad 关: bgA={probe['bgA']['words']} bgB={probe['bgB']['words']}）")

    strata = stratify(per_clip)
    tot = strata.get("总体", {})
    idents = [p["identity"] for p in per_clip]
    n_fail = sum(not x["identical"] for x in idents)
    conclusions = {
        "identity": (
            f"相减恒等成立：{len(per_clip)} 段全部逐样本 bit 级相等（max|Δ|=0）"
            if per_clip and n_fail == 0 else
            f"相减恒等失败：{n_fail}/{len(per_clip)} 段有差（max|Δ| 上限 "
            f"{max((x['max_abs_diff'] for x in idents), default=0):.2e}）—— 检查长度对齐与写盘削波"),
        "subtract_vs_instrumental": (
            f"背景A与背景B差异能量（相对背景A）总体均值 {tot.get('ab_diff_vs_bgA_db', {}).get('mean')} dB；"
            f"bleed 均值 相减={tot.get('bleed_bgA', {}).get('mean')} vs 模型 instrumental={tot.get('bleed_bgB', {}).get('mean')}"),
        "f4_negatives": neg_note,
    }

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "round": "separation_round1",
        "separation": {
            "model": S.MODEL_NAME,
            "model_file_dir": str(S.MODELS_DIR),
            "normalization_threshold": 1.0,
            "note": "normalization_threshold=1.0 保三轨同幅度尺度（默认 0.9 会压低输入且不回补，污染相减背景）",
        },
        "reference": {
            "file": str(CENTER_WAV),
            "protocol": "FC 中置 48k mono，按切片 start/end 用与切片相同的 ffmpeg 方法（-ss 前置）取段重采样 44.1k mono；"
                        "人声轨取 L+R 均值；SI-SDR/Pearson 自实现（未用 museval），对整体增益不敏感",
            "lag_search_samples": LAG_SEARCH,
        },
        "asr": {"model": "small", "device": "cpu", "compute_type": "int8",
                "language": "en", "vad_filter": True,
                "protocol": "三轨统一 ffmpeg 转 16k mono 后转写；词数=规范化文本分词数；bleed=背景词数/原混词数"},
        "per_clip": per_clip,
        "stratified": strata,
        "f4_negatives": {"note": neg_note, "clips": negatives},
        "conclusions": conclusions,
        "clips_skipped_missing_products": missing,
    }
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    log(f"结果写入 {OUT_JSON}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="E1' 首轮客观指标")
    ap.add_argument("--clips", default="", help="逗号分隔 clip_id 子集；缺省=全部")
    a = ap.parse_args()
    only = [x.strip() for x in a.clips.split(",") if x.strip()]
    out = run(only=only or None)
    failed = [p for p in out["per_clip"] if not p["identity"]["identical"]]
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
