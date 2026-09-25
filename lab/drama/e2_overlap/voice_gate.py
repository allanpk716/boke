#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voice_gate.py — 票06 E2 声纹门：speechbrain ECAPA 声纹 + cosine 阈值拦截/放行。

run in .venv-lab:
    ../../../.venv-lab/Scripts/python voice_gate.py               # 纯逻辑自测 +（HF 缓存命中时）ECAPA 真实例
    ../../../.venv-lab/Scripts/python voice_gate.py --selftest    # 只跑纯逻辑自测（无需 torch/模型，任何 python 可跑）

纯逻辑（本文件自足，纯标准库）：
  - cos_sim(a, b)：embedding cosine（嵌入用 list[float]，不依赖 numpy/torch）。
  - gate_decision(enroll_emb, test_emb, threshold)：cosine ≥ 阈值 → pass（放行，同一说话人）；
    < 阈值 → block（拦截，疑似提错人）。语义对齐 E2 用途：TSE 输出对目标说话人声纹档案做校验，
    拦下"提错人"的输出。阈值缺省 THRESHOLD_PLACEHOLDER=0.30 是占位值——调研04 的自校准法定标前
    不作为结论依据，CLI --threshold 可覆盖。
  - gate_summary(decisions)：F10 纪律（漏检率/误拦率必须带分母）——输出 n_trials/n_pass/n_block/
    n_miss/n_false_block 与带分母的比率；无分母的比率不进结论。
真实例（离线，视 HF 缓存而定）：
  - 前置：~/.cache/huggingface/hub/models--speechbrain--spkrec-ecapa-voxceleb 存在（协调者已预热）。
    命中 → HF_HUB_OFFLINE=1 只许走本地缓存 → EncoderClassifier.from_hparams(
    source="speechbrain/spkrec-ecapa-voxceleb", savedir=media/models/ecapa-voxceleb, cpu,
    local_strategy=COPY——Windows 普通权限无建链特权，默认 SYMLINK 会 WinError 1314)。
    **encode_batch 输入必须 torch.float32 张量 shape (1, N)、16k**（协调者实测：numpy 数组输入会
    报 AttributeError 'to'）。
  - 未命中 → 打印"模型未缓存,真实例待协调者预热后跑"降级退出（exit 0，不失败）。
"""
from __future__ import annotations

import argparse
import math
import os
import random
import sys
from pathlib import Path

try:  # Windows 控制台默认 cp936，统一 UTF-8 防乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
DRAMA = HERE.parent
sys.path.insert(0, str(DRAMA))  # 真实例要用 common.wavio 读 wav
HF_CACHE_MODEL = Path.home() / ".cache" / "huggingface" / "hub" / "models--speechbrain--spkrec-ecapa-voxceleb"
SAVEDIR = DRAMA / "media" / "models" / "ecapa-voxceleb"

THRESHOLD_PLACEHOLDER = 0.30  # 占位：调研04 自校准法定标前的经验缺省，不作为结论依据
EMB_DIM = 192                 # ECAPA-TDNN VoxCeleb 输出维度（真实例断言用）


# ---------------------------------------------------------------- 纯逻辑（无第三方依赖）

def cos_sim(a: list[float], b: list[float]) -> float:
    """embedding cosine 相似度；零向量报错（静音/空嵌入不该进门）。"""
    if len(a) != len(b) or not a:
        raise ValueError(f"embedding 维度不符或为空: {len(a)} vs {len(b)}")
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(x * x for x in b))
    if da == 0.0 or db == 0.0:
        raise ValueError("零向量无法计算 cosine（检查输入是否为静音）")
    return num / (da * db)


def gate_decision(enroll_emb: list[float], test_emb: list[float],
                  threshold: float = THRESHOLD_PLACEHOLDER) -> dict:
    """声纹门判定：cosine ≥ threshold → pass（放行）；< threshold → block（拦截疑似提错人）。"""
    c = cos_sim(enroll_emb, test_emb)
    return {"cosine": round(c, 6), "threshold": threshold,
            "decision": "pass" if c >= threshold else "block"}


def gate_summary(decisions: list[dict]) -> dict:
    """汇总判定结果。F10：所有比率带分母 n_trials；label_same 缺失时漏检/误拦记 None（不算比率）。"""
    n = len(decisions)
    if n == 0:
        raise ValueError("空判定列表：F10 要求比率必须带非零分母")
    n_pass = sum(1 for d in decisions if d["decision"] == "pass")
    n_block = n - n_pass
    labeled = [d for d in decisions if "label_same" in d]
    n_miss = sum(1 for d in labeled if d["label_same"] and d["decision"] == "block")      # 同一说话人被拦=漏检
    n_false_block = sum(1 for d in labeled if not d["label_same"] and d["decision"] == "pass")  # 不同人被放行=误拦
    return {
        "n_trials": n, "n_pass": n_pass, "n_block": n_block,
        "n_labeled": len(labeled),
        "n_miss": n_miss if labeled else None,
        "n_false_block": n_false_block if labeled else None,
        "miss_rate": (n_miss / len([d for d in labeled if d["label_same"]])
                      if labeled and any(d["label_same"] for d in labeled) else None),
        "false_pass_rate": (n_false_block / len([d for d in labeled if not d["label_same"]])
                            if labeled and any(not d["label_same"] for d in labeled) else None),
        "note": "F10：miss_rate 分母=label_same 试次数，false_pass_rate 分母=label 不同试次数；"
                "无分母的比率不进结论",
    }


def selftest() -> int:
    """随机向量单元自测：同源簇 cosine 高、异源簇低、门判定与标签一致、F10 字段齐。纯标准库。"""
    rng = random.Random(f"voicegate|{EMB_DIM}")
    dim = EMB_DIM
    speakers = 4
    per_spk = 6
    noise = 0.35
    bases = [[rng.gauss(0.0, 1.0) for _ in range(dim)] for _ in range(speakers)]
    embs = {s: [[b + rng.gauss(0.0, noise) for b in base] for _ in range(per_spk)]
            for s, base in enumerate(bases)}

    same = [cos_sim(embs[s][i], embs[s][j]) for s in range(speakers)
            for i, j in [(0, 1), (0, 2), (1, 3)]]
    diff = [cos_sim(embs[s1][0], embs[s2][0]) for s1 in range(speakers) for s2 in range(s1 + 1, speakers)]
    print(f"[selftest] cosine 同源簇 n={len(same)}: min={min(same):.4f} mean={sum(same)/len(same):.4f}")
    print(f"[selftest] cosine 异源簇 n={len(diff)}: max={max(diff):.4f} mean={sum(diff)/len(diff):.4f}")
    assert min(same) > max(diff), "同源/异源 cosine 未分离，逻辑不可用"

    th = (min(same) + max(diff)) / 2  # 自测专用中点阈值（非 THRESHOLD_PLACEHOLDER）
    decisions = []
    for s in range(speakers):
        for i, j in [(0, 1), (0, 2), (1, 3)]:
            d = gate_decision(embs[s][i], embs[s][j], threshold=th)
            assert d["decision"] == "pass", f"同源被拦: {d}"
            decisions.append({**d, "label_same": True})
    for s1 in range(speakers):
        for s2 in range(s1 + 1, speakers):
            d = gate_decision(embs[s1][0], embs[s2][0], threshold=th)
            assert d["decision"] == "block", f"异源被放行: {d}"
            decisions.append({**d, "label_same": False})

    # 阈值边界语义：cosine == threshold → pass（≥ 放行）
    v = [1.0] + [0.0] * (dim - 1)
    assert gate_decision(v, v, threshold=1.0)["decision"] == "pass"
    assert gate_decision(v, v, threshold=1.0000001)["decision"] == "block"

    summ = gate_summary(decisions)
    print(f"[selftest] 汇总(F10): {summ}")
    assert summ["n_trials"] == len(decisions) > 0
    assert summ["n_miss"] == 0 and summ["n_false_block"] == 0
    assert summ["miss_rate"] == 0.0 and summ["false_pass_rate"] == 0.0
    print(f"[selftest] PASS：同/异源分离 + 门判定 + 阈值边界(≥放行) + F10 字段，共 {len(decisions)} 试次")
    return 0


# ---------------------------------------------------------------- 真实例（离线，视 HF 缓存）

def hf_cache_ready() -> bool:
    snap = HF_CACHE_MODEL / "snapshots"
    return snap.is_dir() and any(p.is_file() for p in snap.rglob("*") if p.is_file())


def _pick_gt_paths() -> list[Path]:
    """真实例输入：优先构造集 60s 真值轨（gtA/gtB 两"说话人"），没有则退回 manifest solo 16k 原段。"""
    import yaml
    manifest = yaml.safe_load((DRAMA / "clips_manifest.yaml").read_text(encoding="utf-8"))
    cons = [c for c in manifest["clips"] if c.get("source") == "constructed" and c.get("duration", 0) >= 60.0]
    if len(cons) >= 1:
        e = cons[0]
        return [DRAMA / e["gt_a"], DRAMA / e["gt_b"]]
    solo = [c for c in manifest["clips"]
            if c.get("source") == "bili" and c.get("language") == "zh" and c.get("solo_candidate") is True]
    return [DRAMA / c["path_16k"] for c in solo[:2]]


def embed_file(enc, path: Path, start_s: float, dur_s: float = 10.0) -> list[float]:
    """16k wav → 指定窗 → float32 torch 张量 (1, N) → ECAPA embedding（list[float]）。"""
    import torch
    from common import wavio
    raw, rate, ch = wavio.read_wav(path)
    if rate != 16000:
        raise ValueError(f"期望 16k，得到 {rate}: {path}")
    mono = wavio.to_mono(raw, ch)
    i0 = int(start_s * rate)
    seg = mono[i0:i0 + int(dur_s * rate)]
    if len(seg) < rate:  # 不足 1s 不进门（过短 embedding 不稳）
        raise ValueError(f"窗口过短: {path} @{start_s}s")
    t = torch.tensor([s / 32768.0 for s in seg], dtype=torch.float32).unsqueeze(0)  # (1,N) float32
    with torch.no_grad():
        e = enc.encode_batch(t)  # 协调者实测：numpy 输入会 AttributeError 'to'，必须 torch 张量
    return [float(x) for x in e.squeeze().tolist()]


def run_real_example(threshold: float = THRESHOLD_PLACEHOLDER) -> int:
    if not hf_cache_ready():
        print("[degrade] 模型未缓存,真实例待协调者预热后跑"
              f"（检查路径 {HF_CACHE_MODEL} 不存在或为空）；纯逻辑自测已过，exit 0")
        return 0
    print(f"[ok] HF 缓存命中 {HF_CACHE_MODEL.name} → 离线跑真实例（HF_HUB_OFFLINE=1，禁止联网）")
    os.environ.setdefault("HF_HUB_OFFLINE", "1")       # 必须在 import speechbrain/torch 之前
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    from speechbrain.inference.speaker import EncoderClassifier  # noqa: E402
    from speechbrain.utils.fetching import LocalStrategy  # noqa: E402
    # Windows 普通权限建符号链接会 WinError 1314（speechbrain 默认 SYMLINK 策略）→ 用 COPY
    enc = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb",
                                         savedir=str(SAVEDIR), run_opts={"device": "cpu"},
                                         local_strategy=LocalStrategy.COPY)

    gt_a, gt_b = _pick_gt_paths()
    print(f"[ok] 真实例输入（票06 构造集真值轨）: {gt_a.name} / {gt_b.name}")
    trials = [  # (enroll 来源, 窗, test 来源, 窗, label_same)
        (gt_a, 1.0, gt_a, 30.0, True),   # 同轨不同窗 = 同一说话人
        (gt_b, 1.0, gt_b, 30.0, True),
        (gt_a, 1.0, gt_b, 1.0, False),   # 跨轨 = 不同说话人（伪重叠两源）
        (gt_b, 15.0, gt_a, 20.0, False),
    ]
    decisions = []
    for p1, w1, p2, w2, lab in trials:
        e1 = embed_file(enc, p1, w1)
        e2 = embed_file(enc, p2, w2)
        assert len(e1) == len(e2) == EMB_DIM, f"ECAPA 输出维度异常: {len(e1)}"
        d = gate_decision(e1, e2, threshold=threshold)
        d["label_same"] = lab
        decisions.append(d)
        print(f"  {p1.name}@{w1}s vs {p2.name}@{w2}s  label={'同' if lab else '异'} "
              f"cosine={d['cosine']:.4f} → {d['decision']}")

    summ = gate_summary(decisions)
    print(f"[real] 汇总(F10): {summ}")
    print(f"[real] 注意：门限用的是 {threshold}（缺省 0.30 为占位值；调研04 自校准法定标前，"
          f"此结果只作管线冒烟，不进结论）")
    print("[real] 真实例跑通（离线，HF 缓存）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--selftest", action="store_true", help="只跑纯逻辑自测（无需 torch/模型）")
    ap.add_argument("--threshold", type=float, default=THRESHOLD_PLACEHOLDER,
                    help=f"门限（缺省 {THRESHOLD_PLACEHOLDER} 占位，待调研04 自校准法定标）")
    args = ap.parse_args()

    rc = selftest()
    if args.selftest:
        return rc
    return run_real_example(threshold=args.threshold)


if __name__ == "__main__":
    sys.exit(main())
