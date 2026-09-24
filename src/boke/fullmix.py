# -*- coding: utf-8 -*-
"""全片合成编排(06 号票):小样过门后自动全片合成 + 断点重试。

流程(spec 20260924 User Stories 13/14;前提:分P 状态=小样待听):
  1. 小样门:小样在且未作废 → preview_pass,状态 小样待听→全片合成中+busy;
  2. 经 boke.pipeline 调 synthesize+mix:voices 用决策目录
     work/<id>/dec_<hash>/voices.generated.yaml,产物落同一 dec_<hash> 目录内
     (work_dir 直接指向决策目录,产物嵌套其 <id>/ 子目录;hash 变更即换目录,
     旧成品天然不再被引用,F12);
  3. 完成(哨兵)→ 成品路径登记(output_path),full_done,状态→已交付;
  4. 异常 → 失败态(stage+error);retry 回全片合成中后再入本入口,按
     .done 哨兵复用同 dec_hash 目录内已完整产物,无哨兵半产物重跑(F6/F12);
  5. skip 说话人(U4 保留原声):过滤其字幕行不送合成(省 GPU),混音后把
     skip 时间窗替换为原片音频(原声拼接)。票05 的 preview 侧 skip 支持尚未
     落地(工作树无 preview.py),本模块自含该逻辑;其落地后可换公共函数。

与 boke.pipeline 的关系:pipeline 的阶段跳过是"文件存在即跳过"且不含哨兵,
本模块只在产物不完整时才调它并恒传 force=True;完整性由本模块补写的
.done 哨兵(沿用 review_apply 约定:产物完整落盘后最后写)判定,
半产物不会被断点续跑误认。

测试桩点:pipeline.run_pipeline(两阶段)与 run_ffmpeg(原声拼接/出成品)。
仅标准库 + boke 既有模块(PyYAML 仅解析 voices.generated.yaml 时惰性导入)。
"""
import array
import os
import re
import tempfile
import time
import wave
from pathlib import Path

from . import pipeline
from .common import parse_srt, run_ffmpeg, write_srt
from .library import (
    EV_FAIL, EV_FULL_DONE, EV_PREVIEW_PASS, ST_PREVIEW, ST_SYNTHESIZING,
    Library,
)
from .review_apply import TAGGED_NAME, VOICES_NAME, dec_hash, is_complete
from .synthesize import TTS_SR

STAGE_PREPARE = "prepare"      # 前置条件(媒体/决策目录产物)缺失
STAGE_SYNTH = "synthesize"     # 逐句合成
STAGE_MIX = "mix"              # 拼轨混音(含 skip 原声拼接)

_SPK_PREFIX = re.compile(r"\[(SPK_\d+)\]")   # 与 synthesize.merge_units 同口径


# ---------------- 小工具 ----------------

def _line_spk(text: str) -> str:
    """字幕行 → 说话人 id(无前缀默认 SPK_00,与 merge_units 一致)。"""
    m = _SPK_PREFIX.match(text.strip())
    return m.group(1) if m else "SPK_00"


def _atomic_text(path: Path, content: str):
    """原子写:临时文件写完 fsync 再 os.replace(同 review_apply 约定)。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent),
                               prefix="." + path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _seal(path: Path):
    """产物完整落盘后补 .done 哨兵(F12:无哨兵即视为不存在)。"""
    _atomic_text(Path(str(path) + ".done"),
                 time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")


def _fail(lib, bvid, part, stage, error) -> dict:
    rec = lib.transition(bvid, part, EV_FAIL, stage=stage, error=error)
    return {"ok": False, "stage": stage, "error": error, "record": rec}


# ---------------- skip 说话人(F12 决策目录内自含处理) ----------------

def _skip_speakers(voices_yaml: Path, vid: str) -> set:
    """voices.generated.yaml 里该分P episode_map 中 skip:true 的说话人集。

    episode 匹配规则与 synthesize.run 一致(em["file"] 是 vid 的子串)。
    """
    import yaml
    cfg = yaml.safe_load(Path(voices_yaml).read_text(encoding="utf-8")) or {}
    for em in cfg.get("episode_maps") or []:
        if em.get("file") and em["file"] in vid:
            m = em.get("map") or {}
            return {sid for sid, v in m.items()
                    if isinstance(v, dict) and v.get("skip")}
    return set()


def _filter_srt(tagged: Path, dst: Path, skip: set):
    """合成输入 srt:剔除 skip 说话人的行(不送合成),其余原样重编号。"""
    subs = parse_srt(tagged)
    write_srt(dst, [s for s in subs if _line_spk(s.text) not in skip])


def _skip_spans(tagged: Path, skip: set) -> list:
    """skip 说话人的时间窗(合并相邻/重叠),供原声拼接。"""
    spans = sorted((s.t0, s.t1) for s in parse_srt(tagged)
                   if _line_spk(s.text) in skip)
    merged = []
    for t0, t1 in spans:
        if merged and t0 <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], t1)
        else:
            merged.append([t0, t1])
    return [(a, b) for a, b in merged if b > a]


def _slice_padded(buf: array.array, a: int, b: int) -> array.array:
    """取 buf[a:b] 帧,越界部分补零(源轨比窗口短时垫静音)。"""
    out = array.array("h")
    lo, hi = max(0, a), min(len(buf), b)
    if hi > lo:
        out.extend(buf[lo:hi])
    if len(out) < b - a:
        out.extend(array.array("h", bytes(((b - a) - len(out)) * 2)))
    return out


def _stitch_original(dub_wav: Path, orig_src, spans, m4a_out: Path,
                     tmp_dir: Path):
    """skip 单元走原声拼接(U4):配音轨上 skip 窗口的位置换回原片音频。

    align_mix 的配音轨按绝对时间轴落单元、静音垫满,skip 窗口在该轨上是
    静音;把窗口区间替换为原片音频(ffmpeg 统一转 24k mono s16 后按帧切片)
    即得成品轨,再按 align_mix 同参 loudnorm 出 m4a。外部依赖仅 run_ffmpeg。
    """
    resampled = tmp_dir / "orig24k.wav"
    run_ffmpeg(["-i", str(orig_src), "-vn", "-ac", "1", "-ar", str(TTS_SR),
                "-c:a", "pcm_s16le", str(resampled)])
    with wave.open(str(dub_wav), "rb") as w:
        if w.getframerate() != TTS_SR or w.getnchannels() != 1:
            raise RuntimeError(f"{dub_wav} 不是 {TTS_SR}Hz mono,无法拼接")
        dub = array.array("h", w.readframes(w.getnframes()))
    with wave.open(str(resampled), "rb") as w:
        orig = array.array("h", w.readframes(w.getnframes()))

    sr = TTS_SR
    last = int(round(spans[-1][1] * sr)) if spans else 0
    total = max(len(dub), len(orig), last)
    out = array.array("h")
    cursor = 0
    for t0, t1 in spans:
        a, b = int(round(t0 * sr)), int(round(t1 * sr))
        if b <= max(a, cursor):
            continue
        a = max(a, cursor)
        out.extend(_slice_padded(dub, cursor, a))     # 窗前:配音轨
        out.extend(_slice_padded(orig, a, b))         # 窗内:原片音频
        cursor = b
    out.extend(_slice_padded(dub, cursor, len(dub)))  # 尾段:配音轨
    if len(out) < total:
        out.extend(array.array("h", bytes((total - len(out)) * 2)))

    final_wav = tmp_dir / "full_mix_final.wav"
    with wave.open(str(final_wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(out.tobytes())
    run_ffmpeg(["-i", str(final_wav),
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-ar", "44100", "-ac", "1", "-c:a", "aac", "-b:a", "128k",
                str(m4a_out)])
    return final_wav


# ---------------- 主入口 ----------------

def run_full_mix(bvid, part, *, lib=None, work_dir="work") -> dict:
    """小样过门 → 全片合成 → 交付(流程见模块 docstring)。

    lib/work_dir 可注入(服务端用默认,单测用 tmp_path)。返回
    {ok: True, record, output_path, dec_hash, dec_dir, skip_speakers}
    或 {ok: False, stage, error[, record]};状态/小样门/媒体不对时就地拒绝,
    不动账本。断点重试:失败态经账本 retry 回「全片合成中」后重入本函数,
    已带 .done 哨兵的产物直接复用,无哨兵半产物整段重跑。
    """
    lib = lib if lib is not None else Library()
    work_dir = Path(work_dir)

    rec = lib.get(bvid, part)
    if rec is None:
        return {"ok": False, "stage": "",
                "error": f"片库里没有 {bvid} P{part},请先导入。"}
    fresh = rec["status"] == ST_PREVIEW
    if not fresh and rec["status"] != ST_SYNTHESIZING:
        return {"ok": False, "stage": "",
                "error": f"{bvid} P{part} 当前是「{rec['status']}」,"
                         "只有「小样待听」(点过)或「全片合成中」"
                         "(断点重试)能跑全片合成。"}

    media = rec.get("media_path")
    if not media or not Path(media).exists():
        # 小样待听态没有失败边,媒体丢了就地拒绝不吞状态;
        # 重入态已在合成中,走失败态可重试
        msg = f"媒体文件不存在:{media}(全片合成需要原片)。"
        if fresh:
            return {"ok": False, "stage": "", "error": msg}
        return _fail(lib, bvid, part, STAGE_PREPARE, msg)

    if fresh:
        pp = rec.get("preview_path")
        if rec.get("preview_stale"):
            return {"ok": False, "stage": "",
                    "error": "小样已作废(决策改过),请重出小样后再点过。"}
        if not pp or not Path(pp).exists():
            return {"ok": False, "stage": "",
                    "error": f"找不到小样文件({pp or '未生成'}),小样门未过。"}
        rec = lib.transition(bvid, part, EV_PREVIEW_PASS)  # →全片合成中+busy
    else:
        lib.set_busy(bvid, part, True)                     # 断点重入恢复 busy
        rec = lib.get(bvid, part)

    h = rec.get("dec_hash")
    if not h and isinstance(rec.get("review"), dict):
        h = dec_hash(rec["review"])
    if not h:
        return _fail(lib, bvid, part, STAGE_PREPARE,
                     "记录缺决策版本(dec_hash)——先在核对台提交核对决策。")
    vid = Path(media).stem
    dec_dir = work_dir / vid / f"dec_{h}"
    voices = dec_dir / VOICES_NAME
    tagged = dec_dir / TAGGED_NAME
    missing = [p.name for p in (voices, tagged) if not is_complete(p)]
    if missing:
        return _fail(lib, bvid, part, STAGE_PREPARE,
                     f"决策目录产物不完整({dec_dir}):缺 {'、'.join(missing)}"
                     "——先应用核对决策。")

    vid_dir = dec_dir / vid              # 产物落决策目录内(hash 隔离,F12)
    tts_manifest = vid_dir / "tts" / "manifest.json"
    final_m4a = vid_dir / f"{vid}.m4a"
    skip = set()
    stage = STAGE_SYNTH
    try:
        skip = _skip_speakers(voices, vid)
        if not is_complete(tts_manifest):
            vid_dir.mkdir(parents=True, exist_ok=True)
            _filter_srt(tagged, vid_dir / "tagged.srt", skip)
            pipeline.run_pipeline(media, stage=STAGE_SYNTH,
                                  work_dir=str(dec_dir), voices=str(voices),
                                  force=True)
            if not tts_manifest.exists():
                raise RuntimeError(f"合成产物缺失:{tts_manifest}")
            _seal(tts_manifest)
        if not is_complete(final_m4a):
            stage = STAGE_MIX
            pipeline.run_pipeline(media, stage=STAGE_MIX, work_dir=str(dec_dir),
                                  out_dir=str(vid_dir), force=True)
            if not final_m4a.exists():
                raise RuntimeError(f"混音产物缺失:{final_m4a}")
            if skip:
                _stitch_original(vid_dir / "full_mix.wav",
                                 (rec.get("artifacts") or {}).get("audio")
                                 or media,
                                 _skip_spans(tagged, skip),
                                 final_m4a, vid_dir)
            _seal(final_m4a)
    except Exception as e:
        return _fail(lib, bvid, part, stage, str(e) or repr(e))

    rec = lib.transition(bvid, part, EV_FULL_DONE, output_path=str(final_m4a))
    return {"ok": True, "record": rec, "output_path": str(final_m4a),
            "dec_hash": h, "dec_dir": str(dec_dir),
            "skip_speakers": sorted(skip)}
