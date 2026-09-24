# -*- coding: utf-8 -*-
"""核对决策应用器:吃核对台提交的决策 JSON(每分P),产出可执行配置并登记。

契约(spec 20260924 "Implementation Decisions" 决策版本与缓存失效/决策应用器节):
- dec_hash = 决策 JSON 规范化(serialized sort_keys)的 sha256 前 12 位;
  同决策稳定同值,任一字段变化 ⇒ 新值。
- 该分P全部下游产物落 work/<id>/dec_<hash>/;hash 变化 ⇒ 新键,旧目录自然作废。
- 产物完整性以 .done 哨兵标记(产物内容完整落盘后最后写);断点重试仅在
  同一 dec_hash 目录内按"产物存在且哨兵在"复用,半写入文件视为不存在(F12)。
- rediarize 决策(决策 JSON 含 rediarize)不生成配置,返回 status="voided"
  作废信号,由调用方回退状态(账本 EV_REDIARIZE)。
- 主持人挂接经 persons.check_host_attach 校验(缺转写稿/同分P 一律阻断,
  抛 DecisionError,绝不生成降级配置)。

决策 JSON 形状:{verdict: dub|skip, speakers: [{id, note, host, host_person,
merge_into, voice: clone|preset|skip, preset_voice, clone_ref}],
rediarize?: {max_speakers}}。

voices.generated.yaml 格式兼容 synthesize.run 读取(episode_maps 按 file 子串
匹配视频 stem;library 条目 {type, engine, ref_audio, ref_text_file, voice}):
- 主持人   → library 条目 type=clone+engine=cosyvoice3,ref_audio/ref_text_file
             来自 persons 模块校验通过的参考音(zero 模式);
- 嘉宾克隆 → 同上但无 ref_text_file(cross 无文本模式,ref=候选原声段路径);
- preset  → type=preset+engine=edge+voice=<edge 音色>;
- skip    → map 值带 skip: true 且 voice 指向 __skip__ 占位条目,配置头注释
             说明;synthesize 的 skip 支持由票05 落地,本模块只生成配置。
- merge_into 分身不参与配音:重命名 rttm 标签后复用 boke.attribute 的
  merge+归人逻辑重算,写 tagged.dec.srt。
仅标准库(PyYAML 仅兜底读旧 voices.yaml 的 global 段时惰性导入)。
"""
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path

from .attribute import attribute
from .common import SpkSeg, SubLine, _ts, parse_rttm, parse_srt
from .persons import check_host_attach

ROOT = Path(__file__).resolve().parents[2]           # 仓库根(相对 ref 路径基准)
FALLBACK_VOICES = ROOT / "src" / "boke" / "voices.yaml"   # 旧全局兜底(只读)
SKIP_ENTRY = "__skip__"                             # skip 说话人占位条目名

VOICES_NAME = "voices.generated.yaml"
TAGGED_NAME = "tagged.dec.srt"

STATUS_APPLIED = "applied"     # 本次(重新)生成了产物
STATUS_REUSED = "reused"       # 同 hash 目录内哨兵齐备,全部复用未重写
STATUS_VOIDED = "voided"       # rediarize:决策作废,调用方回退状态


class DecisionError(Exception):
    """决策 JSON 不合法或主持人挂接校验失败;抛出前不落任何产物。"""


# ---------------- 纯函数 ----------------

def dec_hash(decision: dict) -> str:
    """决策内容哈希:规范化 JSON(sort_keys 递归)sha256 前 12 位。

    键序无关、同决策稳定;任一字段(含嵌套/列表元素)变化 ⇒ 新值。
    """
    canon = json.dumps(decision, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:12]


def is_complete(path) -> bool:
    """产物完整性判据:.done 哨兵存在(且产物本身在)。

    半写入(无哨兵)⇒ False(F12:断点重试只复用哨兵齐备的产物)。
    """
    p = Path(path)
    return p.exists() and Path(str(p) + ".done").exists()


# ---------------- 哨兵写机制 ----------------

def _atomic_write(path: Path, content: str):
    """原子写:临时文件写完 fsync 再 os.replace,半写入不留主文件。"""
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


def _write_artifact(path: Path, content: str, force: bool) -> str:
    """写产物:内容完整落盘后最后写 .done 哨兵;返回 applied/reused。

    崩溃在哨兵前 ⇒ 产物在但无哨兵 ⇒ 视为不存在,重试时重写。
    """
    if not force and is_complete(path):
        return STATUS_REUSED
    _atomic_write(path, content)
    _atomic_write(Path(str(path) + ".done"),
                  time.strftime("%Y-%m-%dT%H:%M:%S") + "\n")
    return STATUS_APPLIED


# ---------------- 校验 ----------------

def _validate(decision: dict, part, persons) -> dict:
    """决策结构与主持人挂接校验;返回 {被合并id: 最终并入id}(传递解析)。

    任何不满足抛 DecisionError(缺项逐条明示),不静默降级。
    """
    if not isinstance(decision, dict):
        raise DecisionError("决策必须是 dict(核对决策 JSON)")
    rv = decision.get("verdict")
    if rv != "dub":
        raise DecisionError(
            f"verdict 必须为 dub 才能应用(收到 {rv!r};"
            "全中文跳过走 confirm_skip 排除,重跑分人走 rediarize)")
    spks = decision.get("speakers")
    if not isinstance(spks, list):
        raise DecisionError("决策缺 speakers 列表")
    ids = []
    for i, s in enumerate(spks):
        if not isinstance(s, dict) or not str(s.get("id") or "").strip():
            raise DecisionError(f"speakers[{i}] 缺 id")
        sid = s["id"]
        if sid in ids:
            raise DecisionError(f"speakers 重复 id:{sid}")
        ids.append(sid)
    idset = set(ids)

    merge = {}
    for s in spks:
        tgt = str(s.get("merge_into") or "").strip() or None
        if tgt is None:
            continue
        if tgt not in idset:
            raise DecisionError(f"说话人 {s['id']} merge_into 指向不存在的 {tgt}")
        if tgt == s["id"]:
            raise DecisionError(f"说话人 {s['id']} merge_into 指向自己")
        merge[s["id"]] = tgt
    resolved = {}
    for sid in merge:                     # 传递解析 + 环检测(A→B→A)
        chain, cur = [sid], merge[sid]
        while cur in merge:
            if cur in chain:
                raise DecisionError(
                    f"merge_into 成环:{' → '.join(chain + [cur])}")
            chain.append(cur)
            cur = merge[cur]
        resolved[sid] = cur

    for s in spks:
        if s["id"] in resolved:
            continue                      # 被合并分身不参与配音,无需 voice 决策
        voice = str(s.get("voice") or "").strip()
        if voice == "skip":
            continue
        if s.get("host"):                 # 主持人:人物库 zero 克隆(优先于 voice 字段)
            name = str(s.get("host_person") or "").strip()
            if not name:
                raise DecisionError(f"主持人 {s['id']} 未指定 host_person")
            if persons is None:
                raise DecisionError(
                    f"主持人 {s['id']} 挂接 [{name}] 需提供 PersonLibrary")
            person = persons.get(name)
            if person is None:
                raise DecisionError(f"人物库无 [{name}](主持人 {s['id']} 挂接失败)")
            ok, problems = check_host_attach(person, part)
            if not ok:
                raise DecisionError(
                    f"主持人 {s['id']} 挂接 [{name}] 校验失败:" + ";".join(problems))
            continue
        if voice == "clone":
            if not str(s.get("clone_ref") or "").strip():
                raise DecisionError(
                    f"说话人 {s['id']} 选克隆但缺 clone_ref(候选原声段路径)")
            continue
        if voice == "preset":
            if not str(s.get("preset_voice") or "").strip():
                raise DecisionError(f"说话人 {s['id']} 选预设但缺 preset_voice")
            continue
        raise DecisionError(
            f"说话人 {s['id']} 缺有效 voice 决策(clone|preset|skip,收到 {voice!r})")
    return resolved


def _pick_host_ref(person: dict, part):
    """取第一条能通过 check_host_attach 的 ref(整人校验已过的前提下必命中)。

    单条 ref 套用与整人同一校验函数,选取判据零漂移。
    """
    for ref in person.get("refs") or []:
        ok, _ = check_host_attach({**person, "refs": [ref]}, part)
        if ok:
            return ref
    return None


def _resolve(p) -> Path:
    """相对路径按仓库根解析(与 persons 存储基准一致);绝对路径原样。"""
    q = Path(str(p))
    return q if q.is_absolute() else ROOT / q


# ---------------- voices.generated.yaml 生成 ----------------

def _build_voices(decision: dict, resolved: dict, part, persons):
    """决策 → (library 条目表, SPK→map 表)。优先级:skip > 主持人 > voice。"""
    lib, mapping = {}, {}
    for s in decision["speakers"]:
        sid = s["id"]
        if sid in resolved:
            continue                      # 合并分身不进 map/不建条目
        who = str(s.get("note") or "").strip()
        voice = str(s.get("voice") or "").strip()
        entry = sid.lower()               # 条目名与说话人一一对应(如 spk_00)
        if voice == "skip":
            lib[SKIP_ENTRY] = {
                "type": "skip",
                "note": "跳过占位:该说话人片段保留原声不配音(U4);"
                        "synthesize 的 skip 支持由票05 落地",
            }
            mapping[sid] = {"voice": SKIP_ENTRY, "who": who, "skip": True}
            continue
        if s.get("host"):
            person = persons.get(s["host_person"])
            ref = _pick_host_ref(person, part)
            lib[entry] = {
                "type": "clone", "engine": "cosyvoice3",
                "ref_audio": _resolve(ref["audio"]).as_posix(),
                "ref_text_file": _resolve(ref["transcript"]).as_posix(),
                "note": f"主持人→人物库[{person['name']}] zero 克隆"
                        "(参考音+转写稿经 persons 校验)",
            }
            mapping[sid] = {"voice": entry, "who": who}
            continue
        if voice == "clone":
            lib[entry] = {
                "type": "clone", "engine": "cosyvoice3",
                "ref_audio": _resolve(s["clone_ref"]).as_posix(),
                "note": "嘉宾 cross 无文本克隆(候选原声段)",
            }
            mapping[sid] = {"voice": entry, "who": who}
            continue
        # preset
        lib[entry] = {
            "type": "preset", "engine": "edge",
            "voice": s["preset_voice"],
            "note": "预设音色(edge-tts)",
        }
        mapping[sid] = {"voice": entry, "who": who}
    return lib, mapping


def _y(v) -> str:
    """YAML 双引号标量:JSON 字符串转义是 YAML 双引号转义的合法子集。"""
    return json.dumps(str(v), ensure_ascii=False)


_LIB_FIELDS = ("type", "engine", "voice", "ref_audio", "ref_text_file", "note")


def _emit_voices_yaml(lib: dict, mapping: dict, *, vid: str, h: str,
                      global_cfg: dict) -> str:
    """确定性手写 YAML(键排序/字段定序),保留头注释与 skip 约定说明。"""
    L = [
        f"# voices.generated.yaml —— boke.review_apply 按核对决策自动生成"
        f"(dec_hash={h})。",
        "# 勿手改:改决策请回核对台重新提交,新决策落新 dec_<hash> 目录,"
        "本目录自然作废。",
    ]
    if SKIP_ENTRY in lib:
        L.append(f"# skip 约定:map 值带 skip: true 且 voice 指向 {SKIP_ENTRY} "
                 "占位条目 ——")
        L.append("#           该说话人片段保留原声不配音(U4);synthesize 的 "
                 "skip 支持由票05 落地,")
        L.append("#           在此之前含 skip 说话人的本配置不要直接跑合成。")
    L.append("global:")
    if global_cfg:
        L.extend(f"  {_y(k)}: {_y(global_cfg[k])}" for k in sorted(global_cfg))
    else:
        L.append("  {}")
    L.append("library:")
    if lib:
        for name in sorted(lib):
            L.append(f"  {_y(name)}:")
            L.extend(f"    {f}: {_y(lib[name][f])}"
                     for f in _LIB_FIELDS if f in lib[name])
    else:
        L.append("  {}")
    L.append("episode_maps:")
    L.append(f"  - file: {_y(vid)}")
    if mapping:
        L.append("    map:")
        for sid in sorted(mapping):
            v = mapping[sid]
            inner = [f"voice: {_y(v['voice'])}", f"who: {_y(v['who'])}"]
            if v.get("skip"):
                inner.append("skip: true")
            L.append(f"      {_y(sid)}: {{{', '.join(inner)}}}")
    else:
        L.append("    map: {}")
    return "\n".join(L) + "\n"


def _default_global() -> dict:
    """旧 src/boke/voices.yaml 只读兜底:取其 global 段(克隆引擎路径)。"""
    try:
        import yaml
        cfg = yaml.safe_load(FALLBACK_VOICES.read_text(encoding="utf-8")) or {}
        g = cfg.get("global")
        return dict(g) if isinstance(g, dict) else {}
    except Exception:
        return {}


# ---------------- 合并归人重算 ----------------

def _merged_tagged_text(wdir: Path, resolved: dict, srt=None, rttm=None) -> str:
    """merge_into 应用:rttm 标签改名 → 复用 attribute 的 merge+归人逻辑重算,
    产出 tagged.dec.srt 文本(格式同 attribute.write_outputs 的 tagged.srt)。"""
    srt_path = Path(srt) if srt else wdir / "ocr.srt"
    rttm_path = Path(rttm) if rttm else wdir / "diar.rttm"
    for p, what in ((srt_path, "字幕(ocr.srt)"), (rttm_path, "分人(diar.rttm)")):
        if not p.exists():
            raise DecisionError(f"缺{what}产物:{p} —— 先完成分析才能应用决策")
    subs = parse_srt(srt_path)
    segs = parse_rttm(rttm_path)          # 解析时已做相邻合并
    if resolved:
        segs = [SpkSeg(t0=s.t0, t1=s.t1,
                       spk=resolved.get(s.spk, s.spk)) for s in segs]
    rows = attribute(subs, segs)          # 内部再 merge_adjacent + 最大重叠归人
    tagged = [SubLine(idx=r["idx"], t0=r["t0"], t1=r["t1"],
                      text=f"[{r['spk']}] {r['text']}") for r in rows]
    return "\n".join(f"{i}\n{_ts(s.t0)} --> {_ts(s.t1)}\n{s.text}\n"
                     for i, s in enumerate(tagged, 1))


# ---------------- 共用生成核心 ----------------

def _render_core(decision, video, work_dir, *, part=None, persons=None,
                 force=False, global_cfg=None, scope=None) -> dict:
    """共用生成逻辑(render_preview/render_full/apply_decision 同源):
    rediarize 判废 → 校验 → dec_<hash> 目录 → voices.generated.yaml + 哨兵。
    小样与全片同决策同 hash,落同一目录。"""
    if not isinstance(decision, dict):
        raise DecisionError("决策必须是 dict(核对决策 JSON)")
    if decision.get("rediarize"):
        return {"status": STATUS_VOIDED, "scope": scope,
                "rediarize": decision["rediarize"],
                "dec_hash": dec_hash(decision)}
    resolved = _validate(decision, part, persons)
    vid = Path(video).stem
    h = dec_hash(decision)
    dec_dir = Path(work_dir) / vid / f"dec_{h}"
    lib, mapping = _build_voices(decision, resolved, part, persons)
    gcfg = dict(global_cfg) if global_cfg is not None else _default_global()
    text = _emit_voices_yaml(lib, mapping, vid=vid, h=h, global_cfg=gcfg)
    state = _write_artifact(dec_dir / VOICES_NAME, text, force)
    return {"status": state, "scope": scope, "dec_hash": h,
            "dec_dir": str(dec_dir), "voices": str(dec_dir / VOICES_NAME),
            "merged": resolved}


# ---------------- 公开入口 ----------------

def apply_decision(decision, video, work_dir="work", *, part=None, persons=None,
                   srt=None, rttm=None, force=False, global_cfg=None) -> dict:
    """应用核对决策(每分P):校验 → work/<id>/dec_<hash>/ 生成
    voices.generated.yaml 与 tagged.dec.srt(各配 .done 哨兵)。

    video 取 stem 作 work/<id> 的 id(与 attribute/synthesize 口径一致);
    part 用于主持人同分P拒绝(D4);persons 为 PersonLibrary(有主持人时必给);
    srt/rttm 缺省 work/<id>/ocr.srt 与 diar.rttm;force=True 同 hash 也重写
    (默认只补缺失/无哨兵的产物,齐备即复用)。

    返回:{status: applied|reused|voided, dec_hash, dec_dir, voices, tagged,
    merged:{被合并id:最终目标}, rediarize?};rediarize 决策返回 voided
    信号且不生成任何配置,由调用方回退状态(账本 EV_REDIARIZE)。
    校验失败抛 DecisionError,不落任何产物、不降级。
    """
    core = _render_core(decision, video, work_dir, part=part, persons=persons,
                        force=force, global_cfg=global_cfg)
    if core["status"] == STATUS_VOIDED:
        return core
    dec_dir = Path(core["dec_dir"])
    tagged_text = _merged_tagged_text(dec_dir.parent, core["merged"],
                                      srt=srt, rttm=rttm)
    if _write_artifact(dec_dir / TAGGED_NAME, tagged_text, force) == STATUS_APPLIED:
        core["status"] = STATUS_APPLIED
    core["tagged"] = str(dec_dir / TAGGED_NAME)
    return core


def render_preview_config(decision, video, work_dir="work", *, part=None,
                          persons=None, force=False, global_cfg=None) -> dict:
    """小样(预览)音色配置:与全片共用生成逻辑,同决策同 dec_<hash> 目录。"""
    return _render_core(decision, video, work_dir, part=part, persons=persons,
                        force=force, global_cfg=global_cfg, scope="preview")


def render_full_config(decision, video, work_dir="work", *, part=None,
                       persons=None, force=False, global_cfg=None) -> dict:
    """全片音色配置:与全片共用生成逻辑,同决策同 dec_<hash> 目录。"""
    return _render_core(decision, video, work_dir, part=part, persons=persons,
                        force=force, global_cfg=global_cfg, scope="full")
