# -*- coding: utf-8 -*-
"""声纹认人核心:打分/语言路由/双门+互斥/校准/一致性(纯逻辑层,票02)。

契约(spec 20260925 voiceid + 评审裁定 D2/D3/D5/D6/D7/D9-D12/D16):
- 纯标准库,不 import pyannote/torch/numpy——向量是 JSON 里的 list[float],
  由调用方喂入(分人侧 embedding 落盘在票01,样本抽取器在票04)。
- 打分:对某人物的分数 = 参与该次打分的全部条目逐条 cosine 后算术平均
  (合并分身后同人物多来源条目全部参与,D5 打分层聚合);保留逐条分数 → top_ref。
- 语言路由(D3):spk 语言已知且有同语言组 → 用同语言组(同语言档);
  无同语言组:该人物只有一组用那组、多组逐组打分取最高组均值;跨语言比对用跨语言档,
  不静默闭嘴。
- 双门(D16):score≥THR(该语言档;跨语言比对用 thr_cross)且 top1−top2≥MARGIN
  才算过门;一期内一说话人只配一人物、一人物只配一说话人,贪心取最高分,
  过门落选方 verdict=compete(D11 竞争提示);其余 unknown。
- 冷启动(D12/F6):语言档未达解锁双条件(同人样本≥5 且冒认对≥10)→ calibrated=false,
  只显示分数、预填全关:suggest 压成 uncalibrated,compete 显示保留
  (verdict="compete", calibrated=false 并存,由调用方渲染)。
- 校准(D7):同人分布=留一法(每条 vs 同人物其余全部条目,跨语言组都算);
  冒认分布=跨人物条目两两 cosine(某语言档的冒认池=测试侧为该语言的对,对侧任意语言,
  其中对侧不同语言的为跨语言冒认对);THR=冒认 p99(冒认对<20 → max(p99, max+0.02));
  MARGIN=同人 top1−top2 差 p5(下限 0.03);THR_host=max(THR+0.05, 同人 p25),
  主持人预填沿用该语言档校准状态;跨语言档=该方向跨语言冒认对≥10 用其专属分布 p99,
  否则 THR+0.05;EER 顺手输出。落 work/voiceid_thresholds.json(按语言分档+样本数+标记)。
- 一致性检查(D6/F5):新向量对该人物该语言组条目的均分低于同人分布 p5 → 拦截;
  组内条目 <2 条跳过检查直接入库。
- 存储(D2/D17):work/persons_emb.json 条目 {person, lang, vec, ref_path, bvid,
  part, spk, ts, model}(ref_path 与 persons.json refs[].audio 关联);
  每人每语言封顶 MAX_ENTRIES_PER_LANG 条 FIFO;原子写同 persons.py。
"""
import copy
import json
import math
import os
import threading
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EMB_PATH = ROOT / "work" / "persons_emb.json"
DEFAULT_THR_PATH = ROOT / "work" / "voiceid_thresholds.json"

MAX_ENTRIES_PER_LANG = 10      # 每人每语言档案封顶(D2)
UNLOCK_GENUINE_N = 5           # 解锁双条件:同人样本≥5(D7/D12)
UNLOCK_IMPOSTOR_N = 10         # 解锁双条件:冒认对≥10
THR_BUMP_MIN_IMPOSTOR = 20     # 冒认对 <20 → THR 加余量
THR_BUMP = 0.02
THR_HOST_LIFT = 0.05           # THR_host = max(THR+0.05, 同人 p25)
CROSS_TIER_MIN_PAIRS = 10      # 跨语言档专属分布的样本门槛
CROSS_FALLBACK_LIFT = 0.05     # 跨语言档 fallback = 同语言档 THR + 0.05
MARGIN_FLOOR = 0.03            # MARGIN 下限


# ---------- 基础统计 ----------

def cosine(v1, v2):
    """余弦相似度;零向量按 0(方向未定不给分)。输入不必归一。"""
    v1, v2 = v1 or [], v2 or []
    dot = sum(a * b for a, b in zip(v1, v2))
    n1 = math.sqrt(sum(a * a for a in v1))
    n2 = math.sqrt(sum(b * b for b in v2))
    if n1 == 0 or n2 == 0:
        return 0.0
    return dot / (n1 * n2)


def percentile(values, q):
    """线性插值百分位(同 numpy 默认 'linear'):rank=(n-1)*q/100。
    values 空 → None;单个值 → 自身。"""
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return float(xs[0])
    rank = (len(xs) - 1) * (q / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(xs[int(rank)])
    return xs[lo] + (xs[hi] - xs[lo]) * (rank - lo)


def equal_error_rate(genuine_scores, impostor_scores):
    """EER:扫描候选阈值(全部唯一分数+相邻中点)取 |FAR−FRR| 最小处
    (平手取低阈值),EER=(FAR+FRR)/2。FAR(t)=冒认分≥t 比例,FRR(t)=同人分<t 比例。
    任一分布空 → None。
    """
    if not genuine_scores or not impostor_scores:
        return None
    cands = sorted(set(genuine_scores) | set(impostor_scores))
    mids = [(a + b) / 2 for a, b in zip(cands, cands[1:])]
    n_g, n_i = len(genuine_scores), len(impostor_scores)
    best = None  # (|diff|, eer)
    for t in sorted(set(cands) | set(mids)):
        far = sum(1 for s in impostor_scores if s >= t) / n_i
        frr = sum(1 for s in genuine_scores if s < t) / n_g
        d = abs(far - frr)
        if best is None or d < best[0]:
            best = (d, (far + frr) / 2)
    return best[1]


# ---------- 打分与路由 ----------

def score(spk_vec, person_entries):
    """对一个人的分数:参与条目逐条 cosine 再算术平均,保留逐条分与最像条目。

    返回 {"score": 均分|None, "per_entry": [{index, ref_path, score}...],
    "top_ref": 最像条目的 ref_path|None(并列取靠前)}。条目空 → score/top_ref None。
    """
    per = []
    for i, e in enumerate(person_entries or []):
        per.append({"index": i, "ref_path": e.get("ref_path"),
                    "score": cosine(spk_vec, e.get("vec"))})
    mean = (sum(p["score"] for p in per) / len(per)) if per else None
    top = max(per, key=lambda p: p["score"]) if per else None
    return {"score": mean, "per_entry": per,
            "top_ref": top["ref_path"] if top else None}


def group_by_lang(person_entries):
    """按语言分组(跨语言样本不混组,D3);lang 缺失归入 None 组。"""
    groups = {}
    for e in person_entries or []:
        groups.setdefault(e.get("lang"), []).append(e)
    return groups


def route(spk_vec, spk_lang, person_entries):
    """语言路由:选出参与打分的条目。返回 (entries, is_cross)。

    - spk_lang 已知且该人物有同语言组 → (该组, False);
    - 无同语言组(含 spk_lang 未知):只有一组 → (该组, True);
      多组 → 逐组打分取最高组均值(并列取语言名排序最前,确定性)→ (最优组, True);
    - 人物无条目 → ([], True)。
    """
    groups = group_by_lang(person_entries)
    if spk_lang and groups.get(spk_lang):
        return groups[spk_lang], False
    others = [g for _, g in sorted(groups.items(), key=lambda kv: str(kv[0])) if g]
    if len(others) <= 1:
        return (others[0] if others else []), True
    best = max(others, key=lambda g: score(spk_vec, g)["score"])
    return best, True


# ---------- 校准 ----------

def calibrate(entries):
    """全量档案条目 → 阈值表(voiceid_thresholds.json 的内存形态)。

    输入 persons_emb.json 的 entries;输出 {"version": 1, "langs": {lang: 档}}:
    每档含 calibrated / genuine_n / impostor_n / cross_impostor_n / margin_n /
    thr / margin / thr_host / thr_cross / genuine_p5 / genuine_p25 / eer。
    样本不足的档照算统计量(供显示与竞争判定),但 calibrated=false 不预填。
    """
    by_person = {}
    for e in entries or []:
        if e.get("vec"):
            by_person.setdefault(e.get("person"), []).append(e)

    genuine = {}    # lang -> [留一法均分](同人分布)
    margins = {}    # lang -> [同人 top1−top2 差](MARGIN 样本)
    impostor = {}   # lang -> [cos](测试侧为该语言的冒认对,对侧任意语言)
    cross_imp = {}  # lang -> [cos](其中对侧≠该语言的跨语言冒认对)

    for person, es in by_person.items():
        for i, e in enumerate(es):
            lang = e.get("lang")
            rest = es[:i] + es[i + 1:]
            own = score(e.get("vec"), rest)["score"] if rest else None
            if own is not None:
                genuine.setdefault(lang, []).append(own)
            # MARGIN 样本:该条留一打分全部人物,own − 最好他人(同人 top1−top2 差)
            if rest and len(by_person) > 1:
                best_other = None
                for q in sorted(by_person):
                    if q == person:
                        continue
                    used, _ = route(e.get("vec"), lang, by_person[q])
                    if not used:
                        continue
                    s = score(e.get("vec"), used)["score"]
                    best_other = s if best_other is None else max(best_other, s)
                if best_other is not None:
                    margins.setdefault(lang, []).append(own - best_other)

    persons = sorted(by_person)
    for i in range(len(persons)):
        for j in range(i + 1, len(persons)):
            for a in by_person[persons[i]]:
                for b in by_person[persons[j]]:
                    c = cosine(a.get("vec"), b.get("vec"))
                    la, lb = a.get("lang"), b.get("lang")
                    if la == lb:
                        impostor.setdefault(la, []).append(c)
                    else:
                        # 跨语言对按两个方向各记一次(测试侧口径)
                        impostor.setdefault(la, []).append(c)
                        impostor.setdefault(lb, []).append(c)
                        cross_imp.setdefault(la, []).append(c)
                        cross_imp.setdefault(lb, []).append(c)

    langs = {}
    for lang in sorted(set(genuine) | set(impostor) | set(cross_imp) | set(margins),
                       key=str):
        gen = genuine.get(lang) or []
        imp = impostor.get(lang) or []
        ximp = cross_imp.get(lang) or []
        mar = margins.get(lang) or []

        thr = percentile(imp, 99)
        if thr is not None and len(imp) < THR_BUMP_MIN_IMPOSTOR:
            thr = max(thr, max(imp) + THR_BUMP)
        p5_mar = percentile(mar, 5)
        margin_req = MARGIN_FLOOR if p5_mar is None else max(p5_mar, MARGIN_FLOOR)
        p25 = percentile(gen, 25)
        if thr is None:
            thr_host = None
        elif p25 is None:      # 该语言暂无同人分布 → 只有 THR+0.05 一项
            thr_host = thr + THR_HOST_LIFT
        else:
            thr_host = max(thr + THR_HOST_LIFT, p25)
        if len(ximp) >= CROSS_TIER_MIN_PAIRS:
            thr_cross = percentile(ximp, 99)
        else:
            thr_cross = None if thr is None else thr + CROSS_FALLBACK_LIFT

        langs[lang] = {
            "calibrated": len(gen) >= UNLOCK_GENUINE_N and len(imp) >= UNLOCK_IMPOSTOR_N,
            "genuine_n": len(gen), "impostor_n": len(imp),
            "cross_impostor_n": len(ximp), "margin_n": len(mar),
            "thr": thr, "margin": margin_req, "thr_host": thr_host,
            "thr_cross": thr_cross,
            "genuine_p5": percentile(gen, 5), "genuine_p25": p25,
            "eer": equal_error_rate(gen, imp),
        }
    return {"version": 1, "langs": langs}


def save_thresholds(thresholds, path=None):
    """阈值表原子落盘(同 persons.py:临时文件写全后 os.replace)。"""
    path = Path(path) if path else DEFAULT_THR_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(thresholds, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    os.replace(tmp, path)
    return path


def load_thresholds(path=None):
    """读阈值表;文件不存在 → None;存在但结构异常 → RuntimeError。"""
    path = Path(path) if path else DEFAULT_THR_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as e:
        raise RuntimeError(f"阈值文件损坏:{path}({e})") from e
    if not isinstance(data, dict) or not isinstance(data.get("langs"), dict):
        raise RuntimeError(f"阈值文件结构异常:{path}")
    return data


# ---------- 识别总入口 ----------

def identify(speakers, person_entries, thresholds=None, host_person=None):
    """总入口:说话人向量 × 人物档案 × 阈值 → voiceid.json 结构。

    speakers: [{"spk": "SPK_00", "vec": [...], "lang": "zh"|None}, ...]
    person_entries: persons_emb.json 的 entries(全部人物;无 vec 条目忽略)。
    thresholds: calibrate()/load_thresholds() 的表,None 或缺语言档 → 只显示不预填。
    host_person: 博主人物名(主持人候选只对它)。

    返回 {"results": [每说话人 {spk, person, score, margin, verdict, calibrated,
    top_ref, host_candidate}], "host_candidates": [spk...]}。
    - person/score/top_ref/margin = 第一名信息(供徽章显示;无候选 → None);
    - verdict ∈ suggest / unknown / compete / uncalibrated(F6:冷启动压 suggest 的
      预填语义 → uncalibrated;compete 保留显示,calibrated=false 并存);
    - calibrated = 该说话人语言档校准状态(与 verdict 无关,渲染层据此关预填);
    - host_candidate = 互斥胜出且匹配博主且过主持人档(跨语言比对用
      thr_cross+0.05);是否预填主持人由 host_candidate && calibrated 决定;
    - 仅一名候选人物时无第二名,余量门视为通过,margin=None。
    """
    thresholds = thresholds or {}
    langs = thresholds.get("langs") or {}
    by_person = {}
    for e in person_entries or []:
        if e.get("vec"):
            by_person.setdefault(e.get("person"), []).append(e)

    rows = []
    for spk in speakers or []:
        vec, lang = spk.get("vec"), spk.get("lang")
        tier = langs.get(lang) if lang else None
        row = {"spk": spk.get("spk"), "person": None, "score": None,
               "margin": None, "verdict": "unknown",
               "calibrated": bool(tier and tier.get("calibrated")),
               "top_ref": None, "host_candidate": False,
               "_tier": tier, "_cross": False, "_gates": False, "_won": False}
        cands = []
        if vec:
            for pname in sorted(by_person):
                used, cross = route(vec, lang, by_person[pname])
                if not used:
                    continue
                sc = score(vec, used)
                cands.append({"person": pname, "score": sc["score"],
                              "top_ref": sc["top_ref"], "cross": cross})
        if cands:
            cands.sort(key=lambda c: (-c["score"], c["person"]))
            top = cands[0]
            second = cands[1] if len(cands) > 1 else None
            row.update(person=top["person"], score=top["score"],
                       top_ref=top["top_ref"], _cross=top["cross"])
            if second is not None:
                row["margin"] = top["score"] - second["score"]
            if tier is not None:
                thr = tier.get("thr_cross" if top["cross"] else "thr")
                ok_thr = thr is not None and top["score"] >= thr
                if second is None:
                    ok_margin = True          # 无第二名:余量门视为通过
                else:
                    mreq = tier.get("margin")
                    ok_margin = (mreq is not None
                                 and top["score"] - second["score"] >= mreq)
                row["_gates"] = ok_thr and ok_margin
        rows.append(row)

    # 一对一互斥贪心:过门者按分数降序(平手按 spk 名)配对,落选方 compete
    taken = set()
    for row in sorted((r for r in rows if r["_gates"]),
                      key=lambda r: (-r["score"], str(r["spk"]))):
        if row["person"] in taken:
            row["verdict"] = "compete"
        else:
            taken.add(row["person"])
            row["_won"] = True
            row["verdict"] = "suggest" if row["calibrated"] else "uncalibrated"

    # 主持人候选:胜出 + 匹配博主 + 过主持人档(显示保留;预填看 calibrated)
    for row in rows:
        if not (row["_won"] and host_person and row["person"] == host_person
                and row["_tier"] is not None):
            continue
        if row["_cross"]:
            base = row["_tier"].get("thr_cross")
            host_thr = None if base is None else base + THR_HOST_LIFT
        else:
            host_thr = row["_tier"].get("thr_host")
        row["host_candidate"] = host_thr is not None and row["score"] >= host_thr

    keys = ("spk", "person", "score", "margin", "verdict",
            "calibrated", "top_ref", "host_candidate")
    return {"results": [{k: row[k] for k in keys} for row in rows],
            "host_candidates": [r["spk"] for r in rows if r["host_candidate"]]}


# ---------- 一致性检查(D6/F5) ----------

def check_consistency(new_vec, lang, person_entries, thresholds):
    """入库一致性检查:新向量对该人物【该语言组】已有条目的均分低于同人分布 p5 → 拦截。

    person_entries 传该人物该语言组的条目(调用方已按语言过滤)。
    组内条目 <2 条跳过检查直接入库(F5);该语言档无 genuine_p5(无同人分布)
    → 无法判定,放行并注明。返回 (ok, score, reason)。
    """
    entries = [e for e in (person_entries or []) if e.get("vec")]
    if len(entries) < 2:
        return True, None, "组内条目 <2,跳过一致性检查(F5)"
    sc = score(new_vec, entries)["score"]
    tier = ((thresholds or {}).get("langs") or {}).get(lang) or {}
    p5 = tier.get("genuine_p5")
    if p5 is None:
        return True, sc, "该语言档无同人分布 p5,无法判定,放行"
    if sc < p5:
        return False, sc, f"新样本均分 {sc:.4f} 低于同人分布 p5 {p5:.4f},拦截"
    return True, sc, "ok"


# ---------- 档案读写(persons_emb.json,D2/D17) ----------

class VoiceArchive:
    """work/persons_emb.json 的单写入者:声纹档案向量缓存,所有增改经本类串行落盘。"""

    def __init__(self, path=None):
        self.path = Path(path) if path else DEFAULT_EMB_PATH
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "entries": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"声纹档案文件损坏:{self.path}({e})") from e
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise RuntimeError(f"声纹档案文件结构异常:{self.path}")
        return data

    def _save(self):
        """原子写:同目录临时文件写全后 os.replace,半写入不留主文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    # ---------- 读取 ----------

    def entries(self, person=None, lang=None):
        """按人物/语言过滤的条目深拷贝(persons_emb.json 全量形态)。"""
        with self._lock:
            es = self._data["entries"]
            if person is not None:
                es = [e for e in es if e.get("person") == person]
            if lang is not None:
                es = [e for e in es if e.get("lang") == lang]
            return copy.deepcopy(es)

    # ---------- 写入 ----------

    def add_entry(self, person, lang, vec, ref_path, bvid=None, part=None,
                  spk=None, ts=None, model=None) -> dict:
        """追加入库;每人每语言封顶 MAX_ENTRIES_PER_LANG 条,FIFO 挤掉最旧。"""
        with self._lock:
            entry = {"person": person, "lang": lang, "vec": list(vec or []),
                     "ref_path": str(ref_path), "bvid": bvid, "part": part,
                     "spk": spk,
                     "ts": ts or datetime.now().isoformat(timespec="seconds"),
                     "model": model}
            es = self._data["entries"]
            es.append(entry)
            same = [i for i, e in enumerate(es)
                    if e.get("person") == person and e.get("lang") == lang]
            for i in reversed(same[:-MAX_ENTRIES_PER_LANG]):
                es.pop(i)
            self._save()
            return copy.deepcopy(entry)

    def remove_by_ref_path(self, ref_path) -> int:
        """级联删:persons.json 删 refs 条目时按 ref_path 删对应向量记录。返回删除条数。"""
        with self._lock:
            before = len(self._data["entries"])
            self._data["entries"] = [e for e in self._data["entries"]
                                     if e.get("ref_path") != ref_path]
            removed = before - len(self._data["entries"])
            if removed:
                self._save()
            return removed

    def remove_person(self, person) -> int:
        """级联删:人物删除时清掉其全部向量记录。返回删除条数。"""
        with self._lock:
            before = len(self._data["entries"])
            self._data["entries"] = [e for e in self._data["entries"]
                                     if e.get("person") != person]
            removed = before - len(self._data["entries"])
            if removed:
                self._save()
            return removed
