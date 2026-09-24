# -*- coding: utf-8 -*-
"""全片合成编排单测(06 号票)。

全桩化(tmp_path + monkeypatch):不调真实 GPU 合成、不跑真实 ffmpeg。
桩点:boke.pipeline.run_pipeline(两阶段,造假产物+可指定失败点)/
boke.fullmix.run_ffmpeg(原声拼接的重采样与 loudnorm 出成品)。
覆盖验收:状态流转(小样待听→全片合成中→已交付)与失败登记、
哨兵断点(带哨兵不重跑/无哨兵半产物重跑/全齐直通交付)、
决策 hash 变更只读新目录、skip 说话人拦截(过滤合成输入+原声拼接)。
"""
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boke import fullmix, pipeline   # noqa: E402
from boke.common import SubLine, write_srt    # noqa: E402
from boke.library import (    # noqa: E402
    EV_ANALYSIS_DONE, EV_DOWNLOAD_DONE, EV_PREVIEW_READY, EV_REVIEW_SUBMIT,
    EV_RETRY, ST_DELIVERED, ST_FAILED, ST_PREVIEW,
    ST_REVIEWED, ST_SYNTHESIZING, Library,
)
from boke.review_apply import (TAGGED_NAME, dec_hash, is_complete,   # noqa: E402
                               render_full_config)

BV = "BV1fullmix01"
VID = f"{BV}_P1"              # 媒体文件 <BV>_P<n>.mp4,stem 即工作目录键
TTS_SR = 24000


# ---------- 夹具 ----------

def _write_wav(path, seconds=1.0, sr=TTS_SR):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(b"\x00\x00" * int(sr * seconds))


def _decision(preset="zh-CN-YunxiNeural", skip_spk00=False):
    """最小核对决策:preset 单说话人;skip 变体含一个 skip 说话人。"""
    if skip_spk00:
        return {"verdict": "dub", "speakers": [
            {"id": "SPK_00", "note": "原声保留", "voice": "skip"},
            {"id": "SPK_01", "note": "嘉宾", "voice": "preset",
             "preset_voice": preset}]}
    return {"verdict": "dub", "speakers": [
        {"id": "SPK_00", "note": "主持", "voice": "preset",
         "preset_voice": preset}]}


def _plain_subs():
    return [SubLine(1, 0.5, 1.5, "[SPK_00] 欢迎收看"),
            SubLine(2, 3.0, 4.0, "[SPK_00] 我们开始")]


def _skip_subs():
    """skip 场景的 tagged.dec.srt:SPK_00 两个窗(0.5-1.5 / 3.0-4.0)+ 嘉宾一句。"""
    return [SubLine(1, 0.5, 1.5, "[SPK_00] 欢迎收看"),
            SubLine(2, 1.6, 2.8, "[SPK_01] 我是嘉宾"),
            SubLine(3, 3.0, 4.0, "[SPK_00] 我们开始")]


def _make_dec_dir(tmp_path, decision, subs):
    """经 review_apply.render_full_config 构造带哨兵的决策目录(真格式)。"""
    r = render_full_config(decision, VID, tmp_path / "work", part=1)
    assert r["status"] == "applied"
    dec_dir = Path(r["dec_dir"])
    write_srt(dec_dir / TAGGED_NAME, subs)
    (dec_dir / (TAGGED_NAME + ".done")).write_text("ok", encoding="utf-8")
    return dec_dir


def _mk_record(tmp_path, decision, media=True, preview=True):
    """造一条小样待听记录(全链事件推进),返回 (lib, media_path)。"""
    lib = Library(tmp_path / "library.json")
    media_path = tmp_path / "media" / f"{VID}.mp4"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    if media:
        media_path.write_bytes(b"fake mp4")
    lib.add(BV, 1, title="测试期", media_path=str(media_path))
    lib.transition(BV, 1, EV_DOWNLOAD_DONE, media_path=str(media_path))
    lib.transition(BV, 1, EV_ANALYSIS_DONE)
    lib.transition(BV, 1, EV_REVIEW_SUBMIT, review=decision)
    lib.update(BV, 1, dec_hash=dec_hash(decision))
    if preview:
        p = tmp_path / "work" / VID / "preview_sample.wav"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"fake preview")
        lib.transition(BV, 1, EV_PREVIEW_READY, preview_path=str(p))
    return lib, media_path


class PipeSpy:
    """pipeline.run_pipeline 桩:记录调用、按阶段造假产物、可指定失败点。

    synthesize → work_dir/<vid>/tts/manifest.json;
    mix → out_dir/<vid>.m4a + work_dir/<vid>/full_mix.wav(24k mono 1s,
    供原声拼接测试)。顺带记录每次调用时账本的状态/busy。
    """

    def __init__(self, lib=None, fail_at=None):
        self.calls = []
        self.status_seen = []
        self.busy_seen = []
        self.fail_at = fail_at
        self.lib = lib

    def __call__(self, video, stage="all", work_dir="work", out_dir="out",
                 voices=None, mock=False, tts_engine="auto", fps=2.0,
                 force=False, max_speakers=6):
        self.calls.append({"stage": stage, "video": str(video),
                           "work_dir": Path(work_dir),
                           "out_dir": Path(out_dir),
                           "voices": Path(voices) if voices else None,
                           "force": force})
        if self.lib is not None:
            rec = self.lib.get(BV, 1)
            self.status_seen.append(rec["status"])
            self.busy_seen.append(rec["busy"])
        vid = Path(video).stem
        # 先落产物再失败:模拟"产物写了但哨兵没补"的中途崩溃
        if stage == "synthesize":
            tdir = Path(work_dir) / vid / "tts"
            tdir.mkdir(parents=True, exist_ok=True)
            (tdir / "manifest.json").write_text("{}", encoding="utf-8")
        elif stage == "mix":
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / f"{vid}.m4a").write_bytes(b"dub-only m4a")
            _write_wav(Path(work_dir) / vid / "full_mix.wav", seconds=1.0)
        if self.fail_at == stage:
            raise RuntimeError(f"{stage} 崩了")
        return {"stage": stage}


def _install_ffmpeg(monkeypatch, orig_seconds=2.0):
    """fullmix.run_ffmpeg 桩:重采样调用(orig24k.wav)造 24k 假原片;
    loudnorm 调用(成品 m4a)写标记字节,验证拼接产物覆盖了纯配音版。"""
    calls = []

    def fake(args, check=True):
        out = Path(args[-1])
        calls.append((args[0], out.name))
        out.parent.mkdir(parents=True, exist_ok=True)
        if out.name == "orig24k.wav":
            _write_wav(out, seconds=orig_seconds)
        else:
            out.write_bytes(b"stitched m4a")

    monkeypatch.setattr(fullmix, "run_ffmpeg", fake)
    return calls


def _vid_dir(tmp_path, decision):
    return tmp_path / "work" / VID / f"dec_{dec_hash(decision)}" / VID


# ---------- 状态流转与登记 ----------

def test_happy_path_state_flow_and_registration(monkeypatch, tmp_path):
    dec = _decision()
    lib, media = _mk_record(tmp_path, dec)
    _make_dec_dir(tmp_path, dec, _plain_subs())
    spy = PipeSpy(lib=lib)
    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    monkeypatch.setattr(fullmix, "run_ffmpeg",
                        lambda *a, **kw: (_ for _ in ()).throw(
                            AssertionError("无 skip 说话人不应触 ffmpeg")))

    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")

    assert r["ok"] is True
    h = dec_hash(dec)
    # 进 pipeline 时已 全片合成中(preview_pass 先落账),收尾 已交付
    assert spy.status_seen == [ST_SYNTHESIZING, ST_SYNTHESIZING]
    assert spy.busy_seen == [True, True]
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_DELIVERED and rec["busy"] is False
    assert rec["error"] is None and rec["failed_stage"] is None
    # 成品路径登记在决策目录内
    expect = tmp_path / "work" / VID / f"dec_{h}" / VID / f"{VID}.m4a"
    assert Path(rec["output_path"]) == expect
    assert r["output_path"] == str(expect)
    # 两阶段调用:work_dir 指向决策目录,voices 是决策目录里的配置,恒 force
    assert [c["stage"] for c in spy.calls] == ["synthesize", "mix"]
    for c in spy.calls:
        assert c["work_dir"] == tmp_path / "work" / VID / f"dec_{h}"
        assert c["force"] is True
    assert spy.calls[0]["voices"] == \
        tmp_path / "work" / VID / f"dec_{h}" / "voices.generated.yaml"
    assert spy.calls[1]["out_dir"] == tmp_path / "work" / VID / f"dec_{h}" / VID
    # 哨兵齐:合成清单与成品
    assert is_complete(expect)
    assert is_complete(vid_dir_p := tmp_path / "work" / VID / f"dec_{h}" / VID
                       / "tts" / "manifest.json")
    # 合成输入 = tagged.dec.srt 原样(无 skip 不过滤)
    tagged_copy = tmp_path / "work" / VID / f"dec_{h}" / VID / "tagged.srt"
    assert "[SPK_00] 欢迎收看" in tagged_copy.read_text(encoding="utf-8")


def test_refuses_when_not_awaiting_preview(tmp_path):
    dec = _decision()
    lib, _ = _mk_record(tmp_path, dec, preview=False)   # 停在 已核对待合成
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is False and "小样待听" in r["error"]
    assert lib.get(BV, 1)["status"] == ST_REVIEWED       # 状态没动


def test_refuses_stale_preview(tmp_path):
    dec = _decision()
    lib, _ = _mk_record(tmp_path, dec)
    lib.update(BV, 1, preview_stale=True)
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is False and "作废" in r["error"]
    assert lib.get(BV, 1)["status"] == ST_PREVIEW        # 未过门,不吞状态


def test_refuses_when_media_missing(tmp_path):
    dec = _decision()
    lib, _ = _mk_record(tmp_path, dec, media=False)
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is False and "媒体文件不存在" in r["error"]
    assert lib.get(BV, 1)["status"] == ST_PREVIEW


def test_refuses_unknown_record(tmp_path):
    lib = Library(tmp_path / "library.json")
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is False and "请先导入" in r["error"]


# ---------- 失败登记 ----------

def test_missing_dec_artifacts_fails_prepare(tmp_path, monkeypatch):
    dec = _decision()
    lib, _ = _mk_record(tmp_path, dec)
    dec_dir = tmp_path / "work" / VID / f"dec_{dec_hash(dec)}"
    dec_dir.mkdir(parents=True)
    (dec_dir / "voices.generated.yaml").write_text("global: {}\n",
                                                   encoding="utf-8")
    # voices 无 .done 哨兵、tagged.dec.srt 整个缺失 → 前置不完整
    spy = PipeSpy(lib=lib)
    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is False and r["stage"] == "prepare"
    assert spy.calls == []                        # 没碰 pipeline
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_FAILED
    assert rec["failed_from"] == ST_SYNTHESIZING
    assert rec["failed_stage"] == "prepare"
    assert rec["busy"] is False


def test_synthesize_failure_registers_stage_and_error(tmp_path, monkeypatch):
    dec = _decision()
    lib, _ = _mk_record(tmp_path, dec)
    _make_dec_dir(tmp_path, dec, _plain_subs())
    spy = PipeSpy(lib=lib, fail_at="synthesize")
    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is False and r["stage"] == "synthesize"
    assert "synthesize 崩了" in r["error"]
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_FAILED and rec["failed_stage"] == "synthesize"
    assert rec["failed_from"] == ST_SYNTHESIZING and rec["busy"] is False
    # 半产物:manifest 在但无哨兵(断点续跑时不得误认)
    man = _vid_dir(tmp_path, dec) / "tts" / "manifest.json"
    assert man.exists() and not is_complete(man)


def test_mix_failure_registers_stage(tmp_path, monkeypatch):
    dec = _decision()
    lib, _ = _mk_record(tmp_path, dec)
    _make_dec_dir(tmp_path, dec, _plain_subs())
    spy = PipeSpy(lib=lib, fail_at="mix")
    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is False and r["stage"] == "mix"
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_FAILED and rec["failed_stage"] == "mix"
    assert rec["failed_from"] == ST_SYNTHESIZING


# ---------- 断点重试(哨兵判据) ----------

def test_retry_reuses_sentineled_synthesize(tmp_path, monkeypatch):
    dec = _decision()
    lib, _ = _mk_record(tmp_path, dec)
    _make_dec_dir(tmp_path, dec, _plain_subs())
    bad = PipeSpy(lib=lib, fail_at="mix")
    monkeypatch.setattr(pipeline, "run_pipeline", bad)
    assert fullmix.run_full_mix(BV, 1, lib=lib,
                                work_dir=tmp_path / "work")["ok"] is False
    lib.transition(BV, 1, EV_RETRY)               # 失败 → 全片合成中
    good = PipeSpy(lib=lib)
    monkeypatch.setattr(pipeline, "run_pipeline", good)
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is True
    assert [c["stage"] for c in good.calls] == ["mix"]   # 哨兵齐的合成不重跑
    assert good.busy_seen == [True]                      # 重入恢复 busy
    assert lib.get(BV, 1)["status"] == ST_DELIVERED
    assert lib.get(BV, 1)["error"] is None


def test_reruns_products_without_sentinel(tmp_path, monkeypatch):
    dec = _decision()
    lib, _ = _mk_record(tmp_path, dec)
    _make_dec_dir(tmp_path, dec, _plain_subs())
    bad = PipeSpy(lib=lib, fail_at="mix")
    monkeypatch.setattr(pipeline, "run_pipeline", bad)
    fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    # 抹掉哨兵模拟半产物(manifest 在但视为不存在)
    man_done = _vid_dir(tmp_path, dec) / "tts" / "manifest.json.done"
    man_done.unlink()
    lib.transition(BV, 1, EV_RETRY)
    good = PipeSpy(lib=lib)
    monkeypatch.setattr(pipeline, "run_pipeline", good)
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is True
    # 无哨兵半产物重跑:合成从头再来,再混音
    assert [c["stage"] for c in good.calls] == ["synthesize", "mix"]
    assert lib.get(BV, 1)["status"] == ST_DELIVERED


def test_unsentineled_mix_rerun_while_synthesize_reused(tmp_path, monkeypatch):
    """合成哨兵齐 + 成品无哨兵:只重跑 mix 段。"""
    dec = _decision()
    lib, _ = _mk_record(tmp_path, dec)
    _make_dec_dir(tmp_path, dec, _plain_subs())
    vid_dir = _vid_dir(tmp_path, dec)
    (vid_dir / "tts").mkdir(parents=True)
    (vid_dir / "tts" / "manifest.json").write_text("{}", encoding="utf-8")
    (vid_dir / "tts" / "manifest.json.done").write_text("ok", encoding="utf-8")
    (vid_dir / f"{VID}.m4a").write_bytes(b"half m4a")   # 有文件无哨兵
    spy = PipeSpy(lib=lib)
    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is True
    assert [c["stage"] for c in spy.calls] == ["mix"]
    assert lib.get(BV, 1)["status"] == ST_DELIVERED


def test_all_sentineled_products_skip_pipeline(tmp_path, monkeypatch):
    """两段产物齐且带哨兵(交付前崩过):直通登记交付,不碰 pipeline。"""
    dec = _decision()
    lib, _ = _mk_record(tmp_path, dec)
    _make_dec_dir(tmp_path, dec, _plain_subs())
    vid_dir = _vid_dir(tmp_path, dec)
    (vid_dir / "tts").mkdir(parents=True)
    (vid_dir / "tts" / "manifest.json").write_text("{}", encoding="utf-8")
    (vid_dir / "tts" / "manifest.json.done").write_text("ok", encoding="utf-8")
    (vid_dir / f"{VID}.m4a").write_bytes(b"m4a")
    (vid_dir / f"{VID}.m4a.done").write_text("ok", encoding="utf-8")
    spy = PipeSpy(lib=lib)
    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is True and spy.calls == []
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_DELIVERED
    assert rec["output_path"].endswith(f"{VID}.m4a")


# ---------- 决策 hash 隔离 ----------

def test_hash_change_reads_only_new_dec_dir(tmp_path, monkeypatch):
    d1 = _decision(preset="zh-CN-YunxiNeural")
    d2 = _decision(preset="zh-CN-XiaoxiaoNeural")
    h1, h2 = dec_hash(d1), dec_hash(d2)
    assert h1 != h2
    _make_dec_dir(tmp_path, d1, _plain_subs())     # 上一版决策的遗留目录
    _make_dec_dir(tmp_path, d2, _plain_subs())     # 当前决策目录
    lib, _ = _mk_record(tmp_path, d2)              # 记录指向 d2
    spy = PipeSpy(lib=lib)
    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")
    assert r["ok"] is True
    new_dir = tmp_path / "work" / VID / f"dec_{h2}"
    for c in spy.calls:
        assert c["work_dir"] == new_dir            # 只读新 hash 目录
    assert str(lib.get(BV, 1)["output_path"]).startswith(str(new_dir))
    # 旧 hash 目录从未被写入(目录键天然隔离,旧成品不引用)
    assert not (tmp_path / "work" / VID / f"dec_{h1}" / VID).exists()


# ---------- skip 说话人:过滤合成输入 + 原声拼接 ----------

def test_skip_speakers_filtered_and_original_audio_stitched(tmp_path,
                                                            monkeypatch):
    dec = _decision(skip_spk00=True)
    lib, media = _mk_record(tmp_path, dec)
    orig = tmp_path / "media" / "orig16k.wav"
    orig.write_bytes(b"fake 16k wav")
    lib.update(BV, 1, artifacts={"audio": str(orig)})
    _make_dec_dir(tmp_path, dec, _skip_subs())
    spy = PipeSpy(lib=lib)
    monkeypatch.setattr(pipeline, "run_pipeline", spy)
    ff = _install_ffmpeg(monkeypatch, orig_seconds=2.0)

    r = fullmix.run_full_mix(BV, 1, lib=lib, work_dir=tmp_path / "work")

    assert r["ok"] is True and r["skip_speakers"] == ["SPK_00"]
    vid_dir = _vid_dir(tmp_path, dec)
    # 合成输入过滤掉 skip 说话人(不送合成,省 GPU)
    srt = (vid_dir / "tagged.srt").read_text(encoding="utf-8")
    assert "[SPK_00]" not in srt and "[SPK_01] 我是嘉宾" in srt
    # 拼接两步:重采样原片音频 → loudnorm 出成品(覆盖纯配音版)
    assert [c[1] for c in ff] == ["orig24k.wav", f"{VID}.m4a"]
    assert ff[0][0] == "-i"
    assert (vid_dir / f"{VID}.m4a").read_bytes() == b"stitched m4a"
    assert is_complete(vid_dir / f"{VID}.m4a")
    # 成品轨长 = max(配音轨 1s, 原片 2s, 末尾 skip 窗 4.0s)= 4s
    with wave.open(str(vid_dir / "full_mix_final.wav"), "rb") as w:
        assert w.getframerate() == TTS_SR and w.getnchannels() == 1
        assert w.getnframes() == TTS_SR * 4
    rec = lib.get(BV, 1)
    assert rec["status"] == ST_DELIVERED
    assert rec["output_path"].endswith(f"{VID}.m4a")
