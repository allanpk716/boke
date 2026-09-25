# 票07 · E1 三臂 ABX 试听包制备

## What to build
听感实验的"待听包"（只制备不执行听）：①e1_separation/abx/make_stimuli.py——主臂：分离背景床（票05 背景A）+克隆配音回混（克隆 TTS 若 .venv-lab 内 CosyVoice 可用则真克隆；不可用则"原对白直通+背景床"版先打通并显著标注 substitute）；克隆对照臂：同一批句子的克隆声（或原声 substitute）干声段；**分离对照臂（F1 复审修正版）：主臂实际分离所得背景床+原对白回混**，产物与文件名标注 "F1-corrected-pending-user"；三臂统一响度配平（loudnorm 对齐原对白轨 integrated LUFS）+true peak 校验；②e1_separation/abx/make_trials.py——试次清单 json（每试次 A/B/X 文件与真值标签，X 随机、A/B 顺序随机，16~20 试次/语言条件、总体口径；F9：分档优先于显著性、闸门看总体、分层只报告）；③results/abx/README_listener.md（听者说明：耳机/环境/时长/如何记录）+ trials_*.json。
## 验收标准
- [ ] 三臂刺激存在，文件名含臂名/语言/分层标签；substitute 版（若有）显著标注
- [ ] 分离对照臂明确标注 F1 修正版待确认
- [ ] 每试次三段等长、响度配平后有 true peak 数字
- [ ] trials json 与音频一一对应，自检脚本通过（无缺文件）
- [ ] README_listener.md 自包含（不看其他文件就能开始听）
## Blocked by
票02, 票03, 票04, 票05
## 涉及路径
lab/drama/e1_separation/abx/**; lab/drama/results/abx/**
## 副作用声明
GPU 推理允许（TTS/分离复用票05 产物）；无网络
decision_refs: D7, F1, F9
review_blocks: F1（归因回合设计与执行——本票仅按修正版"制备"，执行留 R1 盲听后）
