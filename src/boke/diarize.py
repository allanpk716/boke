# -*- coding: utf-8 -*-
"""stage C: pyannote speaker-diarization-community-1 声纹分人。

gated 403 / 环境不可用时走 mock:用 ocr.srt 时间轴轮流标 SPK_00/SPK_01。
HF token 从 ~/.cache/huggingface/token 读(环境变量未设,显式传)。
"""
import os
from pathlib import Path

from .common import parse_srt, SpkSeg, write_rttm

MODEL_ID = "pyannote/speaker-diarization-community-1"


def hf_token() -> str:
    p = Path(os.environ.get("HF_TOKEN_FILE",
                            Path.home() / ".cache/huggingface/token"))
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return os.environ.get("HF_TOKEN", "")


def run(video, work_dir="work", audio=None, mock=False, min_speakers=1,
        max_speakers=6, force=False) -> dict:
    vid = Path(video).stem
    wdir = Path(work_dir) / vid
    wdir.mkdir(parents=True, exist_ok=True)
    rttm = wdir / "diar.rttm"
    if rttm.exists() and not force:
        return {"rttm": str(rttm), "skipped": True}

    audio = audio or (wdir / "audio.wav")

    if mock:
        segs = mock_from_srt(wdir / "ocr.srt")
        write_rttm(rttm, segs)
        return {"rttm": str(rttm), "mock": True, "n_seg": len(segs)}

    token = hf_token()
    if not token:
        raise RuntimeError("无 HF token(且未指定 --diarize-mock)")

    import torch
    from pyannote.audio import Pipeline

    try:
        pl = Pipeline.from_pretrained(MODEL_ID, token=token)          # pyannote>=3.1/4.x
    except TypeError:
        pl = Pipeline.from_pretrained(MODEL_ID, use_auth_token=token)  # 旧版兼容
    if torch.cuda.is_available():
        pl.to(torch.device("cuda"))
        print("[diarize] on cuda:", torch.cuda.get_device_name(0))
    else:
        print("[diarize] WARN: cuda 不可用,CPU 会很慢")

    ann = pl(str(audio), min_speakers=min_speakers, max_speakers=max_speakers)
    segs = [SpkSeg(t0=turn.start, t1=turn.end, spk=spk)
            for turn, spk in ann.itertracks(yield_label=True)]
    write_rttm(rttm, segs)
    speakers = sorted({s.spk for s in segs})
    print(f"[diarize] {len(segs)} segments, speakers: {speakers}")
    return {"rttm": str(rttm), "n_seg": len(segs), "speakers": speakers}


def mock_from_srt(srt_path, n_speakers=2) -> list:
    """用字幕时间轴生成占位 rttm(相邻同说话人段会在 parse_rttm 里合并,
    这里故意给每条字幕一个段,体现轮流说话)。"""
    subs = parse_srt(srt_path)
    segs = []
    for i, s in enumerate(subs):
        spk = f"SPK_{i % n_speakers:02d}"
        segs.append(SpkSeg(t0=s.t0, t1=s.t1, spk=spk))
    return segs


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--min-speakers", type=int, default=1)
    ap.add_argument("--max-speakers", type=int, default=6)
    a = ap.parse_args()
    print(run(a.video, mock=a.mock, min_speakers=a.min_speakers,
              max_speakers=a.max_speakers))
