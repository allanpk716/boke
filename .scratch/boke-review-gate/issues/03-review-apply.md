# 03 · 核对决策应用器与决策版本(F12 核心)

## What to build
`src/boke/review_apply.py`:吃核对台提交的决策 JSON(每分P),产出可执行配置并登记:
1. **dec_hash** = 决策 JSON 规范化(serialized sort_keys)的 sha256 前 12 位;
2. 决策目录 `work/<id>/dec_<hash>/`:生成 `voices.generated.yaml`(格式兼容 synthesize.run 读取:library/episode_maps 结构;主持人→人物库克隆 zero(ref_audio+ref_text 来自 persons 模块校验通过的 ref);嘉宾 clone→cross 无文本(ref=候选原声段路径);preset→edge 音色;skip→标记 skip_voice);归人 rttm 合并结果(merge_into 重跑 attribute:用 boke.attribute 的 merge 后逻辑,写 `tagged.dec.srt`);
3. **哨兵完整性**:目录内每个生成产物配套 `.done` 哨兵(内容产物完整后最后写);`is_complete(path)` 判据=哨兵存在;
4. rediarize 决策(决策 JSON 含 rediarize)→ 不生成配置,返回"决策作废"信号(由调用方回退状态);
5. `render_preview_config` / `render_full_config` 共用生成逻辑(小样与全片同 hash 目录)。

## 验收标准
- [ ] dec_hash:同决策稳定同值;任一字段变化→新值
- [ ] 合并生效:构造 rttm/srt(复用 tests/test_attribute.py 风格),SPK_03 并入 SPK_00 后归人重算正确
- [ ] voices.generated.yaml 被 boke.synthesize.run 正确消费(现有 episode_maps 匹配逻辑;单测构造假引擎路径校验结构)
- [ ] 哨兵:产物写完才出现 .done;is_complete 对半写入(无哨兵)返回 False
- [ ] 主持人校验失败(缺转写稿/同分P)→ 抛出明确错误,不生成降级配置
- [ ] `tests/test_review_apply.py` 覆盖上述;既有 pytest 全绿

## Blocked by
01(library 记录登记)、02(persons 校验)

## 涉及路径
- src/boke/review_apply.py(新增)
- tests/test_review_apply.py(新增)
- src/boke/attribute.py(只读复用;如需加 merge 入口函数可最小新增,不破坏现有 API)

## 副作用声明
测试用 tmp_path 构造工作目录;不得写真实 work/<id>

## decision_refs
D3、D4、D6、D9、D10、F1、F2、F3、F4、F12

## review_blocks
F12(本票即其解除条件的实现;终局评审验证)
