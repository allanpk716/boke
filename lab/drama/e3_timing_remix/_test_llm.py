# -*- coding: utf-8 -*-
import json, os, urllib.request
body = {"model": "glm-5.3-flash",
        "messages": [{"role": "user", "content": "把这句翻成口语自然的英文,只输出译文:他娘的,老子跟你拼了!"}],
        "max_tokens": 300}
req = urllib.request.Request(
    "https://aihubmix.com/v1/chat/completions",
    data=json.dumps(body).encode(), method="POST",
    headers={"Authorization": "Bearer " + os.environ["AIHUBMIX_API_KEY"],
             "Content-Type": "application/json"})
with urllib.request.urlopen(req, timeout=60) as r:
    d = json.loads(r.read().decode())
print(d["choices"][0]["message"]["content"])
