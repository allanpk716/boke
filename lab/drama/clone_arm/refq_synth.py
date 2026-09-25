# -*- coding: utf-8 -*-
"""参考音质量实验 步骤2:各条件合成同一句 held-out。
run(主线 venv 只读): C:/WorkSpace/agent/boke/.venv/Scripts/python.exe refq_synth.py
输出: R<n>_syn.wav ×7 + refq_final.json
"""
import json
import sys
from pathlib import Path

REPO = Path("D:/boke_media/tools/CosyVoice")
MODEL = Path("D:/boke_media/models/Fun-CosyVoice3-0.5B-2512")
MARKER = "<|endofprompt|>"
SYS = "You are a helpful assistant."
HERE = Path(__file__).resolve().parent
for sub in (REPO, REPO / "third_party" / "Matcha-TTS"):
    s = str(sub)
    if s not in sys.path:
        sys.path.insert(0, s)


def main():
    import torchaudio
    from cosyvoice.cli.cosyvoice import CosyVoice3
    pairs = json.loads((HERE / "refq_pairs.json").read_text(encoding="utf-8"))
    model = CosyVoice3(str(MODEL), load_trt=False, load_vllm=False, fp16=False)
    sr_out = getattr(model, "sample_rate", 24000)
    final = []
    for it in pairs:
        it_model = model.inference_zero_shot(
            it["held_text"], f"{SYS}{MARKER}{it['ref_text']}", it["ref"],
            stream=False)
        wav = next(it_model)["tts_speech"].detach().cpu()
        if wav.shape[0] > 1:
            wav = wav.mean(dim=0, keepdim=True)
        # 峰值归一到 -3dB 防截幅
        peak = float(wav.abs().max()) or 1.0
        wav = (wav / peak * 0.7).clamp(-1, 1)
        out = HERE / f"R{it['rid'][1:]}_syn.wav"
        torchaudio.save(str(out), wav, sr_out, encoding="PCM_S",
                        bits_per_sample=16)
        final.append({**{k: it[k] for k in ("rid", "lang", "desc",
                                            "held_text")},
                      "syn": out.name, "anchor": Path(it["anchor"]).name,
                      "syn_s": round(wav.shape[1] / sr_out, 2)})
        print(f"[ok] {it['rid']} {it['desc']} -> {out.name} "
              f"{final[-1]['syn_s']}s")
    (HERE / "refq_final.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")
    print("refq_final.json:", len(final))


if __name__ == "__main__":
    main()
