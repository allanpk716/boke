# 08 · 端到端验收冒烟

## What to build
`work/tools/acceptance_check.py`:按 spec 验收 6 条的可执行冒烟(允许用既有已分析媒体,标注哪些步走真实引擎哪些走桩):
1. 账本播种"嘉宾中国行 P1/P2"(既有产物路径挂入)→ 核对台提交决策(合并 SPK_03→SPK_00、指认、配声、P2 确认排除)→ /api/review → 断言:决策目录生成、小样任务排起(P1 真实小样可跑:GPU,或桩模式 SKIP_GPU=1 出静音小样)、P2=已排除;
2. 片库勾选导入一个未下载 BV(cookie 有效时真下载一个最小分P;失效时断言 423 红条);
3. 人为制造失败(桩 stage 抛错)→ 失败态+重试断点续跑断言;
4. cookie 置空→/api/ingest 返回 423;
5. 决策变更→dec_hash 变化→旧小样/成品不再被引用(账本路径指向新 hash);
6. 三个页面 /api 数据一致性断言(期名/状态同源)。
输出逐条 PASS/FAIL 报告(写 stdout 与 work/acceptance_report.txt)。

## 验收标准
- [ ] 脚本可重复执行(幂等:每次重播种临时账本副本)
- [ ] SKIP_GPU=1 时全程不调真实 TTS(静音桩),默认时小样真实合成
- [ ] 六条各有断言与明确输出
- [ ] 不破坏既有 tests/

## Blocked by
05、06、07

## 涉及路径
- work/tools/acceptance_check.py(新增)

## 副作用声明
独占命令:python work/tools/acceptance_check.py(默认含 GPU 小样路径时占用显卡约 2-3 分钟;SKIP_GPU=1 无 GPU);写 work/acceptance_report.txt

## decision_refs
D7、D9、D13;spec §User Stories 全部

## review_blocks
无
