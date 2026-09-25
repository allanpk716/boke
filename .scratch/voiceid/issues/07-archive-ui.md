# 票07 · 人物库详情页档案列表

## What to build
人物库详情页(实施时核三页现状:index/library/review 中承载人物库的位置,人物卡片/详情在哪页就长在哪页)增加声纹档案管理区:

1. API(web_server.py):GET /api/persons/<id>/archive —— 按人物×语言分组返回档案条目(person/lang/ref_path/bvid/part/spk/ts/model/有效性);DELETE 归档条目 → 走票03 级联清单(删向量缓存记录+自动样本文件+refs 条目)。
2. UI:每组一列/一节,条目行=试听 ▶(audio 元素播 ref_path,经静态服务)+来源(期号/SPK)+删除按钮;组头显示"中文组 8 条"。
3. F11 有效性:加载时 ref_path 存在性校验,缺失条目标"文件缺失(失效)"置灰,不参与识别打分(调票02 读侧过滤)与计数提示。
4. 最小实现,不做批量操作/重命名。

## 验收标准
- [ ] archive API:分组结构正确;删除后 refs/向量缓存/样本文件三者级联清理(单测 tmp 目录);缺失文件条目标记失效
- [ ] UI smoke:列表渲染+试听元素+删除调用(沿用页面既有测试先例)
- [ ] 全量 pytest 零回归

## Blocked by
票03(级联)、票06(档案有真实数据可显示;API 可先行但验收以闭环为准)

## 涉及路径
- work/tools/web_server.py(改)
- work/web/library.html(改;若人物库实际在 review.html 则改之,回报注明)
- work/web/review.html(仅当人物库在此页)
- tests/test_persons_archive_api.py(新)

## 副作用声明
- 独占验证命令:pytest tests/test_persons_archive_api.py -q

## decision_refs
D2、D11 之外的管理入口(spec US10)、D14

## review_blocks
F11(存在性校验+失效标记)
