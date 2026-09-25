# 票05 · 核对门声纹建议接入(API+UI)

## What to build
把识别结果接到核对门,端到端可见:

**API 侧(work/tools/web_server.py)**:
1. 分人完成钩子:分析阶段产 spk_emb.npy 后自动跑识别,落 `work/<id>/voiceid.json`(结构=票02 identify 输出);mock/桩分 P 无 embeddings → 写 {"available": false, "reason": "stub-diarization"}。
2. `GET /api/episode` 每个说话人对象附 `suggest` 字段:读 voiceid.json;若 persons_emb.json/thresholds 比它新 → 用 voiceid.identify 重算一次再返回(毫秒级);桩分 P 返回 available:false。
3. 既有 8 条 API 与 busy 检查/目录围栏行为零回归。

**UI 侧(work/web/review.html)**:
4. 说话人卡片徽章:"声纹建议: 圆脸 0.72"(suggest)或"声纹分数: 圆脸 0.72(未校准,仅参考)"(uncalibrated)或"与 SPK_00 竞争圆脸(0.69 vs 0.72),未建议"(compete)或小字"声纹建议不可用(分人为桩数据)"(stub)。
5. 预填:suggest 且非 uncalibrated → 人物下拉自动预选(可改);匹配圆脸且过 THR_host(后端在 suggest 里给 host_candidate 判定)→ host=true+host_person 预填(可改可关);uncalibrated/compete/unknown → 不预填任何东西(分数与竞争照常显示)。
6. 克隆选音列表默认过滤 use≠voiceid 之外……即默认只列 use=clone(用票03 list_refs);**审计 refs[] 既有全部读取点**(人物卡片计数/列表展示/其他 API 输出),逐处决定过滤并在票回报中列清单(F9)。

## 验收标准
- [ ] API 合约测试:suggest 字段四态(suggest/uncalibrated/compete/stub 不可用);档案更新触发重算(mock 时钟/文件 mtime);桩分 P available:false
- [ ] UI:四态渲染 smoke(沿用现有 review.html 测试先例);预填可改可关;决策 JSON 仍需人工提交(api_review 不变)
- [ ] refs[] 读取点审计清单完整(回报中列出每一处及处置)
- [ ] 既有 web_server 测试全绿;全量 pytest 零回归

## Blocked by
票01(spk_emb 落盘)、票02(identify/verdict)、票03(use 过滤)

## 涉及路径
- work/tools/web_server.py(改)
- work/web/review.html(改)
- tests/test_web_voiceid.py(新)

## 副作用声明
- 独占验证命令:pytest tests/test_web_voiceid.py tests/ -x -q

## decision_refs
D4/D9/D10/D11/D12/D13/D14(桩模式小字)

## review_blocks
F6(冷启动×竞争显示规则落地)、F9(refs 读取点审计)
