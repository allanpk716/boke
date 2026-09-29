# -*- coding: utf-8 -*-
"""E3 步骤3:三臂合成+时长测量(主线 venv 只读)。
run: C:/WorkSpace/agent/boke/.venv/Scripts/python.exe e3_synth.py [--limit N]
- A 自由译: speed=1.0
- B 音节预算: 按校准速率(chars/s|words/s)挑候选 → 超窗±10% 则 speed 微调回炉
- C atempo兜底: A 的音频 ffmpeg atempo 压/拉进窗
时长口径: 静音裁剪后的语音跨度(能量阈值 1% 峰值)。
输出: out/*.wav + e3_synth_result.json
"""
import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
CLONE = HERE.parent / "clone_arm"

REF_ZH = CLONE / "yuanlian_chinese_01.wav"          # zh 目标嗓音(博主,refq R1 基线 4 分)
SYS = "You are a helpful assistant."
MARKER = "<|endofprompt|>"


def load_refs():
    refq = {x["rid"]: x for x in json.loads(
        (CLONE / "refq_pairs.json").read_text(encoding="utf-8"))}
    en = refq["R6"]                                    # en 目标嗓音(Shaman 6.5s, refq R6 4 分)
    zh_text = (CLONE / "ref01_transcript.txt").read_text(encoding="utf-8").strip()
    return {"en2zh": (REF_ZH, zh_text),
            "zh2en": (CLONE / en["ref"], en["ref_text"])}


def speech_span(x, sr, thr_rel=0.01, frame=0.02):
    """返回裁掉首尾静音后的时长(秒);全静音返回原长。"""
    n = int(sr * frame)
    if len(x) < n:
        return len(x) / sr, 1
    peak = float(np.abs(x).max()) or 1.0
    thr = peak * thr_rel
    loud = np.abs(x[: len(x) // n * n].reshape(-1, n)).max(axis=1) > thr
    if not loud.any():
        return len(x) / sr, 1
    a, b = np.argmax(loud), len(loud) - np.argmax(loud[::-1])
    seg = loud[a:b]
    # 突发段计数:响段被 >0.6s 静音隔开 → 多段(复读/多次起声的量化)
    gap = int(0.6 / frame)
    bursts, run = 1, 0
    for v in seg:
        if v:
            run = 0
        else:
            run += 1
            if run == gap:
                bursts += 1
    return (b - a) * frame, bursts


def wav_dur(p):
    with wave.open(str(p), "rb") as w:
        return w.getnframes() / w.getframerate()


def atempo(src, dst, factor):
    """ffmpeg atempo,支持 0.5-2.0 之外的链式。factor>1 加速。"""
    f, chain = factor, []
    while f > 2.0:
        chain.append(2.0)
        f /= 2.0
    while f < 0.5:
        chain.append(0.5)
        f /= 0.5
    chain.append(f)
    filt = ",".join(f"atempo={c:.6f}" for c in chain)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                    "-filter:a", filt, str(dst)], check=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ref-mode", choices=("dirty", "zh4all"), default="dirty",
                    help="dirty=各方向原生参考(zh=博主净/en=Shaman脏stem); "
                         "zh4all=两方向都用博主 zh 参考(跨语言)")
    ap.add_argument("--suffix", default="", help="输出目录/结果文件后缀")
    args = ap.parse_args()
    OUT = HERE / f"out{args.suffix}"
    OUT.mkdir(exist_ok=True)
    SUF = args.suffix

    import torchaudio
    REPO = Path("D:/boke_media/tools/CosyVoice")
    MODEL = Path("D:/boke_media/models/Fun-CosyVoice3-0.5B-2512")
    for sub in (REPO, REPO / "third_party" / "Matcha-TTS"):
        s = str(sub)
        if s not in sys.path:
            sys.path.insert(0, s)
    from cosyvoice.cli.cosyvoice import CosyVoice3

    tr = json.loads((HERE / "e3_translations.json").read_text(encoding="utf-8"))
    if args.limit:
        tr = tr[:args.limit]
    refs = load_refs()
    if args.ref_mode == "zh4all":
        zh = refs["en2zh"]
        refs = {"en2zh": zh, "zh2en": zh}
    model = CosyVoice3(str(MODEL), load_trt=False, load_vllm=False, fp16=False)
    sr_out = getattr(model, "sample_rate", 24000)

    def synth(text, ref, ref_text, speed, dst):
        it = model.inference_zero_shot(text, f"{SYS}{MARKER}{ref_text}",
                                       str(ref), stream=False, speed=speed)
        w = next(it)["tts_speech"].detach().cpu()
        if w.shape[0] > 1:
            w = w.mean(dim=0, keepdim=True)
        peak = float(w.abs().max()) or 1.0
        w = (w / peak * 0.7).clamp(-1, 1)
        torchaudio.save(str(dst), w, sr_out, encoding="PCM_S",
                        bits_per_sample=16)
        x = w.numpy()[0]
        d, b = speech_span(x, sr_out)
        return round(float(d), 2), int(b)

    def save(rows):
        (HERE / f"e3_synth_result{SUF}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    rows = []
    # ---- A 臂:自由译 speed=1.0,顺带校准语速 ----
    for it in tr:
        ref, ref_text = refs[it["dir"]]
        dur, bursts = synth(it["free"], ref, ref_text, 1.0,
                            OUT / f"{it['id']}_A.wav")
        rows.append({"id": it["id"], "dir": it["dir"], "win_dur": it["win_dur"],
                     "text": it["text"], "free": it["free"], "dur_A": dur,
                     "bursts_A": bursts})
        print(f"[A] {it['id']} win={it['win_dur']}s dur={dur:.2f}s "
              f"bursts={bursts}", flush=True)
    save(rows)
    rates = {}
    for d in ("en2zh", "zh2en"):
        rs = [r["dur_A"] / (len(r["free"]) if d == "en2zh"
                            else len(r["free"].split()))
              for r in rows if r["dir"] == d and r["dur_A"] > 0]
        if not rs:
            continue
        rs.sort()
        rates[d] = rs[len(rs) // 2]
        unit = "s/char" if d == "en2zh" else "s/word"
        print(f"[calib] {d}: {rates[d]:.3f} {unit} (n={len(rs)})")

    # ---- B 臂:预算挑候选 + speed 回炉 ----
    for it, r in zip(tr, rows):
        ref, ref_text = refs[it["dir"]]
        win = it["win_dur"]
        rate = rates[it["dir"]]
        units = lambda t: (len(t) if it["dir"] == "en2zh" else len(t.split()))
        ests = [(c["t"], units(c["t"]) * rate, c["est_s"]) for c in it["cands"]]
        fits = [e for e in ests if e[1] <= win * 1.02]
        pick = max(fits, key=lambda e: e[1])[0] if fits else min(ests, key=lambda e: e[1])[0]
        events = []
        dur, bursts = None, 1
        tryn = 0
        for cand in [c for c in [pick] + [e[0] for e in ests if e[0] != pick]][:2]:
            speed = 1.0
            dur, bursts = synth(cand, ref, ref_text, speed,
                                OUT / f"{it['id']}_B.wav")
            tryn += 1
            if abs(dur - win) / win <= 0.10:
                break
            need = dur / win
            events.append(f"try{tryn}:{cand[:18]!r} dur={dur:.2f} → speed {need:.3f}")
            if 0.8 <= need <= 1.35:
                speed = round(need, 3)
                dur, bursts = synth(cand, ref, ref_text, speed,
                                    OUT / f"{it['id']}_B.wav")
                events.append(f"resynth speed={speed} → dur={dur:.2f}")
                if abs(dur - win) / win <= 0.10:
                    break
        r.update(picked=pick, est_by_rate=round(
            next(e[1] for e in ests if e[0] == pick), 2),
            speed_used=speed, dur_B=dur, bursts_B=bursts, events=events)
        print(f"[B] {it['id']} win={win} dur={dur:.2f} bursts={bursts} "
              f"speed={speed} ev={len(events)}", flush=True)
    save(rows)

    # ---- C 臂:A 音频 atempo 兜底 ----
    for r in rows:
        f = r["dur_A"] / r["win_dur"]
        atempo(OUT / f"{r['id']}_A.wav", OUT / f"{r['id']}_C.wav", f)
        r["atempo_C"] = round(f, 3)
        r["dur_C"] = round(wav_dur(OUT / f"{r['id']}_C.wav"), 2)
        # atempo 输出的静音也被拉伸,语音跨度按同比例折算
        r["dur_C_speech"] = round(r["dur_A"] / f, 2)
        print(f"[C] {r['id']} atempo={f:.3f}")

    for r in rows:
        for k in ("A", "B"):
            d = r[f"dur_{k}"]
            r[f"fit_{k}"] = bool(abs(d - r["win_dur"]) / r["win_dur"] <= 0.10)
        # C 臂按构造必进窗(f=dur_A/win),无独立 fit 指标;有效性看变速幅度+R3 听感
    save(rows)

    # ---- 汇总 ----
    print("\n==== 汇总(±10% 进窗) ====")
    for d in ("en2zh", "zh2en"):
        rs = [r for r in rows if r["dir"] == d]
        if not rs:
            continue
        n = len(rs)
        print(f"[{d}] n={n}")
        for k in ("A", "B"):
            fit = sum(r[f"fit_{k}"] for r in rs)
            over = sum(1 for r in rs if r[f"dur_{k}"] > r["win_dur"] * 1.10)
            print(f"  {k}: 进窗 {fit}/{n} ({100*fit/n:.0f}%)  超窗 {over}/{n}")
        rework = sum(1 for r in rs if r["events"])
        spd = [r["speed_used"] for r in rs if r["speed_used"] != 1.0]
        print(f"  B 回炉句数: {rework}/{n}  speed≠1 句数: {len(spd)} "
              f"(>1.15: {sum(1 for s in spd if s > 1.15)})")
        at = [r["atempo_C"] for r in rs]
        print(f"  C atempo 均值 {sum(at)/len(at):.3f}; >1.15: "
              f"{sum(1 for a in at if a > 1.15)}/{n} "
              f"(>1.3: {sum(1 for a in at if a > 1.3)}, "
              f">1.5: {sum(1 for a in at if a > 1.5)}; "
              f"<0.87(拖慢): {sum(1 for a in at if a < 0.87)})")


if __name__ == "__main__":
    main()
