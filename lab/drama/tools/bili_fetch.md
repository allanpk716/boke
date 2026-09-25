# bili_fetch.md — B 站真实轨选片决策与获取记录（票03）

> 用途：登记 B 站真实轨（zh 国产剧 + en 海外剧替代方案）的选片决策、链接、获取方式与产物核实结论。
> 获取日期：2026-09-25（夜链，协调者已选片并下载完毕，本票登记）。素材文件本身 gitignored（`lab/drama/media/**`），本文件入库。
> 全程无凭据：内容免费公开，未使用任何登录态/Cookie/API Key。

## ① 选片决策

### zh 真实轨：《亮剑》(2005) ep01/ep02

| 项 | 内容 |
|---|---|
| 片名 | 《亮剑》（2005，国产战争剧） |
| B 站链接 | 番剧 ep143217（ep01）/ ep143218（ep02），BV1rs41197uG |
| 获取方式 | yt-dlp `bestaudio`（免费公开内容，无需登录凭据） |
| 产物 | `media/bili/liangjian_ep01.m4a`（2614.462s ≈ 43.6min）/ `media/bili/liangjian_ep02.m4a`（2707.177s ≈ 45.1min） |
| 流核实 | 均为 AAC 立体声 48 kHz（ffprobe 实测，2026-09-25，见 ③） |

**选片理由**：战争剧三场景齐全，一张片源覆盖三个难分层——
1. **密集对话与争吵**（指挥部争论、李云龙与上级/同僚交锋戏）→ `双人重叠` 层原料；
2. **战斗爆炸**（苍云岭突围、李家坡攻坚）→ `音效压对白` 层原料（枪炮音效压对白）；
3. **煽情配乐段**（行军蒙太奇、战后煽情）→ `音乐重` 层原料。
且 B 站番剧区免费可播、单集 40~45min 时长足够选段，剧情锚点常识清晰（ep01 苍云岭突围战斗/李云龙与坂田对话/医院；ep02 李家坡战斗/山崎大队），便于 provisional 粗标后人工听审收敛。

### en 真实轨：替代决策（票面预案）

**B 站无免费可看的英文真人剧**（协调者核实），按票03 预案：**en 真实轨由 Sintel（Blender 开源电影，英文对白）充当**。
- Sintel 已在票02 完成登记与 30s 网格切片（`clips_manifest.yaml` 中 `blender_sintel_g0000~g0029`，源 `media/blender/sintel_stereo.wav`）。
- 替代影响如实记录：en 侧缺失"多人重叠生活对白"形态（Sintel 对白重叠段稀缺，manifest notes 已声明双人重叠配额由 B 站 zh 剧承担）；en 分层结论（尤其 `双人重叠` 层）适用范围受限，报告需注明。
- 详细素材核实见 `tools/media_sources.md` ③/④（替代记录原登记处）。

## ② 获取方式复现（无凭据）

```bash
# yt-dlp bestaudio，免费内容无需任何登录凭据/cookies
yt-dlp -f bestaudio -x --audio-format m4a \
  -o "lab/drama/media/bili/liangjian_ep01.%(ext)s" \
  "https://www.bilibili.com/bangumi/play/ep143217"
yt-dlp -f bestaudio -x --audio-format m4a \
  -o "lab/drama/media/bili/liangjian_ep02.%(ext)s" \
  "https://www.bilibili.com/bangumi/play/ep143218"
```

注意：产物为源流音频转封装（AAC 48k 立体声），未做响度归一或重采样；切片由 `tools/cut_clips.py` 完成（重采样 44.1k/16k 双列 wav）。

## ③ ffprobe 实测结论（2026-09-25）

| 文件 | 时长 | 音频流 | 容器码率 |
|---|---|---|---|
| `media/bili/liangjian_ep01.m4a` | 2614.462 s | AAC stereo 48 kHz | 140,879 bps |
| `media/bili/liangjian_ep02.m4a` | 2707.177 s | AAC stereo 48 kHz | 133,209 bps |

复现命令同 `media_sources.md` ⑤（`ffprobe -show_entries stream=...`）。

## ④ 切片与标注方法（票03 执行记录）

1. **网格**：`cut_clips.py grid` 对两集各生成 30s 网格切点表（ep01 88 格/ep02 91 格，`media/clips/liangjian_ep0*_grid.json`）。
2. **能量启发式**：ffmpeg `astats`（`reset=1` 逐帧）+ 按 30s 格聚合，得每格 p90/动态范围（p90−p10）/低能量占比（`media/clips/liangjian_ep0*_energy.txt`）。判读口径：
   - 持续高能低停顿（p90 ≈ −21~−23dB、低占比 <0.1）→ 战斗/持续声源（`音效压对白`/`音乐重` 候选）；
   - 语音突发+长停顿（低占比 0.3~0.7）→ 安静环境对话（`对白干净` 候选）；
   - 能量连续无快速交替（dyn <10、低占比 ≈0）→ 单人连续讲话或配乐铺底（`solo_candidate`/`音乐重` 候选，二者需听审甄别）。
3. **剧情锚点**（对剧常识，非逐段核实，全部 provisional）：ep01 开场苍云岭突围战斗→李云龙与坂田对话→医院；ep02 李家坡战斗/山崎大队。推测场景与格位映射**未经画面核实**，note 内逐段标"待听审"。
4. **选段**：每集 6~10 段（ep01 10 段 + ep02 7 段 = 17 段），选段表 `media/clips/liangjian_ep01_sel.json` / `liangjian_ep02_sel.json`，`cut_clips.py cut` 执行（ffprobe 时长校验 ±0.5s 通过）。
5. **双人重叠段**：4 段，能量只能证明"对白密集无长停顿"，**重叠关系能量无法证实**，均标 provisional + "待听审确认"。
6. **solo_candidate 段**：4 段（票06 伪重叠原料，疑似单人连续对白），manifest 中 `solo_candidate: true` + note 标注依据，待听审确认。

## ⑤ 配额覆盖（zh/bili，provisional，F6 目标见 manifest quota_progress）

| 分层 | 本票 provisional | F6 target | 状态 |
|---|---|---|---|
| 对白干净 | 6（含 solo_candidate 4） | 3 | 达标（待听审收敛） |
| 音乐重 | 3 | 3 | 达标（待听审收敛） |
| 音效压对白 | 4 | 3 | 达标（待听审收敛） |
| 双人重叠 | 4 | 4 | 达标（待听审收敛） |

配额进度由 `cut_clips.py` 自动重算进 `clips_manifest.yaml` 的 `quota_progress` 节（bili/zh 子树）；全部为 provisional 单列，听审确认后才计入 confirmed。

## ⑥ 已知限制（如实记录）

- 所有分层标签均为**能量启发式+剧情锚点推测**，未经听审/画面核对；`双人重叠` 与 `solo_candidate` 的判定尤其依赖听审（能量特征二者同形）。
- B 站流为有损 AAC（~133~141 kbps），高频细节有限；对分离/ASR 实验可用，频谱类结论需注明源质量。
- en 侧替代方案（Sintel）缺真人剧形态，见 ① 替代决策。
