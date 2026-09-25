# -*- coding: utf-8 -*-
"""参考音质量实验 步骤1:构造 7 个条件的参考音 + 转写 → refq_pairs.json
run: <repo>/.venv-lab/Scripts/python.exe refq_prep.py
条件矩阵:
  zh 博主: R1=ref01 25s净(基线) R2=ref01 前6s净 R3=ref01 25s+混入Sintel M&E音乐(SNR≈6dB,模拟脏分离)
           R4=ref02 25s净(换源稳定性)
  en Shaman: R5=3s脏(P03已有) R6=g0003 118-124.5s脏 R7=Shaman多句拼接~12s脏
held-out 合成句(不在任何参考里):
  zh: 说实话,我一开始也没想到事情会变成这个样子,但既然做了,就要把它做完。
  en: A dangerous quest for a lone hunter.
锚点(真声): zh=ref01 前8s; en=g0015 原stem "You are closer than you know."
"""
import json
import io
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from math import gcd
from faster_whisper import WhisperModel

HERE = Path(__file__).resolve().parent
SR = 16000
STEMS = HERE.parent / "media" / "separated" / "stems"
ME = HERE.parent / "media" / "blender" / "sintel_me.wav"
m = WhisperModel("small", device="cpu", compute_type="int8")

HELD = {
    "zh": "说实话,我一开始也没想到事情会变成这个样子,但既然做了,就要把它做完。",
    "en": "A dangerous quest for a lone hunter.",
}


def rd16(p):
    x, sr = sf.read(str(p), always_2d=True)
    mono = x.mean(axis=1)
    g = gcd(SR, sr)
    return resample_poly(mono, SR // g, sr // g).astype(np.float32)


def wr(cut, p):
    peak = float(np.max(np.abs(cut))) or 1.0
    sf.write(str(p), (cut / peak * 0.95 * 32767).astype(np.int16),
             SR, subtype="PCM_16")


def asr(p, lang):
    segs, _ = m.transcribe(str(p), language=lang, beam_size=5)
    return (" ".join if lang == "en" else "".join)(
        s.text for s in segs).strip()


def rms(x):
    return float(np.sqrt((x.astype(np.float64) ** 2).mean() + 1e-12))


def main():
    pairs = []
    ref01 = rd16(HERE / "yuanlian_chinese_01.wav")
    ref02 = rd16(HERE / "yuanlian_chinese_02.wav")
    tr01 = (HERE / "ref01_transcript.txt").read_text(encoding="utf-8").strip()

    # R1: ref01 25s 净(基线,复用逐字稿)
    p = HERE / "refq_R1_ref01full.wav"
    wr(ref01, p)
    pairs.append(dict(rid="R1", lang="zh", desc="25s净(基线)",
                      ref=str(p), ref_text=tr01))
    # R2: ref01 前 6s 净
    p = HERE / "refq_R2_ref01_6s.wav"
    cut = ref01[: int(6 * SR)]
    wr(cut, p)
    pairs.append(dict(rid="R2", lang="zh", desc="6s净",
                      ref=str(p), ref_text=asr(p, "zh")))
    # R3: ref01 全长 + Sintel M&E 音乐(SNR≈6dB 模拟脏分离)
    music = rd16(ME)
    rng = np.random.default_rng(20260925)
    s0 = rng.integers(0, max(1, len(music) - len(ref01)))
    mus = music[s0: s0 + len(ref01)]
    g = rms(ref01) / (rms(mus) * (10 ** (6 / 20)))   # 音乐压到 SNR≈6dB
    p = HERE / "refq_R3_ref01_dirty.wav"
    wr(ref01 + g * mus, p)
    pairs.append(dict(rid="R3", lang="zh", desc="25s脏(混音乐SNR6dB)",
                      ref=str(p), ref_text=tr01))
    # R4: ref02 25s 净
    p = HERE / "refq_R4_ref02.wav"
    wr(ref02, p)
    pairs.append(dict(rid="R4", lang="zh", desc="另一条25s净(换源)",
                      ref=str(p), ref_text=""))
    for it in pairs:
        if it["rid"] == "R4":
            it["ref_text"] = asr(p, "zh")

    def stem_cut(clip, a, b):
        x = rd16(STEMS / f"{clip}_44k1_(Vocals)_melband_roformer_instvox_duality_v2.wav")
        base = int(clip.split("g")[-1]) * 30
        return x[int((a - base) * SR): int((b - base) * SR)]

    # R5: 3s 脏(P03 同源)
    p = HERE / "refq_R5_en3s.wav"
    cut = stem_cut("blender_sintel_g0014", 445.7, 448.6)
    wr(cut, p)
    pairs.append(dict(rid="R5", lang="en", desc="3s脏(基线)",
                      ref=str(p), ref_text="I have failed."))
    # R6: g0003 118-124.5s 脏(Shaman 两句)
    p = HERE / "refq_R6_en6s.wav"
    cut = stem_cut("blender_sintel_g0003", 117.8, 124.6)
    wr(cut, p)
    pairs.append(dict(rid="R6", lang="en", desc="6.5s脏",
                      ref=str(p),
                      ref_text="You're a fool for traveling alone, "
                               "so completely unprepared. You're lucky "
                               "your blood's still flowing."))
    # R7: Shaman 多句拼接(g0014 445.7-448.6 + g0015 452.6-456.2 + 457.6-459.8 + 460.7-463.5)
    parts = [stem_cut("blender_sintel_g0014", 445.7, 448.6),
             stem_cut("blender_sintel_g0015", 452.6, 456.2),
             stem_cut("blender_sintel_g0015", 457.6, 459.8),
             stem_cut("blender_sintel_g0015", 460.7, 463.5)]
    gap = np.zeros(int(0.35 * SR), dtype=np.float32)
    concat = np.concatenate([x for part in parts for x in (part, gap)][:-1])
    p = HERE / "refq_R7_en_concat.wav"
    wr(concat, p)
    pairs.append(dict(rid="R7", lang="en", desc="~12s脏拼接",
                      ref=str(p),
                      ref_text="I have failed. You've only failed to see. "
                               "These are dragon lands, Sintel. "
                               "You are closer than you know."))
    # 锚点
    pa = HERE / "refq_anchor_zh.wav"
    wr(ref01[: int(8 * SR)], pa)
    pb = HERE / "refq_anchor_en.wav"
    wr(stem_cut("blender_sintel_g0015", 460.7, 463.5), pb)
    for it in pairs:
        it["held_text"] = HELD[it["lang"]]
        it["anchor"] = str(pa if it["lang"] == "zh" else pb)
        print(f"[ok] {it['rid']} {it['lang']} {it['desc']} ref_text={it['ref_text'][:40]!r}")
    io.open(HERE / "refq_pairs.json", "w", encoding="utf-8", newline="\n").write(
        json.dumps(pairs, ensure_ascii=False, indent=1))
    print("refq_pairs.json:", len(pairs), "条件")


if __name__ == "__main__":
    main()
