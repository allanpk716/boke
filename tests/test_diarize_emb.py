# -*- coding: utf-8 -*-
"""票01:分人产物落盘每说话人 embedding(spk_emb.npy / spk_emb.json)。

不跑真 venv/pyannote:stub DiarizeOutput(带 speaker_diarization.itertracks
与 speaker_embeddings / labels())直接调 diar_run 的落盘函数;
diarize.py 侧测产物登记字段。

F8 对齐保障:labels 必须用与 npy 行序同源的 labels() 原序(经 SPEAKER_→SPK_
替换),不排序;set(labels) 与 rttm SPK 集合一致。
"""
import importlib.util
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boke import diarize  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


def _load_diar_run():
    """work/tools/diar_run.py 不在包里,按路径加载(模块级无重依赖,可安全导入)。"""
    spec = importlib.util.spec_from_file_location(
        "diar_run", _ROOT / "work" / "tools" / "diar_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- stub pyannote 对象 ----------

@dataclass
class _Turn:
    start: float
    end: float


class _FakeAnn:
    """stub Annotation:labels() 返回构造给定的原序(可故意非字典序,
    模拟与 npy 行序同源的来源顺序);itertracks 按 items 顺序 yield。"""

    def __init__(self, items, label_order):
        self._items = items              # [(turn, track, label)]
        self._label_order = label_order  # labels() 原序

    def itertracks(self, yield_label=False):
        for t, tr, lb in self._items:
            yield (t, tr, lb) if yield_label else (t, tr)

    def labels(self):
        return list(self._label_order)


class _FakeResult:
    """stub DiarizeOutput。emb=None 时模拟无 speaker_embeddings。"""

    def __init__(self, ann, emb):
        self.speaker_diarization = ann
        self.speaker_embeddings = emb


def _make_ann():
    """两个说话人,labels 原序故意非字典序(SPEAKER_03 在前)。"""
    labels = ["SPEAKER_03", "SPEAKER_00"]
    items = [
        (_Turn(0.0, 1.5), "T00", "SPEAKER_03"),
        (_Turn(2.0, 3.0), "T01", "SPEAKER_00"),
        (_Turn(3.5, 4.0), "T02", "SPEAKER_03"),
    ]
    return _FakeAnn(items, labels), labels


# ---------- diar_run 落盘 ----------

def test_save_embeddings_two_files_shape_order(tmp_path):
    dr = _load_diar_run()
    ann, labels = _make_ann()
    emb = np.arange(2 * 4, dtype=np.float32).reshape(2, 4)
    out = dr.save_speaker_embeddings(_FakeResult(ann, emb), tmp_path)
    assert out is not None

    npy, js = tmp_path / "spk_emb.npy", tmp_path / "spk_emb.json"
    assert npy.exists() and js.exists()

    m = np.load(npy)
    assert m.shape == (2, 4)
    assert np.allclose(m, emb)          # 逐行与输入一致(行序未动)

    meta = json.loads(js.read_text(encoding="utf-8"))
    assert meta["labels"] == ["SPK_03", "SPK_00"]   # 原序+替换,未排序
    assert meta["dim"] == 4


def test_labels_consistent_with_rttm_spk(tmp_path):
    """F8:json labels 集合 == rttm SPK 集合;rttm 行来自同一 ann。"""
    dr = _load_diar_run()
    ann, _ = _make_ann()
    dr.save_speaker_embeddings(
        _FakeResult(ann, np.zeros((2, 8), dtype=np.float32)), tmp_path)
    meta = json.loads((tmp_path / "spk_emb.json").read_text(encoding="utf-8"))

    rows = dr.rttm_rows(ann)
    rttm_spk = {r.split()[7] for r in rows}
    assert rttm_spk == {"SPK_03", "SPK_00"}
    assert set(meta["labels"]) == rttm_spk


def test_no_embeddings_writes_nothing(tmp_path):
    """无 embeddings(经典 Annotation 直通 / 属性为 None):不写文件、不报错。"""
    dr = _load_diar_run()
    ann, _ = _make_ann()
    assert dr.save_speaker_embeddings(ann, tmp_path) is None            # 无该属性
    assert dr.save_speaker_embeddings(_FakeResult(ann, None), tmp_path) is None
    assert not (tmp_path / "spk_emb.npy").exists()
    assert not (tmp_path / "spk_emb.json").exists()


def test_shape_mismatch_guard_skips_writing(tmp_path):
    """emb 行数与 labels 数不符(对齐已破):不落盘,免得下游吃到错位数据。"""
    dr = _load_diar_run()
    ann, _ = _make_ann()
    out = dr.save_speaker_embeddings(
        _FakeResult(ann, np.zeros((3, 4), dtype=np.float32)), tmp_path)
    assert out is None
    assert not (tmp_path / "spk_emb.npy").exists()
    assert not (tmp_path / "spk_emb.json").exists()


def test_main_end_to_end_with_stub(tmp_path, monkeypatch):
    """stub 整条 main 数据面:写 rttm + 落盘,目录里五个 SPK 字段一致。"""
    dr = _load_diar_run()
    ann, _ = _make_ann()
    emb = np.random.RandomState(0).rand(2, 256).astype(np.float32)
    out_rttm = tmp_path / "diar.rttm"
    rows = dr.rttm_rows(ann)
    out_rttm.write_text("\n".join(rows) + "\n", encoding="utf-8")
    dr.save_speaker_embeddings(_FakeResult(ann, emb), out_rttm.parent)

    rttm_spk = {r.split()[7] for r in
                out_rttm.read_text(encoding="utf-8").splitlines() if r.strip()}
    meta = json.loads((tmp_path / "spk_emb.json").read_text(encoding="utf-8"))
    assert set(meta["labels"]) == rttm_spk
    assert np.load(tmp_path / "spk_emb.npy").shape == (2, 256)


# ---------- diarize.py 产物登记 ----------

def test_emb_artifacts_registered_only_when_present(tmp_path):
    (tmp_path / "spk_emb.npy").write_bytes(b"x")
    (tmp_path / "spk_emb.json").write_text("{}", encoding="utf-8")
    got = diarize.emb_artifacts(tmp_path)
    assert got == {"spk_emb": str(tmp_path / "spk_emb.npy"),
                   "spk_emb_json": str(tmp_path / "spk_emb.json")}

    # 缺任一 / 目录不存在:不登记(优雅缺省,下游以文件不存在判无声纹建议)
    (tmp_path / "spk_emb.json").unlink()
    assert diarize.emb_artifacts(tmp_path) == {}
    assert diarize.emb_artifacts(tmp_path / "nope") == {}


def test_mock_run_no_emb_files(tmp_path):
    """mock 分人:两文件不写,返回 dict 无声纹登记键。"""
    vid = "vidMockP1"
    wdir = tmp_path / "work" / vid
    wdir.mkdir(parents=True)
    (wdir / "ocr.srt").write_text(
        "1\n00:00:00,000 --> 00:00:02,000\n你好\n\n"
        "2\n00:00:02,500 --> 00:00:04,000\nhello\n", encoding="utf-8")
    out = diarize.run(tmp_path / f"{vid}.mp4", str(tmp_path / "work"), mock=True)
    assert out["mock"] is True
    assert "spk_emb" not in out and "spk_emb_json" not in out
    assert not (wdir / "spk_emb.npy").exists()
    assert not (wdir / "spk_emb.json").exists()


def _fake_venv_subprocess(wdir, with_emb=True):
    """替换掉 .venv-diar 子进程:由桩写 rttm(和可选的两份声纹产物)。"""
    rttm = wdir / "diar.rttm"

    def fake_run(cmd, **kw):
        class R:
            returncode = 0
            stdout = "[diar-run] stub ok"
            stderr = ""
        rttm.write_text(
            "SPEAKER audio 1 000.000 100.000 <NA> <NA> SPK_00 <NA> <NA>\n"
            "SPEAKER audio 1 100.000 050.000 <NA> <NA> SPK_01 <NA> <NA>\n",
            encoding="utf-8")
        if with_emb:
            np.save(wdir / "spk_emb.npy", np.ones((2, 4), dtype=np.float32))
            (wdir / "spk_emb.json").write_text(
                '{"labels": ["SPK_00", "SPK_01"], "dim": 4}', encoding="utf-8")
        return R()

    return fake_run


@pytest.fixture()
def _stub_token(monkeypatch):
    monkeypatch.setattr(diarize, "hf_token", lambda: "stub-token")


def test_real_run_registers_emb_paths(tmp_path, monkeypatch, _stub_token):
    """真路径:子进程产出两文件 → run() 返回 dict 登记两路径。"""
    vid = "vidRealP1"
    wdir = tmp_path / "work" / vid
    wdir.mkdir(parents=True)
    monkeypatch.setattr(subprocess, "run", _fake_venv_subprocess(wdir))
    out = diarize.run(tmp_path / f"{vid}.mp4", str(tmp_path / "work"))
    assert out["rttm"].endswith("diar.rttm")
    assert out["spk_emb"] == str(wdir / "spk_emb.npy")
    assert out["spk_emb_json"] == str(wdir / "spk_emb.json")
    assert out["speakers"] == ["SPK_00", "SPK_01"]


def test_real_run_without_emb_no_error_no_keys(tmp_path, monkeypatch, _stub_token):
    """真路径但 runner 没写声纹产物(如 pyannote 无 embeddings):不报错、不登记。"""
    vid = "vidRealP2"
    wdir = tmp_path / "work" / vid
    wdir.mkdir(parents=True)
    monkeypatch.setattr(subprocess, "run", _fake_venv_subprocess(wdir, with_emb=False))
    out = diarize.run(tmp_path / f"{vid}.mp4", str(tmp_path / "work"))
    assert "spk_emb" not in out and "spk_emb_json" not in out
    assert not (wdir / "spk_emb.npy").exists()
    assert not (wdir / "spk_emb.json").exists()
