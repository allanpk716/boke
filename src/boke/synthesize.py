# -*- coding: utf-8 -*-
"""stage F: 逐句合成。引擎: cosyvoice3(克隆) / edge-tts(预设) / silence(mock)。

voices.yaml 决定 SPK → 音色;引擎选择: episode_map 里 type=clone 用 cosyvoice3,
type=preset 用 edge-tts(或 cosyvoice 预设)。T5 未就绪时 --tts-engine 强制覆盖。
产物: work/<id>/tts/NNNN.wav + manifest.json
"""
import asyncio
import json
import re
import subprocess
from pathlib import Path

from .common import parse_srt, run_ffmpeg

TTS_SR = 24000  # 统一输出采样率(与 CosyVoice3 一致)


def clean_text(text: str) -> str:
    """去掉 [SPK_xx] 前缀与多余空白。"""
    t = re.sub(r"^\[SPK_\d+\]\s*", "", text.strip())
    return re.sub(r"\s+", " ", t)


# ---------------- silence mock ----------------

def synth_silence(text: str, est_dur: float, out: Path):
    """静音占位:时长=预计窗口时长(冒烟只验证拼接时间轴)。"""
    run_ffmpeg(["-f", "lavfi", "-i", f"anullsrc=r={TTS_SR}:cl=mono",
                "-t", f"{max(0.5, est_dur):.3f}", "-c:a", "pcm_s16le", str(out)])


# ---------------- edge-tts ----------------

EDGE_VOICES = {
    "SPK_00": "zh-CN-YunxiNeural",    # 男,自然
    "SPK_01": "zh-CN-XiaoxiaoNeural",  # 女,自然
}
DEFAULT_VOICE = "zh-CN-YunxiNeural"


def synth_edge(text: str, out: Path, voice: str = None, retries=3):
    import edge_tts

    voice = voice or DEFAULT_VOICE
    last = None
    for i in range(retries):
        try:
            async def _one():
                c = edge_tts.Communicate(text, voice, rate="+8%")
                await c.save(str(out))
            asyncio.run(_one())
            if out.exists() and out.stat().st_size > 1000:
                return
            raise RuntimeError("edge-tts 输出为空")
        except Exception as e:
            last = e
            out.unlink(missing_ok=True)
            import time
            time.sleep(2 * (i + 1))
    raise RuntimeError(f"edge-tts 3 次失败: {last}")


# ---------------- cosyvoice3 克隆 ----------------

class CosyVoiceEngine:
    """CosyVoice3 零样本克隆。repo 路径与模型目录来自 voices.yaml global 段。"""

    def __init__(self, repo_dir: str, model_dir: str, device="cuda"):
        import sys
        repo = Path(repo_dir)
        for sub in (repo, repo / "third_party" / "Matcha-TTS"):
            s = str(sub)
            if s not in sys.path:
                sys.path.insert(0, s)
        import torch
        from cosyvoice.cli.cosyvoice import CosyVoice3  # noqa: F401

        self.torch = torch
        self.device = device if torch.cuda.is_available() else "cpu"
        self.model = CosyVoice3(model_dir, load_jit=False, load_trt=False,
                                load_vllm=False, device=self.device)
        self.sr = getattr(self.model, "sample_rate", TTS_SR)

    def zero_shot(self, text: str, prompt_text: str, ref_wav: Path, out: Path):
        import torchaudio
        ref, sr = torchaudio.load(str(ref_wav))
        if sr != 16000:
            ref = torchaudio.functional.resample(ref, sr, 16000)
        res = next(self.model.inference_zero_shot(
            text, prompt_text, ref, stream=False))
        wav = res["tts_speech"].detach().cpu()
        self.save_wav(wav, out)

    def torchaudio_load(self, path: Path):
        import torchaudio
        return torchaudio.load(str(path))

    def save_wav(self, wav_tensor, out: Path):
        import torchaudio
        torchaudio.save(str(out), wav_tensor, self.sr, encoding="PCM_S",
                        bits_per_sample=16)


# ---------------- 统一入口 ----------------

def normalize_wav(src: Path, dst: Path):
    """任意引擎输出 → 24k mono s16,便于混音器按字节拼接。"""
    run_ffmpeg(["-i", str(src), "-ac", "1", "-ar", str(TTS_SR),
                "-c:a", "pcm_s16le", str(dst)])


def run(video, work_dir="work", srt=None, voices_yaml=None,
        engine="auto", mock=False, force=False) -> dict:
    vid = Path(video).stem
    wdir = Path(work_dir) / vid
    tts_dir = wdir / "tts"
    manifest = tts_dir / "manifest.json"
    if manifest.exists() and not force:
        return {"manifest": str(manifest), "skipped": True}

    import yaml
    cfg = yaml.safe_load(Path(voices_yaml).read_text(encoding="utf-8")) \
        if voices_yaml and Path(voices_yaml).exists() else {}
    lib = cfg.get("library", {})
    ep_map = (cfg.get("episode_map", {}) or {}).get("map", {})

    subs = parse_srt(srt or wdir / "tagged.srt")
    tts_dir.mkdir(parents=True, exist_ok=True)

    cosy = None
    engine_used = {}

    def pick_engine(spk):
        """决定该说话人的引擎与参数;返回 (engine_name, kwargs)"""
        m = ep_map.get(spk, {})
        voice_ref = m.get("voice")
        entry = lib.get(voice_ref, {})
        want = entry.get("type", "preset") if entry else "preset"
        if want == "clone" and engine in ("auto", "cosyvoice3"):
            return "cosyvoice3", entry
        if engine in ("edge", "auto"):
            return "edge", {"voice": EDGE_VOICES.get(spk, DEFAULT_VOICE)}
        return "silence", {}

    manifest_rows = []
    for i, s in enumerate(subs, 1):
        text = clean_text(s.text)
        if not text:
            continue
        spk_m = re.match(r"\[(SPK_\d+)\]", s.text)
        spk = spk_m.group(1) if spk_m else "SPK_00"
        eng, kw = pick_engine(spk)
        raw = tts_dir / f"{i:04d}_raw"
        out = tts_dir / f"{i:04d}.wav"
        est = max(0.8, s.t1 - s.t0)
        if mock or eng == "silence":
            synth_silence(text, est, out)
            eng = "silence"
        elif eng == "edge":
            suffix = ".mp3"
            tmp = raw.with_suffix(suffix)
            synth_edge(text, tmp, **kw)
            normalize_wav(tmp, out)
            tmp.unlink(missing_ok=True)
        elif eng == "cosyvoice3":
            global_cfg = cfg.get("global", {}) or {}
            if cosy is None:
                cosy = CosyVoiceEngine(
                    repo_dir=global_cfg.get("cosyvoice_repo"),
                    model_dir=global_cfg.get("cosyvoice_model"))
            ref_audio = Path(kw["ref_audio"])
            ref_text = kw.get("ref_text", "")
            tmp = raw.with_suffix(".wav")
            cosy.zero_shot(text, ref_text, ref_audio, tmp)
            normalize_wav(tmp, out)
            tmp.unlink(missing_ok=True)
        engine_used[eng] = engine_used.get(eng, 0) + 1
        manifest_rows.append({"idx": s.idx, "t0": s.t0, "t1": s.t1,
                              "spk": spk, "text": text, "wav": str(out)})
        if i % 20 == 0:
            print(f"[tts] {i}/{len(subs)}", flush=True)

    with open(manifest, "w", encoding="utf-8") as f:
        json.dump({"engine_used": engine_used, "lines": manifest_rows},
                  f, ensure_ascii=False, indent=1)
    print(f"[tts] done {len(manifest_rows)} lines, engines={engine_used}")
    return {"manifest": str(manifest), "lines": len(manifest_rows),
            "engine_used": engine_used}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--voices", default=None)
    ap.add_argument("--engine", default="auto",
                    choices=["auto", "edge", "silence", "cosyvoice3"])
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    print(run(a.video, voices_yaml=a.voices, engine=a.engine, mock=a.mock))
