# -*- coding: utf-8 -*-
"""R3 变速定标揭盲:各档自然率 + 拐点。
run: <repo>/.venv-lab/Scripts/python.exe analyze_r3.py
拐点定义: 自然率(grade==1 占比) ≥ 75% 的最大档。
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ANS = HERE.parent / "results" / "abx" / "answers_r3.json"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    items = json.loads((HERE / "r3_items.json").read_text(encoding="utf-8"))
    ans = json.loads(ANS.read_text(encoding="utf-8"))
    by_t = {}
    for it in items:
        g = ans.get(it["item"], {}).get("grade")
        by_t.setdefault(it["tempo"], []).append((it["item"], g))
    print(f"{'tempo':>5} {'自然':>4}/{'n':<3} 自然率  有点赶 不能接受")
    rows = []
    for t in sorted(by_t):
        gs = [g for _, g in by_t[t] if g]
        n = len(gs)
        if not n:
            print(f"{t:>5}  (未评)")
            continue
        nat = sum(1 for g in gs if g == 1)
        rush = sum(1 for g in gs if g == 2)
        bad = sum(1 for g in gs if g == 3)
        rate = nat / n
        rows.append((t, rate, n))
        print(f"{t:>5} {nat:>4}/{n:<3} {rate:>5.0%}  {rush:>4}  {bad:>4}")
    knee = max((t for t, r, n in rows if r >= 0.75), default=None)
    print(f"\n拐点(MAX_TEMPO 建议): {knee}")
    print("口径: 自然率≥75% 的最大变速档; n=每档 4 句(2 zh向 + 2 en向)。")


if __name__ == "__main__":
    main()
