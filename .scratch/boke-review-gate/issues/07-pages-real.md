# 07 · 三个页面接真数据与服务端路由

## What to build
把 mock 页面接到真实后端(work/tools/web_server.py 扩展路由,复用静态服务骨架):
1. **GET /api/library**:返回账本(01)全量(含管道计数/待办);**GET /api/episode/<bvid>**:期详情(parts/说话人样本路径/langid 结论/状态/小样路径/成品路径);
2. **POST /api/ingest**:BV 粘贴导入(body: {bv} 或 {items:[...] 清单勾选});cookie 失效返回 423+人话错误→页面红条;
3. **POST /api/review**:收核对台提交(每分P决策 JSON)→ 调 03 应用器(rediarize→返回决策作废信号→前端跳回核对态);成功→触发 05 小样(后台线程)→状态已核对待合成+busy;
4. **POST /api/preview/<bvid>/<part>** {"verdict":"pass|fail"}:pass→触发 06(后台线程);fail→回退待核对+旧小样标作废;
5. **POST /api/retry/<bvid>/<part>**:失败重试;
6. **页面改造**:library.html 改读 /api/library(期名/状态/管道计数/CTA 全真数据;下载所选→/api/ingest;cookie 红条);review.html 改读 /api/episode(说话人卡数据真源;提交→/api/review;小样区播放 preview.mp3;过/不过→/api/preview;重跑分人选项→决策 JSON 带 rediarize);index.html(试听台)增"已交付"成品列表区(/api/library 过滤);
7. 人物库区改读 /api/persons(GET);存入弹窗 POST /api/persons/ref(收录说话人候选到人物,带来源溯源)。
后台长任务(下载/分析/小样/全片)经 threading.Thread 驱动 04/05/06,页面轮询 /api/library 刷新(2s)。

## 验收标准
- [ ] 各路由按上述契约工作(用账本/人物库桩+真实模块组合测:tests/test_web_api.py 用 http client 起本地服务实例)
- [ ] 片库页刷新后显示账本真实状态(人工点验步骤写进 08 验收)
- [ ] review.html 提交落 /api/review 并在账本出现决策记录与 dec 目录
- [ ] cookie 失效路径返回 423 与红条文案
- [ ] 既有静态路由(/audio /media 静态页)不回归
- [ ] `tests/test_web_api.py`;既有 pytest 全绿

## Blocked by
01、02、03、04、05、06

## 涉及路径
- work/tools/web_server.py(改)
- work/web/library.html(改)
- work/web/review.html(改)
- work/web/index.html(改)
- tests/test_web_api.py(新增)

## 副作用声明
测试起服务用随机端口;不得占用 8765

## decision_refs
D2、D7、D9、D11、D13、F6、F7、F13

## review_blocks
无
