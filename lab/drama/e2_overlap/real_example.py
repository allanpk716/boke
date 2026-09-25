#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""real_example.py — 票06 R1 补录：ECAPA 声纹门真实例（4 个 solo 源段互算），结果落盘。

背景（评审 FAIL 项 1）：此前 voice_gate.py 的真实例只 print 不落盘，且汇报数字
"同源0.87/跨源0.10" 经核实与纯逻辑自测（随机向量，seed 固定）数值相同——本脚本
重跑真实例并把每个试次与汇总写入 results/e2/voice_gate_realexample.json，作为
真实例口径的权威记录。

run in .venv-lab（离线，模型已在 HF 缓存）:
    ../../../.venv-lab/Scripts/python e2_overlap/real_example.py

设计（票06 R1 指定口径）：
  - 输入 = manifest 中 4 条 bili/zh solo_candidate=true 段（伪重叠原料）。
  - 每段取两个不重叠 10s 窗（0s 与 duration/2-5s），同源试次=同段两窗互算（4 条）；
    异源试次=不同段两两（C(4,2)=6，各取 0s 窗）。
  - 阈值=THRESHOLD_PLACEHOLDER 0.30（占位值，定标前不进结论）。
  - F10：n_same/n_cross 为分母；miss_count/false_pass_count 带分母比率一并落盘。
复用 voice_gate 的 embed_file/gate_decision，不重复实现门逻辑。
"""
from __future__ import annotations

import os
import statistics
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")            # 必须在 import torch/speechbrain 之前
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

try:  # Windows 控制台默认 cp936，统一 UTF-8 防乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = Path(__file__).resolve().parent
DRAMA = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(DRAMA))  # common.wavio

import json  # noqa: E402

import voice_gate as vg  # noqa: E402
import yaml  # noqa: E402

OUT_JSON = DRAMA / "results" / "e2" / "voice_gate_realexample.json"
WINDOW_S = 10.0


def pick_solo_segments() -> list[dict]:
    """manifest 中 4 条 solo 原料（bili/zh, solo_candidate=true），按 clip_id 排序保证可复现。"""
    manifest = yaml.safe_load((DRAMA / "clips_manifest.yaml").read_text(encoding="utf-8"))
    solo = [c for c in manifest["clips"]
            if c.get("source") == "bili" and c.get("language") == "zh"
            and c.get("solo_candidate") is True]
    solo.sort(key=lambda c: c["clip_id"])
    if len(solo) != 4:
        raise SystemExit(f"期望 4 条 solo 原料，manifest 实得 {len(solo)}，口径与票06 R1 不符")
    for c in solo:
        p = DRAMA / c["path_16k"]
        if not p.is_file():
            raise SystemExit(f"16k 副档缺失: {p}")
    return solo


def main() -> int:
    solo = pick_solo_segments()
    print(f"[ok] 4 条 solo 原料: {[c['clip_id'] for c in solo]}")

    from speechbrain.inference.speaker import EncoderClassifier  # noqa: E402
    from speechbrain.utils.fetching import LocalStrategy  # noqa: E402
    # Windows 普通权限建符号链接会 WinError 1314（speechbrain 默认 SYMLINK 策略）→ 用 COPY
    enc = EncoderClassifier.from_hparams(source="speechbrain/spkrec-ecapa-voxceleb",
                                         savedir=str(vg.SAVEDIR), run_opts={"device": "cpu"},
                                         local_strategy=LocalStrategy.COPY)

    # 每段两窗：0s 与 duration/2-5s（30s 段→0/10s，60s 段→0/25s；两窗不重叠）
    for c in solo:
        c["_w2"] = c["duration"] / 2 - WINDOW_S / 2

    trials: list[dict] = []

    def add_trial(cid1: str, w1: float, cid2: str, w2: float, kind: str) -> None:
        p1 = DRAMA / next(c for c in solo if c["clip_id"] == cid1)["path_16k"]
        p2 = DRAMA / next(c for c in solo if c["clip_id"] == cid2)["path_16k"]
        e1 = vg.embed_file(enc, p1, w1, dur_s=WINDOW_S)
        e2 = vg.embed_file(enc, p2, w2, dur_s=WINDOW_S)
        assert len(e1) == len(e2) == vg.EMB_DIM, f"ECAPA 输出维度异常: {len(e1)}"
        d = vg.gate_decision(e1, e2, threshold=vg.THRESHOLD_PLACEHOLDER)
        trials.append({"pair": f"{cid1}@{w1:g}s vs {cid2}@{w2:g}s",
                       "kind": kind, "cosine": d["cosine"], "decision": d["decision"]})
        print(f"  [{kind:5s}] {trials[-1]['pair']}  cosine={d['cosine']:.4f} → {d['decision']}")

    for c in solo:  # 同源：同段两窗
        add_trial(c["clip_id"], 0.0, c["clip_id"], c["_w2"], "same")
    for i in range(len(solo)):  # 异源：不同段两两（各取 0s 窗）
        for j in range(i + 1, len(solo)):
            add_trial(solo[i]["clip_id"], 0.0, solo[j]["clip_id"], 0.0, "cross")

    same = [t["cosine"] for t in trials if t["kind"] == "same"]
    cross = [t["cosine"] for t in trials if t["kind"] == "cross"]
    th = vg.THRESHOLD_PLACEHOLDER
    miss = [t for t in trials if t["kind"] == "same" and t["decision"] == "block"]
    false_pass = [t for t in trials if t["kind"] == "cross" and t["decision"] == "pass"]
    summary = {
        "n_same": len(same), "n_cross": len(cross),
        "same_min": min(same), "same_median": statistics.median(same),
        "cross_max": max(cross), "cross_median": statistics.median(cross),
        "threshold": th,
        "decisions": {
            "same_pass": len(same) - len(miss), "same_block": len(miss),
            "cross_pass": len(false_pass), "cross_block": len(cross) - len(false_pass),
            "pass_total": sum(1 for t in trials if t["decision"] == "pass"),
            "block_total": sum(1 for t in trials if t["decision"] == "block"),
        },
        "miss_count": len(miss),                      # 分母 n_same
        "false_pass_count": len(false_pass),          # 分母 n_cross
        "false_pass_pairs": [t["pair"] for t in false_pass],
        "miss_rate": (len(miss) / len(same)) if same else None,
        "false_pass_rate": (len(false_pass) / len(cross)) if cross else None,
    }
    doc = {
        "schema": "voice_gate_realexample/1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model": "speechbrain/spkrec-ecapa-voxceleb (ECAPA-TDNN, 192 维, HF 缓存离线加载)",
        "window_s": WINDOW_S,
        "threshold_note": "0.30 为占位值（调研04 自校准法定标前），判定只作管线冒烟，不进结论",
        "inputs": [{"clip_id": c["clip_id"], "path_16k": c["path_16k"],
                    "duration_s": c["duration"], "windows_s": [0.0, c["_w2"]]} for c in solo],
        "trials": trials,
        "summary": summary,
        "note": "F10：miss 分母=n_same，false_pass 分母=n_cross；同源=同段不同时间窗，异源=不同段两两",
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[real] 同源 n={len(same)}: min={summary['same_min']:.4f} median={summary['same_median']:.4f}")
    print(f"[real] 异源 n={len(cross)}: max={summary['cross_max']:.4f} median={summary['cross_median']:.4f}")
    print(f"[real] 阈值 {th}（占位）: miss={len(miss)}/{len(same)} false_pass={len(false_pass)}/{len(cross)}"
          f" {summary['false_pass_pairs'] or ''}")
    print(f"[real] 已落盘 {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
