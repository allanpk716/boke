# -*- coding: utf-8 -*-
"""按说话人做语言识别:全部说中文的采访视频 → 标记"无需配音"跳过。
原理: 硬字幕永远是中文(区分不了),必须看原声语言。
用法: check_language.py <video-stem>   (需先有 work/<stem>/audio.wav + diar.rttm)
产物: work/<stem>/langid.json
"""
import json
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ZH_TH = 0.60   # 说话人中文样本占比 ≥ 此值判"中文说话人"


def load_segments(rttm: Path):
    segs = []
    for line in rttm.read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            t0, dur = float(p[3]) / 100, float(p[4]) / 100
            segs.append((t0, t0 + dur, p[7]))
    return segs


def clean_segments(segs, max_n=3, min_len=4.0):
    """每说话人挑不与其他人重叠、够长的段(取最长的 max_n 条)"""
    segs = sorted(segs)
    by = defaultdict(list)
    for i, (t0, t1, spk) in enumerate(segs):
        if t1 - t0 < min_len:
            continue
        overlap = False
        for j, (u0, u1, us) in enumerate(segs):
            if j != i and us != spk and min(t1, u1) - max(t0, u0) > 0.2:
                overlap = True
                break
        if not overlap:
            by[spk].append((t0, t1))
    return {spk: sorted(v, key=lambda x: -(x[1] - x[0]))[:max_n]
            for spk, v in by.items()}


def main(stem: str) -> int:
    w = ROOT / "work" / stem
    audio, rttm = w / "audio.wav", w / "diar.rttm"
    if not audio.exists() or not rttm.exists():
        print(f"缺 audio.wav 或 diar.rttm: {w}")
        return 1

    segs = load_segments(rttm)
    picks = clean_segments(segs)
    if not picks:
        print("无可用干净段")
        return 1

    import whisper
    model = whisper.load_model("small")

    # 候选语言强制转写比 avg_logprob(比 detect_language 可靠:
    # 实测 detect 在日/中之间翻车,用户耳朵纠错过 SPK_01)
    CANDS = ["zh", "ja", "en", "ko"]

    def lang_scores(wav_path: str) -> dict:
        aud = whisper.pad_or_trim(whisper.load_audio(wav_path))
        mel = whisper.log_mel_spectrogram(
            aud, n_mels=getattr(model.dims, "n_mels", 80)).to(model.device)
        out = {}
        for lg in CANDS:
            opts = dict(language=lg, temperature=0.0, beam_size=1)
            r = model.transcribe(wav_path, **opts)
            segs = r.get("segments") or []
            lp = [s["avg_logprob"] for s in segs if s.get("avg_logprob") is not None]
            out[lg] = round(sum(lp) / max(1, len(lp)), 3)
        return out

    per_speaker = {}
    for spk, spans in picks.items():
        votes = defaultdict(float)
        for t0, t1 in spans:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
                tmp = f.name
            r = subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{t0:.2f}", "-t",
                 f"{min(t1-t0, 20):.2f}", "-i", str(audio),
                 "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", tmp],
                capture_output=True)
            if r.returncode != 0:
                continue
            sc = lang_scores(tmp)
            top = max(sc, key=sc.get)
            votes[top] += (t1 - t0)   # 按时长投票
            Path(tmp).unlink(missing_ok=True)
        lang = max(votes, key=votes.get) if votes else "unknown"
        zh_ratio = votes.get("zh", 0) / max(1e-9, sum(votes.values()))
        per_speaker[spk] = {"lang": lang, "zh_ratio": round(zh_ratio, 3),
                            "votes": {k: round(v, 2) for k, v in votes.items()}}
        print(f"[langid] {spk}: {lang} (zh占比 {zh_ratio:.0%}) votes={dict(votes)}")

    all_zh = all(v["zh_ratio"] >= ZH_TH for v in per_speaker.values())
    verdict = "skip_全中文无需配音" if all_zh else "dub_需要配音"
    out = {"verdict": verdict, "speakers": per_speaker}
    (w / "langid.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[langid] 判定: {verdict} → {w/'langid.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
