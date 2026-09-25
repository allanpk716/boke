# REPORT — 影视剧配音研究实验台账（lab/drama）

> 实验基准：`PLAN.rev1.md`（评审修订版 = 实施基准，待用户晨间确认后替换 `PLAN.md`）。
> 状态取值：`pending`（未开始）/ `in_progress`（进行中）/ `done`（完成）/ `blocked`（受阻，记原因）。
> **provisional 约定**：客观兜底未经盲听确认的结论一律标 `provisional`；对应盲听轮（R1~R4）完成后确认或推翻，未确认前不写成定论。
> 台账纪律：每轮记 素材/参数/指标/结论，**fail 也记原因**；原始数据落 `results/`，本文件只引数字。
> 本版为票08 夜链汇总（2026-09-25），覆盖票02~票07 全部客观结果；晨报另见仓库根 `MORNING_REPORT.md`，两文件合读即可晨间决策。

## 0. 总览

| 实验 | 内容 | 状态 | 产出位置 | 结论/备注 |
|---|---|---|---|---|
| E1 | 三臂 ABX（组件闸门：分离/回混可听性） | in_progress | `results/abx/trials_20260925_F1-corrected-separation-arm.json` + `results/abx/README_listener.md` | 分离对照臂试听包就绪（18 试次 F1 修正版，verify 全过），待用户 R1 盲听；主臂真克隆版与克隆对照臂待 CosyVoice（次日）。结论全部 provisional |
| E1' | 分离方法横评（找 E1 最优配置） | in_progress | `results/e1/separation_round1.md` + `.json` | 首轮单模型客观横评 30 段完成（provisional）：相减恒等 30/30 bit 级；多模型横评（BandIt-v2/SDX'23）未做，配置选型留次日/R2 |
| E2 | 伪重叠 TSE + 声纹自检门 | in_progress | `results/e2/pairs_stats.md` + `results/e2/voice_gate_realexample.json` | 构造集 18 条对账 0 偏差；ECAPA 真实例分布重叠 → 0.30 占位阈值不可用（自校准必须）；TSE 两候选缺依赖冒烟待跑 |
| E3 | 时长三段式（双向）+ 变速阈值定标 | blocked | —（`e3_timing_remix/` 空） | 未做：无 LLM API key 接入 + CosyVoice 未装，脚本未开工；夜间资源边界而非遗漏（见 §3.4），留次日 |
| E4 | 5.1 中置路线（条件实验，视素材而定） | in_progress | `results/e4/asr_compare.json` + `results/e4_center51.md` | 快测成立 provisional：中置 WER 0.342 < 下混 0.453 → 对白进中置；正式实验（vs 分离对白轨 WER + ABX 小组）未做 |
| E3b | 端到端全链（双向，产品级 go/no-go 在此判定） | pending | —（未开工） | 未做：依赖 E1'/E3 选型 + R1/R2 盲听，天然次日+（见 §3.6） |

## 1. 待决清单（晨间确认点）

| # | 事项 | 背景与结果 | 建议 | 状态 |
|---|---|---|---|---|
| F1 | E1 归因设计确认 | 分离对照臂已按 F1 复审修正版制备完成（18 试次；设计裁定：票05 已证 背景+分离人声=原混 bit 级，照字面"B 对齐原混 LUFS"则 A/B 全同不可判，故 B=背景A+分离人声×(+2dB 对白再平衡)+母带响度对齐，与真实换声链分离/回混部分同构，详见 §3.1）。归因回合的执行待本项确认后解除（且仅当 R1 识别率 >65% 强制）。 | **晨间第一决策**：确认 +2dB 对白再平衡的 B 臂设计；确认后归因回合解锁。 | 待确认（晨间第一决策） |
| F3 | GPU 探测落账 | 已由票04 执行落账 `results/env_probe.json`：RTX 3060 12GB、CUDA 12.6 可用、.venv-lab 就绪（数字见 §2.1）。 | 无需决策，知悉即可。 | 已落账 |
| F4 | bleed sanity | sanity 已执行（票05）：无对白负例上 Whisper vad 开幻觉 0 词（n=3，弱证据），vad 关残留 ≤1 词=幻觉基线，主口径（vad 开）bleed 数字可信。bleed 与 SDR 的相关方向验证因 bleed 几乎全 0（10 段可算中仅 1 段非零）暂不可判，本轮 bleed 未用于配置排序。 | 方向验证留正式多模型横评时补做。 | sanity 已执行；方向验证留后续 |
| F9 | 判据口径 | 夜间执行口径=分档（50%/65%）优先于显著性检验；闸门判定用总体 18 试次，分层（stratum 字段）只报告不判闸（trials json `note_F9`）。 | 照夜间执行口径确认即可，无返工风险。 | 待确认 |
| F10 | 样本量 | 已执行：E2 漏检/误放行均附分母（1/4、4/6）；构造集 18 条=TSE 指标分母；E1' bleed 附 n=10。 | 照此口径维持。 | 已执行（待确认） |
| F11 | 措辞 | manifest note 已按候选池口径记录：Sintel 对白重叠段稀缺，双人重叠配额由 B 站 zh 剧承担；en 侧缺"多人重叠生活对白"形态、分层结论适用范围受限已声明。E3b 未做，报告措辞届时照此口径执行。 | 确认口径措辞，E3b 报告时沿用。 | 前置已落（待确认） |

## 2. 环境与 GPU 探测落账（F3）

### 2.1 环境与 GPU（票04 `results/env_probe.json`，2026-09-25 09:00）

- **GPU**：NVIDIA GeForce RTX 3060，显存 12288 MiB（torch 侧实测 12287 MiB），驱动 560.70。
- **.venv-lab（状态 ready）**：Python 3.11.9；torch 2.14.0+cu126，**CUDA 12.6 可用**；包清单：audio-separator 0.47.0、faster-whisper 1.2.1、museval 0.4.1、speechbrain 1.1.1、soundfile 0.14.0、scipy 1.17.1、matplotlib 3.11.2、pyyaml 6.0.3。
- **工具链**：ffmpeg / ffprobe 6.0-full_build（gyan.dev，scoop shim）。
- 注：系统解释器（非 venv）缺 audio-separator/faster-whisper/speechbrain 等包，全部实验走 `.venv-lab`。CosyVoice 未装（装入过重，留次日）。

### 2.2 素材与 manifest 汇总（票02/票03/票06）

- **manifest 总条目 65** = blender（Sintel）30 + bili（亮剑）17 + constructed（伪重叠）18（与 `results/e2/pairs_stats.md` §1 一致）。
- **分层配额表**（来源×语言，数字=provisional 粗标计数/配额目标；**confirmed 全为 0**——全部标签待听审复核，粗标数不计入 confirmed）：

| 来源/语言 | 对白干净 | 音乐重 | 音效压对白 | 双人重叠 | 备注 |
|---|---|---|---|---|---|
| blender / en | 8/3 达标 | **2/3 缺 1** | 8/3 达标 | —（Sintel 重叠段稀缺，manifest note 已声明） | 另有待听审标注 12 段 |
| bili / zh | 6/3 达标 | 3/3 达标 | 4/3 达标 | 4/4 达标 | solo_candidate 4 段=票06 伪重叠原料 |

- 音乐重 blender 侧缺 1 段 → E1' 音乐重层仅 2 段、按弱证据对待（见 §3.2）。
- **亮剑选片理由（一句话）**：战争剧一张片源覆盖三个难分层——密集对话/争吵（双人重叠原料）、战斗爆炸（音效压对白）、煽情配乐段（音乐重），B 站番剧区免费可播、单集 40~45min 足够选段（`tools/bili_fetch.md`）；ep01 选 10 段 + ep02 选 7 段，标注=能量启发式+剧情锚点推断，全部 provisional 待听审。
- **en 侧替代**：B 站无免费可看的英文真人剧（协调者核实），en 真实轨由 Sintel（英文对白）充当（票面预案）；影响=en 缺"多人重叠生活对白"形态，en 分层结论适用范围受限（报告需注明）。
- **E4 快测执行说明**：`results/e4/asr_compare.json` 由协调者在夜链执行 `e4_center51/asr_compare.py` 产出，实施泳道未复跑，数字原样引用。

## 3. 实验记录

### 3.1 E1 三臂 ABX（组件闸门）

- **轮1（票07，夜间交付=分离对照臂试听包，全部 provisional）**
  - 素材：Sintel 30 段 + 亮剑 17 段的分离三轨产物（orig/vocals/bgA）已在位（`media/separated/`，共 47 段，本票未新跑 GPU 分离）。
  - **替代决策（显著）**：CosyVoice 未装 → **主臂（分离床+真克隆配音回混）与克隆对照臂今晚缺席**，克隆版次日补装环境后另行制备——不是静默降质（trials json `note_substitute`）。今晚只测分离对照臂（F1 复审修正版）。
  - **设计裁定（F1 修正版，trials json `note_design`）**：票05 已证 背景A+分离人声=原混（bit 级），照字面"B 对齐原混 LUFS"恒为 0 dB、A/B 完全相同、ABX 不可判；按票面"更干净设计在 note 说明后采用"条款：**B = 背景A（票05 相减床）+ 分离人声×g，g=固定 +2.0 dB 对白再平衡**（真实换声链中"新对白"按参考对白 LUFS 落位后的代表性残差），再做 B 成片 integrated LUFS 对齐 A（主增益级）与 true peak 联合防护（A/B 同增益，目标 −1.2 dBTP，验收线 −1.0 留 0.2 dB 余量）。A/B 差异=分离伪影+对白电平再平衡+母带响度处理，与真实换声链的分离/回混部分同构。
  - 参数：seed=20260925；每试次 3 段（A/B/X）× 20 s，44.1 kHz 立体声；18 试次成片 integrated LUFS 已对齐（A/B 最大差 0.1 dB，仅 T05，舍入级；响度不露馅）；播放副本（中性命名）在 `media/abx/play/`；制备自检（json↔音频一一对应 verify）全过（票07）。
  - 试次分层：en_dlg_clean 6 / zh_dlg_clean 5 / zh_dlg_clean_backfill 1 / en_sfx 3 / zh_sfx 3（合计 18）。
  - 选段调整：`bili_liangjian_ep02_g0087_g0088` 因分离人声近无声被剔除（viability ratio=0.016 < 0.10 闸），zh 对白干净层由 `bili_liangjian_ep02_g0000_g0001`（音效压对白）回填 1 席（`media/abx/stimuli_index.json` selection_audit/viability_note）。
  - **near_transparent 三题单列解读提示**：**T03 / T10 / T15**（A/B 中段差异 rms 比 0.00081 / 0.001276 / 0.00969，均 <0.02）背景床几乎无声（对白主导），+2dB 再平衡被母带响度对齐数学抵消，重建≈原混——听不出差异是"该段链路透明"的正确感知，晨间分析对这三题的 50% 命中应按"不可判"解读，**不计入链路缺陷**（trials json `near_transparent_note`，README_listener 同）。
  - 判定依据（F9 口径）：总体 18 题正确率分档（50%=瞎猜 / 65%=可辨），优先于显著性检验；分层只报告。
  - 结论：**未判定**——待用户 R1 盲听（怎么听一句话见 §4 R1）；归因回合执行待 F1 确认（§1）。所有制备侧陈述（如"A/B 差异与真实换声链分离/回混部分同构"）均为设计判断，未经盲听验证。

### 3.2 E1' 分离横评

- **轮1（票05，provisional）**：模型 `melband_roformer_instvox_duality_v2.ckpt`（normalization_threshold=1.0，保三轨同幅度尺度——默认 0.9 会压低输入且不回补，污染相减背景）；30 段 Sintel；真值参照=sintel_center.wav FC 中置按时间窗取段；ASR=faster-whisper small cpu-int8 (en, vad)。
  - **相减恒等成立：30 段全部逐样本 bit 级相等（max|Δ|=0，30/30）**——背景A 由纯波形相减产生（原混−人声，float32 落盘），不引入第二模型自由度。
  - **相减 vs 模型第二输出（背景A vs 背景B）**：差异能量（相对背景A）总体均值 **−29.71 dB**（−29.7083）；对白残留 bleed 均值 相减=0.10 vs instrumental=0.10（打平）。
  - SI-SDR（vs 中置，best 口径=±60 样本/±1.4ms 微对齐后最优）总体 median **−2.36** / mean −5.30；对白干净层 median −0.35、**corr median 0.69**；音效压对白 median −1.77 / corr 0.63；待听审标注 median −3.09 / corr 0.57；音乐重（仅 2 段）median −21.39 / corr 0.31。
  - bleed（背景轨听写词数/原混词数，vad 开）：可算 10 段（原混有词者），bgA 均值 0.10，其中仅 1 段非零（g0024=1.00），其余全 0。
  - **口径注记（评审"口径判断补 conclusions"，在此写明）**：真值参照=FC 中置（48k mono），而人声轨取自立体声下混（L+R 均值）分离——**两者不是同一混音母版**，故 SI-SDR/corr 的绝对值只在"本轮同口径"内做分层相对比较，**SI-SDR 中位 −2.36 不作跨母版的"分离质量分"引用**；母版无关的硬结论是相减恒等（30/30 bit 级）与 bleed 打平。本轮未据 SI-SDR 做配置排序。
  - F4 负例（无对白切片上的 Whisper 幻觉，弱证据）：音乐重 2 段 + 补 1 段待听审标注（g0010），vad 开下 bgA/bgB 听写词数全 0；vad 关残留 ≤1 词（g0000=1，其余 0）=幻觉基线。
  - **音乐重仅 2 段（配额缺 1），该层数字按弱证据对待**。
  - 结论（provisional）：相减纪律可作为背景床的标准产法（恒等+不劣于模型第二输出）；**E1 复跑配置的选定未完成**——BandIt-v2 / SDX'23 横评未跑（spec Out of Scope：只冒烟不作承诺），选型留次日/R2。

### 3.3 E2 伪重叠 TSE + 声纹自检门

- **构造集（票06，provisional）**：4 条 bili/zh solo 原料 × C(4,2)=6 配对 × SNR{0, +3, −3}dB = **18 条**（F10 分母）；每档 6 条；时长 30~60s（=两源较短者）；混音定义 两轨 RMS 归一 → B 乘 10^(−SNR/20) → 峰值≤0.98 → int16。
  - 对账（抽 3 条读回复核）：**3/3 PASS，逐样本 mix==gtA+gtB 0 样本不符，实测 SNR 偏差 0.000 dB**。
  - 性别分层：**全部 unknown/unknown（18/18，100%）**——solo 段未听审无性别标注，待听审后按 manifest `gender_pair` 字段重算。
- **ECAPA 声纹门真实例（票06 R1 补录，权威记录=`voice_gate_realexample.json`）**：输入=4 条 solo 段各取两个不重叠 10s 窗；同源试次 n=4，异源 n=6；模型=speechbrain ECAPA-TDNN VoxCeleb（192 维，HF 缓存离线）；阈值=0.30（占位）。
  - 同源（n=4）：min 0.1981 / **median 0.7030** / max 0.8687；异源（n=6）：min 0.0566 / **median 0.3937** / **max 0.7156**。
  - **分布重叠（same_min 0.1981 < cross_max 0.7156）→ 占位阈值 0.30 下漏检 1/4（25%）、误放行 4/6（66.7%）→ 0.30 阈值不可用，调研04 自校准定标是硬前置**。
  - 两条 caution（均不进结论）：① 同源最低 0.1981 的窗口可能内容不纯（solo 段未听审，静音/配乐/换人混入），低分不必然是"门错"；② 异源语义=不同素材段，不等于确证不同说话人（可能是同一真人在两集的独白），高分不能直接判为门的误报。
  - **口径澄清**：先前汇报"同源 0.87 / 跨源 0.10"经核实为 `voice_gate.py --selftest` 的**自测口径**（固定 seed 随机向量：同源簇 min 0.8706 / 异源簇 max 0.0997），并非真实例；两套口径自此分开记，以真实例实测为准。
- **TSE**：两候选 NeMo `tse_sortformer_4spk-v1` 与 X-TF-GridNet **均缺依赖，冒烟待跑**（`e2_overlap/tse_smoke.py` 就绪检查+调用骨架已就绪，今晚不装包；冒烟判据=对 18 条构造集出 SI-SDR 分层表，不设 PASS/FAIL 闸门）。
- 结论（provisional）：数据侧就绪（构造集 18 条+真值三轨）；**声纹门在自校准定标前不可用；E2 判据（提错人漏检率=0 一票否决）未到判定点**。

### 3.4 E3 时长三段式（双向）+ 变速阈值定标

- **轮1：未做。**
  - 原因（诚实记录）：无 LLM API key 接入、CosyVoice 未装（TTS speed 档无源），时长三段式（LLM 音节预算多候选 → TTS speed → atempo 兜底）脚本未开工（`e3_timing_remix/` 空）。
  - 这**不是遗漏而是夜间资源边界**：spec 明确夜间实施范围=可机器自主完成的部分，Out of Scope 另列盲听轮与归因回合；E3 依赖的 LLM/CosyVoice 两项资源夜间不可得，留次日。
  - 进窗率 / atempo>1.15 使用率 / 定标拐点均无数字，不引用任何占位值。

### 3.5 E4 5.1 中置路线（条件实验）

- **前置核实（票02，`results/e4_center51.md`，全部实测）**：Sintel 正片混音本身即 AC3 **5.1(side)** 6 声道 48 kHz（888.032 s），FC 中置已派生 `sintel_center.wav` 并**升格为全研究的对白参照主角色**；BBB 5.1（596.458 s）成立但**全片无对白**，只承担中置音乐/音效能量占比测量与无对白反例校准；Tears of Steel 官方镜像 404 下架（缺口如实记录，不阻塞）。
- **M&E 分轨 mastering 失配证伪（如实记录）**：M&E 与正片下混不是同一 mastering——最优增益 **−21.2 dB**、极性为负、配准后滑动窗相关中位仅 **0.274**（逐样本相减要求 ≈1）→ `sintel_true_dialogue.wav` 废弃留档，M&E 降级为音乐/音效活动与频谱参照。
- **快测（协调者执行，`results/e4/asr_compare.json`，provisional）**：faster-whisper small cpu-int8（cublas12 缺 dll，GPU 路径留待补装 nvidia-cublas-cu12），ref_words=117；**中置 WER 0.342**（hyp 77 词） vs **立体声下混 WER 0.453**（hyp 66 词）→ **对白进中置成立（provisional）**。
  - 注意口径：快测对比是"中置 vs 立体声下混"；PLAN E4 正式判据是"C 通道对白纯度显著优于**分离对白轨**"（C 通道 WER vs 分离人声轨 WER），**该项未测**。
- 结论：**有 5.1 优先走中置**的结论暂按 provisional 成立；正式 E4（判据对比 + C 通道音乐能量占比 + E1 配置 5.1 复跑 ABX 小组）未做。

### 3.6 E3b 端到端全链（产品级 go/no-go）

- **轮1：未做。** 依赖 E1'/E3 的配置选型 + R1/R2 盲听（PLAN §⑥ 触发点：R2 最晚在 E3b 选定配置前），E3 未开工、选型未定，E3b 天然次日+。选段占比（候选池口径，F11）/ 自然度 / LUFS 差均无数字。

## 4. 盲听轮记录（R1~R4）

- **R1（E1 后）：待用户，试听包已就绪。** 怎么听（一句话）：封闭耳机+安静房间、音量调定后全程固定，每题依次播 `T<nn>_1`→`T<nn>_2`→`T<nn>_X`，判断 X 更像 _1 还是 _2（二选一，推荐直接填 trials json 的 `answer` 字段），18 题约 20~30 分钟；**T03/T10/T15 三题反复听仍觉得一样是正常的，凭第一感觉猜一个照常记录**；听完全部 18 题之前不要看 json 的 `X` 与 `play_order` 列（答案钥匙）（`results/abx/README_listener.md`）。
- R2（E1' 后）：待触发——最晚在 E3b 选定配置前；当前 E1' 多模型横评未做，R2 素材未制备。
- R3（E3 后）：待触发——E3 未开工。
- R4（E3b 后）：待触发——E3b 未开工。

## 5. 夜间未做与为什么（诚实清单）

| 项 | 为什么 | 下一步 |
|---|---|---|
| R1~R4 盲听 | 要用户耳朵，机器不可替代（spec Out of Scope） | R1 试听包已就绪，晨间即可听 |
| E1 归因回合执行 | F1 修正待用户确认 + 需 R1 结果（仅识别率 >65% 强制）；spec Out of Scope | 确认 F1 → R1 出数 → 按需执行 |
| E1 主臂真克隆版 + 克隆对照臂 | CosyVoice 未装（装入 .venv-lab 过重），替代决策已注明非静默降质 | 次日补装环境后制备 |
| E3 时长三段式 | 无 LLM API key + CosyVoice 未装，脚本未开工；资源边界而非遗漏 | 次日补资源后开工 |
| E3b 端到端 | 依赖 E1'/E3 选型 + R1/R2 盲听 | 选型与 R2 后排期 |
| E1' 多模型横评（BandIt-v2 / SDX'23） | spec Out of Scope：只冒烟不作交付承诺；首轮为单模型基线 | 次日按需展开 |
| TSE 冒烟 | 两候选（NeMo tse_sortformer / X-TF-GridNet）依赖重未装；今晚不装包（票06 约定） | 脚本已就绪，装包后跑 |
| DnR v2 全量 | 10.7GB（10×1GB 分卷）不值，真值已由 Sintel 中置通道承担 | 需"教科书域"参照时再补拉 |
