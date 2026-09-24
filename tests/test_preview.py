# -*- coding: utf-8 -*-
"""小样生成器单测(D8 固定两段 / D13 小样门 / F11 密集窗 / F12 同 hash 复用)。

任务书约束:一律 tmp_path 构造工作目录,不写真实 work/<id>;合成走 mock 静音桩
(monkeypatch run_ffmpeg 为 stdlib 真写 wav 的替身),不调真实 TTS/GPU;
另有一条真实 ffmpeg 端到端(无 ffmpeg 环境自动跳过)。
"""
import array
import shutil
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

sys_path = Path(__file__).resolve().parents[1] / "src"
import sys  # noqa: E402

sys.path.insert(0, str(sys_path))

from boke import preview, review_apply, synthesize  # noqa: E402
from boke.common import SubLine, parse_srt, probe_duration, write_rttm, write_srt  # noqa: E402
from boke.library import (  # noqa: E402
    EV_ANALYSIS_DONE, EV_DOWNLOAD_DONE, EV_REVIEW_SUBMIT, ST_PREVIEW,
    ST_REVIEWED, Library,
)

BVID = "BV1pvw0001"
PART = 1
VID = f"{BVID}_P{PART}"          # 与 orchestrator 下载命名 <BV>_P<n> 一致
TOTAL = 300.0


# ---------- 构造:字幕/分人/决策 ----------

def _timeline():
    """字幕条与说话人段:片头 5 句(SPK_00)+ 密集区 20 句(150s 起,每 3s 一句,
    偶数句 SPK_00 / 奇数句 SPK_01)+ 片尾 1 句(290s)。密集窗应落在 [150,210]。"""
    subs, segs = [], []
    for i in range(5):                       # 片头 10,18,26,34,42
        t = 10 + i * 8
        subs.append(SubLine(i + 1, float(t), float(t + 1), f"开场第{i}句"))
    segs.append((0.0, 50.0, "SPK_00"))
    base = 6
    for i in range(20):                      # 密集 150,153,...,207
        t = 150 + i * 3
        spk = "SPK_00" if i % 2 == 0 else "SPK_01"
        subs.append(SubLine(base + i, float(t), float(t + 1), f"密集第{i}句"))
        segs.append((t - 0.2, t + 1.2, spk))
    subs.append(SubLine(base + 20, 290.0, 291.0, "收尾一句"))
    segs.append((289.0, 291.0, "SPK_00"))
    return subs, segs


def _make_work(tmp_path):
    wdir = tmp_path / "work" / VID
    wdir.mkdir(parents=True, exist_ok=True)
    subs, segs = _timeline()
    write_srt(wdir / "ocr.srt", subs)
    write_rttm(wdir / "diar.rttm",
               [type("S", (), {"t0": a, "t1": b, "spk": s}) for a, b, s in segs])
    return wdir


def _dec(skip=False):
    """两说话人决策:SPK_00 预设;SPK_01 按需 skip(无需人物库/主持人)。"""
    return {"verdict": "dub", "speakers": [
        {"id": "SPK_00", "note": "主持", "voice": "preset",
         "preset_voice": "zh-CN-YunxiNeural"},
        {"id": "SPK_01", "note": "嘉宾", "voice": "skip" if skip else "preset",
         **({} if skip else {"preset_voice": "zh-CN-XiaoxiaoNeural"})},
    ]}


def _apply_decision(tmp_path, dec):
    _make_work(tmp_path)    # 先铺 ocr.srt/diar.rttm(tagged.dec.srt 由它重算)
    return review_apply.apply_decision(dec, VID, str(tmp_path / "work"),
                                       part=PART)


def _make_library(tmp_path, dec):
    lib = Library(tmp_path / "work" / "library.json")
    lib.add(BVID, PART, title="测试期", duration=int(TOTAL))
    lib.transition(BVID, PART, EV_DOWNLOAD_DONE,
                   media_path=f"D:/boke_media/{VID}.mp4")
    lib.transition(BVID, PART, EV_ANALYSIS_DONE)
    lib.transition(BVID, PART, EV_REVIEW_SUBMIT, review=dec)
    return lib


# ---------- ffmpeg 替身(stdlib 真写 wav,不调外部二进制) ----------

def _write_wav(path, sr, frames):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(array.array("h", frames).tobytes())


def _read_frames(path):
    with wave.open(str(path), "rb") as w:
        assert w.getframerate() == 24000 and w.getnchannels() == 1
        return array.array("h", w.readframes(w.getnframes()))


def _positional_frames(seconds, sr=24000):
    """每秒振幅不同(可按内容定位时间),用于验证切原片切片正确。"""
    out = array.array("h")
    for s in range(int(seconds)):
        out.extend(array.array("h", [(s % 30000) + 100] * sr))
    return out


class FakeFFmpeg:
    """run_ffmpeg 替身:按本票三种调用形状分流,全部 stdlib 真写文件。

    - anullsrc(synthesize.synth_silence 的 TTS 静音桩):真写 24k 静音 wav;
    - -ss ...(preview 切原片):按帧切片源 wav;
    - libmp3lame(preview 拼接编码):真拼两段 pcm + 1s 静音写入产物文件。
    """

    def __init__(self):
        self.calls = []

    def __call__(self, args, check=True):
        a = [str(x) for x in args]
        self.calls.append(a)
        joined = " ".join(a)
        if "anullsrc" in joined:
            dur = float(a[a.index("-t") + 1])
            _write_wav(a[-1], 24000, [0] * int(dur * 24000))
        elif a[0] == "-ss":
            t0, dur = float(a[1]), float(a[a.index("-t") + 1])
            src = _read_frames(a[3])
            _write_wav(a[-1], 24000,
                       src[int(t0 * 24000):int((t0 + dur) * 24000)])
        elif "libmp3lame" in joined:
            inputs = [a[i + 1] for i, x in enumerate(a) if x == "-i"]
            pcm = b"".join(_read_frames(p).tobytes() for p in inputs)
            Path(a[-1]).parent.mkdir(parents=True, exist_ok=True)
            Path(a[-1]).write_bytes(pcm + b"\x00\x00" * (24000 * 1))
        else:
            raise AssertionError("未预期的 ffmpeg 调用: " + joined)
        return SimpleNamespace(returncode=0, stderr="")

    def count(self, needle):
        return sum(1 for c in self.calls if needle in " ".join(c))

    def n_tts(self):
        """TTS 静音桩调用数(24k anullsrc;编码用的 44.1k anullsrc 不算)。"""
        return self.count("anullsrc=r=24000")


@pytest.fixture
def fake_ff(monkeypatch):
    ff = FakeFFmpeg()
    monkeypatch.setattr(synthesize, "run_ffmpeg", ff)
    monkeypatch.setattr(preview, "run_ffmpeg", ff)
    return ff


def _generate(tmp_path, dec, *, library=None, **kw):
    return preview.generate_preview(
        BVID, PART, dec, library=library, work_root=str(tmp_path / "work"),
        total_dur=TOTAL, mock=True, **kw)


# ---------- 密集窗(纯函数) ----------

def test_dense_window_default_rule_and_determinism():
    subs, _ = _timeline()
    w1 = preview.dense_window(subs, TOTAL)
    w2 = preview.dense_window(subs, TOTAL)
    assert w1 == (150.0, 210.0)          # 唯一最大覆盖窗,同输入同窗
    assert w2 == w1


def test_dense_window_fallbacks():
    # 总时长 < 60s → 整段
    one = [SubLine(1, 5.0, 8.0, "x")]
    assert preview.dense_window(one, 50.0) == (0.0, 50.0)
    # 排除后无窗(总长 120,有效起点区间空)→ 去掉排除取最密:
    # 含住 [100,110] 的窗 ws∈[50,100],并列取最靠前 → [50,110]
    mid = [SubLine(1, 100.0, 110.0, "x")]
    assert preview.dense_window(mid, 120.0) == (50.0, 110.0)
    # 全范围也无覆盖(无字幕)→ 中点 60s
    assert preview.dense_window([], 100.0) == (20.0, 80.0)
    # 字幕全在片头(默认区间覆盖为 0)→ 放宽后取片头最密窗
    head = [SubLine(i, float(5 * i), float(5 * i + 1), "x") for i in range(8)]
    assert preview.dense_window(head, TOTAL) == (0.0, 60.0)


# ---------- 两段合成 / 哨兵 / skip 原声 ----------

def _dec_dir(tmp_path, res=None):
    d = tmp_path / "work" / VID / f"dec_{res['dec_hash']}" if res else None
    return d


def test_preview_applied_two_segments_sentinel(tmp_path, fake_ff):
    dec = _dec()
    _apply_decision(tmp_path, dec)
    res = _generate(tmp_path, dec)
    assert res["status"] == "applied"
    dec_dir = tmp_path / "work" / VID / f"dec_{res['dec_hash']}"
    mp3 = dec_dir / "preview.mp3"
    assert res["preview"] == str(mp3)
    assert review_apply.is_complete(mp3)               # 产物 + .done 哨兵
    assert res["windows"] == {"intro": [0.0, 60.0], "dense": [150.0, 210.0]}
    # 两段各 60s(24k mono wav)
    for i in (1, 2):
        seg = dec_dir / f"preview_seg{i}.wav"
        with wave.open(str(seg), "rb") as w:
            assert abs(w.getnframes() / w.getframerate() - 60.0) < 0.1
            assert w.getframerate() == 24000 and w.getnchannels() == 1
    # 合成单元:两窗内全部单元(片头 5 + 密集窗 20;此决策无人 skip 全合成)
    man = dec_dir / VID / "tts" / "manifest.json"
    import json
    rows = json.loads(man.read_text(encoding="utf-8"))["lines"]
    assert len(rows) == 25
    assert {r["spk"] for r in rows} == {"SPK_00", "SPK_01"}
    assert fake_ff.count("libmp3lame") == 1            # 一次拼接编码
    assert res["units"] == {"synth": 25, "skip": 0}


def test_skip_speaker_uses_original_audio(tmp_path, fake_ff):
    dec = _dec(skip=True)
    _apply_decision(tmp_path, dec)
    work = tmp_path / "work"
    _write_wav(work / VID / "audio.wav", 24000, _positional_frames(TOTAL))
    res = _generate(tmp_path, dec)
    assert res["units"] == {"synth": 15, "skip": 10}
    dec_dir = work / VID / f"dec_{res['dec_hash']}"
    import json
    rows = json.loads(
        (dec_dir / VID / "tts" / "manifest.json").read_text(encoding="utf-8")
    )["lines"]
    assert {r["spk"] for r in rows} == {"SPK_00"}      # skip 单元不进合成
    # 切片内容 = 原片对应秒(每秒振幅不同,可按内容定位):
    # 密集句每 3s 一句,奇数句 SPK_01(skip)→ t = 153,159,...,207 共 10 段
    audio = _read_frames(work / VID / "audio.wav")
    expect = {audio[int(t * 24000):int((t + 1) * 24000)].tobytes()
              for t in (153 + 6 * k for k in range(10))}
    cuts = list((dec_dir / "preview_cuts").glob("*.wav"))
    assert len(cuts) == 10
    assert {_read_frames(c).tobytes() for c in cuts} == expect  # 逐帧一致(24k)
    assert review_apply.is_complete(dec_dir / "preview.mp3")


def test_retry_reuses_same_hash_and_only_fills_incomplete(tmp_path, fake_ff):
    dec = _dec(skip=True)
    _apply_decision(tmp_path, dec)
    work = tmp_path / "work"
    _write_wav(work / VID / "audio.wav", 24000, _positional_frames(TOTAL))
    r1 = _generate(tmp_path, dec)
    n_tts, n_enc = fake_ff.n_tts(), fake_ff.count("libmp3lame")
    mp3 = Path(r1["preview"])
    # 哨兵被删 = 小样不完整 → 重跑补产物,但合成单元 wav 按 manifest 复用
    Path(str(mp3) + ".done").unlink()
    r2 = _generate(tmp_path, dec)
    assert r2["status"] == "applied" and r2["dec_hash"] == r1["dec_hash"]
    assert fake_ff.n_tts() == n_tts                    # 不重跑 TTS(manifest 复用)
    assert fake_ff.count("libmp3lame") == n_enc + 1    # 只重拼小样
    assert review_apply.is_complete(mp3)
    # 哨兵齐备 → 整体复用,不再动任何 ffmpeg
    r3 = _generate(tmp_path, dec)
    assert r3["status"] == "reused"
    assert fake_ff.count("libmp3lame") == n_enc + 1


def test_ledger_registration_and_retry_idempotent(tmp_path, fake_ff):
    dec = _dec()
    _apply_decision(tmp_path, dec)
    lib = _make_library(tmp_path, dec)
    assert lib.get(BVID, PART)["status"] == ST_REVIEWED
    res = _generate(tmp_path, dec, library=lib)
    rec = lib.get(BVID, PART)
    assert rec["status"] == ST_PREVIEW                  # 已核对待合成 → 小样待听
    assert rec["preview_path"] == res["preview"]
    assert rec["preview_stale"] is False
    assert res["record"]["status"] == ST_PREVIEW
    # 重试(小样已完整)幂等:不重复转移、不抛 IllegalTransition
    res2 = _generate(tmp_path, dec, library=lib)
    assert res2["status"] == "reused"
    rec2 = lib.get(BVID, PART)
    assert rec2["status"] == ST_PREVIEW
    assert rec2["preview_path"] == res["preview"]


def test_missing_decision_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        _generate(tmp_path, _dec())


def test_decision_from_library_when_arg_omitted(tmp_path, fake_ff):
    dec = _dec()
    _apply_decision(tmp_path, dec)
    lib = _make_library(tmp_path, dec)
    res = preview.generate_preview(
        BVID, PART, None, library=lib, work_root=str(tmp_path / "work"),
        total_dur=TOTAL, mock=True)
    assert res["status"] == "applied"
    assert res["dec_hash"] == review_apply.dec_hash(dec)


# ---------- 真实 ffmpeg 端到端(无 ffmpeg 环境跳过;无 GPU/TTS,mock 静音) ----------

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="需要 ffmpeg")
def test_real_ffmpeg_end_to_end(tmp_path):
    dec = _dec(skip=True)
    _apply_decision(tmp_path, dec)
    work = tmp_path / "work"
    _write_wav(work / VID / "audio.wav", 24000, _positional_frames(TOTAL))
    lib = _make_library(tmp_path, dec)
    res = preview.generate_preview(
        BVID, PART, dec, library=lib, work_root=str(work),
        total_dur=TOTAL, mock=True)
    mp3 = Path(res["preview"])
    assert review_apply.is_complete(mp3)
    dur = probe_duration(mp3)
    assert 119.0 <= dur <= 123.0, f"小样应≈60+1+60s,实测 {dur}"
    assert lib.get(BVID, PART)["status"] == ST_PREVIEW
