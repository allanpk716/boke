# -*- coding: utf-8 -*-
"""独立声纹分人 runner(跑在 .venv-diar,pyannote 4.x 环境;与 CosyVoice 主 venv 隔离)。
用法: diar_run.py <audio.wav> <out.rttm> [max_speakers] [min_speakers]
产物: <out>.rttm + 同目录 spk_emb.npy / spk_emb.json(声纹落盘,票01);
      无 embeddings(经典路径/旧管线)时只写 rttm,不写声纹两件。
"""
import json
import os
import sys
from pathlib import Path

# torchcodec 的 FFmpeg 依赖 DLL 在 venv 的 Scripts/ 目录,确保 DLL 搜索可见
_scripts = Path(sys.executable).parent
os.environ["PATH"] = str(_scripts) + os.pathsep + os.environ.get("PATH", "")


def rttm_rows(ann) -> list:
    """Annotation → rttm 行列表(SPEAKER_→SPK_ 统一命名,voices.yaml 对齐)。"""
    rows = []
    for turn, _track, spk in ann.itertracks(yield_label=True):
        t0, t1 = turn.start, turn.end
        spk = str(spk).replace("SPEAKER_", "SPK_")
        rows.append(f"SPEAKER audio 1 {t0*100:07.3f} {(t1-t0)*100:07.3f} "
                    f"<NA> <NA> {spk} <NA> <NA>")
    return rows


def save_speaker_embeddings(result, out_dir):
    """票01:把 DiarizeOutput.speaker_embeddings 与标签顺序落盘(F8 数据侧保障)。

    pyannote 4.x 已核事实:speaker_embeddings 形状 (num_speakers, dim),
    行序与 speaker_diarization.labels() 原序一致;rttm 里的 SPK 标签就来自
    同一批 labels(仅做 SPEAKER_→SPK_ 替换)。json 的 labels 必须用同一来源
    原序、同样替换、不排序——保住 npy 行序与 SPK 标签的对齐关系,不做任何
    自行排序的硬编码假设(票04 --verify-alignment 依赖这一点)。
    无 speaker_embeddings(经典 Annotation 直通/None)→ 打 WARN 不写文件;
    行数与 labels 数不符(对齐已破)→ 打 WARN 不写文件。均返回 None。
    """
    emb = getattr(result, "speaker_embeddings", None)
    if emb is None:
        print("[diar-run] WARN 无 speaker_embeddings,跳过声纹落盘(rttm 照写)",
              flush=True)
        return None
    import numpy as np
    emb = np.asarray(emb)
    ann = getattr(result, "speaker_diarization", result)
    labels = [str(lb).replace("SPEAKER_", "SPK_") for lb in ann.labels()]
    if len(labels) != emb.shape[0]:
        print(f"[diar-run] WARN embedding 行数 {emb.shape[0]} != labels 数 "
              f"{len(labels)},对齐存疑,跳过声纹落盘", flush=True)
        return None
    out_dir = Path(out_dir)
    np.save(out_dir / "spk_emb.npy", emb)
    (out_dir / "spk_emb.json").write_text(
        json.dumps({"labels": labels, "dim": int(emb.shape[1])},
                   ensure_ascii=False), encoding="utf-8")
    print(f"[diar-run] spk_emb: {emb.shape[0]}x{emb.shape[1]} labels={labels}",
          flush=True)
    return {"npy": out_dir / "spk_emb.npy", "json": out_dir / "spk_emb.json"}


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

    rows = rttm_rows(ann)
    Path(out_rttm).write_text("\n".join(rows) + "\n", encoding="utf-8")
    save_speaker_embeddings(result, Path(out_rttm).parent)
    speakers = sorted({r.split()[7] for r in rows})
    print(f"[diar-run] done: {len(rows)} segs, speakers={speakers}", flush=True)


if __name__ == "__main__":
    main()
