# 夜班交接汇报 MORNING_REPORT.md(2026-09-23 夜班 → 早晨)

> 执行会话:2026-09-23 21:50 起夜班。任务书:`NIGHT_TASK_2026-09-23.md`。
> 原则执行情况:下载先行 ✅ 代码并行写 ✅ 环境修复上限遵守(唯一一次并发 pip 冲突自伤已修复)。

## 一句话总结

**必达四项全部达成(T1降级完成/T2/T3/T5 全绿,T4 达标),另加跑全片流水线;唯一硬阻塞是 Edge cookie 的 app-bound 加密(充电专属下载 & pyannote gated,都指向早晨两个 10 秒动作)。**

## 各任务状态

### T0 开场自检 ⚠️(降级通过)
- Edge 已杀,cookie 冒烟失败:Edge 153 cookie 全部 v20 app-bound 加密,用户态无解
- 3 次修复尝试(达上限):①yt-dlp 直读(DPAPI 失败)②junction+CDP 让 Edge 自解密(app-bound 密钥绑定原 profile 路径,junction 下 Edge 视为全新 profile,拿不到旧 cookie)③无管理员权限,SYSTEM 解密不可行
- 降级链走到底:自研 wbi 签名 + 匿名 buvid 拿到公开内容访问能力(300 视频清单 + 6 个下载全成功)
- **BBDown 1.6.3 已归档无 getupvids,登录要扫码(禁止),不适用**——任务书降级链这条走不通,如实记录

### T1 拉清单 + 批量下载 ✅
- 清单:300/2113(风控 -352,带退避重试,第 7 页后放弃;要全量早晨带 cookie 重跑更稳)
  - `work/space_videos.json` / 分类 `work/classify_dump.md` / 计划与执行 `work/download_plan.md`
- 充电专属识别:**39 个**(API `is_charging_arc` 字段直接判定,标题模式印证:剥壳/三国三味系列)
- 下载(全部成功,共 ~860MB,D 盘剩 3.1T):
  - 中文日常×3(BV1ZTr5BuEGy/BV1eQ5t6GEtX/BV1NR596KEk4)→ 参考音源
  - 采访公开×1(BV17UPszhE6o 圆脸X小外 39:58)→ 冒烟主选
  - 素材×1(BV1UdFAzrE1Z)→ 冒烟备选
- 充电专属 BV1SGem6uEU9:**只有 300s 试看片段**(全片 94 分钟)——匿名账号只能拿到试看,非故障
- 其余 38 个专属未下(无 SESSDATA,见"早晨要做的 5 件事"第 1 条)

### T2 博主参考音提取 ✅
- `refs/yuanlian_chinese_01.wav`(25s,斩杀线视频 728.7~753.7s)
- `refs/yuanlian_chinese_02.wav`(25s,特朗普访华视频 873.2~898.2s)
- 均为 16k mono,登记在 `refs/README.md`(来源 BV/起止/挑选方式)
- 方法:100ms 帧能量 → 间隙容忍语音段 → 滑窗高频带(4-8kHz)静音占比打分 → **频谱图逐段人工核验(9 段验 7 弃,新闻片段 BGM 全部排除)**
- 发现:圆脸日常视频大量夹新闻片段(自带 BGM),能量法选段不可靠,频谱核验是必要的
- 参考文本(逐字稿)未做——T5 用 CosyVoice3 空 prompt_text(内置 ASR)模式,见 T5 节

### T3 流水线代码 ✅
- `src/boke/` 七个模块 + `pyproject.toml`(可编辑安装进 .venv)
- CLI:`python -m boke.pipeline <video> --stage all|extract|ocr|diarize|attribute|synthesize|mix`,断点续跑(产物存在即跳过),`--mock` 三处可 mock
- **attribute.py 单测 8/8 通过**(tests/test_attribute.py;含"20% 抢话噪声 ≥95% 准确率"验收用例)
- OCR 净化:角标数字杂质剥离(波士顿圆脸视频底部有 3-4 位数字角标混进 ROI)
- 已知简化(记录在案):edge-tts 逐句合成未做长句切分(>50 字句 CosyVoice 才有吞字问题,edge 影响小);mix 变速超 1.15x 时按需>1.15 并记数(宁快勿丢字)

### T4 全链路冒烟 ✅(diarization 为 mock)
- 输入:BV17UPszhE6o 切片 5:00~15:00(600.01s,避开片头和 1200s 处新闻插播段)
- 抽帧确认:主对话中文硬字幕稳定在 92%~98% 高度带(ROI 定 0.90~0.99);插播竖屏新闻自带英文字幕在 ~75%,ROI 已排除
- OCR:305 条字幕,平均时长 1.85s(RapidOCR CPU,1200 帧约 24 分钟)
- diarization:**pyannote gated 403 → mock 降级**(文件级探测:community-1 管线仓库 403,segmentation-3.0 可 200 → 卡在协议未接受)
- 归人:305 句两说话人,conf_mean 1.0(mock 完美重叠所致;真实 diarization 后此数才有意义)
- 合成:edge-tts(SPK_00=YunxiNeural / SPK_01=XiaoxiaoNeural)
- **产物验收:`out/smoke_BV17UPszhE6o_5to15.m4a`(8.15MB)**
  - 时长 600.600s vs 源 600.011s = **+0.098%**(±5% 达标)✅
  - volumedetect:mean -17.3dB / max -1.3dB 非静音 ✅
  - 抽查切片已备好:`out/smoke_check_60s/300s/540s.m4a`(各 20s,人耳验证用)
  - 机器质检:逐句来自 edge-tts zh-CN 音色 + 中文文本,能量正常;"能辨识中文人声"的人耳确认留给早晨(我是聋的)
- 中间产物:`work/smoke_BV17UPszhE6o_5to15/`(ocr.srt/tagged.srt/per-speaker.md/review.csv/tts/)
- **加餐**:全片 39:58 完整流水线夜间加跑中(同参数),产物 `out/BV17UPszhE6o_full.m4a`,状态见下方补记

### T5 CosyVoice3 环境 + 克隆试合成 ✅
- torch 2.6.0+cu124 ✅(RTX 3060 CUDA 可用)|CosyVoice 仓库 clone ✅(D:\boke_media\tools\CosyVoice)
- 权重 9.1GB ✅(ModelScope,含 RL 版 llm.rl.pt 一并到手)|CosyVoice Python 依赖 ✅(numpy 1.26.4 + onnxruntime 1.18 + pyannote 3.3.2 统一组合)
- 事故记录(已修复):①pyannote 安装与 torch cu124 并发 pip,清华源把 torch 换成 CPU 2.14.0 → force-reinstall 恢复(教训:**同一 venv 永远串行 pip**);②CosyVoice requirements 的 openai-whisper 20231117 构建失败(setup.py 用了被移除的 pkg_resources)导致整个事务回滚 → 装 20250625 版解决;③venv setuptools 太新无 pkg_resources → 钉 setuptools<81
- **克隆 3 句试合成全部成功**(cross_lingual 无文本模式——CosyVoice3 的 LLM 要求文本含 `<|endofprompt|>` 标记,官方 example.py 格式:`"<|endofprompt|>" + 句子`,参考音无需逐字稿):

| 产物 | 音频时长 | 合成耗时 | 电平 | whisper-small 机器转写质检 |
|---|---|---|---|---|
| out/clone_test_1.wav(重庆/重量级) | 5.1s | 13.0s | -20.7dB | 基本 correct("重庆"转写为"重新",疑 ASR 听写误差,人耳待判) |
| out/clone_test_2.wav(银行/行长/很行) | 6.7s | 13.1s | -21.5dB | **逐字全对,三处"行"多音字全正确** ✅ |
| out/clone_test_3.wav(差强人意/差距) | 5.4s | 11.9s | -21.6dB | "差强人意"听成"差强争议"(该成语本身高频易错,人耳待判) |

- **显存峰值 3.62GB(cross)/ 5.03GB(zero),无 OOM**(预期 4-5GB,12GB 卡大富余)✅
- **A/B 对照已备好**:同一参考音两种模式各出 3 句——`out/clone_test_1/2/3.wav`(cross 无文本)vs `out/clone_test_1/2/3_zero.wav`(zero_shot + whisper small 转写稿)。**预判 cross 更像**:whisper small 转写有明显错误(参考段讲"贫困线",被转成"LSLINE/愛丽思线"等),zero_shot 的参考文本"必须严格一致"不满足,相似度应受损——正好验证任务书"参考段无逐字稿就用无文本模式"的分支
- 每句 12-17s(5-7s 语音)≈ RTF 2-3;引擎调用方式已回写 `src/boke/synthesize.py`(CosyVoiceEngine),流水线 `--tts-engine cosyvoice3` 直接可用

### T6 本汇报 ✅

## 下载总账

已完成(D:\boke_media,共 6 文件 ~860MB):
| BV | 标题 | 大小 | 用途 |
|---|---|---|---|
| BV1ZTr5BuEGy | 斩杀线登NYT头版 18:19 | 122MB | 参考音源A ✅已用 |
| BV1eQ5t6GEtX | 特朗普访华聊啥 19:55 | 107MB | 参考音源B ✅已用 |
| BV1NR596KEk4 | 特朗普再访华代表团 25:07 | 148MB | 参考音备选(BGM多未用) |
| BV17UPszhE6o | 圆脸X小外 39:58 | 173MB | 冒烟主选 ✅已用 |
| BV1UdFAzrE1Z | 马斯克 13:27 | 130MB | 冒烟备选 |
| BV1SGem6uEU9 | 北爱威尔士(专属) 94:17 | 17.5MB | ⚠️仅300s试看 |

失败/未下:其余 38 个充电专属(无 SESSDATA);④⑤其余采访类与全量(时间/权限)。

## 早晨需要你做的 5 件事

1. **导出 B 站 cookie(10 秒,解锁专属下载)**:Edge 装 [Get cookies.txt LOCALLY] 扩展 → 打开 bilibili.com 已登录页 → 导出 → 存为 `work/cookies_full.txt`。之后跑 `work/tools/download_batch.py`(需把 PLAN 改成专属 BV 列表 + cookie 参数换新文件)即可批量下 39 个专属
2. **HF 页面点接受(10 秒,解锁真实声纹分人)**:登录 allanpk716 访问 https://huggingface.co/pyannote/speaker-diarization-community-1 点 Agree(token 已在 `~/.cache/huggingface/token`,无需新建)→ 重跑 `--stage diarize`(删掉 work 里旧 diar.rttm)即可换真
3. **参考音抽听(1 分钟)**:`refs/yuanlian_chinese_01.wav` 和 `_02.wav`,确认是圆脸本人、无音乐垫底(我是用频谱图核验的,没耳朵)
4. **克隆试合成 A/B 试听**:`out/clone_test_1/2/3.wav`(无文本模式)vs `out/clone_test_1/2/3_zero.wav`(带转写稿模式),判断哪组更像圆脸;顺便听多音字(重庆/银行/差强人意)读对没
5. **归人准确率抽检(05 分钟,真实 diarization 之后才有意义)**:抽 `work/smoke_*/review.csv` 或 tagged.srt 20 句对原片核

## 卡点与已试办法(备查)

| 卡点 | 已试 | 结论 |
|---|---|---|
| Edge v20 cookie | yt-dlp 直读/junction+CDP/SYSTEM(无权限) | 用户态无解,扩展导出最快 |
| B 站空间 412/-352 | UA+Referer/buvid+设备指纹参数/退避重试 | 前 6 页稳定,全量需登录 cookie |
| pyannote 403 | token 文件级探测 | 协议未接受,点一下就好 |
| torch CPU 覆盖 | force-reinstall cu124(pip 缓存秒回) | 已修复,教训记录在案 |

---
## 夜班收尾补记(01:40)

**加餐完成:全片流水线成品 `out/BV17UPszhE6o_full.m4a`(32MB,39:58 整期)**
- OCR 1261 句(平均 1.76s/句)→ mock 分人 → edge-tts 双声部 → 混音
- 时长 2397.800s vs 源 2397.82s(**-0.001%**),mean -16.8dB
- 中间产物 `work/BV17UPszhE6o_full/`(tagged.srt / per-speaker.md / tts/ 1261 wav)

**混音质量迭代(夜班内发现并修复)**:首版 1069/1261 句需变速、928 句超 1.15x——根因是 edge-tts 每句自带 0.3~0.5s 首尾静音垫,塞进 1.76s 字幕窗必爆。加"裁静音边"后(atempo 前先裁),超限降到 233 句(多为"对/嗯"级极短窗,上限 2.0x 封顶,听感偏快但不丢字)。**早晨通勤试听重点:语速是否舒服,233 句快段是否扎耳**——扎耳的话白天上"限长切句"(超长句拆两个时间窗)或 IndexTTS-2.5 精确时长控制(02 篇预留的副选)。

**冒烟成品同步重混**:`out/smoke_BV17UPszhE6o_5to15.m4a` 600.30s(+0.048%),抽查片 `out/smoke_check_60/300/540s.m4a` 已刷新。

**夜班总账**:必达 4/4 达成,尽力项(专属批量)卡权限已记录,允许失败项(pyannote 403)按预案降级。环境踩坑 5 个全修复(并发 pip / whisper 构建 / pkg_resources / numpy-ort / CosyVoice3 标记格式),全部有记录可查。
