#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cut_clips.py — 影视剧配音研究·通用切片工具（纯标准库 + subprocess 调 ffmpeg/ffprobe）。

功能：
  1. grid  子命令：对源媒体按固定步长（默认 30s）生成起止点表（JSON），最后不足一段的尾巴自动
     clamp 并标 allow_short。可选 --range-layer "start:end:分层" 给粗分段打 provisional 标签。
  2. cut   子命令：读起止点表（JSON，或本脚本能解析的 YAML 子集），对每个切点输出两列 wav
     （默认 44.1kHz 主档 + 16kHz 副档，均为 16bit PCM；5.1 源默认下混立体声，--keep-channels
     可保留原声道布局），ffprobe 实测时长校验（±0.5s），通过后写入/更新 clips_manifest.yaml
     条目并重算 quota_progress 节。

清单文件格式（本脚本维护的 YAML 子集，PyYAML 亦可读）：
  - 顶层标量键（header）+ clips 列表（扁平字典条目）+ quota_progress（脚本自动生成，勿手改）。
  - 解析器只认上述结构；遇到其他顶层节会报错拒绝（防手改结构后静默丢数据）。
  - 整文件由脚本按内存状态重写，手工注释会被覆盖；schema 说明固定写在文件头注释里。

起止点表字段：
  源级：source_file（相对清单所在目录）、source（bili|blender|dnr|constructed）、source_label
        （clip_id 前缀）、language、default_layer（可选）、clips[]。
  片级：clip_id、start、end（秒）、layer（对白干净|音乐重|音效压对白|双人重叠|有5.1|待听审标注）、
        note（可选）、allow_short（可选，true 时豁免最短时长校验）、provisional（可选，默认 true）。

时长校验：片段计划时长须在 [min_duration, max_duration]（默认 30~120s）内，allow_short 豁免下限，
超上限一律报错。输出实际时长偏差 >0.5s 判失败。

用法示例：
  python tools/cut_clips.py grid --source-file media/blender/Sintel.2010.1080p.mkv \
      --source blender --label blender_sintel --language en --grid 30 \
      --range-layer "0:60:音乐重" --range-layer "600:840:音效压对白" \
      --out-table media/clips/sintel_grid.json
  python tools/cut_clips.py cut --table media/clips/sintel_grid.json \
      --manifest clips_manifest.yaml --out-dir media/clips --skip-existing
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------- 常量

SCHEMA_VERSION = 1
ALLOWED_SOURCES = ("bili", "blender", "dnr", "constructed")
ALLOWED_LAYERS = ("对白干净", "音乐重", "音效压对白", "双人重叠", "有5.1", "待听审标注")
QUOTA_TARGETS = {"对白干净": 3, "音乐重": 3, "音效压对白": 3, "双人重叠": 4}
KNOWN_SECTIONS = ("clips", "quota_progress")
DUR_TOLERANCE = 0.5
RATE_SUFFIX = {44100: "44k1", 16000: "16k"}

MANIFEST_HEADER_COMMENT = """\
# clips_manifest.yaml — 实验片段统一清单（schema_version {v}）
# 维护方：tools/cut_clips.py（cut 子命令会整体重写本文件；手工改请只改 clips 条目的 layer/note/provisional 字段值）
# 字段说明：
#   clip_id      唯一 ID（前缀=来源标签）
#   source       来源：bili|blender|dnr|constructed
#   source_file  源媒体路径（相对本文件所在目录；gitignored）
#   language     语言：en|zh|...
#   layer        分层标签：对白干净|音乐重|音效压对白|双人重叠|有5.1|待听审标注
#   provisional  true=粗标待复核（配额进度单列，不计入 confirmed）
#   start/end    计划起止点（秒）；duration 实测输出时长（秒）
#   path         44.1kHz 主档 wav；path_16k 16kHz 副档 wav（相对本文件所在目录）
#   status       cut=已切片且时长校验通过；pending=已登记待切片
#   note         备注
# quota_progress 每来源每语言分层计数（confirmed/provisional 与 F6 配额目标），由脚本从 clips 自动重算。
""".format(v=SCHEMA_VERSION)


# ---------------------------------------------------------------- 控制台

def _setup_stdio() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def log(msg: str) -> None:
    print(msg, flush=True)


# ---------------------------------------------------------------- ffmpeg/ffprobe

def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")


def probe_duration(path: str) -> float:
    """ffprobe 取媒体时长（秒）。"""
    proc = _run([
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", path,
    ])
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe 失败: {path}\n{proc.stderr.strip()}")
    try:
        return float(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(f"ffprobe 时长不可解析: {path!r} -> {proc.stdout!r}")


def cut_wav(src: str, start: float, duration: float, out_path: str, sample_rate: int,
            keep_channels: bool, mono_16k: bool = False) -> None:
    """切一段 wav（重采样 + 16bit PCM）。-ss 放 -i 前做输入快 seek；重编码故精度足够。"""
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-ss", f"{start:.3f}", "-i", src, "-t", f"{duration:.3f}",
        "-vn", "-acodec", "pcm_s16le", "-ar", str(sample_rate),
    ]
    if not keep_channels:
        cmd += ["-ac", "1" if (mono_16k and sample_rate == 16000) else "2"]
    cmd += [out_path]
    proc = _run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 切片失败: {src} [{start:.2f}s +{duration:.2f}s] -> {out_path}\n{proc.stderr.strip()}")


# ---------------------------------------------------------------- 极小 YAML 子集（读）

def _strip_inline_comment(line: str) -> str:
    """去掉引号外的 ' #...' 行内注释。"""
    out, in_quote = [], False
    i = 0
    while i < len(line):
        ch = line[i]
        if ch == '"' and (i == 0 or line[i - 1] != "\\"):
            in_quote = not in_quote
        if not in_quote and ch == "#" and (i == 0 or line[i - 1] in (" ", "\t")):
            break
        out.append(ch)
        i += 1
    return "".join(out).rstrip()


def _parse_scalar(s: str):
    s = s.strip()
    if s == "":
        return ""
    if len(s) >= 2 and s[0] == '"' and s[-1] == '"':
        return s[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    low = s.lower()
    if low == "true":
        return True
    if low == "false":
        return False
    if low == "null":
        return None
    if re.fullmatch(r"[+-]?\d+", s):
        return int(s)
    if re.fullmatch(r"[+-]?\d+\.\d+", s):
        return float(s)
    return s


def _split_kv(text: str) -> tuple[str, str]:
    key, sep, val = text.partition(":")
    if not sep:
        raise ValueError(f"清单行缺少 'key: value' 形式: {text!r}")
    return key.strip(), val.strip()


def load_manifest(path: str) -> dict:
    """解析清单 YAML 子集 -> {header: dict, clips: list[dict]}。quota_progress 不读（重算），
    未知顶层节直接报错（防重写时静默丢数据）。"""
    with open(path, "r", encoding="utf-8") as f:
        raw_lines = f.read().splitlines()

    header: dict = {}
    clips: list[dict] = []
    section = None
    unknown: list[str] = []

    for lineno, raw in enumerate(raw_lines, 1):
        line = _strip_inline_comment(raw)
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            key, val = _split_kv(line)
            if val == "":
                section = key
                if key not in KNOWN_SECTIONS:
                    unknown.append(key)
                continue
            section = None
            header[key] = _parse_scalar(val)
        else:
            if section == "clips":
                if stripped.startswith("- "):
                    item: dict = {}
                    clips.append(item)
                    stripped = stripped[2:].strip()
                elif not clips:
                    raise ValueError(f"清单第 {lineno} 行：clips 条目缺 '- ' 前缀: {stripped!r}")
                k, v = _split_kv(stripped)
                clips[-1][k] = _parse_scalar(v)
            elif section == "quota_progress":
                continue  # 自动重算，跳过
            elif section is None:
                raise ValueError(f"清单第 {lineno} 行：缩进行不在任何已知节内: {stripped!r}")

    if unknown:
        raise ValueError(
            "清单含未知顶层节（本工具只管理 clips/quota_progress，为防丢数据拒绝处理）："
            + ", ".join(unknown)
            + "。请手工处理这些节后重跑。"
        )
    if not isinstance(header.get("schema_version", SCHEMA_VERSION), int):
        raise ValueError("schema_version 必须是整数")
    return {"header": header, "clips": clips}


# ---------------------------------------------------------------- 极小 YAML 子集（写）

def _yaml_str(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int,)):
        return str(v)
    if isinstance(v, float):
        return str(round(v, 3))
    s = str(v)
    low = s.lower()
    need_quote = (
        s == ""
        or low in ("true", "false", "null")
        or re.fullmatch(r"[+-]?\d+(\.\d+)?", s) is not None
        or any(ch in s for ch in (":", "#", '"', "\\"))
        or s != s.strip()
        or s.startswith("- ")
    )
    if need_quote:
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def _emit_node(node: dict, indent: int, lines: list[str]) -> None:
    pad = "  " * indent
    for k, v in node.items():
        if isinstance(v, dict):
            lines.append(f"{pad}{_yaml_str(k)}:")
            _emit_node(v, indent + 1, lines)
        else:
            lines.append(f"{pad}{_yaml_str(k)}: {_yaml_str(v)}")


_CLIP_FIELD_ORDER = [
    "clip_id", "source", "source_file", "language", "layer", "provisional",
    "start", "end", "duration", "path", "path_16k", "status", "note",
]


def save_manifest(path: str, header: dict, clips: list[dict]) -> None:
    """整体重写清单：header 标量 + clips 列表 + 自动重算的 quota_progress。"""
    lines: list[str] = []
    for block in MANIFEST_HEADER_COMMENT.splitlines():
        lines.append(block)
    hdr = dict(header)
    hdr.setdefault("schema_version", SCHEMA_VERSION)
    for k, v in hdr.items():
        lines.append(f"{k}: {_yaml_str(v)}")

    lines.append("clips:")
    for item in clips:
        keys = [k for k in _CLIP_FIELD_ORDER if k in item] + sorted(k for k in item if k not in _CLIP_FIELD_ORDER)
        for i, k in enumerate(keys):
            prefix = "  - " if i == 0 else "    "
            lines.append(f"{prefix}{k}: {_yaml_str(item[k])}")

    lines.append("quota_progress:")
    lines.append(f"  generated_at: {_yaml_str(datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds'))}")
    lines.append("  note: confirmed=已定标签片段数 provisional=粗标待复核片段数(单列不计配额); target 按 spec F6(每来源每语言)")
    quota = compute_quota(clips)
    _emit_node(quota, 1, lines)

    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)


def compute_quota(clips: list[dict]) -> dict:
    """按 来源 -> 语言 -> 分层 统计 confirmed/provisional，附 F6 target。"""
    tree: dict = {}
    for c in clips:
        src = str(c.get("source", "?"))
        lang = str(c.get("language", "?"))
        layer = str(c.get("layer", "?"))
        node = tree.setdefault(src, {}).setdefault(lang, {})
        bucket = node.setdefault(layer, {"confirmed": 0, "provisional": 0})
        if c.get("provisional"):
            bucket["provisional"] += 1
        else:
            bucket["confirmed"] += 1
    for src in tree:
        for lang in tree[src]:
            for layer in list(tree[src][lang]):
                if layer in QUOTA_TARGETS:
                    tree[src][lang][layer]["target"] = QUOTA_TARGETS[layer]
    return {"per_source": tree}


# ---------------------------------------------------------------- 起止点表

def load_points_table(path: str) -> dict:
    """读切点表。JSON 原生；.yaml/.yml 走同一 YAML 子集解析器（源级键在 header，clips 在列表）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".json":
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _normalize_table(data)
    parsed = load_manifest(path)
    if not parsed["clips"]:
        raise ValueError(f"切点表没有 clips: {path}")
    return _normalize_table({**parsed["header"], "clips": parsed["clips"]})


def _normalize_table(data: dict) -> dict:
    for key in ("source_file", "source", "source_label", "language"):
        if not data.get(key):
            raise ValueError(f"切点表缺少源级字段: {key}")
    if data["source"] not in ALLOWED_SOURCES:
        raise ValueError(f"source 必须是 {'|'.join(ALLOWED_SOURCES)}，得到 {data['source']!r}")
    clips = data.get("clips") or []
    if not clips:
        raise ValueError("切点表 clips 为空")
    seen = set()
    for c in clips:
        for key in ("clip_id", "start", "end"):
            if key not in c:
                raise ValueError(f"切点条目缺少字段 {key}: {c!r}")
        if c["clip_id"] in seen:
            raise ValueError(f"clip_id 重复: {c['clip_id']}")
        seen.add(c["clip_id"])
        if float(c["end"]) <= float(c["start"]):
            raise ValueError(f"{c['clip_id']}: end({c['end']}) 必须 > start({c['start']})")
        layer = c.get("layer") or data.get("default_layer") or "待听审标注"
        if layer not in ALLOWED_LAYERS:
            raise ValueError(f"{c['clip_id']}: layer 必须是 {'|'.join(ALLOWED_LAYERS)}，得到 {layer!r}")
        c["layer"] = layer
    return data


def generate_grid(src_path: str, label: str, grid: float, dur: float, start_at: float,
                  end_at: float, min_duration: float) -> list[dict]:
    """按 grid 秒切网格；最后不足 min_duration 的尾巴 clamp 并标 allow_short。"""
    clips = []
    end_at = min(end_at, dur) if end_at else dur
    i = 0
    s = start_at
    while s < end_at - 0.01:
        e = min(s + grid, end_at)
        piece = e - s
        allow_short = piece < min_duration
        clips.append({
            "clip_id": f"{label}_g{i:04d}",
            "start": round(s, 3),
            "end": round(e, 3),
            "allow_short": allow_short,
        })
        s = e
        i += 1
    return clips


def apply_range_layers(clips: list[dict], ranges: list[str]) -> None:
    """把 'start:end:layer' 粗分段标到网格上（命中即覆盖 layer 并写 provisional note）。"""
    for spec in ranges:
        parts = spec.split(":", 2)
        if len(parts) != 3:
            raise ValueError(f"--range-layer 格式应为 start:end:layer，得到 {spec!r}")
        rs, re_, layer = float(parts[0]), float(parts[1]), parts[2].strip()
        if layer not in ALLOWED_LAYERS:
            raise ValueError(f"layer 必须是 {'|'.join(ALLOWED_LAYERS)}，得到 {layer!r}")
        hit = 0
        for c in clips:
            if float(c["start"]) >= rs - 1e-6 and float(c["end"]) <= re_ + 1e-6:
                c["layer"] = layer
                hit += 1
        log(f"[range] {rs:.0f}-{re_:.0f}s -> {layer}: 命中 {hit} 段")


# ---------------------------------------------------------------- cut 主流程

def validate_clip_length(clip: dict, min_duration: float, max_duration: float, src_dur: float) -> float:
    plan = float(clip["end"]) - float(clip["start"])
    if plan > max_duration + 1e-6:
        raise ValueError(f"{clip['clip_id']}: 计划时长 {plan:.1f}s 超上限 {max_duration:.0f}s（切点表需修改）")
    if plan < min_duration - 1e-6 and not clip.get("allow_short"):
        raise ValueError(f"{clip['clip_id']}: 计划时长 {plan:.1f}s 低于下限 {min_duration:.0f}s 且未标 allow_short")
    if float(clip["end"]) > src_dur + 0.1 and not clip.get("allow_short"):
        raise ValueError(f"{clip['clip_id']}: end={clip['end']} 超出源时长 {src_dur:.3f}s")
    return min(plan, src_dur - float(clip["start"]))


def cmd_cut(args: argparse.Namespace) -> int:
    manifest_path = os.path.abspath(args.manifest)
    root = os.path.dirname(manifest_path)
    table = load_points_table(args.table)

    src_file = table["source_file"]
    if not os.path.isabs(src_file):
        src_file = os.path.join(root, src_file)
    if not os.path.exists(src_file):
        raise RuntimeError(f"源媒体不存在: {src_file}")
    src_dur = probe_duration(src_file)
    log(f"[源] {src_file}  时长 {src_dur:.3f}s")

    out_dir = os.path.join(root, args.out_dir) if not os.path.isabs(args.out_dir) else args.out_dir
    os.makedirs(out_dir, exist_ok=True)

    rates = [int(r) for r in str(args.rates).split(",")]
    manifest = load_manifest(manifest_path) if os.path.exists(manifest_path) else {"header": {}, "clips": []}
    by_id = {c.get("clip_id"): c for c in manifest["clips"]}

    ok = skipped = failed = 0
    failures: list[str] = []
    for clip in table["clips"]:
        cid = clip["clip_id"]
        try:
            plan = validate_clip_length(clip, args.min_duration, args.max_duration, src_dur)
        except ValueError as e:
            failed += 1
            failures.append(str(e))
            continue

        outs = []
        for rate in rates:
            suffix = RATE_SUFFIX.get(rate, f"r{rate}")
            outs.append((rate, suffix, os.path.join(out_dir, f"{cid}_{suffix}.wav")))

        if args.skip_existing and all(os.path.exists(p) for _, _, p in outs):
            entry = by_id.get(cid)
            if entry and entry.get("status") == "cut":
                skipped += 1
                continue

        measured = []
        for rate, suffix, out_path in outs:
            cut_wav(src_file, float(clip["start"]), plan, out_path, rate,
                    keep_channels=args.keep_channels, mono_16k=args.mono_16k)
            actual = probe_duration(out_path)
            if abs(actual - plan) > DUR_TOLERANCE:
                raise RuntimeError(
                    f"{cid}_{suffix}: 输出时长 {actual:.3f}s 与计划 {plan:.3f}s 偏差超 {DUR_TOLERANCE}s")
            measured.append((rate, suffix, out_path, actual))
            log(f"[cut] {cid}_{suffix}  plan={plan:.2f}s actual={actual:.3f}s ok")

        rel = lambda p: os.path.relpath(p, root).replace("\\", "/")
        main_rate, main_suffix, main_path, main_dur = measured[0]
        entry = {
            "clip_id": cid,
            "source": table["source"],
            "source_file": rel(src_file),
            "language": table["language"],
            "layer": clip.get("layer", "待听审标注"),
            "provisional": bool(clip.get("provisional", True)),
            "start": round(float(clip["start"]), 3),
            "end": round(float(clip["end"]), 3),
            "duration": round(main_dur, 3),
            "path": rel(main_path),
            "path_16k": rel(dict((r, p) for r, _, p, _ in measured).get(16000, "")) if any(r == 16000 for r, _, _, _ in measured) else None,
            "status": "cut",
            "note": clip.get("note", ""),
        }
        # path_16k 取 16kHz 档实际路径（若在 rates 里）
        for rate, _, out_path, _ in measured:
            if rate == 16000:
                entry["path_16k"] = rel(out_path)
        if entry["path_16k"] is None:
            entry.pop("path_16k")

        if cid in by_id:
            idx = manifest["clips"].index(by_id[cid])
            manifest["clips"][idx] = entry
        else:
            manifest["clips"].append(entry)
        by_id[cid] = entry
        save_manifest(manifest_path, manifest["header"], manifest["clips"])
        ok += 1

    log(f"[汇总] 切片 {ok}，跳过 {skipped}，失败 {failed}")
    if failures:
        for f_ in failures:
            log(f"  [失败] {f_}")
        return 1
    return 0


def cmd_grid(args: argparse.Namespace) -> int:
    src = os.path.abspath(args.source_file)
    if not os.path.exists(src):
        raise RuntimeError(f"源媒体不存在: {src}")
    dur = probe_duration(src)
    log(f"[源] {src}  时长 {dur:.3f}s")
    clips = generate_grid(src, args.label, args.grid, dur, args.start, args.end, args.min_duration)
    apply_range_layers(clips, args.range_layer or [])
    table = {
        "source_file": args.source_file.replace("\\", "/"),
        "source": args.source,
        "source_label": args.label,
        "language": args.language,
        "default_layer": args.layer,
        "clips": clips,
    }
    out = os.path.abspath(args.out_table)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(table, f, ensure_ascii=False, indent=2)
        f.write("\n")
    n_short = sum(1 for c in clips if c["allow_short"])
    log(f"[grid] {len(clips)} 段（含 {n_short} 段短尾） -> {out}")
    return 0


# ---------------------------------------------------------------- CLI

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="通用切片工具：网格生成切点表 + 切 wav 双列输出 + 更新清单")
    sub = p.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grid", help="按固定步长生成起止点表(JSON)")
    g.add_argument("--source-file", required=True, help="源媒体路径")
    g.add_argument("--source", required=True, choices=ALLOWED_SOURCES, help="来源")
    g.add_argument("--label", required=True, help="clip_id 前缀，如 blender_sintel")
    g.add_argument("--language", required=True, help="语言，如 en/zh")
    g.add_argument("--grid", type=float, default=30.0, help="网格步长秒（默认 30）")
    g.add_argument("--start", type=float, default=0.0, help="起始秒")
    g.add_argument("--end", type=float, default=0.0, help="结束秒（0=到片尾）")
    g.add_argument("--min-duration", type=float, default=30.0, help="最短片段秒（尾巴自动 allow_short）")
    g.add_argument("--layer", default="待听审标注", choices=ALLOWED_LAYERS, help="默认分层标签")
    g.add_argument("--range-layer", action="append", metavar="START:END:LAYER",
                   help="粗分段标签，可多次（如 0:60:音乐重）")
    g.add_argument("--out-table", required=True, help="输出切点表 JSON 路径")
    g.set_defaults(func=cmd_grid)

    c = sub.add_parser("cut", help="按切点表切片并更新清单")
    c.add_argument("--table", required=True, help="起止点表（JSON 或 YAML 子集）")
    c.add_argument("--manifest", required=True, help="clips_manifest.yaml 路径（不存在则创建）")
    c.add_argument("--out-dir", default="media/clips", help="输出目录（相对清单目录）")
    c.add_argument("--rates", default="44100,16000", help="输出的采样率列，逗号分隔（默认 44100,16000）")
    c.add_argument("--min-duration", type=float, default=30.0, help="最短片段秒")
    c.add_argument("--max-duration", type=float, default=120.0, help="最长片段秒")
    c.add_argument("--keep-channels", action="store_true", help="保留源声道布局（默认下混立体声）")
    c.add_argument("--mono-16k", action="store_true", help="16kHz 列输出单声道（供 ASR/声纹用）")
    c.add_argument("--skip-existing", action="store_true", help="输出已存在且清单已登记 cut 的片段跳过")
    c.set_defaults(func=cmd_cut)
    return p


def main(argv: list[str] | None = None) -> int:
    _setup_stdio()
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (RuntimeError, ValueError) as e:
        log(f"[错误] {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
