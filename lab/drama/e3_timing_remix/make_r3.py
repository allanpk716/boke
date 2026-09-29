# -*- coding: utf-8 -*-
"""E3 步骤4:R3 变速阈值定标刺激制备。
从 A 臂挑 4 句(每方向 2 句,窗 2.5-5s 优先),atempo 1.0/1.15/1.3/1.5 四档 → r3/*.wav
run(任意 python): python make_r3.py
输出: r3_items.json + r3/{item}.wav
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
R3 = HERE / "r3"
R3.mkdir(exist_ok=True)
TEMPOS = [1.0, 1.15, 1.3, 1.5]


def atempo(src, dst, factor):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(src),
                    "-filter:a", f"atempo={factor:.6f}", str(dst)], check=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    rows = json.loads((HERE / "e3_synth_result.json").read_text(encoding="utf-8"))
    pick = []
    for d in ("en2zh", "zh2en"):
        cand = [r for r in rows if r["dir"] == d and 2.5 <= r["win_dur"] <= 5.0]
        cand = cand or [r for r in rows if r["dir"] == d]
        cand.sort(key=lambda r: -r["win_dur"])
        pick.extend(cand[:2])
    items = []
    for r in pick:
        for t in TEMPOS:
            item = f"{r['id']}_{str(t).replace('.', '_')}"
            dst = R3 / f"{item}.wav"
            if abs(t - 1.0) < 1e-6:
                shutil.copy(HERE / "out" / f"{r['id']}_A.wav", dst)
            else:
                atempo(HERE / "out" / f"{r['id']}_A.wav", dst, t)
            items.append({"item": item, "sid": r["id"], "dir": r["dir"],
                          "tempo": t, "free": r["free"],
                          "win_dur": r["win_dur"]})
            print(f"[r3] {item}")
    (HERE / "r3_items.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    print("r3_items.json:", len(items))


if __name__ == "__main__":
    main()
