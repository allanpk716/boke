# 今晚任务书：配音流水线夜班（2026-09-23）

> 你是接手夜班的新会话。本文件是用户睡前与其共同定稿的任务书，**自包含**。术语以 `CONTEXT.md` 为准，技术蓝本以 `docs/research/01_需求与技术方案.md`（下称"01"）和 `docs/research/02_组件选型与效果验证.md`（下称"02"）为准。
>
> 执行原则：**下载先行、代码并行写、环境地狱有三次修复上限**。遇到本文件和交接包都没覆盖的方向性决策——停下记录，等早晨，不要发明需求。修复类小事（重试、换镜像、换备选组件）自己拿主意。

---

## 目标（一句话）

天亮前把"波士顿圆脸英文采访视频 → 中文配音音频"流水线推进到：**视频在下载、博主中文参考音已提取、全链路代码完成并在真实视频上冒烟通过、CosyVoice3 已能用博主音色合成中文**。

## 早晨验收标准（按优先级）

| 级 | 标准 |
|---|---|
| 必达 | T2：`refs/` 有 ≥2 段博主中文参考音（10~60s，登记来源） |
| 必达 | T5：`out/clone_test_*.wav` 3 句克隆试合成，有能量非静音，无 OOM |
| 必达 | T4：`out/smoke_*.m4a` 全链路产物，时长=原视频±5%，能听到中文人声（音色/归属正确**不**作要求） |
| 尽力 | T1：充电专属视频验证可下且批量下载进行中/完成 |
| 允许失败 | pyannote gated 403、412 风控持续不散、CosyVoice 环境超三次修复——如实记录卡点即算尽责 |

## 全局停止条件（命中任一：停该项、记录、继续其他任务；全部任务停则写汇报收工）

1. 同一错误连续 3 次修复尝试仍失败
2. 需要付费、输验证码、扫码等人工动作
3. D 盘剩余 <100GB，或下载总量已达 400GB 上限
4. 显存持续 OOM 且降级（减 batch/换 CPU）无效
5. 出现未覆盖的方向性决策

---

## 前置事实（已核实，勿重复查）

- 博主：**波士顿圆脸** `https://space.bilibili.com/346563107/`。日常视频说中文（参考音来源）；英文采访视频带中文硬字幕（目标视频）；部分为充电专属
- 用户已在本机 **Edge 登录 B 站**（大会员账号）**且已完成对该博主的充电** → 充电专属视频对该账号可看可下
- 本机：Win10、**RTX 3060 12GB**、Python 3.11.9（scoop）、ffmpeg、yt-dlp（scoop 已装）；仓库 `C:\WorkSpace\agent\boke`（git 已 init，含 .gitignore/README/CONTEXT/docs/research）
- HF token 有效：`C:\Users\allan716\.cache\huggingface\token`（账号 allanpk716），环境变量未设——用时显式传
- 存储：大文件一律 `D:\boke_media`（已建，3TB 空闲）；仓库内只放代码/配置/文档
- 系统已设"交流电源下永不睡眠/休眠"——**别再动电源设置**
- 已知坑：① Edge 运行中→cookie 数据库锁死；② 无 cookie 列 space 会被 412 风控（对策见 T1）

---

## 任务（T0→T6；T1 启动后 T2/T3/T5 并行推进）

### T0 开场自检（约 5 分钟）

1. `taskkill //IM msedge.exe //F`（用户已睡，Edge 全退才能读 cookie；杀不掉说明有挂起进程，用 `taskkill //IM msedge.exe //F //T` 再试一次）
2. cookie 冒烟：`yt-dlp --cookies-from-browser edge --skip-download --print "%(title)s" "https://www.bilibili.com/video/BV1GJ411x7h7"`（任意公开视频都行，能打印标题即通过）

**验收**：步骤 2 无 ERROR。
**降级链**（依次试）：加 `--user-agent "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36" --add-headers "Referer:https://www.bilibili.com/"` → `scoop install bbdown` 改用 BBDown（它自带 wbi 签名，抗 412）→ 仍不行：无 cookie 模式继续（参考音可用，专属和 1080P 冒烟降级，明早处理）。

### T1 拉清单 + 批量下载（最长任务，先后台启动再干别的）

1. 列全量清单：`yt-dlp --cookies-from-browser edge --flat-playlist -J "https://space.bilibili.com/346563107/video"`（412 则按 T0 降级链；等 60s 重试 ≤3 次）
2. 分类（标题启发式即可，不确定的标注）：**中文日常**（参考音候选，挑时长 ≥10min 说话密集的 2~3 个）/ **英文采访**（目标视频）/ 其他。写入 `work/download_plan.md`
3. 充电专属识别：尽力而为——space API 的 `is_upower_exclusive` 字段或标题/分区线索；识别不出就按"采访类优先"排，不用死磕（账号已充电，专属不下不动是权限之外的故障才需要关心）
4. 下载顺序：**① 2~3 个中文日常（参考音是克隆基准，最优先）→ ② 1~2 个英文采访（冒烟用，公开优先）→ ③ 充电专属全量 → ④ 其余采访类 → ⑤ 时间允许再全量**
5. 参数：`--cookies-from-browser edge`、最高可用画质（大会员可拿 1080P 高码率）、`-N 3`、并发同时最多 2 个视频；命名 `D:\boke_media\<BV号>_<标题去特殊字符>.mp4`；每条结果记 `work/download_log.md`（BV号/标题/大小/状态/失败原因）
6. 用 `run_in_background` 跑批量脚本，边下边推进 T2/T3/T5；**06:00 或 400GB 先到即停新开下载**，已在下的下完

**验收**：≥2 个中文日常完整可播（`ffprobe` 时长正常）+ 专属视频 ≥1 个下载成功（或失败原因写明）。
**降级**：单视频失败重试 ≤3 次后跳过记录；B 站限速/风控 → 降并发到 1、拉长间隔。

### T2 博主参考音提取（依赖 T1①，优先级高）

1. 取 1 个中文日常视频 → `ffmpeg -i x.mp4 -ac 1 -ar 16000 audio.wav`
2. 先判断有无 BGM（听频谱/能量分布；波士顿圆脸日常视频大概率纯人声）——**纯人声直接进 3**；有 BGM 才上 audio-separator + `vocals_mel_band_roformer.ckpt`（pip 装 `audio-separator`，模型首次运行自动下载）
3. 自动挑段：静音/能量检测切候选，取 10~60s、能量平稳、连续独白段 2~3 段 → `refs/yuanlian_chinese_01.wav`（16k/44.1k 均可，wav）
4. `refs/README.md` 登记：每段来自哪个视频（BV号）+ 起止时间 + 挑选理由

**验收**：`refs/` ≥2 段、每段 10s+、登记完整。
**降级**：audio-separator 装不动且必须分离 → 换 BGM 轻的另一个视频挑段；再不行产出 1 段也先收下，记录。

### T3 流水线代码 `src/`（与 T1 并行，不依赖下载）

按 01 §2 架构写模块化 CLI（`python -m boke.pipeline <video> --stage all`，各 stage 可独立跑可 mock）：

```
src/boke/
├── pipeline.py        # 编排：stage 串联、断点续跑（每 stage 产物落盘即跳过）
├── extract_audio.py   # ffmpeg 抽 16k mono wav
├── ocr_subtitles.py   # OpenCV 抽帧 2~4fps → 底部 ROI → RapidOCR（pip 即装，避开 PaddleGPU 环境坑）
│                      #   → 相邻帧文本相似度合并成行 → ocr.srt；行起止=首末命中帧时刻
├── diarize.py         # pyannote community-1（显式传 HF token，.to(cuda)）；403 则 mock：
│                      #   用 ocr.srt 的时间轴生成占位 rttm（每条轮流标 SPK_00/SPK_01）
├── attribute.py       # 【核心自研】归人：字幕条×说话人段重叠占比投票 → tagged.srt/per-speaker.md/review.csv
│                      #   算法照 01 §4.1；conf<0.6 进 review；先写单测（假数据：构造已知归属验证 ≥95% 通过）
├── synthesize.py      # CosyVoice3 逐句合成（T5 就绪前：edge-tts 或静音占位 mock）
├── align_mix.py       # 时长对齐三招（限长→变速≤1.15x→借静音）→ ffmpeg 拼接 + loudnorm → m4a
└── voices.yaml        # 音色映射表骨架（照 01 §5）
```

**验收**：`attribute.py` 单测过；各 stage 有 CLI 入口和 `--mock` 开关。
**降级**：任何组件环境问题不阻塞其他 stage（mock 隔离）。

### T4 全链路冒烟（依赖 T1② + T3）

1. 输入：波士顿圆脸的**公开英文采访视频**优先；没有 → `yt-dlp "bilisearch:英文采访 中文字幕"` 找替代（仍不带 cookie 可下的公开视频）
2. 跑 `--stage all`（diarization 允许 mock、synthesize 允许 edge-tts/静音）→ `out/smoke_<BV>.m4a` + `work/<BV>/` 全套中间产物
3. 记录关键数字：OCR 字幕条数/平均时长、说话人段数、归人置信度分布（多少条 <0.6）

**验收**：产物存在、`ffprobe` 时长=原视频±5%、`ffmpeg volumedetect` 非静音、抽听 3 处（`ffmpeg -ss X -t 20` 切片）能辨识中文人声。

### T5 CosyVoice3 环境 + 克隆试合成（依赖 T2）

1. `.venv`（仓库内）+ 清华 pip 镜像；`git clone https://github.com/FunAudioLLM/CosyVoice --recursive`（放 `D:\boke_media\tools\` 或仓库外层均可，别进 git）
2. 权重走 ModelScope（国内快）：`modelscope snapshot_download('FunAudioLLM/Fun-CosyVoice3-0.5B-2512', local_dir=...)`（id 若 404 以 02 §④ 的拼写为准）
3. 用 `refs/yuanlian_chinese_01.wav` 当参考音，零样本合成固定 3 句中文（自选 ≥10 字/句，含一个多音字）→ `out/clone_test_1/2/3.wav`
4. 单句 ≤50 字；参考文本必须与参考音频严格一致（若参考段无逐字稿，合成时用 `zipvoice/cosyvoice` 的无文本模式或退而求其次给 ASR 转写稿——如实记录用了哪种）

**验收**：3 个 wav 生成、volumedetect 有能量、3060 显存不 OOM（4~5GB 稳态，12GB 富余）。
**降级**：依赖三次修复不过 → 记录完整报错链，T4 的 synthesize 换 edge-tts 完成，克隆留给白天。

### T6 收工汇报 `MORNING_REPORT.md`（仓库根目录）

- 每任务一节：状态（✅/⚠️/❌）/ 产物路径 / 关键数字 / 卡点与已试过的办法
- 下载总账：已完成列表（BV+标题+大小）、失败列表及原因、还剩什么没下
- 结尾固定一节：**「早晨需要你做的 N 件事」**（预计：pyannote gated 点接受 / 嘉宾克隆 A/B 试听 / 归人准确率抽检 20 句 / 音色映射指认）

---

## 禁止事项

- 不碰 `SynologyDrive` 任何原件；**不 SSH 到任何远程服务器**（3090x2/4090x2 等一律不碰）
- 不付费、不输验证码、不扫码、不发布/上传任何内容（克隆音频仅自用，合规边界见 01 §5）
- 不 git push（本地 commit 允许）；不动系统电源/驱动设置
- 下载总量 ≤400GB；D 盘剩余 <100GB 立即停下载
- pyannote 若因 gated 403 不可用：mock 降级继续，**不要**找非官方镜像权重

## 已知坑速查

| 坑 | 对策 |
|---|---|
| Edge 运行锁 cookie | T0 先 taskkill |
| 412 风控 | UA+Referer → 带真 cookie → BBDown → 等 60s 重试 |
| pyannote gated 403 | mock rttm 降级；早晨用户去 HF 页面点接受 |
| CosyVoice 依赖地狱 | venv 隔离 + 镜像加速 + 3 次修复上限 |
| pip/HF 下载慢 | pip 清华镜像；模型优先 ModelScope |
| B 站下载中断 | yt-dlp `.part` 自动续传；单视频重试 ≤3 |
| 中文 OCR 抽帧慢 | 2fps 起步够用；ROI 裁剪后再 OCR 提速 |
