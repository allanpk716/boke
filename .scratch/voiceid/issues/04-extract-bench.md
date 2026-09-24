# 票04 · embedding 抽取器 + 验证实验脚本

## What to build
三个新文件,打通"音频→256 维向量"与实验验证:

1. `work/tools/emb_extract.py`(.venv-diar 侧脚本):读音频路径列表,用 pyannote 加载 embedding 模型(community-1 内置 WeSpeaker ResNet34,与分人管线同源;可用 `pyannote/wespeaker-voxceleb-resnet34-LM` 的 Inference(window="whole") 官方用法),输出 npy/json 到指定路径。跑在 .venv-diar(该 venv 有 pyannote 4.0.7+torch),不装新依赖。
2. `src/boke/voiceid_extract.py`(主 venv 侧封装):extract(path) → 256 维向量;子进程调 .venv-diar 跑 emb_extract.py,沿用 src/boke/diarize.py 的隔离 venv 子进程模式(懒加载、超时、产物落临时目录再读回);并提供 extract_many(paths)。**样本直抽语义(F2 解除裁定):档案向量一律由本抽取器从存储样本片段直接抽取,与整期分人质心无关。**
3. `work/tools/voiceid_bench.py`(实验脚本,主 venv 编排、GPU 侧经 1):三个子命令——
   - `matrix`:对指定 episode(spk_emb.npy+rttm)×人物参考音跑 SPK×person 分数矩阵,输出同人/冒认分布、EER、0.02 分 bin 直方图到 work/voiceid_bench/;
   - `--verify-alignment`:真实 episode 核对 spk_emb.npy 行序=rttm SPK 标签顺序(F8 对齐断言,GPU 环境);
   - `--recompute-consistency`:对 persons_emb.json 抽样条目,用抽取器重抽样本 embedding 与缓存向量比对,断言 cos≥0.999(F2 解除验收;stub 模式可注入假模型单测)。
   第一轮实验素材与判据见调研底稿 docs/research/04 §6(跑不跑真实 GPU 不 gate 交付,命令在即可)。
4. `.gitignore` 最小放行:追加一行 `!work/tools/`(work/ 整体被忽略,新建脚本需要精确放行;不要放开 work/ 其他子目录)。

## 验收标准
- [ ] voiceid_extract:stub 子进程(monkeypatch)单测——调用参数/产物读回/超时缺省;不真跑 venv 也能全绿
- [ ] voiceid_bench:参数解析+输出格式单测(stub 向量);三个子命令 --help 可用
- [ ] .gitignore 放行后 git check-ignore work/tools/voiceid_bench.py 不再命中
- [ ] GPU 实跑说明写进文件 docstring(验收时可选实跑:matrix 对一个已有 diar.rttm 的 episode)
- [ ] 全量 pytest 零回归

## Blocked by
无,可立即开始。

## 涉及路径
- src/boke/voiceid_extract.py(新)
- work/tools/emb_extract.py(新)
- work/tools/voiceid_bench.py(新)
- tests/test_voiceid_extract.py(新)
- .gitignore(改:追加 !work/tools/)

## 副作用声明
- 独占验证命令:pytest tests/test_voiceid_extract.py -q;git check-ignore work/tools/voiceid_bench.py
- 不实跑 GPU/不装依赖(venv 已就绪,实跑留给验收可选步骤)

## decision_refs
D16(复用内置 embedding)、D15(实验不 gate)、D2(样本直抽)

## review_blocks
F2(重算一致性断言工具在本票;解除条件=本工具+票06 验收调用)、F8(对齐断言命令)
