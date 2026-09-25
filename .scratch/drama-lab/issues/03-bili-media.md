# 票03 · B 站真实轨素材（1 部国产剧 + 1 部海外剧）

## What to build
真实轨就位：①lab/drama/tools/bili_fetch.md（选片决策记录：片名/BV 或链接/选它的理由=对话密集+含音乐重/音效压对白/双人重叠场景+免费可看；获取方式说明——yt-dlp 支持 bilibili 免费内容无需登录凭据，**绝不复用主线充电下载脚本与凭据逻辑**）；②切片与分层标注进 clips_manifest.yaml（复用票02 schema）；③配额进度（每部剧每语言 对白干净≥3/音乐重≥3/音效压对白≥3/双人重叠≥4）。选片自主权已授权（D6），必须写明理由；海外剧若 B 站无合适免费英文对话剧，允许以 Blender 英文片充当 en 真实轨并如实记录该替代（仍需选含配乐/音效/重叠场景的段）。
## 验收标准
- [ ] bili_fetch.md 含选片理由与获取方式，无登录凭据明文
- [ ] clips_manifest.yaml 真实轨条目完成切片标注，分层配额进度可读
- [ ] 每段片段文件存在且时长 30s~2min
- [ ] 双人重叠段与独白段来源明确（独白段为票06 伪重叠原料）
## Blocked by
票02（schema 与 cut_clips.py）
## 涉及路径
lab/drama/tools/bili_fetch.md; lab/drama/clips_manifest.yaml（追加）; lab/drama/media/**（不入库）
## 副作用声明
协调者联网下载（泳道自身不联网）；局部验证=对已到文件跑切片与统计
decision_refs: D1（独立下载，不复用主线充电脚本）, D3, D6, F6
review_blocks: 无
