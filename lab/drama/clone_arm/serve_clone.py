# -*- coding: utf-8 -*-
"""克隆臂手机对比页:每对两段(原声 vs CosyVoice 克隆同文复刻),盲 randomized。
run(系统 python): python serve_clone.py [--port 8798]
- 槽位指派(1=orig 还是 syn)服务端固定种子随机,存 assignment.json,永不下发
- 答案: lab/drama/results/abx/answers_clone_arm.json {pair_id:{which,similarity}}
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
PAIRS_F = HERE / "pairs_final.json"
AUD = HERE
ANSWERS = HERE.parent / "results" / "abx" / "answers_clone_arm.json"
ASSIGN = HERE / "assignment.json"

_LOCK = threading.Lock()
_PAIRS = json.loads(PAIRS_F.read_text(encoding="utf-8"))
_IDS = [p["pair_id"] for p in _PAIRS]
_LANG = {p["pair_id"]: p["lang"] for p in _PAIRS}
_FILES = {p["pair_id"]: (p["orig"], p["syn"]) for p in _PAIRS}

if ASSIGN.is_file():
    _ASSIGN = json.loads(ASSIGN.read_text(encoding="utf-8"))
else:
    rng = random.Random(20260925)
    _ASSIGN = {pid: rng.choice(["orig", "syn"]) for pid in _IDS}
    ASSIGN.write_text(json.dumps(_ASSIGN, ensure_ascii=False, indent=1),
                      encoding="utf-8")


def slot_files(pid):
    """返回 (slot1 文件, slot2 文件);assign=orig ⇒ 槽1=orig。"""
    o, s = _FILES[pid]
    return (o, s) if _ASSIGN[pid] == "orig" else (s, o)


def _load_answers():
    try:
        return json.loads(ANSWERS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_answers(a):
    ANSWERS.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(ANSWERS.parent),
                               prefix=".clone_", suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        json.dump(a, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, ANSWERS)


PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>克隆对比</title><style>
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
audio{width:100%;height:40px;margin:4px 0 10px}
.q{font-weight:600;margin:12px 0 6px}
button.opt{padding:14px 8px;font-size:17px;border-radius:12px;border:2px solid #555;background:#222;color:#eee;font-weight:700;margin:0 6px 8px 0;min-width:110px}
button.opt.on{border-color:#7c6;background:#2d3a26}
button.mini{padding:8px 14px;border-radius:9px;border:1px solid #555;background:#222;color:#ccc;font-size:14px;margin:0 6px 6px 0}
#fin{padding:30px 14px;text-align:center}
.big{font-size:22px;font-weight:700;margin:12px 0}
.hide{display:none}
</style></head><body>
<h1>克隆对比 · 原声 vs 合成</h1>
<div class="sub">耳机 · 安静环境 · 每对两段:一段真人原声,一段 AI 用同一音色合成的同句</div>
<details><summary>怎么做(先读)</summary>
<p>每对听<b>样本1、样本2</b>(可重听),回答两问:<br>
① 你猜<b>哪个是 AI 合成的</b>(二选一,猜不出就凭直觉);<br>
② 你猜的那个"合成"样本,<b>像不像本人</b>(1=完全不像,5=几乎同一人)。</p></details>
<div class="bar"><i id="pb"></i></div><div id="pt" class="sub"></div>
<div id="q">
  <div class="card">
    <div class="tid" id="tid"></div>
    <div class="lbl">▶ 样本 1</div><audio id="a1" controls preload="metadata"></audio>
    <div class="lbl">▶ 样本 2</div><audio id="a2" controls preload="metadata"></audio>
    <div class="q">① 哪个是 AI 合成的?</div>
    <div><button class="opt" id="w1">样本 1</button><button class="opt" id="w2">样本 2</button></div>
    <div class="q">② 你猜的合成样本像本人吗?</div>
    <div><button class="opt" id="s1">1</button><button class="opt" id="s2">2</button><button class="opt" id="s3">3</button><button class="opt" id="s4">4</button><button class="opt" id="s5">5</button></div>
  </div>
  <button class="mini" id="prev">← 上一对</button>
  <button class="mini" id="next">下一对 →</button>
  <span class="sub" id="hint"></span>
</div>
<div id="fin" class="hide">
  <div class="big">✅ 全部作答完成</div>
  <p>感谢!答案已保存。结果分析(可辨率+相似度)回头找我出。<br>
  想改答案点下面回去。</p>
  <button class="mini" id="back">回去改答案</button>
</div>
<script>
let ids=[],ans={},i=0;
const $=id=>document.getElementById(id);
function full(p){return ans[p]&&ans[p].which&&ans[p].similarity}
function render(){
 const n=ids.length,done=ids.filter(full).length;
 $('pb').style.width=(100*done/n)+'%';
 $('pt').textContent=done+' / '+n+' 对完成';
 if(done===n){$('q').classList.add('hide');$('fin').classList.remove('hide');return}
 $('fin').classList.add('hide');$('q').classList.remove('hide');
 while(!ids[i]||full(ids[i])){ if(!ids[i]) i=0; if(full(ids[i])) i=(i+1)%n; }
 const p=ids[i],a=ans[p]||{};
 $('tid').textContent='第 '+(ids.indexOf(p)+1)+' 对 · 共'+n+'对'+(full(p)?'(已答,可改)':'');
 $('a1').src='/audio/'+p+'/1.wav';$('a2').src='/audio/'+p+'/2.wav';
 ['a1','a2'].forEach(x=>{const e=$(x);e.pause();try{e.currentTime=0}catch(_){}});
 $('w1').classList.toggle('on',a.which==='1');$('w2').classList.toggle('on',a.which==='2');
 for(let k=1;k<=5;k++)$('s'+k).classList.toggle('on',a.similarity==k);
 $('hint').textContent='';
}
async function save(p){
 const prev=ans[p];
 try{await fetch('/api/answer',{method:'POST',headers:{'Content-Type':'application/json'},
  body:JSON.stringify({pair_id:p,which:ans[p]&&ans[p].which,similarity:ans[p]&&ans[p].similarity})});}
 catch(e){if(prev)ans[p]=prev;else delete ans[p];$('hint').textContent='⚠ 保存失败,重试一下';}
 render();
}
async function set(k,v){const p=ids[i];ans[p]=ans[p]||{};ans[p][k]=v;await save(p);}
async function init(){const r=await fetch('/api/state');const d=await r.json();ids=d.order;ans=d.answers;render();}
$('w1').onclick=()=>set('which','1');$('w2').onclick=()=>set('which','2');
[1,2,3,4,5].forEach(k=>$('s'+k).onclick=()=>set('similarity',k));
$('prev').onclick=()=>{i=(i-1+ids.length)%ids.length;render()};
$('next').onclick=()=>{i=(i+1)%ids.length;render()};
$('back').onclick=()=>{$('fin').classList.add('hide');$('q').classList.remove('hide');i=0;render()};
init();
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[clone]", self.address_string(), fmt % args)

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
                a = _load_answers()
            self._json({"order": _IDS,
                        "answers": {k: v for k, v in a.items()
                                    if k in _IDS}})
        elif self.path.startswith("/audio/"):
            mm = re.match(r"^/audio/(P\d{2})/([12])\.wav$", self.path)
            if not mm or mm.group(1) not in _IDS:
                return self._json({"error": "bad name"}, 400)
            f1, f2 = slot_files(mm.group(1))
            self._send_file(AUD / (f1 if mm.group(2) == "1" else f2),
                            self.headers.get("Range"))
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path != "/api/answer":
            return self._json({"error": "not found"}, 404)
        try:
            n = int(self.headers.get("Content-Length", "0"))
            if n > 256:
                raise ValueError("too big")
            d = json.loads(self.rfile.read(n).decode("utf-8"))
            pid = d.get("pair_id")
            which, sim = d.get("which"), d.get("similarity")
            if pid not in _IDS:
                raise ValueError("bad pair")
            if which not in (None, "1", "2") or sim not in (None, 1, 2, 3, 4, 5):
                raise ValueError("bad values")
        except Exception:
            return self._json({"error": "bad request"}, 400)
        with _LOCK:
            a = _load_answers()
            ent = a.get(pid, {})
            if which:
                ent["which"] = which
            if sim:
                ent["similarity"] = sim
            a[pid] = ent
            _save_answers(a)
        self._json({"ok": True, "saved": pid})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8798)
    args = ap.parse_args()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), H)
    print(f"克隆对比页: http://<本机IP>:{args.port}/  ({len(_IDS)} 对;答案 {ANSWERS})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
