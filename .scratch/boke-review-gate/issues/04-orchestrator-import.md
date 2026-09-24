# 04 · 分析编排与导入入口

## What to build
`src/boke/orchestrator.py`:
1. **导入**:`ingest_bv(bv)` 与 `ingest_catalog(items)`——登记账本(待下载),返回任务;导入前 `check_cookie()`(读 work/cookies_full.txt 的 SESSDATA,GET bilibili nav 接口验证,失效→返回 False+原因,不登记);
2. **下载**:调既有 yt-dlp 命令行(work/tools/download_exclusive.py 的参数模板,输出到 D:/boke_media/<BV>_P<idx>.mp4);完成→每 part 登记媒体路径,状态→待分析;
3. **分析链**:对每 part 依次调 boke.pipeline 各 stage(extract→ocr→diarize 经隔离 venv runner→attribute→check_language 的等价调用:diarize 用 boke.diarize.run 即已内置 subprocess);产物路径入账;全部完成→待核对+登记 langid 结论(建议跳过标记);任一步异常→失败态+错误信息+阶段;
4. 下载/分析为**同步函数**,由调用方(服务端后台线程)驱动;账本所有变更走 01 的接口。

## 验收标准
- [ ] check_cookie:有效 cookie 返回 True;文件缺失/SESSDATA 失效返回 False+人话原因(单测用本地文件桩,不打真实网络——真实网络验证留 08 验收)
- [ ] ingest_bv 登记账本且幂等(重复导入不重复记录)
- [ ] 分析链:用 tmp 目录+假视频/假 stage 产物桩测编排顺序与失败登记(不跑真实 OCR/分人)
- [ ] 失败→失败态含 stage 与错误文本;重试入口 retry(bvid, part) 从失败 stage 续跑(桩测)
- [ ] `tests/test_orchestrator.py`;既有 pytest 全绿

## Blocked by
01(library 接口)

## 涉及路径
- src/boke/orchestrator.py(新增)
- tests/test_orchestrator.py(新增)

## 副作用声明
测试全桩化(tmp_path+monkeypatch);不联网、不写 D 盘

## decision_refs
D7(语言检测结论仅建议)、D11(导入优先级与 cookie 门)、F6

## review_blocks
无
