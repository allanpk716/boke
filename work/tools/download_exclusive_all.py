# -*- coding: utf-8 -*-
"""全量下载充电专属(39个,跳过已有)。每个下完用 ffprobe 核总时长,防"只有试看片段"假成功。"""
import json
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[2]
MEDIA = Path("D:/boke_media")
LOG = ROOT / "work" / "download_log.md"
COOKIES = ROOT / "work" / "cookies_full.txt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")


def parse_len(s):
    """'124:08' 或 '1:24:08' → 秒"""
    parts = [int(x) for x in s.split(":")]
    sec = 0
    for p in parts:
        sec = sec * 60 + p
    return sec


def probe_total(bv):
    """该 BV 已下各 P 的时长总和(秒),失败返回 None"""
    total, n = 0.0, 0
    for f in sorted(MEDIA.glob(f"{bv}_P*.mp4")):
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(f)],
            capture_output=True, text=True)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        total += float(r.stdout.strip())
        n += 1
    return total if n else None


def main():
    vids = json.load(open(ROOT / "work" / "space_videos.json", encoding="utf-8"))
    plan = [(v["bvid"], v["length"]) for v in vids if v.get("is_charging_arc")]
    todo = [(bv, ln) for bv, ln in plan
            if not list(MEDIA.glob(f"{bv}_P*.mp4"))]
    print(f"exclusive total {len(plan)}, todo {len(todo)}", flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n## 专属全量下载({len(plan)}个清单,待下{len(todo)}个, "
                f"{time.strftime('%Y-%m-%d %H:%M')})\n")
    fail = []
    for i, (bv, exp_s) in enumerate(todo, 1):
        cmd = [
            "yt-dlp",
            "--cookies", str(COOKIES),
            "--user-agent", UA,
            "--add-headers", "Referer:https://www.bilibili.com/",
            "-f", "bv*[height<=1080]+ba/b",
            "--merge-output-format", "mp4",
            "-N", "4",
            "--retries", "3",
            "-o", str(MEDIA / f"{bv}_P%(playlist_index)s.%(ext)s"),
            f"https://www.bilibili.com/video/{bv}",
        ]
        print(f"[{i}/{len(todo)}] {bv} exp {exp_s}", flush=True)
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        files = sorted(MEDIA.glob(f"{bv}_P*.mp4"))
        size = sum(f.stat().st_size for f in files) / 1e6
        dur = probe_total(bv)
        ok = r.returncode == 0
        # 时长核验:试看片段模式 = 下成功但只有 ~300s
        verdict = "✅"
        if not ok:
            verdict = "❌ " + ((r.stderr or "").strip().splitlines() or [""])[-1][:150]
        elif dur is None or dur < parse_len(exp_s) * 0.9:
            verdict = f"⚠️ 时长不符 dur={dur and int(dur)}s exp={parse_len(exp_s)}s"
        if "✅" not in verdict:
            fail.append(bv)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"- {bv} | {exp_s} | {len(files)}个文件 {size:.0f}MB | {verdict}\n")
        print(f"[{i}/{len(todo)}] {bv} {verdict} {len(files)}f {size:.0f}MB "
              f"{time.time()-t0:.0f}s", flush=True)
        time.sleep(3)
    print(f"DONE. fail={fail}", flush=True)


if __name__ == "__main__":
    main()
