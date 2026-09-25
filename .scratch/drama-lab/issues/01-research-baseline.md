# 票01 · 研究基线入库与台账骨架

## What to build
把本夜链的研究产物固化为研究线基线：lab/drama/ 目录骨架（各实验子目录+results+common+tools）、REPORT.md 台账骨架（总览表含六实验状态列、provisional 约定="客观兜底未经盲听确认的结论一律标 provisional"、待决清单占位 F1/F3/F4/F9/F10/F11）、PLAN.rev1.md（复制自 `.xcheck/20260925-083546/proposal.md`，文件头注明"评审修订版=实施基准，待用户晨间确认后替换 PLAN.md"）。原 lab/drama/PLAN.md 保持不动（original_source，夜链不写）。
## 验收标准
- [ ] lab/drama/{e1_separation,e2_overlap,e3_timing_remix,e4_center51,results,common,tools}/ 存在（media/ 不建占位）
- [ ] PLAN.rev1.md 与 .xcheck/20260925-083546/proposal.md 逐字一致（除文件头追注的来源说明行）
- [ ] REPORT.md 含总览表、provisional 约定、待决清单占位
- [ ] 核对 .gitignore 已含 .venv-lab/ 与 lab/drama/media/（已在，只核对）
## Blocked by
无，可立即开始
## 涉及路径
lab/drama/PLAN.rev1.md; lab/drama/REPORT.md; lab/drama/e1_separation/.gitkeep; lab/drama/e2_overlap/.gitkeep; lab/drama/e3_timing_remix/.gitkeep; lab/drama/e4_center51/.gitkeep; lab/drama/results/.gitkeep; lab/drama/common/.gitkeep; lab/drama/tools/.gitkeep
## 副作用声明
无（纯文件写入；git 提交由协调者执行）
decision_refs: D2, D9, F7
review_blocks: 无
