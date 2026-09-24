# 02 · 人物库存储与主持人挂接校验

## What to build
`work/persons.json` 人物库模块:人物={备注名,主要语言,头像(可选引用),备注,refs:[{audio,transcript,lang,source}]};source 记录来源(期/分P/说话人)或"手动上传"。提供:`add_person/add_ref/get`;主持人挂接校验 `check_host_attach(person, current_part_p)`:必须存在"参考音+配套转写稿"齐备的 ref,且 ref.source 的分P ≠ 当前分P;不满足返回明确缺项。初始化:库为空时幂等种子——圆脸(refs/yuanlian_chinese_01.wav+ref01_transcript.txt;_02 同)、小外(work/web/audio/guest_ref_english.wav,无转写稿,cross 用)。

## 验收标准
- [ ] `src/boke/persons.py`:存储原子写同账本风格;种子幂等(重复初始化不重复添加)
- [ ] `check_host_attach`:缺转写稿→阻断并指明"缺哪条参考音的转写稿";来源=当前分P→拒绝(防绕 D4)
- [ ] `tests/test_persons.py`:种子幂等、加 ref、校验三分支(通过/缺稿/同分P)
- [ ] 既有 pytest 全绿

## Blocked by
无,可立即开始

## 涉及路径
- src/boke/persons.py(新增)
- tests/test_persons.py(新增)

## 副作用声明
无独占命令;只跑 pytest tests/test_persons.py(不得真实写 work/persons.json——测试用 tmp_path)

## decision_refs
D4、D5、D10、F1、F2、F8

## review_blocks
无
