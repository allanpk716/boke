# results/e2/pairs_stats — E2 伪重叠构造集规模与统计（票06）

> 生成时间 2026-09-25T09:48:09+08:00；生成脚本 `e2_overlap/build_pairs.py`（seed=20260925，可复现）。
> 混音定义：两轨 RMS 归一到 1.0 → B 乘 10^(-SNR/20)（**SNR=A/B，A=配对第一源**）→
> 共同缩放峰值≤0.98 → int16；**mix = gtA + gtB 逐样本成立**（读回可验证）。
> 16k 副档实测为立体声（票面背景按 mono 估计，与实际不符）——先 to_mono 下混再混合。

## 1. 规模（F10：后续比率指标一律以本表分母为准）

| 项 | 值 |
|---|---|
| solo 原料段（bili/zh, solo_candidate=true） | 4 |
| 两两配对数 C(4,2) | 6 |
| SNR 档 | 0dB, +3dB, -3dB |
| **构造集总数（F10 分母）** | **18** |
| manifest 总条目（含 Sintel 30 + 亮剑 17 + constructed） | 65 |

SNR 分布：0dB=6 条，+3dB=6 条，−3dB=6 条；时长 30~60s（=两源较短者）。

## 2. 性别分层现状（票06 注意项）

**全部 unknown/unknown**：solo 段没有性别标注（无人听审），无法做同性别/不同性别分层；
**待听审后补分层**，届时按 manifest `gender_pair` 字段重算本节。当前分层统计如实记：

| gender_pair | 条数 | 占比 |
|---|---|---|
| unknown/unknown | 18 | 100% |

## 3. 逐条清单

| clip_id | 源A | 源B | SNR | 时长s | 偏移A/B s |
|---|---|---|---|---|---|
| constructed_ep01_g36_g37__ep01_g47_g48_snr0 | bili_liangjian_ep01_g0036_g0037 | bili_liangjian_ep01_g0047_g0048 | +0dB | 60 | 0.00/0.00 |
| constructed_ep01_g36_g37__ep01_g47_g48_snr+3 | bili_liangjian_ep01_g0036_g0037 | bili_liangjian_ep01_g0047_g0048 | +3dB | 60 | 0.00/0.00 |
| constructed_ep01_g36_g37__ep01_g47_g48_snr-3 | bili_liangjian_ep01_g0036_g0037 | bili_liangjian_ep01_g0047_g0048 | -3dB | 60 | 0.00/0.00 |
| constructed_ep01_g36_g37__ep02_g64_snr0 | bili_liangjian_ep01_g0036_g0037 | bili_liangjian_ep02_g0064 | +0dB | 30 | 26.21/0.00 |
| constructed_ep01_g36_g37__ep02_g64_snr+3 | bili_liangjian_ep01_g0036_g0037 | bili_liangjian_ep02_g0064 | +3dB | 30 | 24.66/0.00 |
| constructed_ep01_g36_g37__ep02_g64_snr-3 | bili_liangjian_ep01_g0036_g0037 | bili_liangjian_ep02_g0064 | -3dB | 30 | 23.28/0.00 |
| constructed_ep01_g36_g37__ep02_g87_g88_snr0 | bili_liangjian_ep01_g0036_g0037 | bili_liangjian_ep02_g0087_g0088 | +0dB | 60 | 0.00/0.00 |
| constructed_ep01_g36_g37__ep02_g87_g88_snr+3 | bili_liangjian_ep01_g0036_g0037 | bili_liangjian_ep02_g0087_g0088 | +3dB | 60 | 0.00/0.00 |
| constructed_ep01_g36_g37__ep02_g87_g88_snr-3 | bili_liangjian_ep01_g0036_g0037 | bili_liangjian_ep02_g0087_g0088 | -3dB | 60 | 0.00/0.00 |
| constructed_ep01_g47_g48__ep02_g64_snr0 | bili_liangjian_ep01_g0047_g0048 | bili_liangjian_ep02_g0064 | +0dB | 30 | 21.95/0.00 |
| constructed_ep01_g47_g48__ep02_g64_snr+3 | bili_liangjian_ep01_g0047_g0048 | bili_liangjian_ep02_g0064 | +3dB | 30 | 5.24/0.00 |
| constructed_ep01_g47_g48__ep02_g64_snr-3 | bili_liangjian_ep01_g0047_g0048 | bili_liangjian_ep02_g0064 | -3dB | 30 | 14.91/0.00 |
| constructed_ep01_g47_g48__ep02_g87_g88_snr0 | bili_liangjian_ep01_g0047_g0048 | bili_liangjian_ep02_g0087_g0088 | +0dB | 60 | 0.00/0.00 |
| constructed_ep01_g47_g48__ep02_g87_g88_snr+3 | bili_liangjian_ep01_g0047_g0048 | bili_liangjian_ep02_g0087_g0088 | +3dB | 60 | 0.00/0.00 |
| constructed_ep01_g47_g48__ep02_g87_g88_snr-3 | bili_liangjian_ep01_g0047_g0048 | bili_liangjian_ep02_g0087_g0088 | -3dB | 60 | 0.00/0.00 |
| constructed_ep02_g64__ep02_g87_g88_snr0 | bili_liangjian_ep02_g0064 | bili_liangjian_ep02_g0087_g0088 | +0dB | 30 | 0.00/4.74 |
| constructed_ep02_g64__ep02_g87_g88_snr+3 | bili_liangjian_ep02_g0064 | bili_liangjian_ep02_g0087_g0088 | +3dB | 30 | 0.00/13.67 |
| constructed_ep02_g64__ep02_g87_g88_snr-3 | bili_liangjian_ep02_g0064 | bili_liangjian_ep02_g0087_g0088 | -3dB | 30 | 0.00/20.80 |

注：偏移=各源内随机窗起点（seed 固定可复现）；等长对（如 60s+60s）窗长=源长，偏移无自由度故为 0.00/0.00——两声源在混音时间轴仍全程共现。
## 4. 混音对账（验收：抽 3 条读回复核）

| clip_id | 逐样本 mix==gtA+gtB | 实测SNR dB | 目标 dB | 偏差 dB | 结论 |
|---|---|---|---|---|---|
| constructed_ep01_g36_g37__ep01_g47_g48_snr0 | PASS(0 样本不符) | -0.000 | +0 | 0.000 | PASS |
| constructed_ep01_g36_g37__ep02_g87_g88_snr-3 | PASS(0 样本不符) | -3.000 | -3 | 0.000 | PASS |
| constructed_ep01_g36_g37__ep01_g47_g48_snr+3 | PASS(0 样本不符) | 3.000 | +3 | 0.000 | PASS |

对账结论：3/3 通过（逐样本和严格相等 + SNR 偏差≤0.1dB）。

## 5. 后续接口（票06 交付的下游）

- TSE 冒烟：`e2_overlap/tse_smoke.py`（就绪检查+调用骨架；输入=mix，配对真值=gt_a/gt_b，指标 SI-SDR）。
- 声纹门：`e2_overlap/voice_gate.py`（ECAPA cosine+阈值拦截/放行，输出带 F10 样本量字段）。
