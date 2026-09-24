# -*- coding: utf-8 -*-
"""独立声纹分人 runner(跑在 .venv-diar,pyannote 4.x 环境;与 CosyVoice 主 venv 隔离)。
用法: diar_run.py <audio.wav> <out.rttm> [max_speakers] [min_speakers]
"""
import os
import sys
from pathlib import Path

# torchcodec 的 FFmpeg 依赖 DLL 在 venv 的 Scripts/ 目录,确保 DLL 搜索可见
_scripts = Path(sys.executable).parent
os.environ["PATH"] = str(_scripts) + os.pathsep + os.environ.get("PATH", "")


def main():
    audio, out_rttm = sys.argv[1], sys.argv[2]
    max_spk = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    min_spk = int(sys.argv[4]) if len(sys.argv) > 4 else 1
    hf_token = Path.home().joinpath(".cache/huggingface/token").read_text(
        encoding="utf-8").strip()

    import torch
    from pyannote.audio import Pipeline

    pl = Pipeline.from_pretrained(
        "pyannote/speaker-diarization-community-1", token=hf_token)
    if torch.cuda.is_available():
        pl.to(torch.device("cuda"))
        print("[diar-run] cuda:", torch.cuda.get_device_name(0), flush=True)
    else:
        print("[diar-run] WARN cpu mode", flush=True)

    kw = {"min_speakers": min_spk, "max_speakers": max_spk}
    print(f"[diar-run] {audio} {kw}", flush=True)
    result = pl(audio, **kw)
    # pyannote 4.x 返回 DiarizeOutput;经典 Annotation 在 .speaker_diarization
    ann = getattr(result, "speaker_diarization", result)

    rows = []
    for turn, _track, spk in ann.itertracks(yield_label=True):
        t0, t1 = turn.start, turn.end
        spk = str(spk).replace("SPEAKER_", "SPK_")  # 统一 SPK_xx 命名(voices.yaml 对齐)
        rows.append(f"SPEAKER audio 1 {t0*100:07.3f} {(t1-t0)*100:07.3f} "
                    f"<NA> <NA> {spk} <NA> <NA>")
    Path(out_rttm).write_text("\n".join(rows) + "\n", encoding="utf-8")
    speakers = sorted({r.split()[7] for r in rows})
    print(f"[diar-run] done: {len(rows)} segs, speakers={speakers}", flush=True)


if __name__ == "__main__":
    main()
