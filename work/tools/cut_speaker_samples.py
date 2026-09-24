# -*- coding: utf-8 -*-
"""按说话人切原声样本(试听台指认用)。
用法: cut_speaker_samples.py <video-stem> [每人段数=3]
产物: work/web/audio/speaker_<stem>_<SPK>_sample.wav + talk_time.json 追加
"""
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(stem: str, n_each: int = 3):
    w = ROOT / "work" / stem
    tagged = w / "tagged.srt"
    audio = w / "audio.wav"
    if not tagged.exists():
        sys.exit(f"缺 {tagged}")

    subs = []
    for m in re.finditer(
            r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)\n\[(SPK_\d+)\] (.+)",
            tagged.read_text(encoding="utf-8")):
        g = [int(x) for x in m.groups()[:8]]
        t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000
        t1 = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000
        subs.append((t0, t1, m.group(9), m.group(10)))

    by = defaultdict(list)
    for t0, t1, spk, txt in subs:
        by[spk].append((t0, t1))
    outdir = ROOT / "work" / "web" / "audio"
    outdir.mkdir(parents=True, exist_ok=True)
    (ROOT / "work" / "tmp").mkdir(exist_ok=True)

    talk = {spk: round(sum(t1 - t0 for t0, t1 in items), 1)
            for spk, items in by.items()}
    for spk, items in sorted(by.items()):
        picked = []
        for t0, t1 in sorted(items):
            if 3.5 <= t1 - t0 <= 8 and all(abs(t0 - p) > 60 for p, _ in picked):
                picked.append((t0, t1))
            if len(picked) >= n_each:
                break
        if not picked:
            continue
        tmps = []
        for i, (t0, t1) in enumerate(picked):
            t = ROOT / "work" / "tmp" / f"cut_{stem}_{spk}_{i}.wav"
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-ss", f"{t0:.2f}", "-t",
                 f"{t1-t0:.2f}", "-i", str(audio), "-c:a", "pcm_s16le", str(t)],
                check=True, capture_output=True)
            tmps.append(str(t.resolve()))
        lst = ROOT / "work" / "tmp" / f"cut_{stem}_{spk}.txt"
        lst.write_text("\n".join(f"file '{t}'" for t in tmps))
        out = outdir / f"speaker_{stem}_{spk}_sample.wav"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
             "-i", str(lst), "-c:a", "pcm_s16le", str(out)],
            check=True, capture_output=True)
        print(f"[cut] {stem} {spk}: talk {talk[spk]/60:.1f}min -> {out.name}")

    meta_path = ROOT / "work" / "talk_time.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    for spk, sec in talk.items():
        meta[f"{stem}/{spk}"] = round(sec / 60, 1)
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    print(f"[cut] talk_time -> {meta_path}")


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
