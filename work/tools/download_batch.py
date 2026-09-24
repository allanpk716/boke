# -*- coding: utf-8 -*-
"""T1 批量下载: 按任务书优先级 ①中文日常(参考音) → ②英文采访(冒烟) → ③充电专属尝试。
产物: D:\\boke_media\\<BV>_<标题净化>.mp4 + work/download_log.md
"""
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MEDIA = Path("D:/boke_media")
LOG = ROOT / "work" / "download_log.md"
COOKIES = ROOT / "work" / "buvid_cookies.txt"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")

# (bvid, 分类, 用途, 备注)
PLAN = [
    ("BV1ZTr5BuEGy", "中文日常", "参考音", "斩杀线登NYT头版 18:19 play4.4M"),
    ("BV1eQ5t6GEtX", "中文日常", "参考音", "特朗普访华聊啥 19:55 play2.5M"),
    ("BV1NR596KEk4", "中文日常", "参考音", "特朗普再访华代表团 25:07 play3.0M"),
    ("BV17UPszhE6o", "采访(公开)", "冒烟", "圆脸X小外 39:58 play3.2M 外语原声+中字"),
    ("BV1UdFAzrE1Z", "素材(公开)", "冒烟备选", "马斯克 13:27 play3.3M"),
    ("BV1SGem6uEU9", "充电专属", "记录失败模式", "北爱威尔士苏格兰 94:17 无SESSDATA预期失败"),
]


def sanitize(title: str) -> str:
    t = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", title)
    return t.strip()[:60]


def log_append(line: str):
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def title_of(bvid: str) -> str:
    try:
        data = json.load(open(ROOT / "work" / "space_videos.json", encoding="utf-8"))
        for v in data:
            if v["bvid"] == bvid:
                return v["title"]
    except Exception:
        pass
    return bvid


def download(bvid: str, cat: str, purpose: str, note: str) -> bool:
    title = title_of(bvid)
    out_tmpl = str(MEDIA / f"{bvid}_{sanitize(title)}.%(ext)s")
    cmd = [
        "yt-dlp",
        "--cookies", str(COOKIES),
        "--user-agent", UA,
        "--add-headers", "Referer:https://www.bilibili.com/",
        "-f", "bv*[height<=1080]+ba/b",
        "--merge-output-format", "mp4",
        "-N", "3",
        "--retries", "3",
        "--no-playlist",
        "-o", out_tmpl,
        f"https://www.bilibili.com/video/{bvid}",
    ]
    print(f"[dl] {bvid} {title[:40]} ...", flush=True)
    t0 = time.time()
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    el = time.time() - t0
    ok = r.returncode == 0
    # 找产物
    files = list(MEDIA.glob(f"{bvid}_*.*"))
    size = sum(f.stat().st_size for f in files) / 1e6 if files else 0
    err = (r.stderr or "").strip().splitlines()
    err_last = err[-1] if err else ""
    log_append(
        f"| {bvid} | {cat} | {title[:50]} | {size:.1f}MB | {el:.0f}s | "
        f"{'✅' if ok else '❌ ' + err_last[:120]} | {purpose} |")
    print(f"[dl] {bvid} {'OK' if ok else 'FAIL'} {size:.1f}MB {el:.0f}s "
          f"{err_last[:100] if not ok else ''}", flush=True)
    return ok


def main():
    MEDIA.mkdir(exist_ok=True)
    if not LOG.exists():
        log_append("# T1 下载日志 (时间: " + time.strftime("%Y-%m-%d %H:%M") + ")")
        log_append("| BV | 分类 | 标题 | 大小 | 耗时 | 状态 | 用途 |")
        log_append("|---|---|---|---|---|---|---|")
    fails = 0
    for bvid, cat, purpose, note in PLAN:
        if fails >= 3 and cat != "充电专属":
            log_append(f"| {bvid} | {cat} | - | - | - | ⏭️ 跳过(连续失败过多) | {purpose} |")
            continue
        ok = download(bvid, cat, purpose, note)
        if not ok:
            fails += 1
        time.sleep(3)
    log_append("")
    print("[dl] batch done")


if __name__ == "__main__":
    sys.exit(main())
