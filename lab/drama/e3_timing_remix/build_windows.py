# -*- coding: utf-8 -*-
"""E3 步骤1:构造原句窗(带真实语音起止)。
- en2zh: Sintel 官方 SRT(0-888s) × 中置通道 whisper 词级时间戳 → 窗=首词起~末词止
- zh2en: 亮剑 16k 切片 whisper 分段 → 窗=分段起止(前后留 0.05s)
run: <repo>/.venv-lab/Scripts/python.exe build_windows.py [--sintel|--liangjian]
输出: e3_windows.json [{dir,id,clip,t0,t1,win_dur,text,logprob}]
"""
import argparse
import json
import sys
from pathlib import Path

from faster_whisper import WhisperModel

HERE = Path(__file__).resolve().parent
LAB = HERE.parent
SR = 16000
CACHE_SINTEL = HERE / "_sintel_center_asr.json"
CACHE_LJ = HERE / "_liangjian_asr.json"
SRT = LAB / "media" / "blender" / "sintel_en.srt"
CENTER = LAB / "media" / "blender" / "sintel_center.wav"
CLIPS = LAB / "media" / "clips"


def ts(sec):
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def parse_srt(p):
    import re
    blocks = re.split(r"\r?\n\r?\n", p.read_text(encoding="utf-8-sig").strip())
    out = []
    for b in blocks:
        lines = [x for x in b.splitlines() if x.strip()]
        if len(lines) < 2:
            continue
        mm = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", lines[1])
        if not mm:
            continue
        g = list(map(int, mm.groups()))
        t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        t1 = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        out.append((t0, t1, " ".join(lines[2:])))
    return out


def _resample16k(src, dst):
    import numpy as np
    import soundfile as sf
    from math import gcd
    from scipy.signal import resample_poly
    x, sr = sf.read(str(src), always_2d=True)
    mono = x.mean(axis=1)
    g = gcd(SR, sr)
    y = resample_poly(mono, SR // g, sr // g)
    peak = float(np.max(np.abs(y))) or 1.0
    sf.write(str(dst), (y / peak * 0.95 * 32767).astype(np.int16),
             SR, subtype="PCM_16")


def transcribe_file(model, path, lang, words=False):
    segs, _ = model.transcribe(str(path), language=lang, beam_size=5,
                               vad_filter=True, word_timestamps=words)
    res = []
    for s in segs:
        it = {"start": s.start, "end": s.end, "text": s.text.strip(),
              "logprob": float(s.avg_logprob)}
        if words:
            it["words"] = [{"s": w.start, "e": w.end, "w": w.word}
                           for w in (s.words or [])]
        res.append(it)
    return res


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--sintel", action="store_true")
    ap.add_argument("--liangjian", action="store_true")
    args = ap.parse_args()
    model = WhisperModel("small", device="cpu", compute_type="int8")
    out = []

    if args.sintel:
        if CACHE_SINTEL.is_file():
            segs = json.loads(CACHE_SINTEL.read_text(encoding="utf-8"))
        else:
            print("[whisper] sintel center 888s …", flush=True)
            segs = transcribe_file(model, CENTER, "en", words=True)
            CACHE_SINTEL.write_text(json.dumps(segs, ensure_ascii=False),
                                    encoding="utf-8")
        allw = [w for s in segs for w in s["words"]]
        cues = parse_srt(SRT)
        cues = [c for c in cues if c[0] < 885]
        n = 0
        for i, (t0, t1, text) in enumerate(cues):
            dur = t1 - t0
            if not (1.2 <= dur <= 8.0) or len(text.split()) < 3:
                continue
            # 邻句间隙(非重叠对白)
            prev = cues[i - 1] if i else None
            nxt = cues[i + 1] if i + 1 < len(cues) else None
            if prev and t0 - prev[1] < 0.15:
                continue
            if nxt and nxt[0] - t1 < 0.15:
                continue
            # 词起点落在放宽范围内的都算(词尾可稍越出)
            ws = [w for w in allw
                  if t0 - 0.35 <= w["s"] <= t1 + 0.6]
            if len(ws) < 2:
                print(f"[drop] cue{i+1} 词数不足: {text!r}")
                continue
            a, b = min(ws[0]["s"], t0), max(ws[-1]["e"], ws[0]["s"] + 0.5)
            win = b - a
            if win < 0.9:
                print(f"[drop] cue{i+1} 语音窗过短 {win:.2f}s")
                continue
            n += 1
            out.append({"dir": "en2zh", "id": f"E{n:02d}", "clip": "sintel",
                        "t0": round(a, 2), "t1": round(b, 2),
                        "win_dur": round(win, 2), "text": text,
                        "asr_text": "".join(
                            f"{w['w']}" for w in ws).strip(), "logprob": None})
            print(f"[en] E{n:02d} {ts(a)} win={win:.2f}s {text[:60]}")
        print(f"en2zh 窗: {n}")

    if args.liangjian:
        cache = json.loads(CACHE_LJ.read_text(encoding="utf-8")) if CACHE_LJ.is_file() else {}
        lj_clips = sorted(p.name.replace("_16k.wav", "")
                          for p in CLIPS.glob("bili_liangjian_*_16k.wav"))
        per_clip = []
        for clip in lj_clips:
            if clip not in cache:
                # 人声 stem 做 ASR(对话场景分离可靠,混音太脏错字多)
                stem = CLIPS.parent / "separated" / "stems" / (
                    f"{clip}_44k1_(Vocals)_melband_roformer_instvox_duality_v2.wav")
                print(f"[whisper] {clip} (vocals stem) …", flush=True)
                tmp = HERE / "_tmp16k.wav"
                _resample16k(stem, tmp)
                cache[clip] = transcribe_file(model, tmp, "zh", words=False)
                CACHE_LJ.write_text(json.dumps(cache, ensure_ascii=False),
                                    encoding="utf-8")
            segs = cache[clip]
            for j, s in enumerate(segs):
                dur = s["end"] - s["start"]
                if not (1.2 <= dur <= 8.0):
                    continue
                prev = segs[j - 1] if j else None
                nxt = segs[j + 1] if j + 1 < len(segs) else None
                if prev and s["start"] - prev["end"] < 0.3:
                    continue
                if nxt and nxt["start"] - s["end"] < 0.3:
                    continue
                t = s["text"].replace(" ", "")
                if len(t) < 6:
                    continue
                # 绝对起点: 文件名第一个 gXXXX 段 ×30s(ep01_g0036_g0037 → 36*30)
                import re as _re
                base = int(_re.findall(r"g(\d{4})", clip)[0]) * 30
                per_clip.append({
                    "dir": "zh2en", "clip": clip,
                    "abs0": round(base + s["start"] + 0.05, 2),
                    "t0": round(s["start"] + 0.05, 2),
                    "t1": round(s["end"] - 0.05, 2),
                    "win_dur": round(dur - 0.10, 2),
                    "text": t, "logprob": round(s["logprob"], 3)})
        # 每 clip 最多 5 条(logprob 优),全局 30 条
        byc = {}
        for it in per_clip:
            byc.setdefault(it["clip"], []).append(it)
        pick = []
        for clip, items in byc.items():
            items.sort(key=lambda x: -x["logprob"])
            pick.extend(items[:5])
        pick.sort(key=lambda x: -x["logprob"])
        pick = pick[:30]
        pick.sort(key=lambda x: (x["clip"], x["t0"]))
        for k, it in enumerate(pick, 1):
            it["id"] = f"Z{k:02d}"
            it.pop("abs0")
            out.append(it)
            print(f"[zh] Z{k:02d} {it['clip'][-12:]} win={it['win_dur']:.2f}s "
                  f"lp={it['logprob']} {it['text'][:50]}")
        print(f"zh2en 窗: {len(pick)}")

    dest = HERE / "e3_windows.json"
    if dest.is_file():
        old = json.loads(dest.read_text(encoding="utf-8"))
        have = {x["dir"] for x in out}
        out = [y for y in old if y["dir"] not in have] + out
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                    encoding="utf-8")
    print("e3_windows.json:", len(out))


if __name__ == "__main__":
    main()
