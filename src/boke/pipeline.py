# -*- coding: utf-8 -*-
"""流水线编排:stage 串联、断点续跑(每 stage 产物落盘即跳过)。

用法:
  python -m boke.pipeline <video> --stage all
  python -m boke.pipeline <video> --stage ocr --fps 3
  python -m boke.pipeline <video> --stage all --mock          # ocr/diarize/synth 全 mock
  python -m boke.pipeline <video> --stage all --tts-engine edge
"""
import argparse
import sys
from pathlib import Path

from . import align_mix, attribute, diarize, extract_audio, ocr_subtitles, synthesize

STAGES = ["extract", "ocr", "diarize", "attribute", "synthesize", "mix"]


def run_pipeline(video: str, stage="all", work_dir="work", out_dir="out",
                 voices=None, mock=False, tts_engine="auto",
                 fps=2.0, force=False, max_speakers=6) -> dict:
    video_p = Path(video)
    if not video_p.exists():
        sys.exit(f"视频不存在: {video}")
    todo = STAGES if stage == "all" else [stage]
    results = {}

    if "extract" in todo:
        print("== [1/6] extract_audio ==")
        results["extract"] = extract_audio.run(video_p, work_dir, force=force)

    if "ocr" in todo:
        print("== [2/6] ocr_subtitles ==")
        results["ocr"] = ocr_subtitles.run(video_p, work_dir, fps=fps,
                                           force=force, mock=mock)

    if "diarize" in todo:
        print("== [3/6] diarize ==")
        try:
            results["diarize"] = diarize.run(video_p, work_dir, mock=mock,
                                             force=force,
                                             max_speakers=max_speakers)
        except Exception as e:
            msg = str(e)
            if "401" in msg or "403" in msg or "gated" in msg.lower():
                print(f"[diarize] gated 403 → mock 降级: {msg[:120]}")
                results["diarize"] = diarize.run(video_p, work_dir, mock=True,
                                                 force=True,
                                                 max_speakers=max_speakers)
            else:
                raise

    if "attribute" in todo:
        print("== [4/6] attribute ==")
        results["attribute"] = attribute.run(video_p, work_dir, force=force)

    if "synthesize" in todo:
        print("== [5/6] synthesize ==")
        if mock:
            tts_engine = "silence"
        results["synthesize"] = synthesize.run(
            video_p, work_dir, voices_yaml=voices, engine=tts_engine,
            mock=mock, force=force)

    if "mix" in todo:
        print("== [6/6] align_mix ==")
        results["mix"] = align_mix.run(video_p, work_dir, out_dir, force=force)

    return results


def main(argv=None):
    ap = argparse.ArgumentParser(prog="boke.pipeline",
                                 description="采访视频中文配音流水线")
    ap.add_argument("video", help="输入视频文件")
    ap.add_argument("--stage", default="all", choices=STAGES + ["all"])
    ap.add_argument("--work", default="work")
    ap.add_argument("--out", default="out")
    ap.add_argument("--voices", default=None, help="voices.yaml 路径")
    ap.add_argument("--mock", action="store_true", help="ocr/diarize/synth 全 mock")
    ap.add_argument("--tts-engine", default="auto",
                    choices=["auto", "edge", "silence", "cosyvoice3"])
    ap.add_argument("--fps", type=float, default=2.0, help="OCR 抽帧率")
    ap.add_argument("--max-speakers", type=int, default=6)
    ap.add_argument("--force", action="store_true", help="重跑(忽略已有产物)")
    a = ap.parse_args(argv)
    res = run_pipeline(a.video, stage=a.stage, work_dir=a.work, out_dir=a.out,
                       voices=a.voices, mock=a.mock, tts_engine=a.tts_engine,
                       fps=a.fps, force=a.force, max_speakers=a.max_speakers)
    import json
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))


if __name__ == "__main__":
    main()
