# 票04 · lab 独立环境与工具箱（GPU 探测落账）

## What to build
研究环境就位：①lab/drama/requirements-lab.txt 与 tools/setup_env.md（建 .venv-lab 的完整步骤：audio-separator[gpu]、faster-whisper、museval、soundfile、pyyaml、speechbrain、matplotlib、scipy；注明全部装在 .venv-lab，**严禁动主线 .venv / .venv-diar**）；②common/ 小工具（不从 boke import：ffmpeg 包装 run_ffmpeg、wav 读写、按需最小集——参考主线 align_mix 的纯标准库风格自行实现）；③tools/probe_env.py（探测 GPU 型号/显存/ffmpeg 可用性/torch.cuda/关键包版本→写 results/env_probe.json）。
## 验收标准
- [ ] probe_env.py 可跑且 results/env_probe.json 落盘（预期 GPU=RTX 3060 12GB）
- [ ] common/ 小工具有最小自测（如 wav 写读回环）
- [ ] setup_env.md 步骤可复现（协调者照此建 venv）
- [ ] 代码中不出现对 src/boke 或 .venv* 的任何 import/路径引用
## Blocked by
无（路径与票01 相交于 common/.gitkeep，若 01 未落地先自建 common/ 目录）
## 涉及路径
lab/drama/requirements-lab.txt; lab/drama/tools/setup_env.md; lab/drama/tools/probe_env.py; lab/drama/common/**; lab/drama/results/env_probe.json
## 副作用声明
协调者执行 pip install（联网，装 .venv-lab）；局部验证=python 语法检查+common 自测
decision_refs: D1, D2, D8, F3
review_blocks: 无
