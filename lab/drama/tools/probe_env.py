"""环境探测：GPU / ffmpeg·ffprobe / torch.cuda / 关键包版本 → lab/drama/results/env_probe.json。

设计目标：任何一项失败不影响其余项（优雅降级）——系统 python 没有任何第三方包也能跑出
GPU、ffmpeg、.venv-lab 状态等结果。.venv-lab 只做"解释器存在性检查 + subprocess 探测"，
本脚本不 import 其内部包。

用法：python lab/drama/tools/probe_env.py [--out <json 路径>]
"""
from __future__ import annotations

import argparse
import datetime
import importlib.metadata as md
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

try:  # Windows 重定向输出默认走本地编码（cp936），统一成 UTF-8 防乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DRAMA = Path(__file__).resolve().parents[1]          # lab/drama
REPO = DRAMA.parents[1]                              # repo 根（.venv-lab 所在层）

# (模块名, 发行名, 展示名)：版本优先走 importlib.metadata（不 import 包本身，快且对半装状态稳）
PKGS = [
    ("audio_separator", "audio-separator", "audio_separator"),
    ("faster_whisper", "faster-whisper", "faster_whisper"),
    ("whisper", "openai-whisper", "whisper"),
    ("museval", "museval", "museval"),
    ("speechbrain", "speechbrain", "speechbrain"),
    ("torch", "torch", "torch"),
    ("soundfile", "soundfile", "soundfile"),
    ("scipy", "scipy", "scipy"),
    ("matplotlib", "matplotlib", "matplotlib"),
    ("yaml", "pyyaml", "pyyaml"),
]
KEY_PKGS = ["audio-separator", "faster-whisper", "museval", "speechbrain"]  # venv ready 判定

# 以下两段代码交由 .venv-lab 解释器 subprocess 执行，只打印一行 json
VENV_META_CODE = r"""
import json, sys
import importlib.metadata as md
pkgs = ['audio-separator', 'faster-whisper', 'openai-whisper', 'museval',
        'speechbrain', 'torch', 'soundfile', 'scipy', 'matplotlib', 'pyyaml']
out = {}
for p in pkgs:
    try:
        out[p] = md.version(p)
    except Exception:
        out[p] = None
print(json.dumps({'python': sys.version.split()[0], 'pkgs': out}))
"""
VENV_TORCH_CODE = r"""
import json
out = {}
try:
    import torch
    out["torch_version"] = torch.__version__
    out["cuda_available"] = bool(torch.cuda.is_available())
    out["cuda_version"] = torch.version.cuda
    if out["cuda_available"]:
        out["device_name"] = torch.cuda.get_device_name(0)
        out["device_mem_total_mib"] = round(torch.cuda.get_device_properties(0).total_memory / 1048576)
except Exception as e:
    out["error"] = repr(e)
print(json.dumps(out))
"""


def _run(cmd: list[str], timeout: float = 120) -> tuple[int, str, str]:
    """跑子进程 → (退出码, stdout, stderr)；任何异常降级为 (-1, '', 错误文本)。"""
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
        return cp.returncode, cp.stdout or "", cp.stderr or ""
    except Exception as e:
        return -1, "", repr(e)


def probe_gpu() -> dict:
    """GPU 型号/显存：nvidia-smi 优先（不依赖 torch）；失败返回空列表。"""
    rc, out, _ = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
                       "--format=csv,noheader,nounits"], timeout=30)
    devices = []
    if rc == 0:
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 2 or not parts[0]:
                continue
            d: dict = {"name": parts[0]}
            try:
                d["memory_total_mib"] = int(parts[1])
            except ValueError:
                pass
            if len(parts) >= 3:
                d["driver_version"] = parts[2]
            devices.append(d)
    return {"source": "nvidia-smi" if devices else None, "devices": devices}


def probe_bin(exe: str) -> dict:
    """PATH 中的可执行文件：路径 + 版本首行。"""
    path = shutil.which(exe)
    if not path:
        return {"path": None, "version": None}
    rc, out, _ = _run([exe, "-version"], timeout=30)
    lines = out.strip().splitlines()
    return {"path": path, "version": lines[0].strip() if rc == 0 and lines else None}


def pkg_map_current() -> dict:
    """当前解释器（跑本脚本的 python）的关键包版本；缺则标 'missing'。"""
    out = {}
    for mod, dist, label in PKGS:
        ver = None
        try:
            ver = md.version(dist)
        except Exception:
            pass
        if ver is None:
            try:  # metadata 查不到再试着直接 import 取 __version__
                ver = getattr(__import__(mod), "__version__", "unknown")
            except Exception:
                ver = "missing"
        out[label] = ver
    return out


def probe_torch_here() -> dict:
    """当前解释器的 torch.cuda 探测（无 torch 时返回 error 说明）。"""
    try:
        import torch
        d = {"torch_version": torch.__version__,
             "cuda_available": bool(torch.cuda.is_available()),
             "cuda_version": torch.version.cuda, "source": "current-interpreter"}
        if d["cuda_available"]:
            d["device_name"] = torch.cuda.get_device_name(0)
            d["device_mem_total_mib"] = round(
                torch.cuda.get_device_properties(0).total_memory / 1048576)
        return d
    except Exception as e:
        return {"cuda_available": False, "source": "current-interpreter", "error": repr(e)}


def probe_venv() -> dict:
    """.venv-lab 存在性 + 其解释器的包版本 / torch.cuda（仅 subprocess，不 import 其包）。"""
    venvpy = REPO / ".venv-lab" / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")
    info: dict = {"exists": venvpy.exists(),
                  "python_path": str(venvpy) if venvpy.exists() else None,
                  "python_version": None, "pkgs": {}, "status": "missing"}
    if not info["exists"]:
        return info
    info["status"] = "partial"
    rc, out, err = _run([str(venvpy), "-c", VENV_META_CODE], timeout=120)
    if rc == 0:
        try:
            data = json.loads(out.strip().splitlines()[-1])
            info["python_version"] = data.get("python")
            info["pkgs"] = {k: (v if v else "missing") for k, v in data.get("pkgs", {}).items()}
        except Exception as e:
            info["meta_error"] = repr(e)
    else:
        info["meta_error"] = err.strip().splitlines()[-1] if err.strip() else f"退出码 {rc}"
    # torch.cuda 深探测（venv 装的是 CUDA 版 torch；半装/未装时在此降级）
    rc, out, _ = _run([str(venvpy), "-c", VENV_TORCH_CODE], timeout=300)
    if rc == 0:
        try:
            info["torch_cuda"] = json.loads(out.strip().splitlines()[-1])
        except Exception as e:
            info["torch_cuda"] = {"error": repr(e)}
    else:
        info["torch_cuda"] = {"cuda_available": False,
                              "note": f"venv 解释器探测失败（退出码 {rc}，可能依赖装到一半）"}
    if all(info["pkgs"].get(k) not in (None, "missing") for k in KEY_PKGS):
        info["status"] = "ready"
    return info


def main() -> int:
    ap = argparse.ArgumentParser(description="lab/drama 环境探测（优雅降级，永不半途崩溃）")
    ap.add_argument("--out", default=str(DRAMA / "results" / "env_probe.json"),
                    help="输出 json 路径（默认 lab/drama/results/env_probe.json）")
    args = ap.parse_args()

    result = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": {"version": sys.version.split()[0], "executable": sys.executable,
                   "platform": platform.platform()},
        "gpu": probe_gpu(),
        "ffmpeg": probe_bin("ffmpeg"),
        "ffprobe": probe_bin("ffprobe"),
        "current_interpreter_pkgs": pkg_map_current(),
        "venv_lab": probe_venv(),
    }
    # torch.cuda 摘要：优先 venv 的深探测（CUDA 版 torch 装在那边），否则看当前解释器
    venv_torch = result["venv_lab"].get("torch_cuda")
    if isinstance(venv_torch, dict) and "torch_version" in venv_torch:
        result["torch_cuda"] = venv_torch
    else:
        result["torch_cuda"] = probe_torch_here()

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 人读摘要
    g = result["gpu"]["devices"][0] if result["gpu"]["devices"] else None
    print("GPU      :", f"{g['name']}, {g.get('memory_total_mib', '?')} MiB"
                        f"（{result['gpu']['source']}）" if g else "未探测到")
    for exe in ("ffmpeg", "ffprobe"):
        b = result[exe]
        print(f"{exe:<9}: {b['version'] or 'missing'}" if b["path"] else f"{exe:<9}: missing")
    v = result["venv_lab"]
    if v["exists"]:
        print(".venv-lab:", f"{v['status']}  python={v['python_version']}  "
              + " ".join(f"{k}={v['pkgs'].get(k, 'missing')}" for k in KEY_PKGS))
        tc = result["torch_cuda"]
        print("torch.cuda:", f"available={tc.get('cuda_available')}  {tc.get('device_name', '')}"
              + (f"  err={tc['error']}" if tc.get("error") else ""))
    else:
        print(".venv-lab: 不存在（探测降级：仅系统 python 结果）")
    print("已写入   :", out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
