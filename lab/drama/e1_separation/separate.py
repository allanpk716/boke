# -*- coding: utf-8 -*-
"""E1' 分离客观横评·首轮 —— mel_band_roformer 分离 + 波形相减产背景A + 模型 instrumental 作背景B。

run in .venv-lab:
    ../../../.venv-lab/Scripts/python separate.py            # 全 30 段（幂等：已产出的段跳过）
    ../../../.venv-lab/Scripts/python separate.py --force    # 全部重跑
    ../../../.venv-lab/Scripts/python separate.py --clips blender_sintel_g0002,blender_sintel_g0003

输入（票02 已就位，gitignored）:
    media/clips/<clip_id>_44k1.wav   44.1k 立体声切片（manifest clips_manifest.yaml, status=cut）
    media/models/melband_roformer_instvox_duality_v2.ckpt + config_melbandroformer_instvoc_duality.yaml

产物（gitignored, media/separated/）:
    stems/<basename>_(Vocals)_*.wav          模型人声轨（audio-separator 原名）
    stems/<basename>_(Instrumental)_*.wav    模型伴奏轨 = 背景B
    <clip_id>_bgA_subtract.wav               背景A = 原混 − 人声（逐样本相减，float32 WAV，纯 python）
    separation_index.json                    段 → 三轨文件路径的索引（objective.py 的输入）

实现要点（影响数字口径，如实记录）:
  * normalization_threshold=1.0：audio-separator 默认 0.9 会把峰值>0.9 的输入整体压低且**不回补**，
    相减出的背景A会残留 (1−k)·原混 的对白成分；1.0 对 int16 源（峰值≤1.0）是恒等变换，
    保证三轨同一幅度尺度，相减干净。
  * 背景A以 FLOAT WAV 落盘：原混−人声可能超 ±1.0（模型过冲），PCM_16/24 写盘会在 ±1.0 削波破坏恒等性。
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
import yaml

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
DRAMA = HERE.parent
MEDIA = DRAMA / "media"
MANIFEST = DRAMA / "clips_manifest.yaml"
MODELS_DIR = MEDIA / "models"
SEP_DIR = MEDIA / "separated"
STEMS_DIR = SEP_DIR / "stems"
MODEL_NAME = "melband_roformer_instvox_duality_v2.ckpt"
MODEL_CONFIG = "config_melbandroformer_instvoc_duality.yaml"
INDEX_PATH = SEP_DIR / "separation_index.json"
RATE = 44100


def log(msg: str) -> None:
    print(f"[separate {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _ffmpeg_run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {' '.join(cmd)}\n{proc.stderr.strip()}")


def load_manifest() -> list[dict]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    clips = [c for c in data["clips"] if c.get("status") == "cut"]
    if not clips:
        raise RuntimeError("manifest 无 status=cut 的切片，先跑 tools/cut_clips.py")
    return clips


def select_clips(only: list[str] | None = None) -> list[dict]:
    clips = load_manifest()
    if only:
        known = {c["clip_id"] for c in clips}
        missing = [x for x in only if x not in known]
        if missing:
            raise RuntimeError(f"--clips 里这些 ID 不在 manifest 或未切片: {missing}")
        clips = [c for c in clips if c["clip_id"] in set(only)]
    return clips


def load_index() -> dict:
    if INDEX_PATH.exists():
        return json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    return {"model": MODEL_NAME, "normalization_threshold": 1.0, "clips": {}}


def products_exist(entry: dict) -> bool:
    return all(k in entry and Path(entry[k]).exists() for k in ("orig", "vocals", "bgB", "bgA"))


def precheck_model(wait_seconds: int = 0, tries: int = 1) -> None:
    """模型文件在场检查（缺了报清单；wait_seconds>0 时按票面协议等 60s 重试）。"""
    need = [MODELS_DIR / MODEL_NAME, MODELS_DIR / MODEL_CONFIG, MODELS_DIR / "download_checks.json"]
    for i in range(1, tries + 1):
        missing = [p.name for p in need if not p.exists()]
        if not missing:
            if i > 1:
                log("模型文件已就位")
            return
        log(f"模型缺失 {missing}（第 {i}/{tries} 次检查；下载归协调者，本脚本不联网）")
        if i < tries:
            time.sleep(wait_seconds)
    raise RuntimeError(f"media/models/ 缺文件 {missing} —— 等协调者预热缓存，本脚本禁止联网")


def separate_one(sep, clip: dict, force: bool, index: dict) -> str:
    """分离一段并产背景A。返回 'done' | 'skipped' | 'failed: <原因>'。"""
    cid = clip["clip_id"]
    entry = index["clips"].get(cid, {})
    if not force and products_exist(entry):
        return "skipped"
    clip_path = DRAMA / clip["path"]
    if not clip_path.exists():
        return f"failed: 切片缺失 {clip_path}"
    t0 = time.perf_counter()
    outs = sep.separate(str(clip_path))
    # 0.47.0 返回的可能是裸文件名（文件实际写在 output_dir），统一解析成绝对路径
    outs = [p if Path(p).is_absolute() else str(STEMS_DIR / Path(p).name) for p in outs]
    voc = [p for p in outs if "vocal" in Path(p).stem.lower()]
    ins = [p for p in outs if "instrumental" in Path(p).stem.lower()]
    if len(voc) != 1 or len(ins) != 1:
        return f"failed: 输出 stem 识别失败 vocals={voc} instrumental={ins}（全部输出={outs}）"
    voc_path, ins_path = voc[0], ins[0]

    orig, sr_o = sf.read(clip_path, dtype="float32", always_2d=True)
    vs, sr_v = sf.read(voc_path, dtype="float32", always_2d=True)
    if sr_o != RATE or sr_v != RATE:
        return f"failed: 采样率不符 orig={sr_o} vocals={sr_v}"
    if orig.shape[1] != 2 or vs.shape[1] != 2:
        return f"failed: 声道数不符 orig={orig.shape} vocals={vs.shape}"
    n = min(len(orig), len(vs))
    if len(orig) != len(vs):
        log(f"  长度差: orig={len(orig)} vocals={len(vs)}，按 min={n} 对齐")
    bga = (orig[:n] - vs[:n]).astype(np.float32)
    bga_path = SEP_DIR / f"{cid}_bgA_subtract.wav"
    sf.write(bga_path, bga, RATE, subtype="FLOAT")

    index["clips"][cid] = {
        "orig": str(clip_path),
        "vocals": str(voc_path),
        "bgB": str(ins_path),
        "bgA": str(bga_path),
        "start": clip["start"],
        "end": clip["end"],
        "layer": clip["layer"],
        "samples": int(n),
        "sample_rate": RATE,
        "length_trimmed": bool(len(orig) != len(vs)),
        "vocals_peak": float(np.abs(vs).max()),
        "bgA_peak": float(np.abs(bga).max()),
        "sec": round(time.perf_counter() - t0, 1),
    }
    log(f"  {cid}: {index['clips'][cid]['sec']}s, vocals_peak={index['clips'][cid]['vocals_peak']:.3f}")
    return "done"


def run(only: list[str] | None = None, force: bool = False, wait_model_seconds: int = 0) -> dict:
    clips = select_clips(only)
    SEP_DIR.mkdir(parents=True, exist_ok=True)
    STEMS_DIR.mkdir(parents=True, exist_ok=True)
    index = load_index()
    index["model"] = MODEL_NAME
    index["normalization_threshold"] = 1.0

    todo = [c for c in clips if force or not products_exist(index["clips"].get(c["clip_id"], {}))]
    log(f"共 {len(clips)} 段，待分离 {len(todo)} 段（force={force}）")
    if todo:
        precheck_model(wait_seconds=wait_model_seconds, tries=5 if wait_model_seconds else 1)
        from audio_separator.separator import Separator

        sep = Separator(
            model_file_dir=str(MODELS_DIR),
            output_dir=str(STEMS_DIR),
            output_format="WAV",
            normalization_threshold=1.0,  # 见文件头说明：保幅度尺度，相减才干净
            log_level=20,
        )
        sep.load_model(MODEL_NAME)
        log(f"模型已加载: {MODEL_NAME}")

    stats = {"done": 0, "skipped": 0, "failed": 0}
    for i, c in enumerate(clips, 1):
        res = separate_one(sep if todo else None, c, force, index)
        stats[res.split(":")[0]] = stats.get(res.split(":")[0], 0) + 1
        log(f"({i}/{len(clips)}) {c['clip_id']}: {res}")
        INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8")

    log(f"分离完成: {stats}")
    return {"stats": stats, "index": index}


def main() -> int:
    ap = argparse.ArgumentParser(description="E1' 首轮分离（mel roformer + 相减背景A）")
    ap.add_argument("--clips", default="", help="逗号分隔 clip_id 子集；缺省=全部")
    ap.add_argument("--force", action="store_true", help="已有产物也重跑")
    ap.add_argument("--wait-model", action="store_true", help="模型缺失时等 60s 重试最多 5 次（下载归协调者）")
    a = ap.parse_args()
    only = [x.strip() for x in a.clips.split(",") if x.strip()]
    out = run(only=only or None, force=a.force, wait_model_seconds=60 if a.wait_model else 0)
    return 1 if out["stats"]["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
