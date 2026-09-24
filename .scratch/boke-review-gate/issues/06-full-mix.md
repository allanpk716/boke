# 06 · 全片合成编排

## What to build
`src/boke/fullmix.py`:`run_full_mix(bvid, part)`(前提:该分P状态=小样待听且已过小样门):
1. 状态→全片合成中+busy;
2. 调 boke.pipeline 的 synthesize+mix(voices 用决策目录 `work/<id>/dec_<hash>/voices.generated.yaml`;产物落同目录);
3. 完成(哨兵)→ 成品路径登记,状态→已交付;
4. 异常→失败态(stage+错误);`retry` 复用同 dec_hash 目录内已完整产物(哨兵判据),从断点续跑;
5. 决策变更(hash 变)后旧成品不引用——由目录键天然隔离,本票保证只读当前 hash 目录。

## 验收标准
- [ ] 桩测:monkeypatch pipeline stages,验证状态流转(小样待听→全片合成中→已交付)与失败登记
- [ ] 断点:同 hash 下已完整中间产物(带哨兵)不重跑;无哨兵半产物重跑
- [ ] 决策 hash 变更后 run_full_mix 读新目录(构造两 hash 桩验证不串)
- [ ] `tests/test_fullmix.py`;既有 pytest 全绿

## Blocked by
03(决策目录)

## 涉及路径
- src/boke/fullmix.py(新增)
- tests/test_fullmix.py(新增)

## 副作用声明
测试全桩;不调真实 GPU 合成

## decision_refs
D13、F6、F12

## review_blocks
无
