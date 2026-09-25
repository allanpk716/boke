# 票05 · 分离客观横评首轮（mel_band_roformer + 相减纪律 + bleed）

## What to build
E1' 客观侧首轮：①e1_separation/separate.py（audio-separator 加载 mel_band_roformer（Kimberley Jensen 版伏 KP），输入片段→人声轨+背景床两版：背景A=波形相减（原混−人声）、背景B=模型 instrumental 输出，均落盘）；②e1_separation/objective.py：真值轨（DnR 自带 D/M/E stem）算 museval SDR/SI-SDR（对背景床与人声轨分别）；bleed 指数（faster-whisper 对 原混/背景A/背景B 听写，WER 以 DnR 转写为参照）+ **F4 sanity：先在真值轨验证 bleed 与 SDR 的相关方向 + 无对白负例（纯音乐段）的 Whisper 幻觉率**；非对白窗背景A 与原混逐样本一致性（相减法应恒等，工程验证）；③run_first.py 跑真值轨全部已到片段，结果按分层汇总到 results/e1/separation_round1.md（+json）。
## 验收标准
- [ ] separate.py 产出人声/背景A/背景B 三轨且可重跑
- [ ] objective.py 输出 SDR/bleed/一致性三组数字，分层汇总
- [ ] F4 sanity 记录在结果文件（负例幻觉率+相关方向结论）
- [ ] 相减恒等验证有明确结论（一致/不一致及原因）
- [ ] 全部数字可从 results/e1/ 的 json 追溯
## Blocked by
票02（素材与 manifest）, 票04（环境）
## 涉及路径
lab/drama/e1_separation/**（不含 abx/ 子目录）; lab/drama/results/e1/**
## 副作用声明
GPU 推理（3060，每片段分钟级，声明允许执行并等待完成）；faster-whisper 权重在 .venv-lab 环境内离线可用，缺则报 NEEDS_CONTEXT
decision_refs: F4, F6
review_blocks: 无
