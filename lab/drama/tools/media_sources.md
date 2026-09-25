# media_sources.md — 真值轨素材来源清单与 ffprobe 核实结论

> 用途：记录 Blender 官方真值轨素材的下载 URL 与每个文件经 ffprobe 实测的流布局结论。
> 核实日期：2026-09-25（夜链）。工具：本机 PATH 内 ffprobe（scoop shim）。
> 素材文件本身 gitignored（`lab/drama/media/**`），本文件入库。

## ① Blender 官方下载 URL 清单（协调者 2026-09-25 实测探测）

| 素材 | URL | 状态 |
|---|---|---|
| Sintel 正片 1080p | `https://download.blender.org/durian/movies/Sintel.2010.1080p.mkv` | **已到** `media/blender/`（1,180,090,590 B） |
| Sintel 官方 Music&Effects 分轨 | `https://download.blender.org/durian/movies/sintel-m+e-st.flac` | **已到** `media/blender/`（74,266,467 B） |
| Sintel Bluray 原盘 | `https://download.blender.org/durian/movies/Sintel-Bluray.iso` | **不拉**（20GB 级，收益不抵体积） |
| Big Buck Bunny 480p 5.1 surround 修正版 | `https://download.blender.org/peach/bigbuckbunny_480p_surround-fix.avi.zip` | **已到并解压** `media/blender/`（zip 220,514,660 B → big_buck_bunny_480p_surround-fix.avi 220,514,438 B） |
| Big Buck Bunny 1080p surround | `https://download.blender.org/peach/`（目录实测含 `1080p_surround` 条目） | 备选，未拉（480p 对音频研究足够） |
| Tears of Steel 正片 | `https://download.blender.org/mango/` | **404 下架**（缺口，见 ⑤） |
| DnR v2 数据集 | Zenodo 记录 6949108（10 个 1GB 分卷） | **deferred**：全量 10.7GB 今晚不拉，真值由 Sintel 中置通道承担（见 ⑤） |

### 协调者派生的工作文件（同目录，均经 ffprobe 实测）

| 文件 | 布局 | 时长 | 用途 |
|---|---|---|---|
| `sintel_center.wav` | pcm_s16le **mono** 48 kHz | 888.032 s | **正片 FC 中置通道——对白参照主角色**（见 ②） |
| `sintel_stereo.wav` | pcm_s16le **stereo** 48 kHz | 888.032 s | 正片立体声下混——切片网格源（cut_clips.py 用它切） |
| `sintel_me.wav` | pcm_s16le stereo 48 kHz | 888.000 s | M&E 转 wav——**已降级**为音乐/音效活动与频谱参照（见 ②） |
| `sintel_en.srt` | 英文字幕 | — | E3 时长实验对白参照 |
| `sintel_true_dialogue.wav` | pcm_s16le stereo 48 kHz | — | 正片−M&E 逐样本相减产物，**已废弃**（mastering 失配，不可作真值；留档防重复尝试） |
| `media/bili/liangjian_ep01.m4a` / `ep02.m4a` | AAC stereo 48 kHz（43.5/45.1 分钟） | — | B 站真实轨（亮剑 ep01/02，免费无凭据，yt-dlp 获取）；属 B 站真实轨票的切片源，此处仅登记 |

## ② ffprobe 实测结论（2026-09-25，文件到库后实测）

### Sintel.2010.1080p.mkv（正片混音）——已实测

| 项 | 实测值 |
|---|---|
| 时长 | **888.032 s**（14 分 48 秒，完整正片） |
| 容器 | matroska，总码率 10,631,063 bps |
| 视频 | h264，1920x818（stream #0） |
| 音频 | **stream #1：AC3 5.1(side)，6 声道，48 kHz，640 kbps，language=eng** |
| 字幕 | 10 条 SRT 轨：ger/eng/spa/fre/ita/dut/pol/por/rus/vie |

**核实结论**：
1. **正片混音本身就是 5.1**——FC 中置通道可直接抽取（已派生 `sintel_center.wav`）。影视混音惯例对白进中置，**中置通道升格为对白参照主角色**：分离实验的"人声轨 vs C 通道"相关性/SI-SDR 是真值轨主评价指标。
2. eng 字幕轨可作 E3 时长实验的对白参照（已导出 `sintel_en.srt`，与音频同容器，零对齐成本）。
3. ~~真对白 = 正片音频 − M&E 波形相减~~ **已证伪，见下条**。

### sintel-m+e-st.flac（官方 Music&Effects 分轨）——已实测 + **mastering 失配实测坏消息**

| 项 | 实测值 |
|---|---|
| 时长 | 888.000 s（与正片 888.032 s 差 32 ms） |
| 音频 | FLAC stereo，2 声道，48 kHz |

**核实结论（含协调者后续实测，2026-09-25）**：
1. **逐样本相减产"真值对白"不可行**：M&E 分轨与正片下混**不是同一 mastering**——实测最优增益 −21.2 dB、极性为负、配准后滑动窗口相关中位数仅 **0.274**（逐样本相减要求相关≈1）。先前派生的 `sintel_true_dialogue.wav` 相减产物随之废弃（留档防重复尝试）。
2. **M&E 降级用途**：音乐/音效**活动与频谱参照**——能量包络对照、频谱对比、非对白窗背景一致性的参照轨，不再承担逐样本真值。
3. 真值主角色改由 **Sintel 中置通道（`sintel_center.wav`）** 承担（对白进 C 的混音惯例）；亮剑等无 5.1 素材走 bleed+能量代理指标。

### big_buck_bunny_480p_surround-fix.avi（5.1 surround 版）——已实测

| 项 | 实测值 |
|---|---|
| 时长 | **596.458 s**（9 分 56 秒） |
| 视频 | mpeg4（stream #0） |
| 音频 | **stream #1：AC3 5.1(side)，6 声道，48 kHz** |

**核实结论**：
1. 5.1 声道确认，作为 E4 的第二 5.1 源。
2. **对白稀缺声明（如实记录）**：Big Buck Bunny 全片无对白（纯音乐+音效动画）。它的 C 通道里没有对白可验纯度，只能用于：中置通道音乐/音效能量占比测量（E4 的另一半指标）、以及"无对白片 C 通道 ≠ 对白"的反例校准。**E4 的对白纯度对比（C 通道 ASR WER vs 分离对白轨 WER）只能用 Sintel 做**。
3. 480p 视频对纯音频研究无影响。

## ③ 与配额/清单的关系（clips_manifest.yaml 依据）

- Sintel 全片按 30s 网格预切（音频任务，无需看画面），**切片源用 `sintel_stereo.wav`**（协调者下混版，与 center/me 派生轨同一 mastering 口径），条目进 manifest，分层标签 provisional（结构粗标：开场吟唱→对白戏→动作戏→配乐段；**Sintel 对白重叠段稀缺，如实记录：双人重叠配额主要由 B 站剧承担**）。
- M&E 派生轨（`sintel_me.wav`）不切网格（整轨使用，作活动/频谱参照）。
- 中置派生轨（`sintel_center.wav`）不切网格（整轨与切片按需对齐截取，作对白参照）。
- BBB 5.1：E4 用途为主，是否入切片网格视 E4 展开再定。
- 海外剧替代记录：无免费可看的英文剧（协调者核实），**en 真实轨由 Sintel 英文对白充当**（票面预案），zh 真实轨为亮剑 ep01/02（B 站真实轨票负责切）。

## ④ 缺口与延后（如实记录）

| 项 | 状态 | 说明 |
|---|---|---|
| Tears of Steel 官方镜像 | **404 下架** | `download.blender.org/mango/` 已不可用；ToS 5.1/分轨渠道待另找（官方 tarball 或 studio 仓库），今晚不阻塞——Sintel 已同时覆盖"对白 5.1 + 中置对白参照"两个需求 |
| DnR v2 全量 | **deferred（今晚不拉）** | Zenodo 6949108，10×1GB 分卷；真值轨由 Sintel 中置通道承担。若后续需要"教科书域"参照分再补拉 |
| Sintel-Bluray.iso | 不拉 | 20GB 级，正片 mkv 已含 5.1，无增益 |
| M&E 逐样本真值 | **不可行（实测证伪）** | mastering 失配（增益 −21.2 dB / 极性负 / 窗口相关中位 0.274），M&E 降级为活动与频谱参照 |
| 免费英文剧 | **未找到** | B 站真实轨的 en 侧由 Sintel 充当（替代记录见 ③） |

## ⑤ 复现命令

```bash
# 声道核实（任一文件）
ffprobe -v error -show_entries format=duration,size,bit_rate \
  -show_entries stream=index,codec_type,codec_name,channels,channel_layout,sample_rate \
  -of json <媒体文件>
```
