# -*- coding: utf-8 -*-
"""T5 克隆试合成: refs/yuanlian_chinese_01.wav 当参考音 → 3 句中文(含多音字)。
输出: out/clone_test_1/2/3.wav + work/t5_clone_report.json
记录: 用了哪种 prompt 模式(无文本/ASR稿), 显存, 耗时。
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = Path("D:/boke_media/tools/CosyVoice")
MODEL_DIR = Path("D:/boke_media/models/Fun-CosyVoice3-0.5B-2512")
REF = ROOT / "refs/yuanlian_chinese_01.wav"
OUT = ROOT / "out"

SENTENCES = [
    "重庆最近重新规划了好几条地铁线路,重量级的建设项目一个接一个。",   # 重 chóng/zhòng
    "他在长春的银行工作,行长对他评价很高,说他做事很行。",             # 行 háng/xíng + 长 cháng/zhǎng
    "这份调查报告的结论差强人意,但和预想的差距实在太大。",             # 差 chā/chà/chāi
]


def main():
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "third_party" / "Matcha-TTS"))

    import torch
    import torchaudio
    from cosyvoice.cli.cosyvoice import CosyVoice3

    print(f"[t5] loading model from {MODEL_DIR} ...", flush=True)
    t0 = time.time()
    # CosyVoice3(model_dir, load_trt, load_vllm, fp16, trt_concurrent) — 本版无 load_jit/device
    model = CosyVoice3(str(MODEL_DIR), load_trt=False, load_vllm=False, fp16=False)
    print(f"[t5] model loaded in {time.time()-t0:.0f}s, "
          f"sr={getattr(model, 'sample_rate', '?')}", flush=True)

    # prompt_wav 接收路径(内部 load_wav 重采样),参考音已是 16k mono
    ref_path = str(REF)
    print(f"[t5] ref: {REF.name} 25.0s @16k")

    # 可用方法探查(记入报告)
    methods = [m for m in dir(model) if m.startswith("inference")]
    print("[t5] inference methods:", methods)

    report = {"methods": methods, "prompt_mode": None, "results": []}
    OUT.mkdir(exist_ok=True)

    mode = sys.argv[1] if len(sys.argv) > 1 else "cross"
    # cross = inference_cross_lingual(免参考文本)  zero = 需 ASR 逐字稿
    if mode == "zero":
        import whisper
        print("[t5] whisper ASR transcribing ref (small, cpu) ...", flush=True)
        wmodel = whisper.load_model("small")
        asr = wmodel.transcribe(str(REF), language="zh")
        prompt_text = ("You are a helpful assistant.<|endofprompt|>"
                       + asr["text"].strip())
        print(f"[t5] ref transcript: {asr['text'].strip()[:60]}...")
        (ROOT / "refs" / "ref01_transcript.txt").write_text(
            asr["text"].strip(), encoding="utf-8")
        report["prompt_mode"] = "zero_shot + whisper ASR transcript"
    else:
        prompt_text = "You are a helpful assistant."   # 标记放 tts_text 头(example.py 官方 cross 用法)
        report["prompt_mode"] = "cross_lingual 无文本模式 (标记在 tts_text)"

    for i, sent in enumerate(SENTENCES, 1):
        t1 = time.time()
        if mode == "zero":
            it = model.inference_zero_shot(sent, prompt_text, ref_path, stream=False)
        else:
            it = model.inference_cross_lingual(
                f"<|endofprompt|>{sent}", ref_path, stream=False)
        res = next(it)
        wav = res["tts_speech"].detach().cpu()
        suffix = "" if mode == "cross" else "_zero"
        out = OUT / f"clone_test_{i}{suffix}.wav"
        torchaudio.save(str(out), wav, getattr(model, "sample_rate", 24000),
                        encoding="PCM_S", bits_per_sample=16)
        dur = wav.shape[-1] / getattr(model, "sample_rate", 24000)
        el = time.time() - t1
        mem = torch.cuda.max_memory_allocated() / 1e9 if torch.cuda.is_available() else 0
        report["results"].append({"i": i, "dur_s": round(dur, 2),
                                  "elapsed_s": round(el, 1),
                                  "vram_gb": round(mem, 2)})
        print(f"[t5] clone_test_{i}: {dur:.1f}s audio in {el:.1f}s, "
              f"vram_peak={mem:.2f}GB", flush=True)
    report["prompt_mode"] = report["prompt_mode"] or "unknown"

    (ROOT / "work" / "t5_clone_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("[t5] report → work/t5_clone_report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
