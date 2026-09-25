# -*- coding: utf-8 -*-
"""克隆臂 步骤1 v2:ASR 时间戳引导挑窗。
- zh(亮剑 solo 段):全段 whisper 带时间戳 → 词密度最高的 5~8s 窗
- en(Sintel):官方 SRT 单人短句定位,直接从人声 stem 切 3~6s
run: <repo>/.venv-lab/Scripts/python.exe pick_and_asr.py
"""
import json
from pathlib import Path

import soundfile as sf
from faster_whisper import WhisperModel
from scipy.signal import resample_poly
from math import gcd

HERE = Path(__file__).resolve().parent
STEMS = HERE.parent / "media" / "separated" / "stems"
OUT = HERE / "pairs.json"
SR = 16000


def stem_track(clip):
    p = STEMS / f"{clip}_44k1_(Vocals)_melband_roformer_instvox_duality_v2.wav"
    x, sr = sf.read(str(p), always_2d=True)
    mono = x.mean(axis=1)
    g = gcd(SR, sr)
    return resample_poly(mono, SR // g, sr // g).astype(np_float := __import__("numpy").float32)


def save_wav(cut, path):
    import numpy as np
    peak = float(np.max(np.abs(cut))) or 1.0
    sf.write(str(path), (cut / peak * 0.95 * 32767).astype(np.int16),
             SR, subtype="PCM_16")


def densest_window(segs, total, want=(5.0, 8.0)):
    """segs=[(t0,t1)] 语音时间戳;返回词密度(语音占比)最高的窗(秒)。"""
    import numpy as np
    best, bscore = None, -1
    for t0, t1 in segs:
        for w in want:
            s = max(0.0, min(t0, total - w))
            e = min(total, s + w)
            cov = sum(max(0.0, min(e, b) - max(s, a)) for a, b in segs)
            score = cov / (e - s)
            if score > bscore and cov >= 3.0:
                bscore, best = score, (s, e)
    return best


def main():
    import numpy as np
    m = WhisperModel("small", device="cpu", compute_type="int8")
    pairs = []
    # ---- zh:亮剑 solo 段,ASR 引导 ----
    for clip in ("bili_liangjian_ep01_g0036_g0037",
                 "bili_liangjian_ep02_g0064",
                 "bili_liangjian_ep01_g0047_g0048"):
        x = stem_track(clip)
        total = len(x) / SR
        sf.write(str(HERE / "_tmp.wav"), (x * 32767 * 0.95).astype(np.int16),
                 SR, subtype="PCM_16")
        segs_i, info = m.transcribe(str(HERE / "_tmp.wav"), language="zh",
                                    vad_filter=True, beam_size=5)
        segs = [(s.start, s.end) for s in segs_i]
        win = densest_window(segs, total) if segs else None
        if not win:
            print(f"[drop] {clip}: 无语音窗")
            continue
        s, e = win
        cut = x[int(s * SR):int(e * SR)]
        # 真转写该窗
        ref = HERE / f"ref_{clip}.wav"
        save_wav(cut, ref)
        segs_w, _ = m.transcribe(str(ref), language="zh", beam_size=5,
                                 vad_filter=False)
        text = "".join(s.text for s in segs_w).strip()
        if len(text) < 8:
            print(f"[drop] {clip}: 窗转写过短({text!r})")
            continue
        pairs.append({"pair_id": "", "lang": "zh", "clip": clip,
                      "ref": str(ref), "ref_text": text,
                      "ref_sec": round(e - s, 2)})
        print(f"[zh ok] {clip} {s:.1f}-{e:.1f}s {text[:50]}")
    # ---- en:Sintel SRT 单人短句(g0004=120-150s / g0014=420-450 / g0015=450-480) ----
    EN = [
        # (clip, 绝对起, 绝对止, 说明)  g0004 覆盖 120-150s
        ("blender_sintel_g0004", 134.8, 137.6, "I'm searching for someone."),
        ("blender_sintel_g0015", 445.7, 448.6, "I have failed."),
        ("blender_sintel_g0015", 460.7, 463.5, "You are closer than you know."),
    ]
    for clip, a, b, note in EN:
        base = int(clip.split("g")[-1]) * 30          # 该 clip 的绝对起点
        x = stem_track(clip)
        s, e = a - base - 0.25, b - base + 0.25       # clip 内相对时间,留边
        if s < 0 or e > len(x) / SR:
            print(f"[drop] {clip} {a}-{b}s 越界")
            continue
        cut = x[int(s * SR):int(e * SR)]
        ref = HERE / f"ref_{clip}_{int(a)}.wav"
        save_wav(cut, ref)
        segs_w, _ = m.transcribe(str(ref), language="en", beam_size=5)
        text = " ".join(x.text for x in segs_w).strip()
        pairs.append({"pair_id": "", "lang": "en", "clip": clip,
                      "ref": str(ref), "ref_text": text or note,
                      "ref_sec": round(e - s, 2)})
        print(f"[en ok] {clip} {note!r} asr={text!r}")
    for i, p in enumerate(pairs):
        p["pair_id"] = f"P{i+1:02d}"
    OUT.write_text(json.dumps(pairs, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"pairs.json: {len(pairs)} 对")


if __name__ == "__main__":
    main()
