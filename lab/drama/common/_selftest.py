"""common/ 三工具最小自测：`python _selftest.py`（系统 python 可跑，纯标准库）。

wav 写读回环 / 声道与重采样 / srt 解析回写 / ffmpeg 正弦波生成+probe（无 ffmpeg 则跳过）。
全部通过退出码 0，任一 FAIL 退出码 1。
"""
from __future__ import annotations

import array
import math
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # Windows 重定向输出默认走本地编码（cp936），统一成 UTF-8 防乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import run_ffmpeg
import srtmini
import wavio


class Skip(Exception):
    """环境缺前置（如无 ffmpeg），跳过不计失败。"""


_CASES = []


def case(fn):
    _CASES.append(fn)
    return fn


def _sine(freq: float, rate: int, n: int, amp: float = 8000) -> array.array:
    return array.array("h", [round(amp * math.sin(2 * math.pi * freq * i / rate)) for i in range(n)])


@case
def wavio_roundtrip_mono(tmp):
    """24k mono s16 写读回环，逐样本一致。"""
    p = os.path.join(tmp, "mono24k.wav")
    src = _sine(440.0, 24000, 2400)
    wavio.write_wav(p, src, 24000, channels=1)
    back, rate, ch = wavio.read_wav(p)
    assert rate == 24000 and ch == 1, (rate, ch)
    assert back == src, "样本回读不一致"


@case
def wavio_roundtrip_stereo(tmp):
    """44.1k stereo s16 写读回环，交错排列一致。"""
    p = os.path.join(tmp, "stereo44k.wav")
    src = array.array("h", [100 * (i % 2) + i % 97 for i in range(4410)])
    wavio.write_wav(p, src, 44100, channels=2)
    back, rate, ch = wavio.read_wav(p)
    assert rate == 44100 and ch == 2, (rate, ch)
    assert back == src, "交错样本回读不一致"


@case
def wavio_to_mono(tmp):
    """stereo → mono 逐帧均值：[100,200,300,400] → [150,350]。"""
    out = wavio.to_mono(array.array("h", [100, 200, 300, 400]), 2)
    assert out == array.array("h", [150, 350]), out
    mono = array.array("h", [7, 8])
    assert wavio.to_mono(mono, 1) == mono


@case
def wavio_resample(tmp):
    """线性重采样长度与数值：24k→16k，恒定值序列长度同比、值不变。"""
    src = array.array("h", [1234] * 2400)
    out = wavio.resample_linear(src, 24000, 16000)
    assert len(out) == 1600, len(out)
    assert all(v == 1234 for v in out)
    assert wavio.resample_linear(src, 24000, 24000) == src


@case
def srt_parse_and_roundtrip(tmp):
    """解析两段（含多行文本/缺序号）→ dump → 再解析，时间与文本一致。"""
    raw = ("1\n00:00:01,500 --> 00:00:03,250\n第一行\n第二行\n\n"
           "00:00:10,000 --> 00:00:12,000\nno index cue\n")
    cues = srtmini.parse_srt(raw)
    assert len(cues) == 2, cues
    assert cues[0].start == 1.5 and cues[0].end == 3.25
    assert cues[0].text == "第一行\n第二行" and cues[0].index == 1
    assert cues[1].index == 0 and cues[1].start == 10.0
    again = srtmini.parse_srt(srtmini.dump_srt(cues))
    assert [(c.start, c.end, c.text) for c in again] == [(c.start, c.end, c.text) for c in cues]
    assert srtmini.fmt_time(3661.5) == "01:01:01,500"
    assert abs(srtmini.parse_time("01:01:01,500") - 3661.5) < 1e-9


@case
def srt_file_io(tmp):
    """save→load 文件回环（CRLF 行尾），shift 平移生效。"""
    p = os.path.join(tmp, "t.srt")
    cues = srtmini.parse_srt("1\n00:00:01,000 --> 00:00:02,000\nhello\n")
    srtmini.save(p, cues)
    got = srtmini.load(p)
    assert len(got) == 1 and got[0].start == 1.0 and got[0].text == "hello"
    sh = srtmini.shift(cues, -0.5)
    assert sh[0].start == 0.5 and cues[0].start == 1.0  # 原列表不被改动


@case
def ffmpeg_sine_probe(tmp):
    """ffmpeg 生成 0.5s 正弦 wav → wavio 读回 + ffprobe 元数据（无 ffmpeg 则 SKIP）。"""
    if not run_ffmpeg.which("ffmpeg"):
        raise Skip("PATH 中无 ffmpeg")
    out = os.path.join(tmp, "sine.wav")
    run_ffmpeg.ffmpeg(["-f", "lavfi", "-i", "sine=frequency=440:duration=0.5:sample_rate=24000",
                       "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", out])
    data, rate, ch = wavio.read_wav(out)
    assert rate == 24000 and ch == 1 and abs(len(data) - 12000) < 100, (rate, ch, len(data))
    meta = run_ffmpeg.ffprobe_json(out)
    assert meta["format"]["format_name"].startswith("wav"), meta["format"]["format_name"]
    # 失败路径也要抛错：给一个不存在的输入
    try:
        run_ffmpeg.ffmpeg(["-i", os.path.join(tmp, "no_such_file.mp3"), out])
    except run_ffmpeg.FFmpegError:
        pass
    else:
        raise AssertionError("坏输入未抛 FFmpegError")


def main() -> int:
    fails = skips = 0
    for fn in _CASES:
        tmp = tempfile.mkdtemp(prefix="labdrama_selftest_")
        try:
            fn(tmp)
            print(f"[PASS] {fn.__name__}  {fn.__doc__ or ''}")
        except Skip as e:
            skips += 1
            print(f"[SKIP] {fn.__name__}  {e}")
        except Exception as e:
            fails += 1
            print(f"[FAIL] {fn.__name__}  {type(e).__name__}: {e}")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    total = len(_CASES)
    print(f"--- {total - fails - skips} passed, {fails} failed, {skips} skipped / {total}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
