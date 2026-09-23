# -*- coding: utf-8 -*-
"""stage H: 时长对齐三招 + 拼接 + loudnorm → out/<id>.m4a。

三招(01 §R6): ①限长(塞得下就原速+静音填充) ②变速 ≤1.15x(atempo)
③借静音(窗口向后扩到与下一条的间隙,上限窗口的 50%)。
仍超长 → atempo 按需 >1.15x 并记录(丢字不可接受,宁可比值大)。

拼接用纯标准库(wave/array):所有 tts wav 已统一 24k mono s16。
"""
import array
import json
import wave
from pathlib import Path

from .common import probe_duration, run_ffmpeg
from .synthesize import TTS_SR

MAX_TEMPO = 1.15
BORROW_RATIO = 0.5   # 借静音最多扩窗 50%
GAP_MIN = 0.05       # 行间最小间隙


def wav_info(path: Path):
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate(), w.getframerate(), w.getnchannels()


def atempo_render(src: Path, dst: Path, tempo: float):
    run_ffmpeg(["-i", str(src), "-filter:a", f"atempo={tempo:.4f}",
                "-ac", "1", "-ar", str(TTS_SR), "-c:a", "pcm_s16le", str(dst)])


def plan_line(i: int, t0: float, t1: float, dur: float, next_t0, prev_end):
    """决定该行的渲染参数。返回 dict(tempo, place_t, win_end, mode)"""
    win = t1 - t0
    mode = "fit"
    tempo = 1.0
    if dur <= win:                       # ① 塞得下
        win_end = t1
    else:
        # ③ 先借静音:窗口末尾向间隙扩(不越过下一条起点)
        gap = (next_t0 - GAP_MIN) - t1 if next_t0 else 1e9
        borrow = min(max(0.0, gap), win * BORROW_RATIO)
        win_end = t1 + borrow
        if dur <= (win_end - t0):
            mode = "borrow"
        else:
            # ② 变速:按需;优先 1.15 上限内的实际需求
            need = dur / (win_end - t0)
            tempo = need
            mode = "atempo"
            if tempo > MAX_TEMPO:
                # 借静音后仍不够 → 收回借用,全力变速(记录)
                win_end = t1
                tempo = min(2.0, dur / win)
    place_t = max(t0, prev_end + GAP_MIN)
    return {"tempo": round(tempo, 4), "place_t": place_t,
            "win_end": win_end, "mode": mode}


def build_track(manifest_lines, total_dur: float, out_wav: Path):
    """manifest 行 → 完整音轨 wav(静音垫满到 total_dur)。"""
    pcm = array.array("h")
    sr = TTS_SR
    silence_cache = {}

    def silence(sec: float) -> array.array:
        n = int(sec * sr)
        key = n
        if key not in silence_cache:
            silence_cache[key] = array.array("h", bytes(n * 2))
        return silence_cache[key]

    cursor = 0.0
    stats = {"fit": 0, "borrow": 0, "atempo": 0, "atempo_over": 0}
    prev_end = 0.0
    for i, ln in enumerate(manifest_lines):
        wav_path = Path(ln["wav"])
        if not wav_path.exists():
            print(f"[mix] WARN 缺 wav: {wav_path.name}, 跳过(该句静音)")
            dur = ln["t1"] - ln["t0"]
        else:
            dur, wsr, wch = wav_info(wav_path)
            if wsr != sr:
                raise RuntimeError(f"{wav_path} 采样率 {wsr} != {sr},需先 normalize")
        nxt = manifest_lines[i + 1]["t0"] if i + 1 < len(manifest_lines) else None
        plan = plan_line(i, ln["t0"], ln["t1"], dur, nxt, prev_end)

        if wav_path.exists():
            if plan["tempo"] > 1.001:
                tmp = wav_path.with_suffix(".tmp.wav")
                atempo_render(wav_path, tmp, plan["tempo"])
                src = tmp
                dur2, _, _ = wav_info(tmp)
            else:
                src = wav_path
                dur2 = dur
            stats[plan["mode"]] += 1
            if plan["mode"] == "atempo" and plan["tempo"] > MAX_TEMPO:
                stats["atempo_over"] += 1
            # 静音垫到 place_t
            lead = plan["place_t"] - cursor
            if lead > 0.001:
                pcm.extend(silence(lead))
            with wave.open(str(src), "rb") as w:
                frames = array.array("h", w.readframes(w.getnframes()))
                pcm.extend(frames)
            cursor = plan["place_t"] + dur2
            prev_end = cursor
            if plan["tempo"] > 1.001:
                src.unlink(missing_ok=True)

    # 尾部垫到原视频时长
    if total_dur > 0 and cursor < total_dur:
        pcm.extend(silence(total_dur - cursor))

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return stats, cursor


def run(video, work_dir="work", out_dir="out", total_dur=None, force=False) -> dict:
    vid = Path(video).stem
    wdir = Path(work_dir) / vid
    m4a = Path(out_dir) / f"{vid}.m4a"
    m4a.parent.mkdir(parents=True, exist_ok=True)
    if m4a.exists() and not force:
        return {"m4a": str(m4a), "skipped": True}

    man = json.loads((wdir / "tts" / "manifest.json").read_text(encoding="utf-8"))
    lines = man["lines"]
    if total_dur is None:
        dur_f = wdir / "source_duration.txt"
        total_dur = float(dur_f.read_text().strip()) if dur_f.exists() \
            else (lines[-1]["t1"] + 5 if lines else 0)

    full_wav = wdir / "full_mix.wav"
    stats, built_dur = build_track(lines, total_dur, full_wav)
    print(f"[mix] track built: {built_dur:.1f}s / target {total_dur:.1f}s, "
          f"modes={stats}")

    run_ffmpeg(["-i", str(full_wav),
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-ar", "44100", "-ac", "1", "-c:a", "aac", "-b:a", "128k",
                str(m4a)])
    (wdir / "mix_stats.json").write_text(
        json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"m4a": str(m4a), "modes": stats, "built_dur": round(built_dur, 1),
            "target_dur": round(total_dur, 1)}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    a = ap.parse_args()
    print(run(a.video))
