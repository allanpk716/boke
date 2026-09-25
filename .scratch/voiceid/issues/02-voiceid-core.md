# 票02 · voiceid 核心识别与校准(纯逻辑层)

## What to build
新建 `src/boke/voiceid.py`:声纹认人的全部判定逻辑,纯函数+JSON 读写,**不依赖 pyannote/torch**(向量由调用方喂入;抽取器在票04)。风格对齐 src/boke 既有模块(标准库+懒加载,docstring 说明契约)。

1. **打分**:`score(spk_vec, person_entries) = mean(cosine(spk_vec, entry.vec))`,逐条打分再平均;保留逐条分数(可报"最像的是哪条")。
2. **语言路由**:spk 语言已知时优先用同语言组条目;无同语言组:该人物只有一组用那组、多组逐组打分取最高组均值;跨语言比对用跨语言档阈值。
3. **双门+互斥**:score≥THR(该语言对)且 top1−top2≥MARGIN 才 suggest;一对一互斥贪心配对(一期内一说话人只配一人物、一人物只配一说话人,取最高分),落选方 verdict=compete;其余 unknown。
4. **冷启动**:语言对未达解锁双条件(同人样本≥5 且冒认对≥10)→ uncalibrated:只显示分数,预填全关(compete 显示保留,预填不保留)。
5. **主持人档**:THR_host = max(THR+0.05, 同人分布 p25);主持人预填沿用该语言对校准状态。
6. **校准计算**:输入全部档案条目,输出 thresholds:同人分布=留一法(每条 vs 其余条目档案打分);冒认分布=跨人物交叉对(含跨语言交叉对);THR=冒认 p99,冒认样本<20 时 max(p99, max+0.02);MARGIN=同人 top1−top2 差 p5 下限 0.03;跨语言档=该方向≥10 跨语言冒认对用其专属分布 p99,否则同语言档 THR+0.05;EER 顺手输出。落 `work/voiceid_thresholds.json`(按语言对分档+样本数+uncalibrated 标记)。
7. **档案读写**:`work/persons_emb.json` 条目 {person, lang, vec, ref_path, bvid, part, spk, ts, model};每人每语言封顶 10 条 FIFO。
8. **一致性检查判定**:新向量 vs 该人物该语言组已有条目分数低于同人分布 p5 → 拦截;组内条目 <2 条跳过检查(F5 默认)。
9. **identify 总入口**:输入(spk 向量+语言, 人物档案, 阈值)→ voiceid.json 结构 [{spk, person, score, margin, verdict, top_ref}],另单列主持人候选。

## 验收标准
- [ ] 合成向量单测逐条覆盖:路由三分支/双门两门各自拒绝/互斥贪心与 compete/冷启动关预填写显示/THR_host/一致性拦截与 <2 条跳过/FIFO 封顶
- [ ] 校准统计量单测:构造已知分布验证 p99/余量/MARGIN 下限/跨语言档 fallback/双条件解锁与不解锁/EER
- [ ] 纯逻辑不 import pyannote/labeled heavy deps(测试环境无 venv 也能全绿)
- [ ] 全量 pytest 零回归

## Blocked by
无,可立即开始。

## 涉及路径
- src/boke/voiceid.py(新)
- tests/test_voiceid.py(新)

## 副作用声明
- 独占验证命令:pytest tests/test_voiceid.py -q

## decision_refs
D2/D3/D5(打分层聚合)/D6/D7/D9/D10/D11/D12/D16

## review_blocks
F6(compete 与 uncalibrated 并存规则:显示开、预填关——本票 verdict 语义为此负责)
