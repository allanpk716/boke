# -*- coding: utf-8 -*-
"""试听台 + 核对台 HTTP 服务。
路由: / → work/web/index.html; /review → review.html; /audio/* → work/web/audio;
      /media/* → in/(本地视频大文件,LAN 流式播放)
POST /save_picks, /save_review → work/*.json
绑定 0.0.0.0:8765
"""
import json
import re
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORK = Path(__file__).resolve().parents[1]
WEB = WORK / "web"
MEDIA_DIRS = [WORK.parent / "in", Path("D:/boke_media")]


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def translate_path(self, path):
        m = re.match(r"^/media/(.+)$", path)
        if m:
            name = m.group(1)
            for d in MEDIA_DIRS:
                p = (d / name).resolve()
                if p.exists():
                    return str(p)
        return super().translate_path(path)

    def _save(self, fname):
        n = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(n).decode("utf-8"))
        out = WORK / fname
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

    def do_POST(self):
        if self.path == "/save_picks":
            self._save("voice_picks.json")
        elif self.path == "/save_review":
            self._save("review_decisions_mock.json")
        else:
            self.send_error(404)

    def log_message(self, fmt, *args):
        print("[http]", self.address_string(), fmt % args, flush=True)


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", 8765), Handler)
    print(f"[web] serving 0.0.0.0:8765 → {WEB} (+/media)", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
