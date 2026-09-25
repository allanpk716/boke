# 票06 · 归档闭环(提交→自动沉淀→校准)

## What to build
`api_review` 提交成功分支接归档闭环(web_server.py;同文件依赖票05 先落地):

1. 按最终说话人集合(应用 merge_into 后)**逐 SPK** 归档:切 ≤30s 说话人样本(复用 work/tools/cut_speaker_samples.py 的切法)→ 经票04 抽取器**从样本直接抽取向量(F2 解除裁定:档案向量=样本直抽,不是整期质心)**→ 写 persons.json refs[](use=voiceid,path=样本,票03 接口)+ persons_emb.json(该语言组,FIFO 封顶 10)。
2. 一致性检查(票02 判定):组内 <2 条跳过;低于同人分布 p5 → 默认不入档,返回 {blocked: [{spk, score, p5, sample_path}]},UI 当场提示+可试听+提供"仍入库"端点(POST /api/persons/ref/force 或复用既有入口加 force 参数)由人工裁决。
3. 归档后自动重算阈值(票02 校准)并落 voiceid_thresholds.json;API 回执附回显文案所需数据("已为圆脸归档 2 条(中文组现有 8 条)")。
4. UI(review.html):提交成功 toast 显示回显;拦截时弹提示块(试听按钮+仍入库按钮)。

## 验收标准
- [ ] 归档单测(stub 抽取器注入):逐 SPK 条目数正确;merge 后同人物多 SPK 各成条;语言分组正确;FIFO 封顶驱逐最旧
- [ ] 一致性拦截:<2 条跳过;低于 p5 默认拦+force 入库成功;toast 文案数据正确
- [ ] **F2 验收断言**:归档写入的向量来自样本直抽路径(测试 stub 模型验证:档案 vec == extract(sample) 的 stub 返回值;并调用票04 --recompute-consistency 的核心比对函数 cos≥0.999 逻辑)
- [ ] 归档后 thresholds 重算被调用(mock 校准器);全量 pytest 零回归

## Blocked by
票03(refs 契约)、票04(抽取器)、票05(同文件先后)

## 涉及路径
- work/tools/web_server.py(改)
- work/web/review.html(改)
- tests/test_archive.py(新)

## 副作用声明
- 独占验证命令:pytest tests/test_archive.py -q

## decision_refs
D2(样本直抽)/D4(提交即归档+回显)/D5(逐 SPK 全集)/D6(拦截+仍入库)/D7(校准重算)/D8

## review_blocks
F2(解除条件验收在本票)、F5(<2 条跳过落地)
