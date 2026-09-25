"""最小 SRT 字幕解析/回写（纯标准库，票03/07 备用）。

只认标准结构：序号行（可缺）、`HH:MM:SS,mmm --> HH:MM:SS,mmm` 时间行、多行文本；
块间以空行分隔，无法解析的块静默跳过。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_TIME = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")


@dataclass
class Cue:
    """一条字幕：start/end 为秒（float），text 为 '\n' 连接的多行文本，index 为原始序号（缺失=0）。"""
    start: float
    end: float
    text: str
    index: int = 0


def parse_time(s: str) -> float:
    """'HH:MM:SS,mmm'（逗号或点）→ 秒。"""
    h, m, sec, ms = re.split(r"[:,.]", s.strip())
    return int(h) * 3600 + int(m) * 60 + int(sec) + int(ms.ljust(3, "0")) / 1000.0


def fmt_time(t: float) -> str:
    """秒 → 'HH:MM:SS,mmm'（SRT 标准毫秒逗号）。"""
    ms = max(0, int(round(t * 1000)))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def parse_srt(text: str) -> list[Cue]:
    """SRT 全文 → Cue 列表。"""
    cues: list[Cue] = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [ln.strip() for ln in block.strip().splitlines() if ln.strip()]
        if not lines:
            continue
        ti = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if ti is None:
            continue
        m = _TIME.search(lines[ti])
        if not m:
            continue
        g = m.groups()
        start = int(g[0]) * 3600 + int(g[1]) * 60 + int(g[2]) + int(g[3].ljust(3, "0")) / 1000.0
        end = int(g[4]) * 3600 + int(g[5]) * 60 + int(g[6]) + int(g[7].ljust(3, "0")) / 1000.0
        idx = int(lines[0]) if ti >= 1 and lines[0].isdigit() else 0
        cues.append(Cue(start=start, end=end, text="\n".join(lines[ti + 1:]), index=idx))
    return cues


def dump_srt(cues) -> str:
    """Cue 列表 → SRT 全文（序号从 1 重排）。"""
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{fmt_time(c.start)} --> {fmt_time(c.end)}\n{c.text}\n")
    return "\n".join(out)


def load(path: str, encoding: str = "utf-8-sig") -> list[Cue]:
    """读 srt 文件并解析（默认兼容 UTF-8 BOM）。"""
    with open(path, encoding=encoding) as f:
        return parse_srt(f.read())


def save(path: str, cues) -> None:
    """写 srt 文件（\\r\\n 行尾，播放器友好）。"""
    with open(path, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(dump_srt(cues))


def shift(cues, dt: float) -> list[Cue]:
    """整体平移（秒，可负），返回新列表不改原列表。"""
    return [Cue(c.start + dt, c.end + dt, c.text, c.index) for c in cues]
