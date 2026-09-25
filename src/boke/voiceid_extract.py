# -*- coding: utf-8 -*-
"""声纹样本 embedding 抽取器(主 venv 侧封装;GPU 侧跑在 .venv-diar,票04)。

样本直抽语义(F2 解除裁定 / D2):档案向量一律由本抽取器从【存储样本片段】
(persons.json refs[] 指向的 wav,≤30s 切片)直接抽取,与整期分人质心
(work/<id>/spk_emb.npy)无关——bench 的 --recompute-consistency 与归档钩子
(票06)都以此为唯一向量来源。

隔离 venv 子进程模式(同 diarize.py):.venv-diar 内有 pyannote 4.0.7 +
torch 2.8 + CUDA,主 venv 不装这套;懒加载(用到才解析路径/起进程)、
带超时、产物落临时目录再读回。runner 协议见 work/tools/emb_extract.py:
    <venv-python> emb_extract.py <out_npy> <out_json> <audio1> [audio2 ...]
一次子进程批量抽多条(模型只加载一次);npy 行序 = 输入路径序,json 回带
paths/dim/model,读回时逐项校验。
"""
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
MODEL_ID = "pyannote/wespeaker-voxceleb-resnet34-LM"  # community-1 内置同源
                                                      # (与 emb_extract.py 保持
                                                      # 一致;跨 venv 不 import)
EMB_DIM = 256
DEFAULT_TIMEOUT_S = 600       # 整段 whole-window 一条音频秒级;600s 已极宽余


def venv_python() -> Path:
    return ROOT / ".venv-diar" / "Scripts" / "python.exe"


def runner_path() -> Path:
    return ROOT / "work" / "tools" / "emb_extract.py"


def _fail(msg: str):
    raise RuntimeError(f"[voiceid-extract] {msg}")


def _run_extractor(paths, timeout):
    """跑一次 .venv-diar 子进程抽全部路径 → (n×256 np 数组, meta dict)。"""
    venv, runner = venv_python(), runner_path()
    if not venv.exists():
        _fail(f"隔离 venv 不存在:{venv}(样本向量抽取需要 .venv-diar)")
    if not runner.exists():
        _fail(f"runner 不存在:{runner}")

    with tempfile.TemporaryDirectory(prefix="emb_extract_") as td:
        out_npy = str(Path(td) / "out.npy")
        out_json = str(Path(td) / "out.json")
        cmd = [str(venv), str(runner), out_npy, out_json] + [str(p) for p in paths]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=timeout)
        except subprocess.TimeoutExpired:
            _fail(f"embedding 抽取超时({timeout}s),paths={list(paths)[:3]}...")
        if r.returncode != 0:
            out = ((r.stdout or "") + (r.stderr or ""))[-800:]
            _fail(f"隔离 venv embedding 抽取失败(rc={r.returncode}):{out}")
        npy_p, js_p = Path(out_npy), Path(out_json)
        if not (npy_p.exists() and js_p.exists()):
            out = ((r.stdout or "") + (r.stderr or ""))[-800:]
            _fail(f"子进程未产出 npy/json:{out}")

        arr = np.load(npy_p)
        meta = json.loads(js_p.read_text(encoding="utf-8"))
        if meta.get("paths") != [str(p) for p in paths]:
            _fail(f"meta paths 与输入不一致:{meta.get('paths')}")
        if arr.shape[0] != len(paths):
            _fail(f"npy 行数 {arr.shape[0]} != 路径数 {len(paths)}")
        return arr, meta


def extract_many(paths, timeout=None) -> list:
    """多条音频 → [[float]*256, ...](行序 = 输入序;一次子进程批量抽)。"""
    paths = [str(p) for p in paths]
    if not paths:
        return []
    arr, meta = _run_extractor(paths, timeout or DEFAULT_TIMEOUT_S)
    return [row.tolist() for row in arr]


def extract(path, timeout=None) -> list:
    """单条音频 → 256 维向量(list[float],可直接入 persons_emb.json 的 vec)。"""
    vecs = extract_many([path], timeout)
    if len(vecs[0]) != EMB_DIM:
        _fail(f"向量维度 {len(vecs[0])} != {EMB_DIM}(model={MODEL_ID})")
    return vecs[0]
