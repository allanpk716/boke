# -*- coding: utf-8 -*-
"""B 站 UP 主视频列表拉取(wbi 签名,匿名+buvid 模式)。
输出: work/space_videos.json  (全部视频: bvid/title/length/created/desc 等)
"""
import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

MID = "346563107"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # work/

MIXIN_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]


class Bili:
    def __init__(self, cookie_file):
        self.cookies = ""
        if cookie_file and os.path.exists(cookie_file):
            pairs = []
            for line in open(cookie_file, encoding="utf-8"):
                if line.startswith("#") or not line.strip():
                    continue
                parts = line.rstrip("\n").split("\t")
                if len(parts) >= 7:
                    pairs.append(f"{parts[5]}={parts[6]}")
            self.cookies = "; ".join(pairs)
        self.base_headers = {
            "User-Agent": UA,
            "Referer": "https://space.bilibili.com/",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        if self.cookies:
            self.base_headers["Cookie"] = self.cookies
        self._mixin = None

    def ensure_cookies(self):
        """补 buvid4(spi 接口),与现有 buvid3/b_nut 合并"""
        spi = self.get("https://api.bilibili.com/x/frontend/finger/spi")
        b3 = spi["data"]["b_3"]
        b4 = spi["data"]["b_4"]
        have = {}
        for kv in self.cookies.split("; "):
            if "=" in kv:
                k, v = kv.split("=", 1)
                have[k] = v
        have.setdefault("buvid3", b3)
        have.setdefault("buvid4", b4)
        self.cookies = "; ".join(f"{k}={v}" for k, v in have.items())
        self.base_headers["Cookie"] = self.cookies
        print("[cookie]", sorted(have))

    def get(self, url, extra_headers=None, retries=3):
        h = dict(self.base_headers)
        if extra_headers:
            h.update(extra_headers)
        last_err = None
        for i in range(retries):
            req = urllib.request.Request(url, headers=h)
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.loads(r.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_err = e
                wait = 5 * (i + 1)
                print(f"[http] {e.code} on attempt {i+1}, sleep {wait}s")
                time.sleep(wait)
        raise last_err or RuntimeError("get failed")

    def mixin_key(self):
        if self._mixin:
            return self._mixin
        nav = self.get("https://api.bilibili.com/x/web-interface/nav")
        wbi = nav["data"]["wbi_img"]
        img_key = wbi["img_url"].rsplit("/", 1)[1].split(".")[0]
        sub_key = wbi["sub_url"].rsplit("/", 1)[1].split(".")[0]
        raw = img_key + sub_key
        self._mixin = "".join(raw[i] for i in MIXIN_TAB)[:32]
        return self._mixin

    def wbi_sign(self, params: dict) -> dict:
        params = dict(params)
        params["wts"] = int(time.time())
        # 去掉 value 中的 !'()* 字符
        params = {k: "".join(ch for ch in str(v) if ch not in "!'()*")
                  for k, v in params.items()}
        qs = urllib.parse.urlencode(sorted(params.items()))
        params["w_rid"] = hashlib.md5((qs + self.mixin_key()).encode()).hexdigest()
        return params

    def space_arc(self, mid, pn, ps=50):
        p = self.wbi_sign({
            "mid": mid, "pn": pn, "ps": ps, "order": "pubdate",
            "platform": "web", "web_location": "1550101",
            # 设备指纹参数(B 站 web 端反爬,匿名访问过 -352 的关键)
            "dm_img_list": "[]",
            "dm_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taWVuKQ",
            "dm_cover_img_str": "V2ViR0wgMS4wIChPcGVuR0wgRVMgMi4wIENocm9taXVtKQ",
            "dm_img_inter": '{"ds":[],"wh":[3415,3415,34],"of":[280,280,280]}',
        })
        qs = urllib.parse.urlencode(sorted(p.items()))
        return self.get(f"https://api.bilibili.com/x/space/wbi/arc/search?{qs}")


def main():
    cookie_file = os.path.join(ROOT, "buvid_cookies.txt")
    b = Bili(cookie_file)
    b.ensure_cookies()
    print("[wbi] mixin key ready:", b.mixin_key()[:8], "...")
    all_v = []
    pn = 1
    total = None
    while True:
        r = b.space_arc(MID, pn)
        tries = 0
        while r.get("code") != 0 and tries < 3:
            tries += 1
            wait = 20 * tries
            print(f"[api] code={r.get('code')} msg={r.get('message')}; retry {tries}/3 after {wait}s")
            time.sleep(wait)
            r = b.space_arc(MID, pn)
        if r.get("code") != 0:
            print(f"[api] giving up at page {pn}: {r.get('code')} {r.get('message')}")
            break
        data = r["data"]
        if total is None:
            total = data["page"]["count"]
            print(f"[api] total videos: {total}")
        vlist = data["list"]["vlist"]
        if not vlist:
            break
        all_v.extend(vlist)
        print(f"[api] page {pn}: +{len(vlist)} (cum {len(all_v)}/{total})")
        if len(all_v) >= total:
            break
        pn += 1
        time.sleep(3.0)
    out = os.path.join(ROOT, "space_videos.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(all_v, f, ensure_ascii=False, indent=1)
    print(f"[out] {out}: {len(all_v)} videos")
    return 0 if all_v else 1


if __name__ == "__main__":
    raise SystemExit(main())
