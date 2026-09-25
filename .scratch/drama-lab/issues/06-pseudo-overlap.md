# 票06 · 伪重叠构造集 + TSE/声纹门冒烟脚本

## What to build
E2 数据与脚本：①e2_overlap/build_pairs.py（从真实轨 manifest 取两人独白段（非重叠窗，用能量间隔或 diarization 输出判定），两两混合 SNR {0,+3,−3}dB，同性别/不同性别分层，输出构造片段+真值两轨，manifest 记 constructed 类型条目）；②e2_overlap/tse_smoke.py（X-TF-GridNet 或 NeMo tse_sortformer 二选一的调用骨架+就绪检查——依赖重，允许交付"就绪检查+调用骨架+冒烟待跑"并如实记录缺什么）；③e2_overlap/voice_gate.py（speechbrain ECAPA 抽 embedding：输入注册音+TSE 输出→cosine+阈值判定，输出 拦截/放行，附 F10 样本量字段）；④构造集规模与分层统计落 results/e2/pairs_stats.md。
## 验收标准
- [ ] 构造集生成且每条含两源段 id/SNR/性别分层/真值轨路径
- [ ] 构造混音=两真值轨线性混合可逐样本验证（对账）
- [ ] voice_gate.py 对样例可跑并输出 cosine
- [ ] tse_smoke.py 有就绪检查结论（能跑/缺什么）
- [ ] pairs_stats.md 含样本量（F10）
## Blocked by
票03（独白段原料）, 票04（环境）
## 涉及路径
lab/drama/e2_overlap/**; lab/drama/results/e2/**; lab/drama/media/**（构造产物，不入库）
## 副作用声明
CPU/GPU 轻量推理允许；无网络
decision_refs: F10
review_blocks: 无
