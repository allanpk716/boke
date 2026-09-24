# -*- coding: utf-8 -*-
"""通过 junction profile + CDP 让 Edge 自己解密 cookie,导出 bilibili cookies.txt。
用法: python edge_cdp_cookies.py   (需先建 junction work/tmp/edge_ud 并已杀干净 Edge)
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

import websocket  # pip install websocket-client

PORT = 9223
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
UD = r"C:\WorkSpace\agent\boke\work\tmp\edge_ud"
OUT = r"C:\WorkSpace\agent\boke\work\cookies.txt"
DOMAIN_FILTER = "bilibili.com"


def wait_for_endpoint(timeout=40):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=3) as r:
                return json.loads(r.read().decode())
        except Exception:
            time.sleep(1)
    raise TimeoutError("devtools endpoint not reachable")


def get_page_ws_url():
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=5) as r:
        targets = json.loads(r.read().decode())
    pages = [t for t in targets if t.get("type") == "page"]
    if not pages:
        raise RuntimeError(f"no page target; targets={targets}")
    return pages[0]["webSocketDebuggerUrl"]


def cdp_call(ws, _id, method, params=None):
    ws.send(json.dumps({"id": _id, "method": method, "params": params or {}}))
    while True:
        msg = json.loads(ws.recv())
        if msg.get("id") == _id:
            return msg.get("result", msg)


def main():
    proc = subprocess.Popen([
        EDGE,
        f"--user-data-dir={UD}",
        "--profile-directory=Default",
        f"--remote-debugging-port={PORT}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--window-position=-32000,-32000",  # 窗口移出屏幕,等效于不可见
        "--window-size=800,600",
        "about:blank",
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[edge] pid={proc.pid} launched, waiting devtools...")
    try:
        ver = wait_for_endpoint()
        print(f"[edge] devtools up: {ver.get('Browser','?')}")
        time.sleep(8)  # 等 profile/cookie 库加载
        # 重新拿 target(devtools 起来后 target 才稳定)
        ws_url = get_page_ws_url()
        print(f"[cdp] page ws: {ws_url[:60]}...")
        ws = websocket.create_connection(ws_url, timeout=30)
        # Storage.getCCookies 不需要 enable;失败再试 Network.getAllCookies
        cookies = None
        try:
            r = cdp_call(ws, 1, "Storage.getCookies")
            cookies = r["cookies"]
            src = "Storage.getCookies"
        except Exception as e1:
            print(f"[cdp] Storage.getCookies failed: {e1}, trying Network...")
            try:
                cdp_call(ws, 2, "Network.enable")
                r = cdp_call(ws, 3, "Network.getAllCookies")
                cookies = r["cookies"]
                src = "Network.getAllCookies"
            except Exception as e2:
                print(f"[cdp] Network.getAllCookies failed: {e2}")
                return 1
        finally:
            ws.close()
        print(f"[cdp] total cookies via {src}: {len(cookies)}")
        domains = {}
        for c in cookies:
            d = c.get("domain") or "?"
            domains[d] = domains.get(d, 0) + 1
        print(f"[cdp] all domains: {json.dumps(domains, ensure_ascii=False)}")
        keep = [c for c in cookies if DOMAIN_FILTER in (c.get("domain") or "")]
        print(f"[cdp] bilibili cookies: {len(keep)}")
        names = sorted({c['name'] for c in keep})
        print(f"[cdp] names: {names}")
        with open(OUT, "w", encoding="utf-8", newline="\n") as f:
            f.write("# Netscape HTTP Cookie File\n")
            for c in keep:
                dom = c["domain"]
                flag = "TRUE" if dom.startswith(".") else "FALSE"
                exp = int(c.get("expires", -1))
                exp = exp if exp and exp > 0 else 0
                secure = "TRUE" if c.get("secure") else "FALSE"
                f.write(f"{dom}\t{flag}\t{c.get('path','/')}\t{secure}\t{exp}\t{c['name']}\t{c['value']}\n")
        print(f"[out] wrote {OUT}")
        key_names = {"SESSDATA", "bili_jct", "DedeUserID", "buvid3"}
        have = key_names & set(names)
        print(f"[out] auth cookies present: {sorted(have) if have else 'MISSING!'}")
        return 0 if {"SESSDATA", "bili_jct"} <= set(names) else 2
    finally:
        # 杀整棵进程树,防止残留实例占住调试端口
        subprocess.run(["taskkill", "/IM", "msedge.exe", "/F", "/T"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)


if __name__ == "__main__":
    sys.exit(main())
