# -*- coding: utf-8 -*-
"""试听台 HTTP 服务: 静态页 + 音频 + POST /save_picks。绑定 0.0.0.0:8765"""
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WEB = Path(__file__).resolve().parents[1] / "web"
ROOT = WEB.parents[0]  # work/


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def do_POST(self):
        if self.path != "/save_picks":
            self.send_error(404)
            return
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n).decode("utf-8"))
        out = ROOT / "voice_picks.json"
        prev = []
        if out.exists():
            try:
                prev = json.loads(out.read_text(encoding="utf-8"))
                if not isinstance(prev, list):
                    prev = [prev]
            except Exception:
                prev = []
        prev.append(data)
        out.write_text(json.dumps(prev, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def log_message(self, fmt, *args):
        print("[http]", self.address_string(), fmt % args, flush=True)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", 8765), Handler)
    print("[web] serving on 0.0.0.0:8765 →", WEB, flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
