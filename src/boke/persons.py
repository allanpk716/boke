# -*- coding: utf-8 -*-
"""人物库:work/persons.json 存储与主持人挂接校验。

契约(spec 20260924 "Implementation Decisions" 人物库节):
- 单 JSON 文件为唯一状态源;进程内锁 + 原子写(写临时文件后 os.replace),同账本风格。
- 人物 = {name 备注名, main_lang 主要语言, avatar 头像引用(可空), note 备注,
  refs: [{audio, transcript, lang, source}]};source = {kind: "episode"|"manual",
  bvid, part, spk}(episode 时),manual 为手动上传/精选素材。
- 主持人挂接校验 check_host_attach:必须存在"参考音+配套转写稿"齐备的 ref,
  且该 ref 来源分P ≠ 当前分P(防绕 D4);不满足返回逐条缺项,不静默降级。
- 初始化:库为空时幂等种子——圆脸(refs/yuanlian_chinese_01/02 + ref01_transcript.txt)
  + 小外(work/web/audio/guest_ref_english.wav,无转写稿,cross 用)。
  种子路径默认相对仓库根,构造参数 refs_dir / web_audio_dir 可覆盖(测试用 tmp_path)。
仅标准库。
"""
import copy
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # 仓库根(种子相对路径基准)
DEFAULT_PERSONS_PATH = ROOT / "work" / "persons.json"


def _norm_part(p):
    """分P 口径归一:1 / "1" / "P1" → 1;None/空 → None。"""
    if p is None:
        return None
    s = str(p).strip().lower()
    if s.startswith("p"):
        s = s[1:]
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return s


def check_host_attach(person: dict, current_part_p):
    """主持人挂接校验(纯函数)。返回 (ok, problems);ok=False 时 problems
    逐条明示缺项:缺哪条参考音的转写稿 / 哪条参考音来自当前分P。

    通过条件:存在一条 ref 满足 audio+transcript 齐备,且
    非(source.kind=="episode" 且 source.part==当前分P)。
    """
    person = person or {}
    name = person.get("name", "?")
    refs = person.get("refs") or []
    if not refs:
        return False, [f"人物[{name}]无任何参考音,无法挂接主持人"]
    cur = _norm_part(current_part_p)
    problems, seen = [], set()
    for i, ref in enumerate(refs):
        ref = ref or {}
        audio = (ref.get("audio") or "").strip()
        transcript = (ref.get("transcript") or "").strip()
        label = Path(audio).name if audio else f"refs[{i}]"
        if not audio:
            msg = f"参考音条目[{label}]缺 audio 路径"
        elif not transcript:
            msg = f"参考音[{label}]缺配套转写稿"
        else:
            src = ref.get("source") or {}
            if cur is not None and src.get("kind") == "episode" \
                    and _norm_part(src.get("part")) == cur:
                msg = f"参考音[{label}]来自当前分P P{cur},按 D4 拒绝当期来源"
            else:
                return True, []  # 找到一条齐备且非当期的参考音即可挂接
        if msg not in seen:
            seen.add(msg)
            problems.append(msg)
    return False, problems


class PersonLibrary:
    """work/persons.json 的单写入者:所有增改经本类串行接口落盘。"""

    def __init__(self, path=None, refs_dir=None, web_audio_dir=None):
        self.path = Path(path) if path else DEFAULT_PERSONS_PATH
        self.refs_dir = Path(refs_dir) if refs_dir else ROOT / "refs"
        self.web_audio_dir = (Path(web_audio_dir) if web_audio_dir
                              else ROOT / "work" / "web" / "audio")
        self._lock = threading.Lock()
        self._data = self._load()
        if not self._data.get("persons"):
            self._seed()  # 库为空 → 幂等种子

    # ---------- 存储 ----------

    def _load(self) -> dict:
        if not self.path.exists():
            return {"version": 1, "persons": []}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(f"人物库文件损坏:{self.path}({e})") from e
        if not isinstance(data, dict) or not isinstance(data.get("persons"), list):
            raise RuntimeError(f"人物库文件结构异常:{self.path}")
        return data

    def _save(self):
        """原子写:同目录临时文件写全后 os.replace,半写入不留主文件。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self._data, ensure_ascii=False, indent=2),
                       encoding="utf-8")
        os.replace(tmp, self.path)

    def _store_path(self, p: Path) -> str:
        """种子引用路径:仓库根内记相对路径(同 synthesize 基准),否则记绝对路径。"""
        p = Path(p)
        try:
            return p.relative_to(ROOT).as_posix()
        except ValueError:
            return str(p)

    def _seed(self):
        """幂等种子:仅库为空时由 __init__ 调一次;重开库 persons 非空即不再播。"""
        t1 = self._store_path(self.refs_dir / "ref01_transcript.txt")
        # 种子 source 记 manual:同分P拒绝(D4)针对"从当期分P收进来的参考音",
        # 且校验只比分P号不分 BV,种子若记 episode/1 会误杀所有 P1 的主持人挂接。
        self._data = {"version": 1, "persons": [
            {"name": "圆脸", "main_lang": "zh", "avatar": None,
             "note": "博主本人;主持人配音走人物库 zero 克隆。"
                     "参考音精选自公开中文视频,出处见 refs/README.md。",
             "refs": [
                 {"audio": self._store_path(self.refs_dir / "yuanlian_chinese_01.wav"),
                  "transcript": t1, "lang": "zh", "source": {"kind": "manual"}},
                 # _02 同:暂与 01 共用 ref01_transcript.txt(refs/ 下仅有此稿)
                 {"audio": self._store_path(self.refs_dir / "yuanlian_chinese_02.wav"),
                  "transcript": t1, "lang": "zh", "source": {"kind": "manual"}},
             ]},
            {"name": "小外", "main_lang": "en", "avatar": None,
             "note": "常客嘉宾;英文参考音无转写稿,只作 cross 跨语言克隆。",
             "refs": [
                 {"audio": self._store_path(self.web_audio_dir / "guest_ref_english.wav"),
                  "transcript": None, "lang": "en", "source": {"kind": "manual"}},
             ]},
        ]}
        self._save()

    # ---------- 读取 ----------

    def _find(self, name):
        for p in self._data["persons"]:
            if p["name"] == name:
                return p
        return None

    def get(self, name):
        """按备注名取人物(深拷贝;改动必须走 add_* 接口,保单写入者)。"""
        with self._lock:
            p = self._find(name)
            return copy.deepcopy(p) if p else None

    def list_persons(self) -> list:
        with self._lock:
            return copy.deepcopy(self._data["persons"])

    # ---------- 写入 ----------

    def add_person(self, name, main_lang, avatar=None, note="") -> dict:
        with self._lock:
            if self._find(name):
                raise ValueError(f"人物已存在:{name}")
            p = {"name": name, "main_lang": main_lang, "avatar": avatar,
                 "note": note, "refs": []}
            self._data["persons"].append(p)
            self._save()
            return copy.deepcopy(p)

    def add_ref(self, person_name, audio, transcript=None, lang=None,
                source=None) -> dict:
        """给人物追加参考音;lang 缺省继承人物主要语言;source 缺省手动上传。"""
        with self._lock:
            p = self._find(person_name)
            if p is None:
                raise KeyError(f"人物不存在:{person_name}")
            ref = {
                "audio": str(audio),
                "transcript": str(transcript) if transcript else None,
                "lang": lang if lang else p.get("main_lang"),
                "source": dict(source) if source else {"kind": "manual"},
            }
            p["refs"].append(ref)
            self._save()
            return dict(ref)
