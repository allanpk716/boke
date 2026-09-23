# -*- coding: utf-8 -*-
"""共享工具:SRT/RTTM 解析与写出、ffmpeg 调用、时长探测。仅标准库。"""
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


def ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg 不在 PATH")
    return exe


def run_ffmpeg(args, check=True):
    cmd = [ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", *args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"ffmpeg 失败: {' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r


def probe_duration(media: Path) -> float:
    """ffprobe 取时长(秒);失败返回 -1"""
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe 不在 PATH")
    r = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(media)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return -1.0
    try:
        return float(json.loads(r.stdout)["format"]["duration"])
    except (KeyError, ValueError):
        return -1.0


# ---------------- SRT ----------------

@dataclass
class SubLine:
    idx: int
    t0: float
    t1: float
    text: str


_SRT_TIME = re.compile(
    r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def _ts(sec: float) -> str:
    ms = max(0, round(sec * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(path) -> list:
    """容忍 [SPK_xx] 前缀与 BOM;返回 [SubLine]。"""
    raw = Path(path).read_text(encoding="utf-8-sig")
    lines = []
    blocks = re.split(r"\n\s*\n", raw.strip())
    for blk in blocks:
        rows = [r.strip() for r in blk.splitlines() if r.strip()]
        if not rows:
            continue
        m = None
        time_row = None
        for i, row in enumerate(rows):
            m = _SRT_TIME.search(row)
            if m:
                time_row = i
                break
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        t1 = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        text = "\n".join(rows[time_row + 1:]).strip()
        try:
            idx = int(rows[0])
        except ValueError:
            idx = len(lines) + 1
        lines.append(SubLine(idx=idx, t0=t0, t1=t1, text=text))
    return lines


def write_srt(path, subs: list):
    """subs: [SubLine];text 原样写出(带 SPK 前缀时调用方自己拼)。"""
    out = []
    for i, s in enumerate(subs, 1):
        out.append(f"{i}\n{_ts(s.t0)} --> {_ts(s.t1)}\n{s.text}\n")
    Path(path).write_text("\n".join(out), encoding="utf-8")


# ---------------- RTTM ----------------

@dataclass
class SpkSeg:
    t0: float
    t1: float
    spk: str


def parse_rttm(path) -> list:
    segs = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        p = line.split()
        if len(p) >= 8 and p[0] == "SPEAKER":
            t0 = float(p[3] or 0) / 100.0
            dur = float(p[4] or 0) / 100.0
            segs.append(SpkSeg(t0=t0, t1=t0 + dur, spk=p[7]))
    return merge_adjacent(segs)


def merge_adjacent(segs: list) -> list:
    """合并相邻同说话人段(01 §4.2:diarization 段碎对策)。"""
    segs = sorted(segs, key=lambda s: (s.t0, s.t1))
    out = []
    for s in segs:
        if out and out[-1].spk == s.spk and s.t0 - out[-1].t1 <= 0.3:
            out[-1].t1 = max(out[-1].t1, s.t1)
        else:
            out.append(SpkSeg(spk=s.spk, t0=s.t0, t1=s.t1))
    return out


def write_rttm(path, segs: list, file_id="audio"):
    rows = []
    for s in sorted(segs, key=lambda x: x.t0):
        dur = max(0.0, s.t1 - s.t0)
        rows.append(
            f"SPEAKER {file_id} 1 {s.t0*100:07.3f} {dur*100:07.3f} "
            f"<NA> <NA> {s.spk} <NA> <NA>")
    Path(path).write_text("\n".join(rows) + "\n", encoding="utf-8")


def video_id(video: Path) -> str:
    return Path(video).stem
