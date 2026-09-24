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
ROOT_REF = Path(__file__).resolve().parents[2]  # 仓库根(ref_text_file 相对路径基准)


def clean_text(text: str) -> str:
    """去掉 [SPK_xx] 前缀与多余空白。"""
    t = re.sub(r"^\[SPK_\d+\]\s*", "", text.strip())
    return re.sub(r"\s+", " ", t)


MERGE_GAP = 0.6      # 相邻同说话人字幕条间隔 ≤0.6s → 合成一个合成单元
MERGE_MAX_CHARS = 45  # 合并单元字符上限(CosyVoice 单句≤50字约束)


def merge_units(subs: list) -> list:
    """碎字幕条 → 说话人连续的"整句"合成单元。

    v1 教训: OCR 按画面停顿切条(平均 1.76s/条),逐条合成导致
    ①每条自带 TTS 首尾垫音塞爆时间窗 ②碎句听感机械 ③轮换频繁。
    合并后单元时长=窗口总和,同一单元内不再变速。
    返回 [dict(idx,t0,t1,spk,text)]
    """
    units = []
    for s in subs:
        m = re.match(r"\[(SPK_\d+)\]", s.text)
        spk = m.group(1) if m else "SPK_00"
        text = clean_text(s.text)
        if not text:
            continue
        if (units and units[-1]["spk"] == spk
                and s.t0 - units[-1]["t1"] <= MERGE_GAP
                and len(units[-1]["text"]) + len(text) <= MERGE_MAX_CHARS):
            u = units[-1]
            joiner = "" if u["text"].endswith(("。",",",",","?","?","!","!")) else ","
            u["text"] = u["text"] + joiner + text
            u["t1"] = s.t1
        else:
            units.append({"idx": s.idx, "t0": s.t0, "t1": s.t1,
                          "spk": spk, "text": text})
    return units


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


def synth_edge(text: str, out: Path, voice: str = None, rate: str = "+0%",
               retries=3):
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
    """CosyVoice3 零样本克隆(repo/模型路径来自 voices.yaml global 段)。

    实测可用调用(2026-09-23 夜班验证):
      model = CosyVoice3(model_dir, load_trt=False, load_vllm=False, fp16=False)
      # 无参考逐字稿时:cross_lingual + tts_text 头部加 <|endofprompt|> 标记
      it = model.inference_cross_lingual(f"<|endofprompt|>{text}", ref_path)
      # 有逐字稿时(zero_shot 更优):
      it = model.inference_zero_shot(text, "You are a helpful assistant.<|endofprompt|>"
                                     + ref_transcript, ref_path)
    注意 prompt_wav 接收路径字符串;cross_lingual 直接裸调会触发
    '<|endofprompt|> not detected' 断言(标记必须在文本里)。
    """

    MARKER = "<|endofprompt|>"
    SYS = "You are a helpful assistant."

    def __init__(self, repo_dir: str, model_dir: str, device=None):
        import sys
        repo = Path(repo_dir)
        for sub in (repo, repo / "third_party" / "Matcha-TTS"):
            s = str(sub)
            if s not in sys.path:
                sys.path.insert(0, s)
        from cosyvoice.cli.cosyvoice import CosyVoice3

        self.model = CosyVoice3(str(model_dir), load_trt=False,
                                load_vllm=False, fp16=False)
        self.sr = getattr(self.model, "sample_rate", TTS_SR)

    def synth(self, text: str, ref_wav: Path, ref_text: str, out: Path):
        if ref_text:
            prompt = f"{self.SYS}{self.MARKER}{ref_text}"
            it = self.model.inference_zero_shot(text, prompt, str(ref_wav),
                                                stream=False)
        else:
            it = self.model.inference_cross_lingual(
                f"{self.MARKER}{text}", str(ref_wav), stream=False)
        res = next(it)
        wav = res["tts_speech"].detach().cpu()
        self.save_wav(wav, out)

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
    units = merge_units(subs)   # v2: 碎字幕条合并成整句合成单元
    print(f"[tts] {len(subs)} 条字幕 → {len(units)} 个合成单元")
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
    for i, u in enumerate(units, 1):
        text, spk = u["text"], u["spk"]
        eng, kw = pick_engine(spk)
        raw = tts_dir / f"u{i:04d}_raw"
        out = tts_dir / f"u{i:04d}.wav"
        est = max(0.8, u["t1"] - u["t0"])
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
            # ref_text 优先;voices.yaml 可用 ref_text_file 指文件(避免 yaml 里塞长文)
            ref_text = kw.get("ref_text", "")
            if not ref_text and kw.get("ref_text_file"):
                rf = ROOT_REF / kw["ref_text_file"] if not Path(kw["ref_text_file"]).is_absolute() \
                    else Path(kw["ref_text_file"])
                if rf.exists():
                    ref_text = rf.read_text(encoding="utf-8").strip()
            tmp = raw.with_suffix(".wav")
            cosy.synth(text, ref_audio, ref_text, tmp)
            normalize_wav(tmp, out)
            tmp.unlink(missing_ok=True)
        engine_used[eng] = engine_used.get(eng, 0) + 1
        manifest_rows.append({"idx": u["idx"], "t0": u["t0"], "t1": u["t1"],
                              "spk": spk, "text": text, "wav": str(out)})
        if i % 20 == 0:
            print(f"[tts] {i}/{len(units)}", flush=True)

    with open(manifest, "w", encoding="utf-8") as f:
        json.dump({"engine_used": engine_used, "lines": manifest_rows,
                   "n_subs": len(subs), "n_units": len(units)},
                  f, ensure_ascii=False, indent=1)
    print(f"[tts] done {len(manifest_rows)} units, engines={engine_used}")
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
