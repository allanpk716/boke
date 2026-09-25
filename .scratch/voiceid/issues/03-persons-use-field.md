# 票03 · persons 参考音契约扩展(use 字段+级联删除)

## What to build
`src/boke/persons.py` 参考音契约扩展(向后兼容,persons.json 仍为唯一状态源、纯文本手工可读、不存向量):

1. refs[] 条目新增**可选 `use` 字段**:取值 `clone | voiceid`;旧条目/未写字段缺省 `clone`(读侧一律按缺省补齐,写侧不回改旧文件除非显式保存)。
2. 自动归档条目写入接口:add_ref(person, path, use="voiceid", source={bvid, part, spk}, lang)——供票06 归档钩子调用;人工"存入人物库"与手动上传路径默认 use="clone"(现有行为不变)。
3. 级联删除辅助:删除某 ref 条目 → 返回需连带清理的(向量缓存记录键, 自动样本文件路径)供调用方执行(模块不直接碰 persons_emb.json,只给出清单;自动样本文件=use voiceid 条目的 path,手动上传只清缓存不删用户原文件);删除人物 → 该人物全部 refs 的级联清单。
4. 只读枚举:iter_ref_paths(person, use=None) / list_refs(person, use="clone")(克隆选音过滤用)。
5. 既有 check_host_attach / person_ref API 契约不变(D4 同分P拒绝规则照旧,只作用于克隆用途)。

## 验收标准
- [ ] 旧 persons.json(无 use 字段)加载兼容,读侧缺省 clone;现有 test_persons 全绿
- [ ] add_ref(use=voiceid)/list_refs(use=clone) 过滤正确;旧条目不出现在 voiceid 列表、新归档条目不出现在克隆列表
- [ ] 级联清单正确:自动样本含文件路径、手动上传不含;删人物覆盖全部条目
- [ ] 全量 pytest 零回归

## Blocked by
无,可立即开始。

## 涉及路径
- src/boke/persons.py(改)
- tests/test_persons.py(改/扩)

## 副作用声明
- 独占验证命令:pytest tests/test_persons.py -q

## decision_refs
D1(一套集合+用途标记)、D17(persons.json 不塞向量)、D2、D4

## review_blocks
无
