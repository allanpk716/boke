# -*- coding: utf-8 -*-
"""克隆臂 步骤2:CosyVoice3 zero_shot 同文复刻 + 响度配平 → 成对样本。
run(主线 venv 只读,不装不改任何主线资产):
  C:/WorkSpace/agent/boke/.venv/Scripts/python.exe synth.py
输出: T<nn>_orig.wav / T<nn>_syn.wav (24k mono s16) + pairs_final.json
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


def rms_db(x):
    import math
    e = float((x.astype("float64") ** 2).mean())
    return 10 * math.log10(e + 1e-12)


def main():
    import torchaudio
    from cosyvoice.cli.cosyvoice import CosyVoice3
    pairs = json.loads((HERE / "pairs.json").read_text(encoding="utf-8"))
    model = CosyVoice3(str(MODEL), load_trt=False, load_vllm=False, fp16=False)
    sr_out = getattr(model, "sample_rate", 24000)
    print("model loaded, sr =", sr_out)
    final = []
    for p in pairs:
        ref = p["ref"]
        # A = 原声参考窗(16k) → 重采样 24k 播放口径
        wa, sr_a = torchaudio.load(ref)
        if sr_a != sr_out:
            wa = torchaudio.functional.resample(wa, sr_a, sr_out)
        if wa.shape[0] > 1:
            wa = wa.mean(dim=0, keepdim=True)
        # B = 同文 zero_shot 复刻
        it = model.inference_zero_shot(
            p["ref_text"], f"{SYS}{MARKER}{p['ref_text']}", str(ref),
            stream=False)
        res = next(it)
        wb = res["tts_speech"].detach().cpu()
        if wb.shape[0] > 1:
            wb = wb.mean(dim=0, keepdim=True)
        # 响度配平:B 增益对齐 A 的 RMS
        g = 10 ** ((rms_db(wa.numpy()) - rms_db(wb.numpy())) / 20)
        wb = (wb * g).clamp(-1.0, 1.0)
        n = int(p["pair_id"][1:])
        fa = HERE / f"T{n:02d}_orig.wav"
        fb = HERE / f"T{n:02d}_syn.wav"
        torchaudio.save(str(fa), wa, sr_out, encoding="PCM_S",
                        bits_per_sample=16)
        torchaudio.save(str(fb), wb, sr_out, encoding="PCM_S",
                        bits_per_sample=16)
        final.append({**p, "orig": fa.name, "syn": fb.name,
                      "orig_s": round(wa.shape[1] / sr_out, 2),
                      "syn_s": round(wb.shape[1] / sr_out, 2),
                      "gain_db": round(20 * (g and __import__("math").log10(g)), 1)})
        print(f"[ok] {p['pair_id']} {p['lang']} orig {final[-1]['orig_s']}s "
              f"syn {final[-1]['syn_s']}s gain {final[-1]['gain_db']}dB")
    (HERE / "pairs_final.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"pairs_final.json: {len(final)} 对")


if __name__ == "__main__":
    main()
