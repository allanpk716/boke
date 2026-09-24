# -*- coding: utf-8 -*-
"""05 号票:小样生成器(D8 固定两段 / D13 小样门 / F11 密集窗 / F12 同 hash 复用)。

在决策应用器(票03)落出的 work/<id>/dec_<hash>/ 目录内生成试听小样
preview.mp3 = 片头 60s 窗 + 对话密集 60s 窗(段间 1s 静音,44.1k mono mp3)。
音色严格按该目录 voices.generated.yaml;skip 说话人单元不合成,直接 ffmpeg
切原片 audio.wav 对应段保留原声(U4 假设,小样如实呈现,F5)。

流程(generate_preview):
1. 密集窗定位:tagged.dec.srt 字幕条按 60s 滑窗(步长 5s)计覆盖条数取最大,
   默认排除片头 90s/片尾 60s;排除后无窗(分P太短/覆盖为 0)→ 去掉排除取
   全范围最密;仍无 → 取中点 60s(总时长<60s 即整段)。全程确定性,同输入同窗。
2. 两段合成:非 skip 单元一次交给 boke.synthesize.run(voices=决策目录配置,
   engine/mock 透传;只喂非 skip 单元的 srt = 对 run 的最小适配,不实现引擎);
   skip 单元切原片段规范到 24k mono。每窗取"整单元落入窗"的单元,时间轴平移
   到窗内局部坐标后交 boke.align_mix.build_track 拼出 ≤60s 的段 wav,
   ffmpeg concat 出 preview.mp3(两段各 ≤60s,段间 1s 静音)。
3. 哨兵与账本:preview.mp3 完整落盘后写 .done 哨兵(F12:半写入视为不存在),
   再经账本 preview_ready 事件登记小样路径→「小样待听」;重试幂等(已在该态
   且路径一致不重复转移)。
4. 复用:同一 dec_hash 目录内 preview.mp3 哨兵齐备即整体复用;合成单元 wav
   由 synthesize.run 自身的 manifest 判据复用(重试只补未完整产物)。

synthesize.run 的 tts 产物落 dec_<hash>/<id>/tts/(run 以 work_dir/<id> 定位,
传入 work_dir=dec_<hash> 即全部收在决策目录内,符合 F12)。
仅标准库 + boke 既有模块(PyYAML 读决策目录配置)。
"""
import json
import time
from pathlib import Path

from . import review_apply
from .align_mix import build_track
from .common import SubLine, parse_srt, probe_duration, run_ffmpeg, write_srt
from .library import EV_PREVIEW_READY, ST_PREVIEW, RecordNotFound
from .synthesize import TTS_SR, merge_units
from .synthesize import run as synth_run

WIN_SEC = 60.0        # 两段各 60s(D8)
STEP_SEC = 5.0        # 密集窗滑窗步长
HEAD_SEC = 90.0       # 密集窗默认排除片头(F11)
TAIL_SEC = 60.0       # 密集窗默认排除片尾(F11)
GAP_SEC = 1.0         # 两段之间静音
MP3_NAME = "preview.mp3"
CUTS_DIR = "preview_cuts"          # skip 单元原片切片目录(dec_<hash> 内)


# ---------------- 密集窗定位(F11,纯函数) ----------------

def _covered(subs, ws, we):
    """窗 [ws,we] 完整含住的字幕条数(与"整单元落入窗才合成"同一判据,
    密度高的窗即有料可取的窗)。eps 吸收滑窗步进浮点误差。"""
    eps = 1e-6
    return sum(1 for s in subs if s.t0 >= ws - eps and s.t1 <= we + eps)


def _best_window(subs, lo, hi):
    """[lo,hi] 内按步长滑 60s 窗,取覆盖条数最多者;并列取最靠前(确定性)。"""
    best_ws, best_n = None, -1
    if hi < lo:
        return best_ws, best_n
    for i in range(int(round((hi - lo) / STEP_SEC)) + 1):
        ws = lo + i * STEP_SEC
        n = _covered(subs, ws, ws + WIN_SEC)
        if n > best_n:
            best_ws, best_n = ws, n
    return best_ws, best_n


def _mid_window(total):
    """中点 60s 窗;总时长<60s 时夹取即整段 [0,total](任务书兜底三)。"""
    mid = float(total) / 2.0
    return (max(0.0, mid - WIN_SEC / 2.0), min(float(total), mid + WIN_SEC / 2.0))


def dense_window(subs, total):
    """密集窗:默认排除片头 90s/片尾 60s 取字幕最密 60s;排除后无窗 →
    去掉排除全范围取最密;仍无 → 中点 60s(总时长<60s 即整段)。
    确定性:同输入同窗。返回 (t0, t1)。"""
    total = float(total)
    if total < WIN_SEC:
        return _mid_window(total)
    hi = total - TAIL_SEC - WIN_SEC               # 默认:窗不得闯入排除区间
    if hi >= HEAD_SEC:
        ws, n = _best_window(subs, HEAD_SEC, hi)
        if n > 0:
            return (ws, ws + WIN_SEC)
    ws, n = _best_window(subs, 0.0, total - WIN_SEC)   # 兜底一:放宽排除
    if n > 0:
        return (ws, ws + WIN_SEC)
    return _mid_window(total)                          # 兜底二:中点


# ---------------- 小工具 ----------------

def _as_decision(decisions) -> dict:
    """入参容错:决策 JSON dict;list/tuple 时取第一个 dict(多分P提交由
    调用方保证顺序)。"""
    if isinstance(decisions, dict):
        return decisions
    if isinstance(decisions, (list, tuple)):
        for d in decisions:
            if isinstance(d, dict):
                return d
    raise ValueError("decisions 必须是核对决策 JSON(dict;list 时取第一个 dict)")


def _skip_speakers(voices_path, vid) -> set:
    """决策目录配置里的 skip 说话人(map 值带 skip:true,票03 约定)。"""
    import yaml
    cfg = yaml.safe_load(Path(voices_path).read_text(encoding="utf-8")) or {}
    for em in cfg.get("episode_maps", []):
        if em.get("file") and em["file"] in vid:
            m = em.get("map") or {}
            return {spk for spk, v in m.items()
                    if isinstance(v, dict) and v.get("skip")}
    return set()


def _total_duration(total_dur, audio_src, rec, subs) -> float:
    """分P总时长:显式参数 > 探测 audio.wav > 账本 duration > 字幕末尾。"""
    if total_dur is not None and float(total_dur) > 0:
        return float(total_dur)
    if audio_src is not None and Path(audio_src).exists():
        d = probe_duration(Path(audio_src))
        if d > 0:
            return d
    if rec is not None:
        try:
            v = float(rec.get("duration") or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v
    if subs:
        return max(s.t1 for s in subs)
    raise RuntimeError("无法确定分P时长(无 total_dur/audio.wav/账本 duration/字幕)")


def _cut_original(audio_src: Path, t0: float, t1: float, dst: Path):
    """skip 单元保留原声:ffmpeg 切原片对应段,规范到 24k mono s16(可混音)。"""
    dst.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg(["-ss", f"{max(0.0, t0):.3f}", "-i", str(audio_src),
                "-t", f"{max(0.05, t1 - t0):.3f}",
                "-ac", "1", "-ar", str(TTS_SR), "-c:a", "pcm_s16le", str(dst)])


def _synth_units(dec_dir: Path, vid: str, win_units, skip_spk,
                 voices: Path, engine, mock, force) -> list:
    """两窗内的非 skip 单元一次交给 boke.synthesize.run(复用引擎调度,不重实现)。

    只喂非 skip 单元重写的 srt(run 不认 skip,拦截在 preview 侧完成);
    窗外单元不合成(小样用不到,不烧 GPU)。manifest 行即真实合成产物
    (t0/t1 绝对时间),供两窗分别取用。
    """
    todo = [u for u in win_units if u["spk"] not in skip_spk]
    manifest = dec_dir / vid / "tts" / "manifest.json"
    if todo:
        srt = dec_dir / "preview.units.srt"
        write_srt(srt, [SubLine(idx=i, t0=u["t0"], t1=u["t1"],
                                text=f"[{u['spk']}] {u['text']}")
                        for i, u in enumerate(todo, 1)])
        synth_run(vid, work_dir=str(dec_dir), srt=str(srt),
                  voices_yaml=str(voices), engine=engine, mock=mock, force=force)
    if manifest.exists():
        return json.loads(manifest.read_text(encoding="utf-8")).get("lines", [])
    return []


def _seg_lines(rows, skip_units, ws, we, audio_src, cuts_dir) -> list:
    """取整单元落入 [ws,we] 的单元,时间平移到窗内局部坐标(build_track 适配)。

    非 skip 单元用 manifest 行;skip 单元切原片段当该单元的 wav——
    对 build_track 而言两者都是一条 24k mono 素材行,拼接逻辑零改动。
    """
    eps = 1e-6

    def contained(t0, t1):
        return t0 >= ws - eps and t1 <= we + eps

    lines = []
    for r in rows:
        if contained(float(r["t0"]), float(r["t1"])):
            lines.append({"t0": float(r["t0"]) - ws, "t1": float(r["t1"]) - ws,
                          "wav": r["wav"], "skip": False})
    for u in skip_units:
        if contained(u["t0"], u["t1"]):
            wav = cuts_dir / f"u{int(u['idx']):04d}.wav"
            _cut_original(audio_src, u["t0"], u["t1"], wav)
            lines.append({"t0": u["t0"] - ws, "t1": u["t1"] - ws,
                          "wav": str(wav), "skip": True})
    lines.sort(key=lambda l: l["t0"])
    return lines


def _encode(segs, mp3: Path):
    """两段 wav + 1s 静音 → 44.1k mono mp3(libmp3lame -q:a 4)。"""
    run_ffmpeg(["-i", str(segs[0]), "-i", str(segs[1]),
                "-f", "lavfi", "-t", f"{GAP_SEC:.3f}",
                "-i", "anullsrc=r=44100:cl=mono",
                "-filter_complex", "[0:a][2:a][1:a]concat=n=3:v=0:a=1[out]",
                "-map", "[out]", "-ar", "44100", "-ac", "1",
                "-c:a", "libmp3lame", "-q:a", "4", str(mp3)])


def _register(lib, bvid, part, mp3: Path):
    """账本登记:preview_ready 事件,小样路径→「小样待听」。

    重试幂等:已在该态且路径一致不重复转移(否则该 (状态,事件) 组合非法)。
    """
    rec = lib.get(bvid, part)
    if rec is None:
        raise RecordNotFound(f"片库无 {bvid} P{part},无法登记小样")
    if rec["status"] == ST_PREVIEW and rec.get("preview_path") == str(mp3):
        return rec
    return lib.transition(bvid, part, EV_PREVIEW_READY, preview_path=str(mp3))


# ---------------- 公开入口 ----------------

def generate_preview(bvid, part, decisions=None, *, library=None,
                     work_root="work", video=None, total_dur=None,
                     audio=None, engine="auto", mock=False, force=False) -> dict:
    """生成分P试听小样并登记账本(D13 小样门的产物侧)。

    bvid+part 定位账本记录;决策取 decisions(核对决策 JSON,与提交核对一致;
    传 list 取第一个 dict),缺省回退货本记录的 review。目录 id(与票03 一致)
    取 video stem > 账本 media_path stem > <bvid>_P<part>;dec_<hash> 目录由
    决策内容哈希定位(同 hash 复用,F12)。总时长取 total_dur > 探测
    audio.wav > 账本 duration > 字幕末尾。

    返回 {status: applied|reused, bvid, part, dec_hash, dec_dir, preview,
    windows: {intro, dense}, total_dur, units: {synth, skip}, record};
    library=None 只产产物不动账本。决策目录缺产物抛 FileNotFoundError。
    """
    rec = library.get(bvid, part) if library is not None else None
    decision = _as_decision(decisions) if decisions is not None \
        else (rec or {}).get("review")
    if not isinstance(decision, dict):
        raise ValueError("缺核对决策:decisions 未传且账本记录无 review")

    h = review_apply.dec_hash(decision)
    if video:
        vid = Path(video).stem
    elif rec is not None and rec.get("media_path"):
        vid = Path(rec["media_path"]).stem
    else:
        vid = f"{bvid}_P{int(part)}"
    dec_dir = Path(work_root) / vid / f"dec_{h}"
    tagged = dec_dir / review_apply.TAGGED_NAME
    voices = dec_dir / review_apply.VOICES_NAME
    for p, what in ((tagged, "归人决策字幕 tagged.dec.srt"),
                    (voices, "音色配置 voices.generated.yaml")):
        if not p.exists():
            raise FileNotFoundError(
                f"缺{what}:{p} —— 先对该分P应用核对决策(boke.review_apply)")

    mp3 = dec_dir / MP3_NAME
    status = "reused" if (review_apply.is_complete(mp3) and not force) \
        else "applied"

    subs = parse_srt(tagged)
    units = merge_units(subs)
    skip_spk = _skip_speakers(voices, vid)
    skip_units = [u for u in units if u["spk"] in skip_spk]
    audio_src = Path(audio) if audio else Path(work_root) / vid / "audio.wav"
    total = _total_duration(total_dur, audio_src, rec, subs)
    intro = (0.0, min(WIN_SEC, total))               # 片头窗 [0,60s](D8)
    dense = dense_window(subs, total)                # 密集窗(F11)

    n_synth = n_skip = 0
    if status == "applied":
        if skip_units and not audio_src.exists():
            raise FileNotFoundError(
                f"缺原片音频 {audio_src} —— skip 说话人保留原声需要它(U4)")
        eps = 1e-6

        def in_win(u, w):
            return u["t0"] >= w[0] - eps and u["t1"] <= w[1] + eps

        win_units = [u for u in units if in_win(u, intro) or in_win(u, dense)]
        rows = _synth_units(dec_dir, vid, win_units, skip_spk, voices,
                            engine, mock, force)
        cuts_dir = dec_dir / CUTS_DIR
        segs = []
        for wi, (ws, we) in enumerate((intro, dense), 1):
            lines = _seg_lines(rows, skip_units, ws, we, audio_src, cuts_dir)
            n_synth += sum(1 for l in lines if not l["skip"])
            n_skip += sum(1 for l in lines if l["skip"])
            seg = dec_dir / f"preview_seg{wi}.wav"
            build_track(lines, we - ws, seg)         # 复用 align_mix 拼接逻辑
            segs.append(seg)
        _encode(segs, mp3)
        # 哨兵最后写:mp3 完整落盘后才算产物完整(F12)
        review_apply._atomic_write(Path(str(mp3) + ".done"),
                                   time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")

    after = _register(library, bvid, part, mp3) if library is not None else None
    return {"status": status, "bvid": bvid, "part": int(part),
            "dec_hash": h, "dec_dir": str(dec_dir), "preview": str(mp3),
            "windows": {"intro": [round(intro[0], 3), round(intro[1], 3)],
                        "dense": [round(dense[0], 3), round(dense[1], 3)]},
            "total_dur": round(total, 3),
            "units": {"synth": n_synth, "skip": n_skip},
            "record": after}
