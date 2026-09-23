# -*- coding: utf-8 -*-
"""stage B: OpenCV 抽帧 → 底部 ROI → RapidOCR → 相邻帧文本相似度合并 → ocr.srt。

选型依据 01 §2/02 §④:RapidOCR(PaddleOCR 模型的 ONNX 版)避开 PaddleGPU 环境坑。
抽帧 2~4fps 起步;行起止 = 首末命中帧时刻 ± 半个帧间隔(单调化防重叠)。
"""
import time
from difflib import SequenceMatcher
from pathlib import Path

from .common import SubLine, write_srt

# 相似度阈值:高于它认为是"同一句字幕仍在画面上"
SIM_TH = 0.75
MIN_CONF = 0.5
MIN_CHARS = 2          # 去空白后至少 2 个字符
MIN_LINE_DUR = 0.3     # 行最短时长(秒)


def _norm(text: str) -> str:
    return "".join(text.split())


def _clean_text(text: str) -> str:
    """去角标杂质:剥离边缘短 ASCII 数字/字母串(保留行内合法数字),丢弃纯杂行。"""
    import re
    has_cjk = re.search(r"[一-鿿]", text)
    if not has_cjk:
        return text if re.fullmatch(r"[0-9A-Za-z.%]{6,}", text) else ""
    # 边缘 1~5 位 ASCII 串后接中文 → 是角标污染,剥掉
    text = re.sub(r"^[0-9A-Za-z.]{1,5}(?=[一-鿿])", "", text)
    text = re.sub(r"(?<=[一-鿿])[0-9A-Za-z.]{1,5}$", "", text)
    return text.strip()


def similar(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


class OcrFrameResult:
    __slots__ = ("ts", "text", "conf")

    def __init__(self, ts, text, conf):
        self.ts = ts
        self.text = text
        self.conf = conf


def frames_from_video(video, fps=2.0, roi=(0.90, 0.99)):
    """抽帧 + OCR,产出 [OcrFrameResult]。roi=(y0比例, y1比例)"""
    import cv2
    from rapidocr_onnxruntime import RapidOCR

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video}")
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    step = max(1, round(src_fps / fps))
    ocr = RapidOCR()

    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    y0 = int(H * roi[0])
    y1 = int(H * roi[1])

    results = []
    fidx = 0
    t_start = time.time()
    n_ocr = 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if fidx % step == 0:
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                break
            crop = frame[y0:y1, :]
            res, _ = ocr(crop)
            ts = fidx / src_fps
            n_ocr += 1
            if res:
                # 字幕通常单行;多 box 按位置拼接(中文不加空格)
                res_sorted = sorted(res, key=lambda r: (r[0][0][1], r[0][0][0]))
                text = _norm("".join(r[1] for r in res_sorted))
                conf = min(float(r[2]) for r in res_sorted)
                if len(text) >= MIN_CHARS and conf >= MIN_CONF:
                    results.append(OcrFrameResult(ts, text, conf))
        fidx += 1
        if n_ocr and n_ocr % 200 == 0:
            el = time.time() - t_start
            print(f"[ocr] {n_ocr} frames ocr'd, {len(results)} hits, {el:.0f}s elapsed")
    cap.release()
    print(f"[ocr] done: {n_ocr} frames (src {src_fps:.2f}fps, video "
          f"{(fidx/src_fps/60):.1f}min), {len(results)} text hits")
    return results


def merge_to_lines(hits, interval) -> list:
    """相邻帧文本相似合并成行;时间 = 首帧-半间隔 ~ 末帧+半间隔,单调化。"""
    lines = []
    cur = None  # [texts, first_ts, last_ts]
    for h in hits:
        if cur is not None and similar(cur[0][-1], h.text) >= SIM_TH:
            cur[0].append(h.text)
            cur[2] = h.ts
        else:
            if cur:
                lines.append(tuple(cur))
            cur = [[h.text], h.ts, h.ts]
    if cur:
        lines.append(tuple(cur))

    subs = []
    prev_t1 = 0.0
    half = interval / 2.0
    for i, (texts, t0f, t1f) in enumerate(lines, 1):
        # 行文本取"最长"的那次(中间帧可能有部分识别更好的),并净化角标杂质
        text = _clean_text(max(texts, key=len))
        if not text:
            continue
        t0 = max(0.0, t0f - half)
        t1 = t1f + half
        # 单调化:不与上一行重叠
        t0 = max(t0, prev_t1 + 0.01)
        t1 = max(t1, t0 + MIN_LINE_DUR)
        subs.append(SubLine(idx=i, t0=round(t0, 3), t1=round(t1, 3), text=text))
        prev_t1 = t1
    return subs


def run(video, work_dir="work", fps=2.0, roi=(0.90, 0.99), force=False,
        mock=False) -> dict:
    vid = Path(video).stem
    wdir = Path(work_dir) / vid
    wdir.mkdir(parents=True, exist_ok=True)
    srt = wdir / "ocr.srt"
    if srt.exists() and not force:
        return {"srt": str(srt), "skipped": True}

    if mock:
        subs = [SubLine(1, 1.0, 4.0, "这是一条模拟字幕"),
                SubLine(2, 5.0, 8.5, "用于流水线冒烟测试"),
                SubLine(3, 9.0, 12.0, "第三句模拟字幕")]
    else:
        hits = frames_from_video(video, fps=fps, roi=roi)
        interval = 1.0 / fps
        subs = merge_to_lines(hits, interval)
        if not subs:
            raise RuntimeError("OCR 未产出任何字幕行(检查 ROI/画质)")

    write_srt(srt, subs)
    stats = {"lines": len(subs),
             "avg_dur": round(sum(s.t1 - s.t0 for s in subs) / max(1, len(subs)), 2)}
    (wdir / "ocr_stats.json").write_text(
        __import__("json").dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"srt": str(srt), **stats}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--fps", type=float, default=2.0)
    ap.add_argument("--mock", action="store_true")
    a = ap.parse_args()
    print(run(a.video, fps=a.fps, mock=a.mock))
