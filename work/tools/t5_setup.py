# -*- coding: utf-8 -*-
"""T5 环境搭建一条龙: torch-cu124 → modelscope → CosyVoice clone → 权重下载。
幂等: 每步检测已存在则跳过。日志: work/t5_setup.log (由调用方重定向)
"""
import os
import subprocess
import sys
from pathlib import Path

VENV_PY = r"C:\WorkSpace\agent\boke\.venv\Scripts\python.exe"
TOOLS = Path("D:/boke_media/tools")
MODELS = Path("D:/boke_media/models")
REPO = TOOLS / "CosyVoice"
WEIGHTS = MODELS / "Fun-CosyVoice3-0.5B-2512"
TS = "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"


def sh(*cmd, **kw):
    print(f"\n[t5] $ {' '.join(map(str, cmd))[:150]}", flush=True)
    r = subprocess.run([str(c) for c in cmd], **kw)
    if r.returncode != 0 and not kw.get("capture_output"):
        sys.exit(f"[t5] FAILED (exit {r.returncode}): {' '.join(map(str, cmd))[:200]}")
    return r


def pip(*args, mirror=True, extra_args=()):
    cmd = [VENV_PY, "-m", "pip", "install", *args, *extra_args]
    if mirror:
        cmd += list(TS)      # 清华镜像只用于普通包;torch cu124 必须走官方源
    sh(*cmd)


def main():
    TOOLS.mkdir(parents=True, exist_ok=True)
    MODELS.mkdir(parents=True, exist_ok=True)

    # 1) torch cu124 (大, ~2.5GB)
    need_torch = sh(VENV_PY, "-c", "import torch", capture_output=True).returncode != 0
    if need_torch:
        pip("torch", "torchaudio", mirror=False,
            extra_args=["--index-url", "https://download.pytorch.org/whl/cu124"])
    else:
        print("[t5] torch already installed")
    sh(VENV_PY, "-c",
       "import torch; assert torch.cuda.is_available(), 'CUDA NOT AVAILABLE'; "
       "print('torch', torch.__version__, 'cuda ok', torch.cuda.get_device_name(0))")

    # 2) modelscope
    r = sh(VENV_PY, "-c", "import modelscope", capture_output=True)
    if r.returncode != 0:
        pip("modelscope")

    # 3) CosyVoice clone
    if not REPO.exists():
        sh("git", "clone", "--recursive", "--depth", "1",
           "https://github.com/FunAudioLLM/CosyVoice", str(REPO))
    else:
        print(f"[t5] repo exists: {REPO}")

    # 4) 权重 (ModelScope 国内快)
    if not WEIGHTS.exists() or not any(WEIGHTS.rglob("*.pt")) and not any(WEIGHTS.rglob("*.safetensors")):
        sh(VENV_PY, "-c",
           "from modelscope import snapshot_download; "
           f"snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', "
           f"local_dir=r'{WEIGHTS}')")
    else:
        print(f"[t5] weights exist: {WEIGHTS}")

    print("\n[t5] ALL DONE")


if __name__ == "__main__":
    main()
