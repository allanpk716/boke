# 01 · 片库账本与状态机

## What to build
片库唯一状态源 `work/library.json` 的读写模块与状态机:每视频每分P一条记录;主链七态(待下载→待分析→待核对→已核对待合成→小样待听→全片合成中→已交付)+旁路两态(已排除/失败)+busy 子标志;转移含:小样待听→待核对(不过回退)、失败→原主态断点重试、改人数重跑分人→决策作废回待核对。单写入者:模块内进程锁+原子写(tmp 写完 rename)。

## 验收标准
- [ ] `src/boke/library.py`:记录 CRUD、按 bvid/part 查询、状态转移唯一入口 `transition(bvid, part, event)`;非法转移抛错
- [ ] 转移表覆盖 spec 全部转移(含回退边/失败重试/busy 置位清除/已排除终态)
- [ ] 原子写:进程内线程锁;写临时文件后 os.replace
- [ ] `tests/test_library.py`:合法转移全枚举、非法转移拒绝、并发写不丢(线程×100 交错)、busy 语义、失败记录错误信息与阶段
- [ ] 既有 `pytest tests/` 全绿

## Blocked by
无,可立即开始

## 涉及路径
- src/boke/library.py(新增)
- tests/test_library.py(新增)

## 副作用声明
无独占命令;只跑 pytest tests/test_library.py

## decision_refs
D7(排除需用户确认)、D9(按分P)、D13(小样门)、F6、F7

## review_blocks
无
