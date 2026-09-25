# results/e4_center51.md — E4 前置：5.1 声道核实结论（D0）

> 核实日期：2026-09-25（夜链）。实测工具：ffprobe（本机 PATH）。
> 结论先行：**E4 成立，且角色升格**——Sintel 正片混音本身就是 5.1，其 FC 中置通道（已派生 `sintel_center.wav`）成为整个研究真值轨的**对白参照主角色**；BBB 5.1 作为第二 5.1 源承担中置音乐/音效能量测量与无对白反例校准，**不承担对白纯度对比**（全片无对白，见 §3）。

## 1. 声道核实结论（全部实测，无占位）

| 文件 | 音频流实测 | 时长 | 结论 |
|---|---|---|---|
| `media/blender/Sintel.2010.1080p.mkv` | AC3 **5.1(side)**，6 声道，48 kHz，640 kbps（eng，stream #1） | 888.032 s | **5.1 成立**；FC 可直接 `pan` 抽取 |
| `media/blender/sintel_center.wav`（协调者派生） | pcm_s16le **mono** 48 kHz（FC 中置） | 888.032 s | 对白参照主角色，真值轨主评价指标用它与分离人声轨算相关性/SI-SDR |
| `media/blender/sintel_stereo.wav`（协调者派生） | pcm_s16le stereo 48 kHz | 888.032 s | 正片立体声下混，切片网格源 |
| `media/blender/sintel_me.wav`（协调者派生） | pcm_s16le stereo 48 kHz | 888.000 s | M&E 参照；**逐样本相减已证伪**（见 §2） |
| `media/blender/big_buck_bunny_480p_surround-fix.avi` | AC3 **5.1(side)**，6 声道，48 kHz（stream #1） | 596.458 s | **5.1 成立**（第二源）；无对白（见 §3） |
| Tears of Steel 官方镜像 | — | — | `download.blender.org/mango/` **404 下架**，缺口如实记录；不阻塞（Sintel 已覆盖需求） |

复现：`ffprobe -v error -show_entries stream=codec_name,channels,channel_layout,sample_rate -of json <文件>`（完整清单见 `tools/media_sources.md`）。

## 2. M&E 分轨降级（协调者实测坏消息，如实记录）

原计划"真对白 = 正片音频 − M&E 波形相减"**不可行**：M&E 分轨与正片下混**不是同一 mastering**——
实测最优增益 −21.2 dB、极性为负、配准后滑动窗口相关中位数仅 **0.274**（逐样本相减要求相关≈1）。
由此：

- `sintel_true_dialogue.wav`（相减产物）**废弃**，留档防重复尝试；
- M&E（`sintel_me.wav`）降级为**音乐/音效活动与频谱参照**（能量包络对照、频谱对比、非对白窗背景一致性参照）；
- 真值主角色由 **Sintel FC 中置通道**承担（影视混音对白进 C 的惯例）；亮剑等无 5.1 的真实轨走 bleed+能量代理指标。

## 3. BBB 5.1 的用途与"对白稀缺"声明

**用途**（E4 展开后使用）：
1. **中置通道音乐/音效能量占比测量**——E4 指标"C 通道音乐能量占比"在 BBB 上可直接测（它只有音乐+音效，C 通道内容即纯非对白能量，是理想的上界参照）。
2. **无对白反例校准**——验证"C 通道 ≈ 对白"这一假设在无对白片上的失效形态，防止把 C 通道能量误读为对白。

**对白稀缺声明（如实记录）**：Big Buck Bunny 全片**无对白**。E4 的核心对比——"C 通道 ASR WER vs 分离对白轨 ASR WER（以字幕为参照）"——**在 BBB 上无法做**（没有对白可转写，也没有字幕可参照）。该项对比只能用 Sintel 做。BBB 也不进对白分层配额（manifest 中无对白相关 layer 条目）。

## 4. 对 E4 实验设计的影响（对 PLAN §④-E4 的修订要点）

| PLAN 原设定 | 实测后修订 |
|---|---|
| E4 是"条件实验，视素材核实而定" | 条件已满足，**E4 成立**；且中置通道价值外溢到 E1/E1'（对白参照主角色） |
| 5.1 源待核实（D0） | Sintel + BBB 双 5.1 源已核实；ToS 404 缺口不阻塞 |
| C 通道对白纯度 vs 分离对白轨 WER | 仅 Sintel 可做；BBB 承担能量占比与反例校准 |
| E4 判据"C 通道对白纯度显著优于分离模型 → 有 5.1 必走中置" | 判据不变；真值参照（sintel_center.wav）同时服务 E1'/分离横评的 SI-SDR/相关性指标 |

## 5. 待办与边界

- [x] 声道核实（本文件 §1，全部实测）
- [x] BBB 无对白对 E4 的影响声明（§3）
- [ ] E4 正式实验（抽 C 通道 → 对白纯度对比 → ABX 小组）在 D3 排期执行，产出另落 `e4_center51/center_eval.md`（PLAN 原路径）
- [ ] BBB 是否切网格：视 E4 展开再定（当前不入 manifest 配额）
