# -*- coding: utf-8 -*-
"""核对决策应用器单测(F12 决策版本):dec_hash / 合并归人重算 / voices 配置
可被 synthesize 消费 / .done 哨兵 / 主持人挂接校验阻断 / render 共用目录。

任务书约束:一律 tmp_path 构造工作目录,不得写真实 work/<id>。
"""
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boke import synthesize  # noqa: F401  # 配置结构对齐其 run() 消费逻辑
from boke.common import SpkSeg, SubLine, parse_srt, write_rttm, write_srt  # noqa: E402
from boke.persons import PersonLibrary  # noqa: E402
from boke.review_apply import (DecisionError, apply_decision, dec_hash,  # noqa: E402
                               is_complete, render_full_config,
                               render_preview_config)

VID = "BV1review0001"


# ---------- 构造 ----------

def _make_persons(tmp_path):
    """tmp 内同构布局的 PersonLibrary(种子圆脸带转写稿/小外无稿)。"""
    refs = tmp_path / "refs"
    weba = tmp_path / "web_audio"
    refs.mkdir(exist_ok=True)
    weba.mkdir(exist_ok=True)
    (refs / "yuanlian_chinese_01.wav").write_bytes(b"RIFF")
    (refs / "ref01_transcript.txt").write_text("圆脸参考音逐字稿占位", encoding="utf-8")
    (weba / "guest_ref_english.wav").write_bytes(b"RIFF")
    return PersonLibrary(path=tmp_path / "work" / "persons.json",
                         refs_dir=refs, web_audio_dir=weba)


def _make_work(tmp_path, vid=VID):
    """构造 ocr.srt + diar.rttm(test_attribute 风格):SPK_03 是 SPK_00 的分身。

    第 5 句骑在 SPK_03→SPK_00 换人点上(12.5-14.5 vs 9.8-13.8 / 13.9-18.5):
    未合并时多数重叠归 SPK_03;SPK_03 并入 SPK_00 后两段相邻(缝 0.1s≤0.3s)
    合成一段,该句整体归 SPK_00 —— 验证"合并后重跑归人"真正生效。
    """
    wdir = tmp_path / "work" / vid
    wdir.mkdir(parents=True, exist_ok=True)
    subs = [
        SubLine(1, 0.5, 4.0, "第一句话"),
        SubLine(2, 4.5, 9.0, "第二句话"),
        SubLine(3, 10.0, 13.5, "第三句话"),
        SubLine(4, 14.0, 18.0, "第四句话"),
        SubLine(5, 12.5, 14.5, "骑线第五句"),
    ]
    segs = [SpkSeg(0.0, 4.2, "SPK_00"), SpkSeg(4.3, 9.2, "SPK_01"),
            SpkSeg(9.8, 13.8, "SPK_03"), SpkSeg(13.9, 18.5, "SPK_00")]
    write_srt(wdir / "ocr.srt", subs)
    write_rttm(wdir / "diar.rttm", segs)
    return wdir


def _decision(clone_ref, merge_spk03=True, host_person="圆脸"):
    """五说话人决策:主持人挂库 / 预设 / 嘉宾克隆 / 分身(可并可不并)/ 跳过。"""
    spk03 = {"id": "SPK_03", "note": "分身"}
    if merge_spk03:
        spk03["merge_into"] = "SPK_00"
    else:
        spk03.update({"voice": "preset", "preset_voice": "zh-CN-XiaoxiaoNeural"})
    return {
        "verdict": "dub",
        "speakers": [
            {"id": "SPK_00", "note": "主持人", "host": True,
             "host_person": host_person, "voice": "clone"},
            {"id": "SPK_01", "note": "嘉宾预设", "voice": "preset",
             "preset_voice": "zh-CN-YunjianNeural"},
            {"id": "SPK_02", "note": "嘉宾克隆", "voice": "clone",
             "clone_ref": str(clone_ref)},
            spk03,
            {"id": "SPK_04", "note": "跳过者", "voice": "skip"},
        ],
    }


def _setup(tmp_path, vid=VID):
    wdir = _make_work(tmp_path, vid)
    lib = _make_persons(tmp_path)
    clone_ref = tmp_path / "cand_SPK_02.wav"
    clone_ref.write_bytes(b"RIFF")
    return wdir, lib, clone_ref


def _apply(tmp_path, dec, lib, **kw):
    return apply_decision(dec, VID, tmp_path / "work", part=1, persons=lib, **kw)


# ---------- dec_hash ----------

def test_dec_hash_stable_and_field_sensitive(tmp_path):
    _, lib, clone_ref = _setup(tmp_path)
    d1 = _decision(clone_ref)
    h = dec_hash(d1)
    assert dec_hash(_decision(clone_ref)) == h          # 同决策稳定同值
    assert len(h) == 12 and all(c in "0123456789abcdef" for c in h)
    # 顶层键序无关(sort_keys 规范化)
    assert dec_hash({"speakers": d1["speakers"], "verdict": d1["verdict"]}) == h
    # 任一字段变化 → 新值
    d = _decision(clone_ref)
    d["speakers"][0]["note"] = "改了备注"
    assert dec_hash(d) != h
    d = _decision(clone_ref)
    d["verdict"] = "skip"
    assert dec_hash(d) != h
    d = _decision(clone_ref)
    d["speakers"][3]["merge_into"] = None
    assert dec_hash(d) != h


# ---------- rediarize ----------

def test_rediarize_voids_decision(tmp_path):
    _, lib, clone_ref = _setup(tmp_path)
    dec = _decision(clone_ref)
    dec["rediarize"] = {"max_speakers": 2}
    res = _apply(tmp_path, dec, lib)
    assert res["status"] == "voided"
    assert res["rediarize"] == {"max_speakers": 2}
    assert not list((tmp_path / "work" / VID).glob("dec_*"))   # 未生成任何配置


# ---------- 产物与哨兵 ----------

def test_apply_generates_artifacts_with_sentinels(tmp_path):
    _, lib, clone_ref = _setup(tmp_path)
    res = _apply(tmp_path, _decision(clone_ref), lib)
    assert res["status"] == "applied"
    assert res["dec_hash"] == dec_hash(_decision(clone_ref))
    dec_dir = tmp_path / "work" / VID / f"dec_{res['dec_hash']}"
    assert res["dec_dir"] == str(dec_dir)
    voices, tagged = dec_dir / "voices.generated.yaml", dec_dir / "tagged.dec.srt"
    assert voices.exists() and tagged.exists()
    assert is_complete(voices) and is_complete(tagged)
    assert Path(str(voices) + ".done").exists()
    assert Path(str(tagged) + ".done").exists()
    assert res["voices"] == str(voices) and res["tagged"] == str(tagged)


def test_sentinel_half_written(tmp_path):
    _, lib, clone_ref = _setup(tmp_path)
    # 干净文件无哨兵 → 不完整
    plain = tmp_path / "plain.txt"
    plain.write_text("x", encoding="utf-8")
    assert not is_complete(plain)
    # 只有哨兵没有产物 → 也不完整
    ghost = tmp_path / "ghost.yaml"
    Path(str(ghost) + ".done").write_text("x", encoding="utf-8")
    assert not is_complete(ghost)

    # 半写入模拟:哨兵被删 → 视为不存在;同 hash 重跑补写并补哨兵
    dec = _decision(clone_ref)
    res = _apply(tmp_path, dec, lib)
    voices = Path(res["voices"])
    Path(str(voices) + ".done").unlink()
    assert voices.exists() and not is_complete(voices)
    res2 = _apply(tmp_path, dec, lib)
    assert res2["status"] == "applied" and is_complete(voices)


def test_same_decision_reuses_artifacts(tmp_path):
    _, lib, clone_ref = _setup(tmp_path)
    dec = _decision(clone_ref)
    r1 = _apply(tmp_path, dec, lib)
    voices = Path(r1["voices"])
    content, mtime = voices.read_bytes(), voices.stat().st_mtime_ns
    r2 = _apply(tmp_path, dec, lib)
    assert r2["status"] == "reused"
    assert (r2["dec_hash"], r2["dec_dir"]) == (r1["dec_hash"], r1["dec_dir"])
    assert voices.read_bytes() == content                 # 未重写
    assert voices.stat().st_mtime_ns == mtime
    r3 = _apply(tmp_path, dec, lib, force=True)           # force 强制重写
    assert r3["status"] == "applied"
    assert voices.read_bytes() == content                 # 内容确定性:同决策同产物


# ---------- 合并归人重算 ----------

def test_merge_into_reattributes(tmp_path):
    _, lib, clone_ref = _setup(tmp_path)
    res = _apply(tmp_path, _decision(clone_ref), lib)
    text = (Path(res["dec_dir"]) / "tagged.dec.srt").read_text(encoding="utf-8")
    assert "SPK_03" not in text                            # 分身标签彻底消失
    got = {s.idx: s.text for s in parse_srt(Path(res["dec_dir"]) / "tagged.dec.srt")}
    assert got[1].startswith("[SPK_00] ")
    assert got[2].startswith("[SPK_01] ")
    assert got[3].startswith("[SPK_00] ")                  # 原 SPK_03 独占句 → SPK_00
    assert got[4].startswith("[SPK_00] ")
    assert got[5].startswith("[SPK_00] ")                  # 骑线句合并后整归 SPK_00

    # 基线对照:不合并(分身配预设)→ 新 hash 新目录,SPK_03 保留、骑线句归 SPK_03
    res2 = _apply(tmp_path, _decision(clone_ref, merge_spk03=False), lib)
    assert res2["dec_hash"] != res["dec_hash"]
    got2 = {s.idx: s.text
            for s in parse_srt(Path(res2["dec_dir"]) / "tagged.dec.srt")}
    assert got2[3].startswith("[SPK_03] ")
    assert got2[5].startswith("[SPK_03] ")


# ---------- voices 配置可被 synthesize 消费 ----------

def test_voices_yaml_consumable_by_synthesize(tmp_path):
    _, lib, clone_ref = _setup(tmp_path)
    fake_global = {"cosyvoice_repo": "D:/fake/CosyVoice",
                   "cosyvoice_model": "D:/fake/model"}
    res = _apply(tmp_path, _decision(clone_ref), lib, global_cfg=fake_global)
    raw = Path(res["voices"]).read_text(encoding="utf-8")
    # 头注释:dec_hash 与 skip 占位约定(票05 契约)
    assert f"dec_hash={res['dec_hash']}" in raw
    assert "__skip__" in raw.split("global:")[0]
    cfg = yaml.safe_load(raw)

    # --- synthesize.run 的 episode_maps 匹配逻辑:file 子串命中视频 stem ---
    lib_cfg = cfg.get("library", {})
    ep_map = {}
    for em in cfg.get("episode_maps", []):
        if em.get("file") and em["file"] in VID:
            ep_map = em.get("map", {})
            break
    assert ep_map, "episode_maps 未命中视频 stem"

    # 主持人 → 人物库 zero 克隆:ref 音频与转写稿路径真实存在
    e0 = lib_cfg[ep_map["SPK_00"]["voice"]]
    assert e0["type"] == "clone" and e0["engine"] == "cosyvoice3"
    assert Path(e0["ref_audio"]).exists()
    assert Path(e0["ref_text_file"]).exists()
    assert ep_map["SPK_00"]["who"] == "主持人"

    # 嘉宾 preset → edge 音色
    e1 = lib_cfg[ep_map["SPK_01"]["voice"]]
    assert e1["type"] == "preset" and e1["engine"] == "edge"
    assert e1["voice"] == "zh-CN-YunjianNeural"

    # 嘉宾 clone → cross 无文本:ref=候选原声段,无 ref_text_file
    e2 = lib_cfg[ep_map["SPK_02"]["voice"]]
    assert e2["type"] == "clone" and e2["engine"] == "cosyvoice3"
    assert Path(e2["ref_audio"]) == clone_ref
    assert "ref_text_file" not in e2

    # skip:map 值带 skip:true 且 voice 指向 __skip__ 占位条目
    m4 = ep_map["SPK_04"]
    assert m4["skip"] is True and m4["voice"] == "__skip__"
    assert lib_cfg["__skip__"]["type"] == "skip"

    # 合并分身不参与配音:不在 map 也无对应 library 条目
    assert "SPK_03" not in ep_map
    assert "spk_03" not in lib_cfg

    # global 段透传(假引擎路径)
    assert cfg["global"]["cosyvoice_repo"] == "D:/fake/CosyVoice"
    assert cfg["global"]["cosyvoice_model"] == "D:/fake/model"


def test_default_global_from_readonly_voices_yaml(tmp_path):
    """不传 global_cfg 时兜底读旧 src/boke/voices.yaml 的 global 段(只读)。"""
    _, lib, clone_ref = _setup(tmp_path)
    res = _apply(tmp_path, _decision(clone_ref), lib)
    cfg = yaml.safe_load(Path(res["voices"]).read_text(encoding="utf-8"))
    assert cfg["global"].get("cosyvoice_repo")


# ---------- 主持人挂接校验阻断 ----------

def test_host_attach_failure_blocks(tmp_path):
    _, lib, clone_ref = _setup(tmp_path)

    def dec_dirs():
        return list((tmp_path / "work" / VID).glob("dec_*"))

    # 缺转写稿:小外种子(参考音无稿)→ 明确报缺项,不生成降级配置
    with pytest.raises(DecisionError) as ei:
        _apply(tmp_path, _decision(clone_ref, host_person="小外"), lib)
    assert "转写稿" in str(ei.value)
    assert dec_dirs() == []

    # 同分P:唯一齐备参考音来自当前分P → 拒绝(D4)
    lib.add_person("当期人", "en")
    lib.add_ref("当期人", audio=str(tmp_path / "cur.wav"),
                transcript=str(tmp_path / "cur.txt"), lang="en",
                source={"kind": "episode", "bvid": VID, "part": 1, "spk": "SPK_00"})
    with pytest.raises(DecisionError) as ei:
        _apply(tmp_path, _decision(clone_ref, host_person="当期人"), lib)
    assert "分P" in str(ei.value)
    assert dec_dirs() == []

    # 人物不存在
    with pytest.raises(DecisionError) as ei:
        _apply(tmp_path, _decision(clone_ref, host_person="没有人"), lib)
    assert "人物库" in str(ei.value)

    # host 未指名人物
    dec = _decision(clone_ref)
    dec["speakers"][0]["host_person"] = None
    with pytest.raises(DecisionError) as ei:
        _apply(tmp_path, dec, lib)
    assert "host_person" in str(ei.value)
    assert dec_dirs() == []


# ---------- 决策形状校验 ----------

def test_decision_shape_errors(tmp_path):
    _, lib, clone_ref = _setup(tmp_path)

    def bad(dec):
        with pytest.raises(DecisionError):
            _apply(tmp_path, dec, lib)

    d = _decision(clone_ref); d["verdict"] = "skip"; bad(d)            # skip 不走应用
    bad({"verdict": "dub"})                                            # 缺 speakers
    d = _decision(clone_ref); d["speakers"][3]["merge_into"] = "SPK_99"; bad(d)
    d = _decision(clone_ref); d["speakers"][3]["merge_into"] = "SPK_03"; bad(d)   # 自指
    d = _decision(clone_ref); d["speakers"][0]["merge_into"] = "SPK_03"; bad(d)   # 成环
    d = _decision(clone_ref); del d["speakers"][2]["clone_ref"]
    with pytest.raises(DecisionError, match="clone_ref"):
        _apply(tmp_path, d, lib)
    d = _decision(clone_ref); del d["speakers"][1]["preset_voice"]
    with pytest.raises(DecisionError, match="preset_voice"):
        _apply(tmp_path, d, lib)
    d = _decision(clone_ref); del d["speakers"][4]["voice"]; bad(d)    # 缺 voice 决策
    d = _decision(clone_ref); d["speakers"][4]["voice"] = "magic"; bad(d)  # 非法值


# ---------- render_preview / render_full 共用 ----------

def test_render_preview_full_share_dir(tmp_path):
    _, lib, clone_ref = _setup(tmp_path)
    dec = _decision(clone_ref)
    p = render_preview_config(dec, VID, tmp_path / "work", part=1, persons=lib)
    f = render_full_config(dec, VID, tmp_path / "work", part=1, persons=lib)
    assert p["scope"] == "preview" and f["scope"] == "full"
    assert p["status"] == "applied" and f["status"] == "reused"
    assert p["dec_hash"] == f["dec_hash"] == dec_hash(dec)
    assert p["dec_dir"] == f["dec_dir"] and p["voices"] == f["voices"]
    assert is_complete(Path(p["voices"]))

    # 与 apply_decision 同 hash 目录:apply 只补归人产物,voices 复用
    a = apply_decision(dec, VID, tmp_path / "work", part=1, persons=lib)
    assert a["dec_dir"] == f["dec_dir"]
    assert is_complete(Path(a["tagged"]))

    # rediarize 在 render 入口同样作废
    dec2 = _decision(clone_ref)
    dec2["rediarize"] = {"max_speakers": 3}
    assert render_full_config(dec2, VID, tmp_path / "work",
                              part=1, persons=lib)["status"] == "voided"
