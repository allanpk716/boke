# -*- coding: utf-8 -*-
"""人物库单测:种子幂等 / 加人物加参考音 / 主持人挂接校验三分支(通过/缺稿/同分P);
契约扩展(票03):use 字段旧档兼容 / 用途过滤枚举 / remove_ref·remove_person 级联清单 /
挂接校验忽略 voiceid 条目。

任务书约束:测试一律用 tmp_path,不得真实写 work/persons.json。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boke.persons import PersonLibrary, check_host_attach  # noqa: E402


def _make_lib(tmp_path):
    """在 tmp_path 里造种子文件并开库,与真实布局同构(refs/ + 音频目录)。"""
    refs = tmp_path / "refs"
    weba = tmp_path / "web_audio"
    refs.mkdir(exist_ok=True)
    weba.mkdir(exist_ok=True)
    (refs / "yuanlian_chinese_01.wav").write_bytes(b"RIFF")
    (refs / "yuanlian_chinese_02.wav").write_bytes(b"RIFF")
    (refs / "ref01_transcript.txt").write_text("逐字稿占位", encoding="utf-8")
    (weba / "guest_ref_english.wav").write_bytes(b"RIFF")
    return PersonLibrary(path=tmp_path / "work" / "persons.json",
                         refs_dir=refs, web_audio_dir=weba)


def _person(lib, name):
    p = lib.get(name)
    assert p is not None, f"种子人物缺失:{name}"
    return p


# ---------- 种子 ----------

def test_seed_content_and_idempotent(tmp_path):
    lib = _make_lib(tmp_path)
    assert [p["name"] for p in lib.list_persons()] == ["圆脸", "小外"]

    yl = _person(lib, "圆脸")
    assert yl["main_lang"] == "zh"
    assert len(yl["refs"]) == 2
    r1, r2 = yl["refs"]
    assert r1["audio"].endswith("yuanlian_chinese_01.wav")
    assert r1["transcript"] is not None and r1["transcript"].endswith("ref01_transcript.txt")
    assert r1["lang"] == "zh"
    assert r2["audio"].endswith("yuanlian_chinese_02.wav")

    xw = _person(lib, "小外")
    assert xw["main_lang"] == "en"
    assert len(xw["refs"]) == 1
    assert xw["refs"][0]["audio"].endswith("guest_ref_english.wav")
    assert not xw["refs"][0]["transcript"]  # 无转写稿,cross 用

    # 重复初始化(重开库)不重复添加
    refs, weba = tmp_path / "refs", tmp_path / "web_audio"
    lib2 = PersonLibrary(path=tmp_path / "work" / "persons.json",
                         refs_dir=refs, web_audio_dir=weba)
    assert len(lib2.list_persons()) == 2
    assert len(_person(lib2, "圆脸")["refs"]) == 2

    # 磁盘 JSON 合法、无临时文件残留
    data = json.loads((tmp_path / "work" / "persons.json").read_text(encoding="utf-8"))
    assert len(data["persons"]) == 2
    leftovers = [p for p in (tmp_path / "work").iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_seed_only_when_empty(tmp_path):
    """库非空(哪怕只剩一个人)不再补种子;空 persons 列表视为库为空。"""
    lib = _make_lib(tmp_path)
    # 模拟"用户删了圆脸"
    path = tmp_path / "work" / "persons.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["persons"] = [p for p in data["persons"] if p["name"] != "圆脸"]
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    lib2 = PersonLibrary(path=path, refs_dir=tmp_path / "refs",
                         web_audio_dir=tmp_path / "web_audio")
    assert [p["name"] for p in lib2.list_persons()] == ["小外"]  # 不回种圆脸

    # 空 persons 列表 = 库为空 → 补种子
    data["persons"] = []
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    lib3 = PersonLibrary(path=path, refs_dir=tmp_path / "refs",
                         web_audio_dir=tmp_path / "web_audio")
    assert [p["name"] for p in lib3.list_persons()] == ["圆脸", "小外"]


# ---------- 增改 ----------

def test_add_person_and_ref(tmp_path):
    lib = _make_lib(tmp_path)
    p = lib.add_person("新嘉宾", "en", note="测试人物")
    assert p["refs"] == []
    assert lib.get("新嘉宾")["note"] == "测试人物"

    with pytest.raises(ValueError):
        lib.add_person("新嘉宾", "zh")  # 重名拒绝
    with pytest.raises(KeyError):
        lib.add_ref("不存在的人", audio="x.wav")

    ref = lib.add_ref("新嘉宾", audio=str(tmp_path / "a.wav"),
                      transcript=str(tmp_path / "a.txt"), lang="en",
                      source={"kind": "episode", "bvid": "BV1xxx", "part": 3, "spk": "SPK_01"})
    assert ref["source"]["part"] == 3

    # 落盘持久:重开库还在,且种子不重播
    lib2 = PersonLibrary(path=tmp_path / "work" / "persons.json",
                         refs_dir=tmp_path / "refs", web_audio_dir=tmp_path / "web_audio")
    got = lib2.get("新嘉宾")
    assert got is not None and len(got["refs"]) == 1
    assert len(lib2.list_persons()) == 3

    # lang 缺省继承人物主要语言
    ref2 = lib2.add_ref("新嘉宾", audio=str(tmp_path / "b.wav"))
    assert ref2["lang"] == "en"


# ---------- 主持人挂接校验 ----------

def _ref(audio="ref.wav", transcript="ref.txt", kind="manual", part=None):
    src = {"kind": kind}
    if kind == "episode":
        src.update({"bvid": "BV1test", "part": part, "spk": "SPK_00"})
    return {"audio": audio, "transcript": transcript, "lang": "zh", "source": src}


def test_check_host_attach_pass(tmp_path):
    # 种子圆脸:参考音+转写稿齐备,来源 manual → 任何分P都可通过
    lib = _make_lib(tmp_path)
    ok, problems = check_host_attach(_person(lib, "圆脸"), current_part_p=1)
    assert ok and problems == []

    # episode 来源但分P不同 → 通过
    person = {"name": "甲", "refs": [_ref(kind="episode", part=2)]}
    ok, problems = check_host_attach(person, current_part_p=1)
    assert ok and problems == []

    # 一条缺稿 + 另一条齐备且非当期 → 仍通过
    person = {"name": "乙", "refs": [
        _ref(audio="a.wav", transcript=None),
        _ref(audio="b.wav", kind="episode", part=2)]}
    ok, problems = check_host_attach(person, current_part_p=1)
    assert ok and problems == []

    # part 口径:1 / "1" / "P1" 等价
    person = {"name": "丙", "refs": [_ref(kind="episode", part="P2")]}
    ok, _ = check_host_attach(person, current_part_p=2)
    assert ok is False


def test_check_host_attach_missing_transcript(tmp_path):
    """缺转写稿 → 阻断,并指明缺哪条参考音的转写稿。"""
    lib = _make_lib(tmp_path)
    ok, problems = check_host_attach(_person(lib, "小外"), current_part_p=1)
    assert not ok
    assert len(problems) == 1
    assert "guest_ref_english.wav" in problems[0]
    assert "转写稿" in problems[0]

    # 无任何参考音 → 阻断且明示
    ok, problems = check_host_attach({"name": "空人", "refs": []}, current_part_p=1)
    assert not ok and problems
    # 空串转写稿视同缺稿
    ok, problems = check_host_attach(
        {"name": "丁", "refs": [_ref(transcript="  ")]}, current_part_p=1)
    assert not ok and "转写稿" in problems[0]


def test_check_host_attach_same_part_rejected(tmp_path):
    """唯一齐备参考音来自当前分P → 拒绝(防绕 D4),缺项里点名该参考音。"""
    person = {"name": "戊", "refs": [_ref(audio="cur.wav", kind="episode", part=2)]}
    ok, problems = check_host_attach(person, current_part_p=2)
    assert not ok
    assert "cur.wav" in problems[0]
    assert "分P" in problems[0]

    # 但同一人对别的分P可用(见 pass 分支);两条都坏时报两条缺项
    person = {"name": "己", "refs": [
        _ref(audio="no_t.txt.wav", transcript=None),
        _ref(audio="same.wav", kind="episode", part=2)]}
    ok, problems = check_host_attach(person, current_part_p=2)
    assert not ok and len(problems) == 2


# ---------- 契约扩展:use 字段 / 用途过滤 / 级联删除(票03) ----------

def _write_legacy_json(tmp_path):
    """手写旧格式 persons.json(条目无 use 字段),返回文件路径。"""
    path = tmp_path / "work" / "persons.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {"version": 1, "persons": [
        {"name": "老嘉宾", "main_lang": "zh", "avatar": None, "note": "旧格式人物",
         "refs": [
             {"audio": "refs/old_ref_zh.wav", "transcript": "refs/old_ref.txt",
              "lang": "zh", "source": {"kind": "manual"}},
             {"audio": "refs/old_cur_p1.wav", "transcript": "refs/old_ref.txt",
              "lang": "zh",
              "source": {"kind": "episode", "bvid": "BV1old", "part": 1, "spk": "SPK_9"}},
         ]},
    ]}
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    return path


def test_legacy_json_no_use_field_defaults_clone(tmp_path):
    """旧 persons.json(无 use 字段)加载兼容:读侧缺省补齐 clone,磁盘不回改。"""
    path = _write_legacy_json(tmp_path)
    raw_before = path.read_text(encoding="utf-8")

    lib = PersonLibrary(path=path, refs_dir=tmp_path / "refs",
                        web_audio_dir=tmp_path / "web_audio")
    p = lib.get("老嘉宾")
    assert p is not None
    assert all(r["use"] == "clone" for r in p["refs"])  # 读侧一律补齐缺省

    # 仅加载不落盘:旧文件原样,未被回写 use 字段
    assert path.read_text(encoding="utf-8") == raw_before

    # 旧条目进克隆列表(缺省),不进 voiceid 列表
    assert lib.list_refs("老嘉宾") == p["refs"]
    assert lib.list_refs("老嘉宾", use="voiceid") == []
    assert lib.iter_ref_paths("老嘉宾") == ["refs/old_ref_zh.wav", "refs/old_cur_p1.wav"]
    assert lib.iter_ref_paths("老嘉宾", use="voiceid") == []

    # 主持人挂接结论与现状一致:齐备 manual 通过;当期分P episode 仍按 D4 拒绝
    ok, problems = check_host_attach(p, current_part_p=1)
    assert ok and problems == []
    ok, problems = check_host_attach(
        {"name": "老嘉宾", "refs": [p["refs"][1]]}, current_part_p=1)
    assert not ok and "分P" in problems[0]


def test_add_ref_use_voiceid_and_filtering(tmp_path):
    """add_ref(use="voiceid") 归档条目:落盘显式 use;克隆/voiceid 列表互不混入。"""
    lib = _make_lib(tmp_path)
    sample = tmp_path / "web_audio" / "auto_BV1new_P2_SPK01_zh.wav"
    sample.write_bytes(b"RIFF")

    ref = lib.add_ref("圆脸", audio=str(sample), lang="zh",
                      source={"kind": "episode", "bvid": "BV1new", "part": 2, "spk": "SPK_01"},
                      use="voiceid")
    assert ref["use"] == "voiceid"

    # 落盘:use 字段显式写出,持久化
    data = json.loads((tmp_path / "work" / "persons.json").read_text(encoding="utf-8"))
    yl = next(p for p in data["persons"] if p["name"] == "圆脸")
    assert yl["refs"][-1]["use"] == "voiceid"

    # 过滤:归档条目只在 voiceid 列表;克隆列表(缺省)仍是种子两条
    assert lib.list_refs("圆脸", use="voiceid") == [ref]
    assert len(lib.list_refs("圆脸")) == 2
    assert all(r["audio"] != str(sample) for r in lib.list_refs("圆脸"))
    # use=None 全量 = 克隆条目在前 + 归档条目在后
    assert lib.iter_ref_paths("圆脸", use=None) == \
        lib.iter_ref_paths("圆脸", use="clone") + [str(sample)]
    assert lib.iter_ref_paths("圆脸", use="voiceid") == [str(sample)]

    # 非法用途拒绝
    with pytest.raises(ValueError):
        lib.add_ref("圆脸", audio=str(tmp_path / "x.wav"), use="bogus")


def test_remove_ref_cascade(tmp_path):
    """删 ref 条目:级联清单=缓存键(全部)+样本文件路径(仅 voiceid);不亲自动手删文件。"""
    lib = _make_lib(tmp_path)
    sample = tmp_path / "web_audio" / "auto_BV1rm_P2_SPK01_zh.wav"
    sample.write_bytes(b"RIFF")
    ref = lib.add_ref("圆脸", audio=str(sample), lang="zh",
                      source={"kind": "episode", "bvid": "BV1rm", "part": 2, "spk": "SPK_01"},
                      use="voiceid")

    # 删自动归档条目:样本文件进级联删除清单;文件本体留给调用方
    cas = lib.remove_ref("圆脸", audio=str(sample))
    assert cas["ref_paths_to_delete"] == [str(sample)]
    assert cas["cache_keys"] == [{"person": "圆脸", "ref_path": str(sample)}]
    assert cas["ref_entries_removed"] == [ref]
    assert sample.exists()
    assert lib.iter_ref_paths("圆脸", use="voiceid") == []

    # 删手动/种子(clone)条目:只清缓存,不列用户原文件
    clone_paths = lib.iter_ref_paths("圆脸", use="clone")
    assert len(clone_paths) == 2
    cas2 = lib.remove_ref("圆脸", audio=clone_paths[0])
    assert cas2["ref_paths_to_delete"] == []
    assert cas2["cache_keys"] == [{"person": "圆脸", "ref_path": clone_paths[0]}]

    # 落盘持久:重开库条目确实没了
    lib2 = PersonLibrary(path=tmp_path / "work" / "persons.json",
                         refs_dir=tmp_path / "refs", web_audio_dir=tmp_path / "web_audio")
    assert lib2.iter_ref_paths("圆脸", use=None) == [clone_paths[1]]

    with pytest.raises(KeyError):
        lib2.remove_ref("圆脸", audio="不存在.wav")
    with pytest.raises(KeyError):
        lib2.remove_ref("查无人", audio="x.wav")


def test_remove_person_cascade_all(tmp_path):
    """删人物:级联清单覆盖该人物全部条目(种子 clone + 归档 voiceid)。"""
    lib = _make_lib(tmp_path)
    sample = tmp_path / "web_audio" / "auto_BV1all_P3_SPK02_zh.wav"
    sample.write_bytes(b"RIFF")
    lib.add_ref("圆脸", audio=str(sample), lang="zh",
                source={"kind": "episode", "bvid": "BV1all", "part": 3, "spk": "SPK_02"},
                use="voiceid")

    cas = lib.remove_person("圆脸")
    assert len(cas["ref_entries_removed"]) == 3  # 种子 2 + 归档 1
    assert [k["person"] for k in cas["cache_keys"]] == ["圆脸"] * 3
    assert {k["ref_path"] for k in cas["cache_keys"]} == \
        {r["audio"] for r in cas["ref_entries_removed"]}
    assert cas["ref_paths_to_delete"] == [str(sample)]  # 只有 voiceid 条目删文件

    lib2 = PersonLibrary(path=tmp_path / "work" / "persons.json",
                         refs_dir=tmp_path / "refs", web_audio_dir=tmp_path / "web_audio")
    assert [p["name"] for p in lib2.list_persons()] == ["小外"]  # 不回种
    with pytest.raises(KeyError):
        lib2.remove_person("圆脸")


def test_check_host_attach_ignores_voiceid_refs(tmp_path):
    """voiceid 条目不参与克隆挂接校验:不报缺稿、不触发 D4 当期拒绝。"""
    v = {"audio": "auto.wav", "transcript": None, "lang": "zh", "use": "voiceid",
         "source": {"kind": "episode", "bvid": "BV1v", "part": 1, "spk": "S"}}
    # 仅 voiceid 条目 → 阻断且明示"无克隆参考音",而非误报缺转写稿
    ok, problems = check_host_attach({"name": "庚", "refs": [v]}, current_part_p=1)
    assert not ok
    assert problems == ["人物[庚]仅有声纹归档样本(use=voiceid),无克隆用途参考音,无法挂接主持人"]

    # 齐备克隆条目在旁 → 通过,voiceid 条目不添乱(哪怕来自当前分P)
    person = {"name": "辛", "refs": [_ref(), v]}
    ok, problems = check_host_attach(person, current_part_p=1)
    assert ok and problems == []
