# 票01 · 分人产物落盘每说话人 embedding

## What to build
分人跑完时,把 pyannote `DiarizeOutput.speaker_embeddings`(形状 num_speakers×256,顺序与 rttm SPK 标签对齐)与标签顺序一并落盘到分人产物目录:`work/<id>/spk_emb.npy`(向量矩阵)+ `work/<id>/spk_emb.json`({"labels": ["SPK_00", ...], "dim": 256})。`src/boke/diarize.py` 登记这两个产物路径(沿用其产物清单惯例)。mock/桩分人路径无 embeddings:两个文件都不写,下游以"文件不存在"判定该分 P 无声纹建议(优雅缺省,不报错)。

背景:pyannote 4.x 管线输出自带该字段(已核源码:`DiarizeOutput.speaker_embeddings` 按 `speaker_diarization.labels()` 顺序排序);`work/tools/diar_run.py` 现在拿到了 DiarizeOutput 只用了 rttm 一半,顺手把另一半存下来,~3 行 np.save + json.dump。

## 验收标准
- [ ] stub DiaroseOutput(构造假 embedding 矩阵+labels)单测:两文件落盘、形状与顺序正确、labels 与 rttm SPK 一致
- [ ] 无 embeddings 的路径(mock/桩):两文件不写、流程不报错(既有 SKIP_GPU 冒烟不回归)
- [ ] 提供 GPU 对齐校验命令(work/tools/voiceid_bench.py --verify-alignment 将在票04实现;本票在 diar_run.py 侧保证字段直通,不留硬编码假设)
- [ ] 既有 diarize 相关测试全绿;全量 pytest 零回归

## Blocked by
无,可立即开始。

## 涉及路径
- work/tools/diar_run.py(改)
- src/boke/diarize.py(改:产物登记)
- tests/test_diarize_emb.py(新)

## 副作用声明
- 独占验证命令:pytest tests/test_diarize_emb.py tests/ -x -q

## decision_refs
D13(分人后自动算的前置)、D16(复用管线自带 embedding)

## review_blocks
F8(标签对齐假设——本票为对齐断言提供数据侧保障)
