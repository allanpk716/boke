# -*- coding: utf-8 -*-
"""stage A: ffmpeg 抽 16k 单声道 wav(OCR 对齐 / diarization 输入)。"""
from pathlib import Path

from .common import run_ffmpeg, probe_duration


def run(video, work_dir="work", force=False) -> dict:
    vid = Path(video).stem
    wdir = Path(work_dir) / vid
    wdir.mkdir(parents=True, exist_ok=True)
    wav = wdir / "audio.wav"
    if wav.exists() and not force:
        return {"audio": str(wav), "skipped": True}

    dur = probe_duration(video)
    (wdir / "source_duration.txt").write_text(f"{dur}\n", encoding="utf-8")

    run_ffmpeg(["-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                "-c:a", "pcm_s16le", str(wav)])
    return {"audio": str(wav), "source_duration": dur}


if __name__ == "__main__":
    import sys
    print(run(sys.argv[1]))
