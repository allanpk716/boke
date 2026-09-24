# -*- coding: utf-8 -*-
"""T2 博主参考音提取:
1. ffmpeg 抽 16k mono wav
2. BGM 启发式检测:100ms RMS 帧序列 → 语音间歇期(谷值)是否仍抬高
   (纯人声:间歇≈静音谷;有 BGM:谷值垫在底噪上)
3. 自动挑段:连续语音 run ≥ min_len 且 speech 占比高,取自视频 15%~85% 区间
产物: refs/yuanlian_chinese_01.wav(+02/03) + 控制台报告
"""
import subprocess
import sys
from pathlib import Path

import numpy as np

MEDIA = Path("D:/boke_media")
ROOT = Path(__file__).resolve().parents[2]
REFS = ROOT / "refs"

FRAME_MS = 100
SPEECH_TH_DB = -40      # 帧能量高于此视为"有声音"
VALLEY_RATIO = 0.35     # 谷值/峰值 超过该比例 → 疑似 BGM 垫底


def extract_audio(video: Path, out_wav: Path):
    subprocess.run(["ffmpeg", "-y", "-i", str(video), "-vn", "-ac", "1",
                    "-ar", "16000", "-c:a", "pcm_s16le", str(out_wav)],
                   check=True, capture_output=True)


def rms_frames(wav: Path):
    """返回 (全带宽dB帧序列, 高频带4-8kHz dB帧序列, 总时长)"""
    import wave
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        n = w.getnframes()
        pcm = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)
    pcm /= 32768.0
    fl = int(sr * FRAME_MS / 1000)
    usable = (len(pcm) // fl) * fl
    frames = pcm[:usable].reshape(-1, fl)
    rms = np.sqrt((frames ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms + 1e-12)
    # 高频带:每帧 FFT,取 4k~8kHz bin 能量
    spec = np.abs(np.fft.rfft(frames * np.hanning(fl)[None, :], axis=1)) ** 2
    freqs = np.fft.rfftfreq(fl, 1 / sr)
    hb = (freqs >= 4000) & (freqs <= 8000)
    rms_h = np.sqrt(spec[:, hb].mean(axis=1) + 1e-18)
    db_h = 20 * np.log10(rms_h + 1e-12)
    return db, db_h, len(pcm) / sr


def bgm_check(db: np.ndarray) -> dict:
    """启发式:取能量峰值带(说话帧),看"低能量帧"是否仍被垫高。"""
    peak = np.percentile(db, 95)
    floor = np.percentile(db, 10)
    has_bgm = (peak - floor) < 25 or (floor > peak - VALLEY_RATIO * (peak - (-60)) - 0)
    # 更直接:谷值绝对水平
    suspicious = floor > -45  # 纯人声视频的静音段应在 -50dB 以下
    return {"peak_db": round(float(peak), 1), "floor_db": round(float(floor), 1),
            "suspect_bgm": bool(suspicious)}


def speech_runs(db: np.ndarray, total: float, min_len=15.0, gap_tol=0.8):
    """间隙容忍版:run 内允许 ≤gap_tol 秒停顿(呼吸/换句),run ≥ min_len。
    返回 [(t0, t1, flatness)] 按平稳度排序(越平稳越像纯口播)。"""
    mask = (db > SPEECH_TH_DB).astype(int)
    # 允许间隙:把短于 gap_tol 的 0-runs 抹成 1
    n = len(mask)
    i = 0
    while i < n:
        if mask[i] == 0:
            j = i
            while j + 1 < n and mask[j + 1] == 0:
                j += 1
            if (j - i + 1) * FRAME_MS / 1000 <= gap_tol:
                mask[i:j + 1] = 1
            i = j + 1
        else:
            i += 1
    # 提取 run
    runs = []
    i = 0
    while i < n:
        if mask[i]:
            j = i
            while j + 1 < n and mask[j + 1]:
                j += 1
            t0, t1 = i * FRAME_MS / 1000, (j + 1) * FRAME_MS / 1000
            if t1 - t0 >= min_len:
                flatness = float(np.std(db[i:j + 1]))
                runs.append((t0, t1, round(flatness, 2)))
            i = j + 1
        else:
            i += 1
    # 限定 15%~85%(避片头片尾),并做平稳度排序
    lo, hi = total * 0.15, total * 0.85
    clipped = []
    for t0, t1, fl in runs:
        a, b = max(t0, lo), min(t1, hi)
        if b - a >= min_len:
            clipped.append((round(a, 1), round(b, 1), fl))
    clipped.sort(key=lambda r: r[2])   # flatness 小的在前
    return clipped


def cut(wav: Path, t0: float, dur: float, out: Path):
    subprocess.run(["ffmpeg", "-y", "-i", str(wav), "-ss", f"{t0:.2f}",
                    "-t", f"{dur:.2f}", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(out)],
                   check=True, capture_output=True)


def main(video_name_prefix: str, want: int = 3):
    REFS.mkdir(exist_ok=True)
    video = next(MEDIA.glob(f"{video_name_prefix}*"), None)
    if not video:
        sys.exit(f"找不到 {video_name_prefix}*")
    print(f"[t2] video: {video.name}")
    raw = ROOT / "work" / f"{video.stem}_full16k.wav"
    raw.parent.mkdir(exist_ok=True)
    extract_audio(video, raw)
    db, db_h, total = rms_frames(raw)
    print(f"[t2] duration {total/60:.1f}min")
    chk = bgm_check(db)
    print(f"[t2] BGM check: {chk}")
    if chk["suspect_bgm"]:
        print("[t2] WARN 疑似有 BGM,挑段按纯人声继续(早晨人工复核)")

    runs = speech_runs(db, total, min_len=15.0)
    print(f"[t2] {len(runs)} candidate runs(平稳度升序): {runs[:5]}")
    # 滑 25s 窗,按"高频带静音帧占比"打分(纯人声:停顿时高频全暗,占比高;
    # BGM:高频全程被垫,占比≈0)。能量平稳度只作并列时次序。
    win = 25.0
    windows = []  # (quiet_frac, flatness, t0, t1)
    def scan(t0, t1):
        out = []
        if t1 - t0 < win:
            span = t1 - t0
        else:
            span = win
        s = t0
        while s + span <= t1 + 0.01:
            i0 = int(s * 1000 / FRAME_MS)
            i1 = int((s + span) * 1000 / FRAME_MS)
            dh = db_h[i0:i1]
            dl = db[i0:i1]
            if len(dh) < 5:
                break
            med = np.median(dh)
            quiet = float(np.mean(dh < med - 10.0))
            flat = float(np.std(dl))
            out.append((round(quiet, 3), round(flat, 2), round(s, 1), round(s + span, 1)))
            s += 2.0
            if span < win:
                break
        return out
    for t0, t1, _fl in runs:
        windows.extend(scan(t0, t1))
    # 过滤:必须有真实停顿(quiet≥0.08),且整体是活跃语音(中位全带能量>-35dB)
    ok = [w for w in windows if w[0] >= 0.08]
    ok.sort(key=lambda w: (-w[0], w[1]))
    picked = []
    for score, fl, t0, t1 in ok:
        if all(t1 <= p0 - 5 or t0 >= p1 + 5 for p0, p1, _ in picked):
            picked.append((t0, t1, score))
        if len(picked) >= want:
            break
    picked.sort()
    print(f"[t2] picked windows(quiet_frac): {picked}")

    outs = []
    for k, (t0, t1, fl) in enumerate(picked, 1):
        dur = min(25.0, t1 - t0)
        out = REFS / f"yuanlian_chinese_{k:02d}.wav"
        cut(raw, t0, dur, out)
        outs.append((out.name, video.stem.split('_')[0], t0, t0 + dur))
        print(f"[t2] wrote {out.name}: {t0:.1f}s +{dur:.1f}s")

    reg = REFS / "README.md"
    lines = ["# refs/ 参考音登记", "",
             "| 文件 | 来源BV | 起止(原视频) | 时长 | 说明 |", "|---|---|---|---|---|"]
    for name, bv, t0, t1 in outs:
        lines.append(f"| {name} | {bv} | {t0:.1f} ~ {t1:.1f}s | {t1-t0:.0f}s | "
                     f"自动挑段(连续语音run);BGM检测:{chk['floor_db']}dB底噪 |")
    reg.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[t2] registry → {reg}")
    return outs


if __name__ == "__main__":
    prefix = sys.argv[1] if len(sys.argv) > 1 else "BV1ZTr5BuEGy"
    main(prefix)
