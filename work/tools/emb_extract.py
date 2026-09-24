# -*- coding: utf-8 -*-
"""声纹 embedding 抽取 runner(跑在 .venv-diar;票04,与 diar_run.py 同环境)。

用法: emb_extract.py <out_npy> <out_json> <audio1.wav> [audio2.wav ...]
产物: out_npy  = (n, 256) float32,行序 = 输入音频序
      out_json = {"paths": [...], "dim": 256, "model": MODEL_ID}

模型:pyannote 内置 WeSpeaker ResNet34(community-1 与分人管线同源;
D16 复用内置 embedding),官方模型卡用法 Inference(window="whole")
整段一向量。HF token 读法同 diar_run.py(~/.cache/huggingface/token)。
模型只加载一次、批量抽全部音频;torch/pyannote/numpy 全部函数内导入,
保证主 venv 侧单测可安全按路径导入本模块。

GPU 实跑示例(.venv-diar,验收可选):
  .venv-diar/Scripts/python.exe work/tools/emb_extract.py o.npy o.json refs/r1.wav
"""
import json
import os
import sys
from pathlib import Path

# torchcodec 的 FFmpeg 依赖 DLL 在 venv 的 Scripts/ 目录,确保 DLL 搜索可见
# (照抄 diar_run.py 头部)
_scripts = Path(sys.executable).parent
os.environ["PATH"] = str(_scripts) + os.pathsep + os.environ.get("PATH", "")

MODEL_ID = "pyannote/wespeaker-voxceleb-resnet34-LM"


def hf_token() -> str:
    p = Path.home().joinpath(".cache/huggingface/token")
    if p.exists():
        return p.read_text(encoding="utf-8").strip()
    return os.environ.get("HF_TOKEN", "")


def extract_all(audio_paths, token):
    """每条音频整段抽一向量 → (n, dim) float32(模型只加载一次)。"""
    import numpy as np
    import torch
    from pyannote.audio import Inference

    try:
        inf = Inference(MODEL_ID, window="whole", token=token)
    except TypeError:  # 旧版 pyannote 参数名
        inf = Inference(MODEL_ID, window="whole", use_auth_token=token)
    if torch.cuda.is_available():
        inf.to(torch.device("cuda"))
        print("[emb-extract] cuda:", torch.cuda.get_device_name(0), flush=True)
    else:
        print("[emb-extract] WARN cpu mode", flush=True)

    embs = []
    for p in audio_paths:
        e = np.asarray(inf(str(p)), dtype=np.float32).reshape(-1)
        print(f"[emb-extract] {p} -> dim={e.shape[0]}", flush=True)
        embs.append(e)
    dims = {e.shape[0] for e in embs}
    if len(dims) != 1:
        raise RuntimeError(f"各音频向量维度不一致:{dims}")
    return np.stack(embs).astype(np.float32)


def main():
    if len(sys.argv) < 4:
        print("usage: emb_extract.py <out_npy> <out_json> <audio.wav> [...]",
              file=sys.stderr)
        return 2
    out_npy, out_json = Path(sys.argv[1]), Path(sys.argv[2])
    paths = sys.argv[3:]
    import numpy as np

    embs = extract_all(paths, hf_token())
    np.save(out_npy, embs)
    out_json.write_text(json.dumps(
        {"paths": paths, "dim": int(embs.shape[1]), "model": MODEL_ID},
        ensure_ascii=False), encoding="utf-8")
    print(f"[emb-extract] done: {embs.shape} -> {out_npy}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
