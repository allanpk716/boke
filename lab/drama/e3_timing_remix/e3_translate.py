# -*- coding: utf-8 -*-
"""E3 步骤2:LLM 翻译两臂。
- A 自由译: 不限长,口语配音腔
- B 音节预算: 给原句窗秒数,要 3 个能塞进窗的候选+各自估时
run: <repo>/.venv-lab/Scripts/python.exe e3_translate.py   (系统 python 也行,只用 urllib)
输出: e3_translations.json
"""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODEL = "glm-5.3-flash"
FALLBACKS = ["glm-5.3-flashx", "qwen3.8-flash"]
URL = "https://aihubmix.com/v1/chat/completions"

GLOSSARY_EN2ZH = "人名固定音译: Sintel=辛特尔, Scales=斯凯尔斯(龙), Shaman/old man=老人"


def ask(messages, max_tokens=7000, tries=4):
    body = {"model": MODEL, "messages": messages, "max_tokens": max_tokens}
    key = os.environ["AIHUBMIX_API_KEY"]
    last = None
    for k in range(tries):
        try:
            req = urllib.request.Request(
                URL, data=json.dumps(body).encode(), method="POST",
                headers={"Authorization": "Bearer " + key,
                         "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=240) as r:
                d = json.loads(r.read().decode())
            return d["choices"][0]["message"]["content"]
        except Exception as e:
            last = e
            if k + 1 < len(FALLBACKS) + 1 and k >= 1:
                body["model"] = FALLBACKS[min(k - 1, len(FALLBACKS) - 1)]
                print(f"  [retry {k+1}] {e} → 换 {body['model']}")
            else:
                print(f"  [retry {k+1}] {e}")
            time.sleep(4 * (k + 1))
    raise RuntimeError(f"LLM 多次失败: {last}")


def extract_json(s):
    """容忍模型在 JSON 前后加话:优先 ``` 块,再扫描第一个可解析值。"""
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", s)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            pass
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch in "[{":
            try:
                return dec.raw_decode(s, i)[0]
            except Exception:
                continue
    raise ValueError(f"no JSON in: {s[:200]!r}")


def arm_a(batch, tgt, extra):
    """自由译,每批 8 句。"""
    lines = "\n".join(f"{i+1}. {it['text']}" for i, it in enumerate(batch))
    sysmsg = (f"You are a professional film dubbing translator. Translate the "
              f"numbered { 'English' if tgt=='zh' else 'Chinese' } lines into "
              f"{'natural spoken Chinese' if tgt=='zh' else 'natural spoken English'} "
              f"suitable for voice-over dubbing: colloquial, in-character, "
              f"no subtitles style, no notes. {extra}")
    msg = ask([{"role": "system", "content": sysmsg},
               {"role": "user", "content": lines + "\n\nReturn ONLY a JSON array "
                "of strings, same order and count."}])
    arr = extract_json(msg)
    assert isinstance(arr, list) and len(arr) == len(batch), f"A 臂数量不符: {arr}"
    return [str(x).strip() for x in arr]


def arm_b(batch, tgt, extra):
    """音节预算:给窗秒数,3 候选+各自估时。"""
    lines = []
    for i, it in enumerate(batch):
        lines.append(f"{i+1}. [{it['win_dur']:.1f}s window] {it['text']}")
    sysmsg = (f"You are a professional dubbing translator doing TIME-CONSTRAINED "
              f"translation. Each numbered line shows the original "
              f"{'English' if tgt=='zh' else 'Chinese'} line and the exact number "
              f"of seconds the dubbed {'Chinese' if tgt=='zh' else 'English'} "
              f"line must fit into. For each line produce 3 translation "
              f"candidates that a voice actor can speak NATURALLY within that "
              f"window (typical natural pace: Chinese ≈ 4.0 chars/sec, English "
              f"≈ 2.6 words/sec). Keep core meaning; trim modifiers/fillers, "
              f"never add. Candidates should span lengths: one comfortable fit, "
              f"one shorter safety option, one maximal-fill option. {extra}")
    msg = ask([{"role": "system", "content": sysmsg},
               {"role": "user", "content": "\n".join(lines) +
                "\n\nReturn ONLY JSON: [{\"i\":1,\"cands\":[{\"t\":\"...\","
                "\"est_s\":2.9},...]}, ...] same order/count as input."}])
    arr = extract_json(msg)
    assert isinstance(arr, list) and len(arr) == len(batch), f"B 臂数量不符: {arr}"
    out = []
    for row in arr:
        cands = [{"t": str(c["t"]).strip(), "est_s": float(c.get("est_s", 0))}
                 for c in row["cands"][:3] if str(c.get("t", "")).strip()]
        assert len(cands) >= 1, f"B 臂候选空: {row}"
        out.append(cands)
    return out


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    win = json.loads((HERE / "e3_windows_final.json").read_text(encoding="utf-8"))
    out_path = HERE / "e3_translations.json"
    done = {}
    if out_path.is_file():
        done = {x["id"]: x for x in json.loads(
            out_path.read_text(encoding="utf-8"))}
    todo = [w for w in win if w["id"] not in done]
    by_dir = {"en2zh": [w for w in todo if w["dir"] == "en2zh"],
              "zh2en": [w for w in todo if w["dir"] == "zh2en"]}
    for d, items in by_dir.items():
        if not items:
            continue
        tgt = "zh" if d == "en2zh" else "en"
        extra = GLOSSARY_EN2ZH if d == "en2zh" else \
            "Military drama register, gruff colonel speech; mild profanity OK."
        for s in range(0, len(items), 4):
            batch = items[s:s + 4]
            print(f"[{d}] batch {s//4+1}: {[b['id'] for b in batch]}", flush=True)
            try:
                free = arm_a(batch, tgt, extra)
            except Exception as e:
                print(f"  [A 批失败 {e}] → 逐句重试", flush=True)
                free = [arm_a([it], tgt, extra)[0] for it in batch]
            try:
                cands = arm_b(batch, tgt, extra)
            except Exception as e:
                print(f"  [B 批失败 {e}] → 逐句重试", flush=True)
                cands = [arm_b([it], tgt, extra)[0] for it in batch]
            for it, f, c in zip(batch, free, cands):
                done[it["id"]] = {"id": it["id"], "dir": d,
                                  "win_dur": it["win_dur"],
                                  "text": it["text"], "free": f, "cands": c}
                print(f"  {it['id']} win={it['win_dur']}s free={f[:36]!r} "
                      f"cands={len(c)}")
            out_path.write_text(
                json.dumps(list(done.values()), ensure_ascii=False, indent=1),
                encoding="utf-8")
    print("e3_translations.json:", len(done))


if __name__ == "__main__":
    main()
