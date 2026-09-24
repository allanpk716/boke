# -*- coding: utf-8 -*-
"""检查 Edge cookie 加密类型 + 尝试用户级 DPAPI 解密 (v10)。
输出: Local State 密钥结构、bilibili cookie 的加密前缀、若 v10 则直接解出并写 cookies.txt
"""
import base64
import ctypes
import ctypes.wintypes
import json
import os
import shutil
import sqlite3
import sys
import tempfile

USER_DATA = os.path.join(os.environ["LOCALAPPDATA"], "Microsoft", "Edge", "User Data")
PROFILE = "Default"

# ---------- DPAPI ----------
class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

def dpapi_unprotect(data: bytes) -> bytes:
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise OSError("CryptUnprotectData failed (err=%d)" % ctypes.GetLastError())
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)

def main():
    # 1) Local State
    with open(os.path.join(USER_DATA, "Local State"), "r", encoding="utf-8") as f:
        ls = json.load(f)
    osc = ls.get("os_crypt", {})
    ek = osc.get("encrypted_key", "")
    abk_present = "app_bound_encrypted_key" in ls
    print(f"[Local State] os_crypt.encrypted_key: {'present' if ek else 'MISSING'} (len={len(ek)})")
    print(f"[Local State] app_bound_encrypted_key present: {abk_present}")

    aes_key = None
    if ek:
        raw = base64.b64decode(ek)
        if raw[:5] == b"DPAPI":
            try:
                aes_key = dpapi_unprotect(raw[5:])
                print(f"[key] user-DPAPI decrypt OK, key len={len(aes_key)}")
            except OSError as e:
                print(f"[key] user-DPAPI decrypt FAILED: {e}")
        else:
            print(f"[key] unknown prefix: {raw[:5]!r}")

    # 2) Cookie DB (copy to temp)
    src = os.path.join(USER_DATA, PROFILE, "Network", "Cookies")
    tmp = os.path.join(tempfile.gettempdir(), "edge_cookies_copy.db")
    shutil.copy2(src, tmp)
    con = sqlite3.connect(tmp)
    rows = con.execute(
        "SELECT host_key, name, encrypted_value, expires_utc FROM cookies "
        "WHERE host_key LIKE '%bilibili%' ORDER BY host_key, name"
    ).fetchall()
    print(f"[db] bilibili cookies: {len(rows)} rows")
    prefixes = {}
    interesting = {}
    for host, name, ev, exp in rows:
        p = ev[:3].decode("latin1", "replace") if ev else "??"
        prefixes[p] = prefixes.get(p, 0) + 1
        if name in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3", "SESSDATA"):
            interesting[name] = (p, len(ev))
    print(f"[db] prefix histogram: {prefixes}")
    print(f"[db] auth cookies: {interesting or 'NONE FOUND'}")

    # 3) 若 v10 且拿到 key,报告可解性(实际解密在下一步)
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        have_crypto = True
    except ImportError:
        have_crypto = False
    print(f"[env] cryptography lib available: {have_crypto}")
    if aes_key and any(k.startswith("v10") for k in prefixes):
        print("[try] v10 detected with user key -> decrypt path viable")
    con.close()
    os.remove(tmp)

if __name__ == "__main__":
    sys.exit(main())
