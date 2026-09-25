"""ffmpeg/ffprobe 的 subprocess 薄包装。

列表参数防转义问题、执行前打印完整命令、非零退出抛 FFmpegError。
纯标准库，不从 boke import。
"""
from __future__ import annotations

import json
import shutil
import subprocess


class FFmpegError(RuntimeError):
    """外部命令失败（非零退出 / 可执行文件不存在 / 超时）。"""


def which(exe: str) -> str | None:
    """在 PATH 中查找可执行文件，找不到返回 None。"""
    return shutil.which(exe)


def run(cmd: list[str], *, timeout: float | None = None) -> str:
    """执行 cmd（列表参数），打印命令行；成功返回 stdout 文本，失败抛 FFmpegError。"""
    print("+", " ".join(str(c) for c in cmd))
    try:
        cp = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
    except FileNotFoundError as e:
        raise FFmpegError(f"找不到可执行文件: {cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise FFmpegError(f"超时({timeout}s): {' '.join(str(c) for c in cmd)}") from e
    if cp.returncode != 0:
        tail = [ln for ln in (cp.stderr or cp.stdout or "").strip().splitlines() if ln.strip()][-10:]
        msg = f"退出码 {cp.returncode}: {' '.join(str(c) for c in cmd)}"
        raise FFmpegError(msg + ("\n  " + "\n  ".join(tail) if tail else ""))
    return cp.stdout or ""


def ffmpeg(args: list[str], *, timeout: float | None = None) -> str:
    """跑 ffmpeg 本体：自动加 -hide_banner -y，日志只留 error 级。args 为转换参数列表。"""
    return run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], timeout=timeout)


def ffprobe_json(path: str, *, timeout: float | None = None) -> dict:
    """ffprobe 元数据（format + streams）为 dict。"""
    out = run(["ffprobe", "-v", "error", "-print_format", "json",
               "-show_format", "-show_streams", str(path)], timeout=timeout)
    return json.loads(out)
