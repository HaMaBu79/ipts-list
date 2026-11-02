#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""REMOTE VERIFY: doc playlist -> giai ma that bang ffmpeg cho 5 kenh bi loi.

Kenh co DRM (DASH + ClearKey) -> can truyen -cenc_decryption_key.
Kenh HLS (ON Phim Viet) -> ffmpeg tu giai ma.
"""
import base64
import os
import re
import subprocess
import sys
import time
import urllib.parse

UA = "Dalvik/2.1.0"
LIST = sys.argv[1] if len(sys.argv) > 1 else "/opt/qweb/list.m3u8"
TARGETS = ["onsports", "onsportsplus", "onfootball", "onsportsnews", "onphimviet"]


def sh(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()


def b64url_to_hex(s):
    s = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s).hex()


def load(path):
    try:
        return open(path, encoding="utf-8", errors="replace").read().split("\n")
    except Exception:
        return []


lines = load(LIST)


def entry(tvg):
    for i, ln in enumerate(lines):
        if 'tvg-id="%s"' % tvg in ln:
            blk = lines[i:i + 12]
            url = next((b.strip() for b in blk if b.strip().startswith("http")), "")
            key = ""
            for b in blk:
                m = re.search(r"license_key=([0-9a-f]{32}):([0-9A-Za-z_-]+)", b)
                if m:
                    key = m.group(1) + ":" + m.group(2)
            return url, key
    return "", ""


print("=== VERIFY %s ===" % LIST)
ok = 0
for tvg in TARGETS:
    url, kid = entry(tvg)
    if not url:
        print("%-14s -> KHONG CO URL" % tvg)
        continue
    extra = []
    if kid:
        khex, kb64 = kid.split(":", 1)
        extra = ["-cenc_decryption_key", b64url_to_hex(kb64)]
    out = "/tmp/_v_%s.ts" % tvg
    if os.path.exists(out):
        os.remove(out)
    try:
        r = subprocess.run(
            ["ffmpeg", "-loglevel", "warning", "-user_agent", UA] + extra +
            ["-i", url, "-t", "10", "-c", "copy", "-f", "mpegts", out],
            capture_output=True, text=True, timeout=180)
        err = r.stderr or ""
    except Exception as e:
        err = str(e)
        r = None
    nerr = sum(1 for l in err.split("\n") if "error while decoding" in l)
    sz = os.path.getsize(out) if os.path.exists(out) else 0
    good = sz > 200000 and nerr == 0
    ok += 1 if good else 0
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    exp = ""
    if "stop" in q:
        exp = "  (han %d phut)" % ((int(q["stop"][0]) - int(time.time())) // 60)
    print("%-14s %-5s size=%-9d dec_err=%-3d%s" % (tvg, "PLAY" if good else "FAIL", sz, nerr, exp))
    if good:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=codec_name,width,height,codec_type",
                            "-of", "csv=p=0", out],
                           capture_output=True, text=True).stdout.strip()
        print("                ", p.replace("\n", " | "))
    else:
        for l in err.split("\n")[:5]:
            if l.strip():
                print("        ", l[:160])

print("\n=> %d/%d kenh PLAY DUOC" % (ok, len(TARGETS)))
sys.exit(0 if ok == len(TARGETS) else 1)
