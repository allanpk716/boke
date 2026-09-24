# 05 · 小样生成器

## What to build
`src/boke/preview.py`:`generate_preview(bvid, part, decisions)` ——
1. 密集窗定位:读该 part 的 tagged.srt(或决策目录内 tagged.dec.srt),默认规则=字幕条数/分钟最高的 60s 窗(排除片头 90s 与片尾 60s);不足(分P太短/排除后无窗)→确定性兜底:放宽排除边界取最密 60s,仍无→取中点 60s;
2. 两段合成:片头 60s 窗 + 密集窗,各窗内取落入窗的决策单元(03 生成的 tagged.dec.srt 分单元),调 boke.synthesize(voices 用决策目录配置,engine 按决策;skip 说话人单元→直接拷贝原片对应段音频)与 boke.align_mix 的拼接逻辑,在 `work/<id>/dec_<hash>/` 产出 `preview.mp3`(两段拼接,段间 1s 静音);
3. 产物哨兵 .done 完整后登记账本:小样路径→状态"小样待听";
4. 复用同一 dec_hash 目录:重试只补未完整产物(同 hash 下哨兵判据)。

## 验收标准
- [ ] 密集窗:构造 srt 桩验证默认规则/兜底规则确定性(同输入同窗)
- [ ] skip 说话人:窗内该单元用原片音频段(ffmpeg 切原片 audio.wav),不合成
- [ ] 产物带哨兵;账本登记路径与状态转移正确(桩测)
- [ ] 不重新实现合成引擎,复用 boke.synthesize.run/boke.align_mix(允许最小适配函数)
- [ ] `tests/test_preview.py`;既有 pytest 全绿

## Blocked by
03(决策目录与配置)

## 涉及路径
- src/boke/preview.py(新增)
- tests/test_preview.py(新增)
- src/boke/synthesize.py(只读;如需抽公共函数最小改动)
- src/boke/align_mix.py(只读)

## 副作用声明
测试用 tmp 桩(假合成器 monkeypatch + 生成静音 wav),不调真实 TTS/GPU

## decision_refs
D8(固定两段)、D13(小样门)、F5(skip=保留原声假设)、F11(密集窗默认)、F12(同 hash 复用)

## review_blocks
无(F5 行为按 U4 假设实现并在小样如实呈现)
