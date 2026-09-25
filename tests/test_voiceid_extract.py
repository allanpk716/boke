# -*- coding: utf-8 -*-
"""票04:embedding 抽取器(voiceid_extract)+ 实验脚本(voiceid_bench)。

不跑真 venv/pyannote:子进程全 stub(monkeypatch subprocess.run,同
test_diarize_emb 先例);bench 用 stub 向量 + 注入式抽取函数;
三个子命令 --help 各以真子进程跑一次(秒回,不碰 GPU)。

.gitignore 放行效果不进 pytest(验收命令 git check-ignore 在票内单独执行)。
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boke import voiceid                    # noqa: E402  (bench 必须复用其口径)
from boke import voiceid_extract as vext    # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]
DIM = 256


def _load_tool(name):
    """work/tools 脚本不在包里,按路径加载(模块级只动 stdlib/env,可安全导入)。"""
    spec = importlib.util.spec_from_file_location(
        name, _ROOT / "work" / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- stub 子进程(voiceid_extract 侧) ----------

class _R:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def _fake_ok(recorder, rows=None, dim=DIM, returncode=0, write=True):
    """替身 .venv-diar 子进程:按协议写 out_npy/out_json(cmd[2]/cmd[3]),
    行内容确定性(第 i 行全 i+1),便于断言读回顺序。"""
    def fake_run(cmd, **kw):
        recorder.append({"cmd": list(cmd), "kw": dict(kw)})
        if returncode:
            return _R(returncode=returncode, stdout="out-lines",
                      stderr="err-lines")
        if write:
            paths = cmd[4:]
            n = len(paths) if rows is None else rows
            arr = np.tile(
                np.arange(1, n + 1, dtype=np.float32)[:, None], (1, dim))
            np.save(cmd[2], arr)
            Path(cmd[3]).write_text(json.dumps(
                {"paths": paths, "dim": dim, "model": "stub-model"}),
                encoding="utf-8")
        return _R(returncode=returncode, stdout="[emb-extract] stub ok")
    return fake_run


def test_extract_returns_256_dim_vec_and_calls_venv(monkeypatch):
    rec = []
    monkeypatch.setattr(subprocess, "run", _fake_ok(rec))
    vec = vext.extract("refs/a.wav")
    assert len(vec) == DIM
    assert all(isinstance(x, float) for x in vec)
    cmd = rec[0]["cmd"]
    assert cmd[0].replace("\\", "/").endswith(".venv-diar/Scripts/python.exe")
    assert cmd[1].replace("\\", "/").endswith("work/tools/emb_extract.py")
    assert cmd[4:] == ["refs/a.wav"]
    assert rec[0]["kw"]["timeout"] == vext.DEFAULT_TIMEOUT_S


def test_extract_many_single_subprocess_and_row_order(monkeypatch):
    rec = []
    monkeypatch.setattr(subprocess, "run", _fake_ok(rec))
    vecs = vext.extract_many(["a.wav", "b.wav"])
    assert len(rec) == 1                       # 批量:模型只加载一次
    assert rec[0]["cmd"][4:] == ["a.wav", "b.wav"]
    assert [v[0] for v in vecs] == [1.0, 2.0]  # 行序与输入路径一一对应


def test_extract_equals_first_row_of_many(monkeypatch):
    rec = []
    monkeypatch.setattr(subprocess, "run", _fake_ok(rec))
    assert vext.extract("a.wav") == vext.extract_many(["a.wav"])[0]


def test_nonzero_returncode_raises_with_aggregated_output(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_ok([], returncode=1))
    with pytest.raises(RuntimeError, match="err-lines"):
        vext.extract("a.wav")


def test_timeout_expiry_raises(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))
    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="超时"):
        vext.extract("a.wav", timeout=5)


def test_missing_venv_raises(monkeypatch):
    monkeypatch.setattr(vext, "venv_python",
                        lambda: Path("Z:/definitely-nothing/python.exe"))
    with pytest.raises(RuntimeError, match="venv"):
        vext.extract("a.wav")


def test_missing_artifacts_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_ok([], write=False))
    with pytest.raises(RuntimeError):
        vext.extract("a.wav")


def test_row_count_mismatch_raises(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _fake_ok([], rows=1))
    with pytest.raises(RuntimeError):
        vext.extract_many(["a.wav", "b.wav"])


def test_runner_file_exists_in_repo():
    assert vext.runner_path() == _ROOT / "work" / "tools" / "emb_extract.py"
    assert vext.runner_path().exists()


# ---------- emb_extract.py(.venv-diar 侧,纯部分不 import pyannote) ----------

def test_emb_extract_usage_error(monkeypatch):
    ee = _load_tool("emb_extract")
    monkeypatch.setattr(sys, "argv", ["emb_extract.py", "o.npy", "o.json"])
    assert ee.main() == 2


def test_emb_extract_writes_npy_json(monkeypatch, tmp_path):
    ee = _load_tool("emb_extract")
    monkeypatch.setattr(ee, "hf_token", lambda: "stub-token")
    monkeypatch.setattr(
        ee, "extract_all",
        lambda paths, token: np.arange(2 * 4, dtype=np.float32).reshape(2, 4))
    out_npy, out_js = tmp_path / "o.npy", tmp_path / "o.json"
    monkeypatch.setattr(sys, "argv", [
        "emb_extract.py", str(out_npy), str(out_js), "a.wav", "b.wav"])
    assert ee.main() == 0
    m = np.load(out_npy)
    assert m.shape == (2, 4)
    meta = json.loads(out_js.read_text(encoding="utf-8"))
    assert meta["paths"] == ["a.wav", "b.wav"]   # 行序 = 输入序
    assert meta["dim"] == 4
    assert meta["model"] == ee.MODEL_ID


# ---------- voiceid_bench.py ----------

def _mk_episode(tmp_path, name, labels, vecs):
    """stub episode:spk_emb.npy/json + diar.rttm(行序 = labels = 首次出现序)。"""
    d = tmp_path / "work" / name
    d.mkdir(parents=True)
    arr = np.array(vecs, dtype=np.float32)
    np.save(d / "spk_emb.npy", arr)
    (d / "spk_emb.json").write_text(json.dumps(
        {"labels": labels, "dim": int(arr.shape[1])}), encoding="utf-8")
    lines = [f"SPEAKER audio 1 {i * 100:07.3f} 050.000 <NA> <NA> {lb} <NA> <NA>"
             for i, lb in enumerate(labels)]
    (d / "diar.rttm").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


def _mk_archive(tmp_path):
    """两人物档案:圆脸(zh, 2 条同向 vec)、小外(en, 1 条正交 vec)。"""
    v_yuan = [1.0] * 8
    v_yuan2 = [0.9, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    v_kwai = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    entries = [
        {"person": "圆脸", "lang": "zh", "vec": v_yuan, "ref_path": "r1.wav"},
        {"person": "圆脸", "lang": "zh", "vec": v_yuan2, "ref_path": "r2.wav"},
        {"person": "小外", "lang": "en", "vec": v_kwai, "ref_path": "r3.wav"},
    ]
    p = tmp_path / "persons_emb.json"
    p.write_text(json.dumps({"version": 1, "entries": entries},
                            ensure_ascii=False), encoding="utf-8")
    return p, entries


def test_bench_three_subcommands_help():
    bench = _ROOT / "work" / "tools" / "voiceid_bench.py"
    for sub in ("matrix", "verify-alignment", "recompute-consistency"):
        r = subprocess.run([sys.executable, str(bench), sub, "--help"],
                           capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, (sub, r.stderr)
        assert sub in r.stdout


def test_bench_matrix_output_format_stub(monkeypatch, tmp_path):
    bench = _load_tool("voiceid_bench")
    ep = _mk_episode(tmp_path, "epX", ["SPK_00", "SPK_01"],
                     [[1.0] * 8, [0.5] * 8])
    emb, entries = _mk_archive(tmp_path)
    truth = tmp_path / "truth.json"
    truth.write_text(json.dumps({"epX": {"SPK_00": "圆脸"}}),
                     encoding="utf-8")
    outdir = tmp_path / "voiceid_bench"

    data = bench.main(["matrix", "--episode", str(ep), "--emb", str(emb),
                       "--truth", str(truth), "--out", str(outdir)])

    assert data["model"] == bench.MODEL_ID
    epd = data["episodes"]["epX"]
    assert epd["labels"] == ["SPK_00", "SPK_01"]
    row0 = epd["matrix"][0]
    assert {c["person"] for c in row0} == {"圆脸", "小外"}
    cell = next(c for c in row0 if c["person"] == "圆脸")
    assert cell["spk"] == "SPK_00"
    yuan_entries = [e for e in entries if e["person"] == "圆脸"]
    used, _ = voiceid.route([1.0] * 8, None, yuan_entries)
    assert cell["score"] == pytest.approx(
        voiceid.score([1.0] * 8, used)["score"])   # 打分层平均(D5)
    assert {"score", "cross", "top_ref"} <= set(cell)

    s = data["summary"]
    assert s["genuine_n"] == 1 and s["impostor_n"] == 1  # 仅 truth 覆盖的 SPK_00
    assert s["genuine"][0] == pytest.approx(cell["score"])
    assert "eer" in s and "genuine_hist" in s and "impostor_hist" in s

    mj = json.loads((outdir / "matrix.json").read_text(encoding="utf-8"))
    assert mj["episodes"]["epX"]["labels"] == ["SPK_00", "SPK_01"]
    md = (outdir / "scores.md").read_text(encoding="utf-8")
    assert "SPK_00" in md and "EER" in md


def test_bench_matrix_reuses_voiceid_scoring(monkeypatch, tmp_path):
    """matrix 的格子必须等于 voiceid.route+score 直算结果(不做重复实现)。"""
    bench = _load_tool("voiceid_bench")
    ep = _mk_episode(tmp_path, "epY", ["SPK_00"],
                     [[1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0]])
    emb, entries = _mk_archive(tmp_path)
    data = bench.main(["matrix", "--episode", str(ep), "--emb", str(emb),
                       "--out", str(tmp_path / "out")])
    cell = data["episodes"]["epY"]["matrix"][0][0]
    used, cross = voiceid.route([1.0] * 8, None,
                                [e for e in entries if e["person"] == "圆脸"])
    expected = voiceid.score([1.0] * 8, used)
    assert cell["score"] == pytest.approx(expected["score"])
    assert cell["cross"] == cross
    assert cell["top_ref"] == expected["top_ref"]


def test_bench_matrix_episode_missing_spk_emb(monkeypatch, tmp_path):
    bench = _load_tool("voiceid_bench")
    d = tmp_path / "work" / "epEmpty"
    d.mkdir(parents=True)
    emb, _ = _mk_archive(tmp_path)
    with pytest.raises(SystemExit):
        bench.main(["matrix", "--episode", str(d), "--emb", str(emb),
                    "--out", str(tmp_path / "out")])


def test_bench_verify_alignment_pass_and_fail(monkeypatch, tmp_path):
    bench = _load_tool("voiceid_bench")
    d = _mk_episode(tmp_path, "epA", ["SPK_01", "SPK_00"],
                    [[1.0] * 8, [0.0] * 8])   # 首次出现序 SPK_01 在前
    ok, problems = bench.verify_alignment(d)
    assert ok and problems == []

    js = d / "spk_emb.json"
    meta = json.loads(js.read_text(encoding="utf-8"))
    meta["labels"] = ["SPK_00", "SPK_01"]      # 顺序被破坏 → F8 断言失败
    js.write_text(json.dumps(meta), encoding="utf-8")
    ok2, problems2 = bench.verify_alignment(d)
    assert not ok2 and problems2

    arr = np.zeros((3, 8), dtype=np.float32)   # 行数不一致 → 断言失败
    np.save(d / "spk_emb.npy", arr)
    ok3, problems3 = bench.verify_alignment(d)
    assert not ok3 and problems3


def test_bench_recompute_consistency_stub(monkeypatch, tmp_path):
    bench = _load_tool("voiceid_bench")
    entries = [
        {"person": "圆脸", "lang": "zh", "vec": [1.0] * 8,
         "ref_path": "r1.wav"},
        {"person": "小外", "lang": "en", "vec": [0.0, 1.0] * 4,
         "ref_path": "r2.wav"},
    ]
    same = {e["ref_path"]: e["vec"] for e in entries}
    report, failed = bench.recompute_consistency(
        entries, lambda p: list(same[p]), sample_n=5)
    assert len(report) == 2 and failed == []
    assert all(r["ok"] and r["cos"] >= bench.RECOMPUTE_MIN_COS
               for r in report)

    report2, failed2 = bench.recompute_consistency(
        entries, lambda p: [0.0] * 8, sample_n=5)   # 正交假模型 → 拦
    assert failed2 and all(not r["ok"] for r in report2)

    assert bench.RECOMPUTE_MIN_COS == 0.999


def test_bench_recompute_sampling_deterministic(monkeypatch, tmp_path):
    bench = _load_tool("voiceid_bench")
    entries = [{"person": f"p{i}", "lang": "zh", "vec": [float(i)] * 8,
                "ref_path": f"r{i}.wav"} for i in range(20)]
    a, _ = bench.recompute_consistency(entries, lambda p: [0.1] * 8,
                                       sample_n=3, seed=42)
    b, _ = bench.recompute_consistency(entries, lambda p: [0.1] * 8,
                                       sample_n=3, seed=42)
    assert [r["ref_path"] for r in a] == [r["ref_path"] for r in b]
    assert len(a) == 3


def test_bench_recompute_cli_wiring_stub(monkeypatch, tmp_path):
    """CLI 侧:--sample 接 VoiceArchive 读档,extract_fn 注入桩(不碰 GPU)。"""
    bench = _load_tool("voiceid_bench")
    emb, entries = _mk_archive(tmp_path)
    monkeypatch.setattr(bench.voiceid_extract, "extract",
                        lambda p, timeout=None:
                            next(e["vec"] for e in entries
                                 if e["ref_path"] == p))
    outdir = tmp_path / "rc_out"
    data = bench.main(["recompute-consistency", "--emb", str(emb),
                       "--sample", "3", "--out", str(outdir)])
    assert data["n_checked"] == 3 and data["n_failed"] == 0
    assert (outdir / "recompute_consistency.json").exists()
