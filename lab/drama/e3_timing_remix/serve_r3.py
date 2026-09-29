# -*- coding: utf-8 -*-
"""R3 变速阈值定标打分页:每题一段(某句某变速档),盲评"赶不赶"。
run(系统 python): python serve_r3.py [--port 8796]
- 变速档不下发,题序固定种子打乱(同句不同档不相邻)
- 答案: lab/drama/results/abx/answers_r3.json {item: {"grade": 1|2|3}}
  1=自然 2=有点赶/怪 3=不能接受
"""
import argparse
import json
import os
import random
import re
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
ITEMS = json.loads((HERE / "r3_items.json").read_text(encoding="utf-8"))
AUD = HERE / "r3"
ANSWERS = HERE.parent / "results" / "abx" / "answers_r3.json"

_BY = {x["item"]: x for x in ITEMS}
# 打乱呈现序:同句(sid)各档尽量隔开 + 固定种子
rng = random.Random(20260929)
_order = ITEMS[:]
rng.shuffle(_order)
for _ in range(4):  # 冒泡换位消除相邻同 sid
    for i in range(len(_order) - 1):
        if _order[i]["sid"] == _order[i + 1]["sid"] and i + 2 < len(_order):
            _order[i + 1], _order[i + 2] = _order[i + 2], _order[i + 1]
_ORDER = [x["item"] for x in _order]
_LOCK = threading.Lock()


def _load():
    try:
        return json.loads(ANSWERS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save(a):
    ANSWERS.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(ANSWERS.parent), prefix=".r3_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(a, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ANSWERS)


PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>语速自然度定标</title><style>
:root{color-scheme:dark}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#111;color:#eee;font:17px/1.5 system-ui,-apple-system,"PingFang SC",sans-serif;padding:max(12px,env(safe-area-inset-top)) 16px 40px}
h1{font-size:20px;margin:8px 0 2px}
.sub{color:#9a9;font-size:13px;margin-bottom:10px}
details{background:#1b1b1b;border:1px solid #333;border-radius:10px;padding:10px 12px;margin:8px 0;font-size:15px}
summary{cursor:pointer;font-weight:600}
.bar{height:6px;background:#2a2a2a;border-radius:3px;overflow:hidden;margin:10px 0}
.bar>i{display:block;height:100%;background:#7c6;width:0%}
.card{background:#1b1b1b;border:1px solid #333;border-radius:14px;padding:14px;margin:10px 0}
.tid{color:#9a9;font-size:13px}
audio{width:100%;height:40px;margin:4px 0 12px}
.q{font-weight:600;margin:10px 0 6px}
button.opt{padding:14px 8px;font-size:17px;border-radius:12px;border:2px solid #555;background:#222;color:#eee;font-weight:700;margin:0 5px 8px 0;min-width:86px}
button.opt.on{border-color:#7c6;background:#2d3a26}
button.mini{padding:8px 14px;border-radius:9px;border:1px solid #555;background:#222;color:#ccc;font-size:14px;margin:0 6px 6px 0}
#fin{padding:30px 14px;text-align:center}
.big{font-size:22px;font-weight:700;margin:12px 0}
.hide{display:none}
</style></head><body>
<h1>语速自然度定标</h1>
<div class="sub">耳机 · 听每段配音,回答:这个语速听着自然吗?</div>
<details><summary>怎么做(先读)</summary>
<p>每段是一句 AI 配音(内容各题不同,有的是原速,有的被加快了——<b>不告诉你哪段快</b>)。
只评<b>语速</b>:1=自然;2=有点赶/有点怪;3=太快了不能接受。语义和音色不用管。共 16 题,约 3 分钟。</p></details>
<div class="bar"><i id="pb"></i></div><div id="pt" class="sub"></div>
<div id="q">
  <div class="card">
    <div class="tid" id="tid"></div>
    <div class="lbl">▶ 本段配音</div><audio id="aa" controls preload="metadata"></audio>
    <div class="q">这个语速听着自然吗?</div>
    <div><button class="opt" id="g1">自然</button><button class="opt" id="g2">有点赶</button><button class="opt" id="g3">不能接受</button></div>
  </div>
  <button class="mini" id="prev">← 上一题</button>
  <button class="mini" id="next">下一题 →</button>
</div>
<div id="fin" class="hide">
  <div class="big">✅ 全部完成</div>
  <p>感谢!答案已保存。回头我出"变速-自然度"拐点。</p>
  <button class="mini" id="back">回去改分</button>
</div>
<script>
let ids=[],ans={},i=0;
const $=id=>document.getElementById(id);
function render(){
 const n=ids.length,done=ids.filter(t=>ans[t]&&ans[t].grade).length;
 $('pb').style.width=(100*done/n)+'%';
 $('pt').textContent=done+' / '+n+' 题完成';
 if(done===n){$('q').classList.add('hide');$('fin').classList.remove('hide');return}
 $('fin').classList.add('hide');$('q').classList.remove('hide');
 while(!ids[i]||ans[ids[i]]&&ans[ids[i]].grade){ if(!ids[i]) i=0; if(ans[ids[i]]&&ans[ids[i]].grade) i=(i+1)%n; }
 const t=ids[i],a=ans[t]||{};
 $('tid').textContent='第 '+(ids.indexOf(t)+1)+' 题 · 共'+n+'题'+(a.grade?'(已评,可改)':'');
 $('aa').src='/audio/'+t+'.wav';
 const e=$('aa');e.pause();try{e.currentTime=0}catch(_){}
 for(let k=1;k<=3;k++)$('g'+k).classList.toggle('on',a.grade==k);
}
async function set(v){const t=ids[i];ans[t]=ans[t]||{};ans[t].grade=v;
 try{await fetch('/api/answer',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({item:t,grade:v})});}
 catch(e){}
 render();}
async function init(){const r=await fetch('/api/state');const d=await r.json();ids=d.order;ans=d.answers;render();}
[1,2,3].forEach(k=>$('g'+k).onclick=()=>set(k));
$('prev').onclick=()=>{i=(i-1+ids.length)%ids.length;render()};
$('next').onclick=()=>{i=(i+1)%ids.length;render()};
$('back').onclick=()=>{$('fin').classList.add('hide');$('q').classList.remove('hide');i=0;render()};
init();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[r3]", self.address_string(), fmt % args)

    def _json(self, obj, code=200):
        b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(b)

    def _send_file(self, f: Path, rng_header):
        if not f.is_file():
            return self._json({"error": "missing"}, 404)
        size = f.stat().st_size
        start, end = 0, size - 1
        partial = False
        if rng_header:
            mm = re.match(r"bytes=(\d*)-(\d*)$", rng_header.strip())
            if mm:
                s0, s1 = mm.groups()
                if s0:
                    start = int(s0)
                    end = int(s1) if s1 else size - 1
                elif s1:
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
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
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

    def do_HEAD(self):
        self.do_GET()

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
                a = _load()
            self._json({"order": _ORDER,
                        "answers": {k: v for k, v in a.items() if k in _BY}})
        else:
            mm = re.match(r"^/audio/([A-Za-z0-9_]+)\.wav$", self.path)
            if not mm or mm.group(1) not in _BY:
                return self._json({"error": "bad name"}, 400)
            self._send_file(AUD / f"{mm.group(1)}.wav",
                            self.headers.get("Range"))

    def do_POST(self):
        if self.path != "/api/answer":
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 128:
                raise ValueError
            d = json.loads(self.rfile.read(n).decode("utf-8"))
            item, grade = d.get("item"), d.get("grade")
            if item not in _BY or grade not in (1, 2, 3):
                raise ValueError
        except Exception:
            return self._json({"error": "bad request"}, 400)
        with _LOCK:
            a = _load()
            a[item] = {"grade": grade}
            _save(a)
        self._json({"ok": True, "saved": item})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8796)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), H)
    print(f"R3 语速定标页: http://<本机IP>:{args.port}/  ({len(_ORDER)} 题;答案 {ANSWERS})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
