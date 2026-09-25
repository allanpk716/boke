# setup_env — 建立实验室独立环境（.venv-lab）

> 用途：`lab/drama/` 影视剧配音研究的独立 Python 环境（决策 D1/D2）。
> **红线：全部装进 `.venv-lab`；严禁动主线 `.venv` / `.venv-diar`，严禁把实验依赖 pip install 到系统 python。**

## 前置条件（本机已满足，2026-09-25 实测）

| 项 | 状态 | 验证命令 |
|---|---|---|
| 系统 Python 3.11+ | 3.11.9（scoop） | `python --version` |
| ffmpeg / ffprobe | scoop shim 在 PATH | `ffmpeg -version` |
| NVIDIA 驱动 + nvidia-smi | RTX 3060, 12288 MiB | `nvidia-smi --query-gpu=name,memory.total --format=csv` |

## 建环境步骤（repo 根目录执行，约 10~30 分钟，大头是 CUDA 版 torch 下载）

```powershell
# 0) 在 repo 根目录（含 .git 的那层，.venv-lab 建在这里）
cd <repo-root>

# 1) 建 venv（纯标准库命令，几秒）
python -m venv .venv-lab

# 2) 激活
#   PowerShell:
.venv-lab\Scripts\Activate.ps1
#   Git Bash:
source .venv-lab/Scripts/activate

# 3) 装依赖（audio-separator[gpu] 会连带装 CUDA 版 torch / onnxruntime，下载量大）
python -m pip install -U pip
pip install "audio-separator[gpu]" faster-whisper museval soundfile pyyaml speechbrain matplotlib scipy
#   等价写法：pip install -r lab/drama/requirements-lab.txt

# 4) 验证（可不激活——probe_env 会自动用 .venv-lab 的解释器做 subprocess 探测）
python lab/drama/tools/probe_env.py
```

验证通过的标准（看 `lab/drama/results/env_probe.json`）：

- `venv_lab.status == "ready"`（四个关键包 audio-separator / faster-whisper / museval / speechbrain 都在）；
- `torch_cuda.cuda_available == true` 且 `device_name` 为本机 GPU；
- `gpu.devices[0]` 为 `NVIDIA GeForce RTX 3060` / `12288 MiB`；
- `ffmpeg.path / ffprobe.path` 非 null。

## 探测脚本的降级行为

`tools/probe_env.py` 用系统 python 即可跑（不要求先装好 .venv-lab）：

- nvidia-smi 探 GPU（不依赖 torch）；ffmpeg/ffprobe 查 PATH；
- 当前解释器缺包时对应项标 `missing`，不报错；
- `.venv-lab` 存在则用它的解释器 subprocess 探包版本与 torch.cuda；半装状态标 `partial`，全部就绪标 `ready`，不存在标 `missing`。

## 纪律（防火墙，见 PLAN.md ①）

- 本环境只服务 lab/drama 研究；任何实验脚本不得引用主线 `.venv` / `.venv-diar` 的解释器或包。
- `.venv-lab/` 已 gitignore，不入库；重建按本手册即可复现。
- 媒体素材只放 `lab/drama/media/`（已 gitignore）。
- 仅第 3 步安装需要联网（由协调者执行）；实验脚本本身离线可跑。

## 可选：版本快照

依赖装完后留一份精确版本，便于事后复现当时结果：

```powershell
.venv-lab\Scripts\python.exe -m pip freeze > lab\drama\results\requirements-frozen.txt
```
