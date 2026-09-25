"""wav 读写助手：s16 PCM、mono/stereo、24k/44.1k（纯标准库 wave + array）。

约定：样本一律 array('h')（int16），立体声为交错排列（LRLRLR…）。
换采样率优先用 ffmpeg，本模块的 resample_linear 只够自测/粗算用。
"""
from __future__ import annotations

import array
import wave


def _check_s16() -> None:
    if array.array("h").itemsize != 2:
        raise RuntimeError("array('h') 不是 2 字节，本模块的 s16 假设失效")


def read_wav(path: str) -> tuple[array.array, int, int]:
    """读 s16 wav → (交错样本 array('h'), 采样率, 声道数)；非 16bit PCM 报错。"""
    _check_s16()
    with wave.open(str(path), "rb") as w:
        nch, sw, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if sw != 2:
        raise ValueError(f"仅支持 16bit PCM，得到 {sw*8}bit（{path}）")
    a = array.array("h")
    a.frombytes(raw)
    return a, rate, nch


def write_wav(path: str, samples, rate: int, channels: int = 1) -> None:
    """写 s16 wav：samples 为可迭代的 int16（立体声交错），长度须是声道数整数倍。"""
    _check_s16()
    a = array.array("h", samples)
    if channels < 1 or len(a) % channels:
        raise ValueError(f"样本数 {len(a)} 与声道数 {channels} 不匹配")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(a.tobytes())


def to_mono(samples, channels: int) -> array.array:
    """多声道 → mono（逐帧取均值截断）；已是 mono 原样拷贝。"""
    a = array.array("h", samples)
    if channels == 1:
        return a
    if channels < 1 or len(a) % channels:
        raise ValueError(f"样本数 {len(a)} 与声道数 {channels} 不匹配")
    n = len(a) // channels
    out = array.array("h", bytes(2 * n))
    for i in range(n):
        out[i] = sum(a[i * channels: (i + 1) * channels]) // channels
    return out


def resample_linear(samples, src_rate: int, dst_rate: int) -> array.array:
    """线性插值粗重采样（int16，端点钳位）。精确重采样请走 ffmpeg -ar。"""
    a = array.array("h", samples)
    if src_rate == dst_rate or not len(a):
        return a
    n_out = round(len(a) * dst_rate / src_rate)
    out = array.array("h", bytes(2 * n_out))
    step = (len(a) - 1) / max(n_out - 1, 1)
    for i in range(n_out):
        pos = i * step
        j, frac = int(pos), pos - int(pos)
        if frac and j + 1 < len(a):
            v = a[j] * (1.0 - frac) + a[j + 1] * frac
        else:
            v = a[j]
        out[i] = max(-32768, min(32767, round(v)))
    return out
