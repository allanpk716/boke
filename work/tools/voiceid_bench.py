# -*- coding: utf-8 -*-
"""声纹验证实验 bench(主 venv 编排;GPU 侧经 boke.voiceid_extract 走 .venv-diar;票04)。

三个子命令(第一轮素材与判据 docs/research/04 §6;实验照跑留底不 gate 交付,D15):
  matrix                 episode(spk_emb.npy+spk_emb.json)× 人物参考音(档案缓存向量)
                         跑 SPK×person 分数矩阵;有 --truth( ground truth:
                         {\"<episode目录名>\": {\"SPK_00\": \"人物\", ...}})时输出
                         同人/冒认分布、EER、0.02 分 bin 直方图 → work/voiceid_bench/
  verify-alignment       真实 episode 核对 spk_emb.npy 行序 = rttm SPK 标签首次出现序
                         (F8 对齐断言,GPU 环境对真实产物跑)
  recompute-consistency  对 persons_emb.json 抽样条目,用抽取器重抽样本 embedding
                         与缓存向量比对,断言 cos>=0.999(F2 解除验收;
                         单测注入假模型,真实跑法见下)

打分/路由/EER/直方图统计口径一律复用 src/boke/voiceid.py 的纯函数
(route/score/cosine/equal_error_rate),不另起一套实现。

GPU 实跑示例(验收可选;需 .venv-diar 就绪):
  python work/tools/voiceid_bench.py matrix --episode work/BV17UPszhE6o_full
  python work/tools/voiceid_bench.py verify-alignment work/BV17UPszhE6o_full
  python work/tools/voiceid_bench.py recompute-consistency --sample 3
"""
import argparse
import json
import math
import random
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from boke import voiceid                     # noqa: E402
from boke import voiceid_extract             # noqa: E402

MODEL_ID = voiceid_extract.MODEL_ID
RECOMPUTE_MIN_COS = 0.999                    # F2:同模型重抽一致性断言线
DEFAULT_EMB = _ROOT / "work" / "persons_emb.json"
DEFAULT_OUT = _ROOT / "work" / "voiceid_bench"


# ---------- 读取 episode 产物 ----------

def load_episode_spk(episode_dir):
    """票01 产物 → (labels, vecs[list[list[float]]])。缺件/行数不符 → SystemExit。"""
    d = Path(episode_dir)
    npy, js = d / "spk_emb.npy", d / "spk_emb.json"
    if not (npy.exists() and js.exists()):
        raise SystemExit(f"[bench] {d} 缺 spk_emb.npy/spk_emb.json"
                         f"(需先在 .venv-diar 跑过分人,票01 产物)")
    meta = json.loads(js.read_text(encoding="utf-8"))
    arr = np.load(npy)
    labels = list(meta.get("labels") or [])
    if arr.shape[0] != len(labels):
        raise SystemExit(f"[bench] {d} npy 行数 {arr.shape[0]} != labels 数 "
                         f"{len(labels)}(对齐已破,先跑 verify-alignment)")
    return labels, [row.tolist() for row in arr]


def rttm_spk_order(rttm_path):
    """rttm 中 SPK 标签按首次出现序(F8 断言基准)。"""
    order = []
    for line in Path(rttm_path).read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) > 7 and parts[0] == "SPEAKER" and parts[7] not in order:
            order.append(parts[7])
    return order


# ---------- matrix ----------

def score_matrix(episode_dirs, entries):
    """每 episode 的 SPK × 每人物分数(语言路由按跨语言口径:bench 未知 spk 语言)。

    复用 voiceid.route + voiceid.score;返回
    {ep名: {"dir", "labels", "matrix": [[{spk,person,score,cross,top_ref}]]}}
    """
    by_person = {}
    for e in entries or []:
        if e.get("vec"):
            by_person.setdefault(e.get("person"), []).append(e)
    if not by_person:
        raise SystemExit(f"[bench] 人物档案为空( persons_emb 无带 vec 条目),"
                         f"先归档参考音(票06)")
    persons = sorted(by_person)

    out = {}
    for ep in episode_dirs:
        labels, vecs = load_episode_spk(ep)
        matrix = []
        for spk, vec in zip(labels, vecs):
            row = []
            for pname in persons:
                used, cross = voiceid.route(vec, None, by_person[pname])
                sc = voiceid.score(vec, used)
                row.append({"spk": spk, "person": pname, "score": sc["score"],
                            "cross": cross, "top_ref": sc["top_ref"]})
            matrix.append(row)
        out[Path(ep).name] = {"dir": str(ep), "labels": labels,
                              "matrix": matrix}
    return out


def hist02(scores):
    """0.02 分 bin 直方图(bin 键 = bin 左端点)。"""
    h = {}
    for s in scores or []:
        lo = math.floor(s / 0.02) * 0.02
        h[f"{lo:.2f}"] = h.get(f"{lo:.2f}", 0) + 1
    return dict(sorted(h.items()))


def apply_truth(matrix_by_ep, truth):
    """truth 覆盖的 (spk, person) 对分同人/冒认;未覆盖的 SPK 不进分布。"""
    genuine, impostor = [], []
    for ep, epd in matrix_by_ep.items():
        tmap = (truth or {}).get(ep) or {}
        for row in epd["matrix"]:
            for cell in row:
                if cell["spk"] not in tmap:
                    continue
                if cell["person"] == tmap[cell["spk"]]:
                    genuine.append(cell["score"])
                else:
                    impostor.append(cell["score"])
    return genuine, impostor


def render_scores_md(matrix_by_ep, summary, truth, model_id, generated):
    lines = [f"# voiceid bench matrix", "",
             f"- generated: {generated}", f"- model: `{model_id}`",
             f"- episodes: {', '.join(matrix_by_ep) or '(无)'}", ""]
    for ep, epd in matrix_by_ep.items():
        persons = [c["person"] for c in epd["matrix"][0]] if epd["matrix"] else []
        tmap = (truth or {}).get(ep) or {}
        lines += [f"## {ep}", "",
                  "| spk | " + " | ".join(persons) + " |",
                  "|---" * (len(persons) + 1) + "|"]
        for row in epd["matrix"]:
            cells = []
            for c in row:
                mark = "*" if tmap.get(c["spk"]) == c["person"] else ""
                cells.append(f"{c['score']:.4f}{mark}")
            lines.append("| " + row[0]["spk"] + " | " + " | ".join(cells)
                         + " |")
        lines.append("")
    lines += ["## 汇总(仅 truth 覆盖的对)", "",
              f"- genuine: n={summary['genuine_n']} "
              f"mean={summary.get('genuine_mean')}",
              f"- impostor: n={summary['impostor_n']} "
              f"mean={summary.get('impostor_mean')}",
              f"- EER: {summary['eer']}",
              f"- genuine hist(0.02 bin): {summary['genuine_hist']}",
              f"- impostor hist(0.02 bin): {summary['impostor_hist']}", ""]
    return "\n".join(lines)


def cmd_matrix(args):
    truth = None
    if args.truth:
        truth = json.loads(Path(args.truth).read_text(encoding="utf-8"))
    entries = voiceid.VoiceArchive(args.emb).entries()
    matrix_by_ep = score_matrix(args.episode, entries)
    genuine, impostor = apply_truth(matrix_by_ep, truth)
    for ep, epd in matrix_by_ep.items():
        epd["truth"] = (truth or {}).get(ep) or {}
    summary = {
        "genuine": genuine, "impostor": impostor,
        "genuine_n": len(genuine), "impostor_n": len(impostor),
        "genuine_mean": (sum(genuine) / len(genuine)) if genuine else None,
        "impostor_mean": (sum(impostor) / len(impostor)) if impostor else None,
        "eer": voiceid.equal_error_rate(genuine, impostor),
        "genuine_hist": hist02(genuine), "impostor_hist": hist02(impostor),
    }
    generated = datetime.now().isoformat(timespec="seconds")
    data = {"model": MODEL_ID, "generated": generated,
            "episodes": matrix_by_ep, "summary": summary}

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "matrix.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "scores.md").write_text(
        render_scores_md(matrix_by_ep, summary, truth, MODEL_ID, generated),
        encoding="utf-8")
    print(f"[bench] matrix: {len(matrix_by_ep)} episode(s) -> {out_dir}")
    print(f"[bench] genuine n={summary['genuine_n']} "
          f"impostor n={summary['impostor_n']} eer={summary['eer']}")
    return data


# ---------- verify-alignment(F8) ----------

def verify_alignment(episode_dir):
    """spk_emb.json labels == diar.rttm SPK 首次出现序,且 npy 行/维一致。

    自行读文件逐项收集问题(不走 load_episode_spk 的硬拦,断言工具要能
    把"哪里没对齐"全列出来);返回 (ok, problems[])。缺件 → 直接列进 problems。
    """
    d = Path(episode_dir)
    npy, js, rttm = d / "spk_emb.npy", d / "spk_emb.json", d / "diar.rttm"
    problems = []
    for name, p in (("spk_emb.npy", npy), ("spk_emb.json", js),
                    ("diar.rttm", rttm)):
        if not p.exists():
            problems.append(f"缺 {name}:{p}")
    if problems:
        return False, problems

    meta = json.loads(js.read_text(encoding="utf-8"))
    arr = np.load(npy)
    labels = list(meta.get("labels") or [])
    order = rttm_spk_order(rttm)
    if order != labels:
        problems.append(f"labels 序 {labels} != rttm 首次出现序 {order}")
    if arr.shape[0] != len(labels):
        problems.append(f"npy 行数 {arr.shape[0]} != labels 数 {len(labels)}")
    if arr.ndim == 2 and meta.get("dim") not in (None, int(arr.shape[1])):
        problems.append(f"npy 维度 {arr.shape[1]} != json dim {meta.get('dim')}")
    return (not problems), problems


def cmd_verify(args):
    ok, problems = verify_alignment(args.episode)
    if ok:
        print(f"[bench] verify-alignment OK: {args.episode}")
        return 0
    print(f"[bench] verify-alignment FAIL: {args.episode}")
    for p in problems:
        print(f"  - {p}")
    raise SystemExit(1)


# ---------- recompute-consistency(F2 解除验收) ----------

def recompute_consistency(entries, extract_fn, sample_n=5, seed=0):
    """抽样重抽比对:extract_fn(ref_path) 得新向量,与缓存 vec 算 cos。

    抽样随机可复现(random.Random(seed).sample);返回 (report, failed),
    report 每条 {ref_path, person, lang, cos, ok},ok = cos>=RECOMPUTE_MIN_COS。
    """
    cands = [e for e in entries or []
             if e.get("vec") and e.get("ref_path")]
    picks = (cands if len(cands) <= sample_n
             else random.Random(seed).sample(cands, sample_n))
    report = []
    for e in picks:
        new_vec = extract_fn(e["ref_path"])
        c = voiceid.cosine(e["vec"], new_vec)
        report.append({"ref_path": e["ref_path"], "person": e.get("person"),
                       "lang": e.get("lang"), "cos": c,
                       "ok": c >= RECOMPUTE_MIN_COS})
    return report, [r for r in report if not r["ok"]]


def cmd_recompute(args):
    entries = voiceid.VoiceArchive(args.emb).entries()
    report, failed = recompute_consistency(
        entries, lambda p: voiceid_extract.extract(p),
        sample_n=args.sample, seed=args.seed)
    data = {"model": MODEL_ID, "min_cos": RECOMPUTE_MIN_COS,
            "sample": args.sample, "seed": args.seed,
            "n_checked": len(report), "n_failed": len(failed),
            "report": report}
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recompute_consistency.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    for r in report:
        print(f"[bench] {r['ref_path']} ({r['person']}/{r['lang']}) "
              f"cos={r['cos']:.6f} {'OK' if r['ok'] else 'FAIL'}")
    print(f"[bench] recompute-consistency: {len(report)} checked, "
          f"{len(failed)} failed -> {out_dir / 'recompute_consistency.json'}")
    if failed:
        raise SystemExit(1)
    return data


# ---------- CLI ----------

def build_parser():
    ap = argparse.ArgumentParser(
        prog="voiceid_bench.py",
        description="声纹验证实验 bench(票04;判据见 docs/research/04 §6)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    pm = sub.add_parser("matrix", help="episode × 人物 SPK×person 分数矩阵"
                                       "(同人/冒认分布、EER、0.02 bin 直方图)")
    pm.add_argument("--episode", action="append", required=True,
                    help="episode 目录(含 spk_emb.npy/json),可多次传")
    pm.add_argument("--emb", default=str(DEFAULT_EMB),
                    help="persons_emb.json 路径")
    pm.add_argument("--truth", default=None,
                    help='ground truth JSON:{"<episode目录名>":'
                         ' {"SPK_00": "人物名"}};缺省只出矩阵')
    pm.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    pm.set_defaults(func=cmd_matrix)

    pv = sub.add_parser("verify-alignment",
                        help="核对 spk_emb.npy 行序=rttm SPK 标签首次出现序"
                             "(F8 对齐断言)")
    pv.add_argument("episode", help="episode 目录")
    pv.set_defaults(func=cmd_verify)

    pc = sub.add_parser("recompute-consistency",
                        help="抽样重抽样本 embedding 与缓存向量比对,"
                             "断言 cos>=0.999(F2 解除验收;GPU)")
    pc.add_argument("--emb", default=str(DEFAULT_EMB),
                    help="persons_emb.json 路径")
    pc.add_argument("--sample", type=int, default=5, help="抽样条数")
    pc.add_argument("--seed", type=int, default=0, help="抽样随机种子")
    pc.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    pc.set_defaults(func=cmd_recompute)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    main()
