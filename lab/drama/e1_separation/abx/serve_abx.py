# -*- coding: utf-8 -*-
"""R1 盲听手机页(ABX 分离对照臂)——零依赖,仅标准库,手机浏览器访问。

用法(系统 python 即可): python serve_abx.py [--port 8799]
- 页面: http://<本机Tailscale IP>:8799/
- 音频: lab/drama/media/abx/play/T<nn>_{1,2,X}.wav(中性命名,不下发映射关系)
- 答案: lab/drama/results/abx/answers_web.json(原子写,可改答案;不回填 trials json)
- 盲听纪律: 服务端永不返回 X/play_order/A/B;答案只存 tid→"1"/"2";完成页不显示对错
"""
import argparse
import json
import os
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent            # .../lab/drama/e1_separation/abx
DRAMA = HERE.parents[1]                           # .../lab/drama
RESULTS = DRAMA / "results" / "abx"
PLAY = DRAMA / "media" / "abx" / "play"
TRIALS_FILE = sorted(RESULTS.glob("trials_*.json"))[0]
ANSWERS_FILE = RESULTS / "answers_web.json"

_LOCK = threading.Lock()
_WAV_RE = re.compile(r"^(T\d{2})/([12X])\.wav$")
_TRIAL_ORDER = [t["trial_id"] for t in json.loads(
    TRIALS_FILE.read_text(encoding="utf-8"))["trials"]]


def _load_answers():
    try:
        return json.loads(ANSWERS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_answers(a):
    ANSWERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(ANSWERS_FILE.parent),
                               prefix=".answers_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(a, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ANSWERS_FILE)


PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>R1 盲听</title><style>
:root{color-scheme:dark}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#111;color:#eee;font:17px/1.5 system-ui,-apple-system,"PingFang SC",sans-serif;padding:max(12px,env(safe-area-inset-top)) 16px 40px}
h1{font-size:20px;margin:8px 0 2px}
.sub{color:#9a9;font-size:13px;margin-bottom:10px}
details{background:#1b1b1b;border:1px solid #333;border-radius:10px;padding:10px 12px;margin:8px 0;font-size:15px}
summary{cursor:pointer;font-weight:600}
.bar{height:6px;background:#2a2a2a;border-radius:3px;overflow:hidden;margin:10px 0}
.bar>i{display:block;height:100%;background:#7c6;width:0%}
.tid{color:#9a9;font-size:13px;letter-spacing:1px}
.card{background:#1b1b1b;border:1px solid #333;border-radius:14px;padding:14px;margin:10px 0}
.samp{margin:12px 0}
.samp .lbl{font-weight:600;margin-bottom:4px}
audio{width:100%;height:40px}
.opts{display:flex;gap:10px;margin-top:14px}
button.opt{flex:1;padding:16px 8px;font-size:18px;border-radius:12px;border:2px solid #555;background:#222;color:#eee;font-weight:700}
button.opt.on{border-color:#7c6;background:#2d3a26}
button.mini{padding:8px 14px;border-radius:9px;border:1px solid #555;background:#222;color:#ccc;font-size:14px;margin:0 6px 6px 0}
.done{color:#7c6;font-size:15px}
.hide{display:none}
#fin{padding:30px 14px;text-align:center}
.big{font-size:22px;font-weight:700;margin:12px 0}
</style></head><body>
<h1>R1 盲听 · 分离对照臂</h1>
<div class="sub">耳机 · 安静环境 · 约 20–30 分钟 · 可中途休息(进度自动保存)</div>
<details><summary>怎么听(先读)</summary>
<p>每题三段音频:<b>样本1、样本2、X</b>。按顺序各听一遍(可重听),
判断 <b>X 更像样本1还是样本2</b>,点下方对应按钮。不确定也要凭直觉选。
共 18 题。答案随时可改,离开页面不丢。</p></details>
<div class="bar"><i id="pb"></i></div><div id="pt" class="sub"></div>
<div id="q">
  <div class="card">
    <div class="tid" id="tid"></div>
    <div class="samp"><div class="lbl">▶ 样本 1</div><audio id="a1" controls preload="metadata"></audio></div>
    <div class="samp"><div class="lbl">▶ 样本 2</div><audio id="a2" controls preload="metadata"></audio></div>
    <div class="samp"><div class="lbl">▶ X(判断对象)</div><audio id="ax" controls preload="metadata"></audio></div>
    <div class="opts">
      <button class="opt" id="o1">X 是样本 1</button>
      <button class="opt" id="o2">X 是样本 2</button>
    </div>
  </div>
  <button class="mini" id="prev">← 上一题</button>
  <button class="mini" id="next">下一题 →</button>
  <span class="sub" id="hint"></span>
</div>
<div id="fin" class="hide">
  <div class="big">✅ 全部 18 题已作答</div>
  <p>感谢!答案已保存到服务端(<code>answers_web.json</code>)。<br>
  你可以回去了——对错分析按 play_order 分支公式稍后进行,此处不显示结果。<br>
  想改答案:点下方按钮回到题目。</p>
  <button class="mini" id="back">回题目改答案</button>
</div>
<script>
let order=[],ans={},i=0;
const $=id=>document.getElementById(id);
function render(){
 const n=order.length,done=order.filter(t=>ans[t]).length;
 $('pb').style.width=(100*done/n)+'%';
 $('pt').textContent=done+' / '+n+' 已作答';
 if(done===n){$('q').classList.add('hide');$('fin').classList.remove('hide');return}
 $('fin').classList.add('hide');$('q').classList.remove('hide');
 while(!order[i]||ans[order[i]]){ if(!order[i]) i=0; if(ans[order[i]]) i=(i+1)%n; }
 const t=order[i];
 $('tid').textContent='第 '+(order.indexOf(t)+1)+' 题 · 共'+n+'题'+(ans[t]?'(已答,可改)':'');
 $('a1').src='/audio/'+t+'/1.wav';$('a2').src='/audio/'+t+'/2.wav';$('ax').src='/audio/'+t+'/X.wav';
 ['a1','a2','ax'].forEach(x=>{const e=$(x);e.pause();try{e.currentTime=0}catch(_){}});
 $('o1').classList.toggle('on',ans[t]==='1');
 $('o2').classList.toggle('on',ans[t]==='2');
 $('hint').textContent='';
}
async function pick(v){
 const t=order[i],prev=ans[t];
 ans[t]=v;render();
 try{await fetch('/api/answer',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({trial_id:t,answer:v})});}
 catch(e){ans[t]=prev;render();$('hint').textContent='⚠ 保存失败,请检查网络后重选';}
}
async function init(){
 const r=await fetch('/api/state');const d=await r.json();
 order=d.order;ans=d.answers;render();
}
$('o1').onclick=()=>pick('1');$('o2').onclick=()=>pick('2');
$('prev').onclick=()=>{i=(i-1+order.length)%order.length;render()};
$('next').onclick=()=>{i=(i+1)%order.length;render()};
$('back').onclick=()=>{$('fin').classList.add('hide');$('q').classList.remove('hide');i=0;render()};
init();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"   # 手机媒体栈要求 keep-alive;配合 Range/206

    def log_message(self, fmt, *args):
        print("[abx]", self.address_string(), fmt % args)

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            b = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
        elif self.path == "/api/state":
            with _LOCK:
                a = _load_answers()
            self._json({"order": _TRIAL_ORDER,
                        "answers": {k: v for k, v in a.items()
                                    if k in _TRIAL_ORDER and v in ("1", "2")}})
        elif self.path.startswith("/audio/"):
            m = _WAV_RE.match(self.path[7:])
            if not m:
                return self._json({"error": "bad name"}, 400)
            tid, tag = m.groups()
            if tid not in _TRIAL_ORDER:
                return self._json({"error": "no such trial"}, 404)
            f = PLAY / f"{tid}_{tag}.wav"
            if not f.is_file():
                return self._json({"error": "missing"}, 404)
            size = f.stat().st_size
            # Range/206:iOS/Android 媒体栈会发 Range 请求,不支持就报错
            start, end = 0, size - 1
            rng = self.headers.get("Range")
            partial = False
            if rng:
                mm = re.match(r"bytes=(\d*)-(\d*)$", rng.strip())
                if mm:
                    s0, s1 = mm.groups()
                    if s0:
                        start = int(s0)
                        end = int(s1) if s1 else size - 1
                    elif s1:            # suffix: bytes=-N (末尾 N 字节)
                        start = max(0, size - int(s1))
                    end = min(end, size - 1)
                    partial = True
            if start > end or start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            length = end - start + 1
            self.send_response(206 if partial else 200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if partial:
                self.send_header("Content-Range",
                                 f"bytes {start}-{end}/{size}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(f, "rb") as fh:
                fh.seek(start)
                left = length
                while left > 0:
                    chunk = fh.read(min(65536, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        else:
            self._json({"error": "not found"}, 404)

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if self.path != "/api/answer":
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 256:
                raise ValueError("too big")
            d = json.loads(self.rfile.read(n).decode("utf-8"))
            tid, v = d.get("trial_id"), d.get("answer")
            if tid not in _TRIAL_ORDER or v not in ("1", "2"):
                raise ValueError("bad payload")
        except Exception:
            return self._json({"error": "bad request"}, 400)
        with _LOCK:
            a = _load_answers()
            a[tid] = v
            _save_answers(a)
        self._json({"ok": True, "saved": tid})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8799)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), H)
    print(f"R1 盲听页: http://<本机IP>:{args.port}/  (音频目录 {PLAY})")
    print(f"试次 {len(_TRIAL_ORDER)} 个,顺序种子沿用 trials json;答案落 {ANSWERS_FILE}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
