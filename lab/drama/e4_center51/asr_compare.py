# -*- coding: utf-8 -*-
"""E4 快测:中置通道 vs 立体声下混的对白纯度对比(faster-whisper WER 对官方英字)。

run in .venv-lab:  ../../.venv-lab/Scripts/python asr_compare.py
输入(同目录 media/blender/): sintel_center.wav / sintel_stereo.wav / sintel_en.srt
输出: lab/drama/results/e4/asr_compare.json
判读: WER_center << WER_stereo ⇒ "对白进中置"成立,C 通道是高质量对白参照(分离真值角色)。
"""
import json
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MEDIA = HERE.parent / "media" / "blender"
RESULTS = HERE.parent / "results" / "e4"


def norm(t: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", t.lower()).strip()


def srt_text(p: Path) -> str:
    lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
    keep, in_cue = [], False
    for ln in lines:
        if "-->" in ln:
            in_cue = True
            continue
        if in_cue:
            if not ln.strip():
                in_cue = False
            elif not ln.strip().isdigit():
                keep.append(ln.strip())
    return norm(" ".join(keep))


def wer(ref: str, hyp: str) -> float:
    r, h = ref.split(), hyp.split()
    if not r:
        return 1.0
    dp = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, dp[0] = dp[0], i
        for j in range(1, len(h) + 1):
            cur = min(dp[j] + 1, dp[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev, dp[j] = dp[j], cur
    return dp[-1] / len(r)


def main():
    from faster_whisper import WhisperModel
    ref = srt_text(MEDIA / "sintel_en.srt")
    out = {"ref_words": len(ref.split()), "model": "small", "device": "cpu-int8(cublas12缺dll,GPU路径留待补装nvidia-cublas-cu12)", "tracks": {}}
    for name, fn in [("center", "sintel_center.wav"), ("stereo_downmix", "sintel_stereo.wav")]:
        t0 = time.time()
        m = WhisperModel("small", device="cpu", compute_type="int8")
        segs, _ = m.transcribe(str(MEDIA / fn), language="en", vad_filter=True)
        hyp = norm(" ".join(s.text for s in segs))
        out["tracks"][name] = {
            "wer": round(wer(ref, hyp), 4),
            "hyp_words": len(hyp.split()),
            "sec": round(time.time() - t0, 1),
        }
    out["conclusion"] = (
        "center WER < stereo WER ⇒ 对白进中置成立"
        if out["tracks"]["center"]["wer"] < out["tracks"]["stereo_downmix"]["wer"]
        else "center 未占优(需人工听查)"
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "asr_compare.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
