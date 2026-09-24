# -*- coding: utf-8 -*-
"""下载最近 2 期充电专属(用户拍板:先 2 期测试)。全 part 下载。"""
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MEDIA = Path("D:/boke_media")
LOG = ROOT / "work" / "download_log.md"
COOKIES = ROOT / "work" / "cookies_full.txt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")

# (bvid, 备注) 最近两期专属
PLAN = [
    ("BV1SGem6uEU9", "26-09-19 北爱威尔士苏格兰脱英 2P 共94:17"),
    ("BV1gpeJ6fEAk", "26-09-15 剥壳嘉宾中国行 74:33"),
]


def main():
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n## 专属下载(带登录cookie, {time.strftime('%Y-%m-%d %H:%M')})\n")
    for bv, note in PLAN:
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
        print(f"[dl] {bv} {note}", flush=True)
        t0 = time.time()
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        files = sorted(MEDIA.glob(f"{bv}_P*"))
        size = sum(f.stat().st_size for f in files) / 1e6
        ok = r.returncode == 0
        err = (r.stderr or "").strip().splitlines()
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"- {bv} | {note} | {len(files)}个文件 {size:.0f}MB | "
                    f"{'✅' if ok else '❌ ' + (err[-1] if err else '')[:150]}\n")
        print(f"[dl] {bv} {'OK' if ok else 'FAIL'} {len(files)} files "
              f"{size:.0f}MB {time.time()-t0:.0f}s", flush=True)
        time.sleep(3)


if __name__ == "__main__":
    main()
