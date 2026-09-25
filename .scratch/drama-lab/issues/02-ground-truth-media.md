# 票02 · 真值轨素材（Blender 正片 + DnR）与清单 schema

## What to build
真值轨就位与统一清单：①lab/drama/tools/media_sources.md（Blender 官方下载 URL 清单+每片声道 ffprobe 核实结论，重点核实哪些带 5.1——结论同时写进 results/e4_center51.md 的"素材核实"节）；②lab/drama/tools/cut_clips.py（通用切片：输入媒体+起止点表→30s~2min 片段 wav，输出片段文件+标签记录）；③lab/drama/clips_manifest.yaml 定义 schema 并填真值轨条目：字段=clip_id/来源(bili|blender|dnr|constructed)/分层标签(对白干净|音乐重|音效压对白|双人重叠|有5.1)/语言/时长/文件路径/备注；含配额进度计数。下载本身由协调者执行（泳道不联网），本票交付脚本+schema+基于已到文件的切片标注；文件未到齐时交付"脚本就绪+已到部分+待跑清单"。
## 验收标准
- [ ] clips_manifest.yaml schema 完整且真值轨已到素材完成切片标注
- [ ] cut_clips.py 对任一已到音频可跑（切片+时长校验）
- [ ] results/e4_center51.md 写明 Blender 各片声道核实结论（哪部有 5.1）
- [ ] 配额进度表（真值轨部分）在 manifest 内可读
## Blocked by
无（与票01并行；若目录未建则先建本票所需目录）
## 涉及路径
lab/drama/tools/media_sources.md; lab/drama/tools/cut_clips.py; lab/drama/clips_manifest.yaml; lab/drama/results/e4_center51.md; lab/drama/media/**（不入库，仅本地）
## 副作用声明
协调者将联网下载 GB 级媒体到 lab/drama/media/（泳道自身不联网）；本票局部验证=python 语法检查+对已到文件跑 cut_clips.py
decision_refs: D3, D6, F6
review_blocks: 无
