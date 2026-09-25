#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tse_smoke.py — 票06 E2 TSE 冒烟·就绪检查 + 调用骨架（今晚不装包、不联网）。

run in .venv-lab:
    ../../../.venv-lab/Scripts/python tse_smoke.py        # 就绪检查 → 打印结论，缺依赖 exit 0（不失败）

两个候选（票06：至少跑一个，另一个视时间；今晚都不装，交付"就绪检查+调用骨架"）：
  1. NeMo `tse_sortformer_4spk-v1`（工程就绪路线）
  2. X-TF-GridNet（研究代码路线）

输入输出约定（两候选统一，写死给后续 E2 实现）：
  输入  = 构造集 mix（media/constructed/*_16k.wav，16k mono s16）→ float32 /32768 → (1, T)
  输出  = 每说话人一路估计波形（nspk, T）
  配对  = 估计轨 vs 真值轨（manifest constructed 条目 gt_a/gt_b 字段）按 max SI-SDR 穷举排列取优
  指标  = SI-SDR（有真值）+ 提错人率（TSE 输出过 voice_gate.py 声纹门，F10 分母=构造集条数 18）

冒烟判据（后续真跑时用）：对 18 条构造集出 SI-SDR 分层表（SNR 0/±3 × gender unknown），
不设 PASS/FAIL 闸门（E2 正式判据在 tse_eval.md，含漏检率一票否决）。
"""
from __future__ import annotations

import importlib.util
import sys

try:  # Windows 控制台默认 cp936，统一 UTF-8 防乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def probe(spec_name: str) -> bool:
    """只查包是否存在（find_spec 不执行包代码，轻量安全）。"""
    try:
        return importlib.util.find_spec(spec_name) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def check_nemo() -> tuple[bool, list[str]]:
    ok = probe("nemo")
    notes = [
        "装法（.venv-lab）: pip install \"nemo_toolkit[asr]\"",
        "依赖与磁盘代价（约数，装后核实）: 本 venv 已有 torch 2.14+cu126 基础上，NeMo 连带"
        " hydra-core/omegaconf/sentencepiece 等新增约 1.5~3GB；模型权重 tse_sortformer_4spk-v1 "
        "需联网从 NGC/HF 下载（数百 MB 量级）",
        "注意: NeMo 对 torch 版本敏感，装前先核对 NeMo↔torch 2.14 兼容矩阵；权重下载属联网操作，"
        "须由协调者执行",
    ]
    return ok, notes


def check_xtfgridnet() -> tuple[bool, list[str]]:
    ok = any(probe(n) for n in ("x_tf_gridnet", "xtfgridnet", "tf_gridnet"))
    notes = [
        "装法: 官方研究仓库 clone 到 lab/drama/e2_overlap/third_party/（或独立目录）→ 按其 "
        "requirements 安装（torch/torchaudio 已备）→ 发布权重或复现训练",
        "依赖与磁盘代价: 仓库本体小（代码为主）；权重体积未核实——装前先查仓库 README/发布页，"
        "磁盘代价以发布为准",
        "注意: 研究代码无 pip 包保证，import 名以仓库实际为准（本检查探测了 x_tf_gridnet/"
        "xtfgridnet/tf_gridnet 三个常见命名，均未命中才判缺）",
    ]
    return ok, notes


def check_torch() -> tuple[bool, list[str]]:
    ok = probe("torch")
    return ok, ["两候选共同前置：torch（.venv-lab 应已有 2.14.0+cu126）"]


# ---------------------------------------------------------------- 调用骨架（伪代码，两候选同约定）

SKELETON = '''
调用骨架（后续 E2 真跑时按此实现，两候选同约定）:

    # 1) 取构造集（票06 build_pairs.py 产物）
    manifest = yaml.safe_load(clips_manifest.yaml)
    pairs = [c for c in manifest["clips"] if c["source"] == "constructed"]   # 18 条

    # 2) 逐条：mix → 模型 → 每说话人估计
    mix = read_wav_16k_mono(pair["path_16k"])            # s16 → float32 /32768
    est = tse_model(mix)                                  # → (nspk, T)，nspk=2

    # 3) 估计轨 ↔ 真值轨配对：穷举 2 种排列，SI-SDR 最大者胜
    gt = [read_wav_16k_mono(pair["gt_a"]), read_wav_16k_mono(pair["gt_b"])]
    perm = max(itertools.permutations(range(2)), key=lambda p: sum(si_sdr(est[i], gt[p[i]]) for i in p))

    # 4) 声纹门（提错人拦截，复用 voice_gate.py）
    enroll = embed(pair["gt_a"]窗)                        # ECAPA
    for est_i in est:
        d = gate_decision(enroll, embed(est_i))           # pass=放行 / block=拦截
    # F10: 漏检率 = 漏检数 / 同源试次数；误放行率 = 误放行数 / 异源试次数（无分母不进结论）

    # 5) 落盘 results/e2/tse_smoke.json + tse_eval.md（SI-SDR 按 SNR 档分层）
'''

NEMO_CALL = '''
NeMo tse_sortformer_4spk-v1 调用要点（装好后）:
    import torch, nemo.collections.asr as nemo_asr
    model = nemo_asr.models.EncDecSpeakerSegModel.from_pretrained("nvidia/tse_sortformer_4spk-v1")
    # 输入 (B, T) float32 16k mono；输出每说话人 mask/分离波形（以模型 forward 签名为准，装后核对）
    est = model(input_signal=mix_tensor, length=torch.tensor([T]))
'''

GRIDNET_CALL = '''
X-TF-GridNet 调用要点（clone 后）:
    model = XTFGridNet.from_pretrained(ckpt)   # 接口以仓库为准
    est = model(mix)                            # (nspk, T)，窗口/分帧按仓库配置
'''


def main() -> int:
    print("=== E2 TSE 冒烟·就绪检查（票06；不装包、不联网） ===")
    checks = [("torch（共同前置）", check_torch()),
              ("NeMo tse_sortformer_4spk-v1", check_nemo()),
              ("X-TF-GridNet（研究代码）", check_xtfgridnet())]
    torch_ok = False
    runnable_tse = []
    for name, (ok, notes) in checks:
        if ok:
            print(f"[有] {name}：import 可用")
        else:
            print(f"[缺] {name}：未安装")
        if name.startswith("torch"):
            torch_ok = ok
        elif ok:
            runnable_tse.append(name)
        for n in notes:
            print(f"     {n}")
        print()

    print("冒烟待跑所需安装（按票06 二选一，至少一个）：")
    if "NeMo tse_sortformer_4spk-v1" in runnable_tse:
        print("  → 方案A NeMo 已就绪，可直接进骨架")
    else:
        print("  → 方案A NeMo: pip install \"nemo_toolkit[asr]\"（约 1.5~3GB + 联网下载权重，"
              "由协调者执行）")
    print("  → 方案B X-TF-GridNet: clone 研究仓库 + 权重（磁盘代价装前核实，需联网）")
    print()
    print("[骨架] 输入输出约定与调用伪代码：")
    print(SKELETON)
    if "NeMo tse_sortformer_4spk-v1" in runnable_tse:
        print(NEMO_CALL)
    if "X-TF-GridNet（研究代码）" in runnable_tse:
        print(GRIDNET_CALL)

    if runnable_tse:
        print(f"[结论] 就绪检查：TSE 候选可跑 = {', '.join(runnable_tse)}"
              f"（torch {'就绪' if torch_ok else '缺失'}）→ 按上面骨架冒烟")
    else:
        print("[结论] 就绪检查：两个 TSE 候选当前均不可跑（缺依赖与权重，均需联网安装——"
              "今晚禁止联网，冒烟待跑）。声纹门 voice_gate.py 与构造集 build_pairs.py 已就绪，"
              "E2 数据面已备好。")
    print("（exit 0：缺依赖不算失败，票06 规格）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
