#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CHECK NHANH: MPD + segment co tai duoc khong (khong giai ma).

Dung khi can biet link co 'song' khong, nhanh hon verify_channels.py.
"""
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


lines = open(LIST, encoding="utf-8", errors="replace").read().split("\n")


def entry(tvg):
    for i, ln in enumerate(lines):
        if 'tvg-id="%s"' % tvg in ln:
            return next((b.strip() for b in lines[i:i + 10] if b.strip().startswith("http")), "")
    return ""


def code(u):
    return sh("curl -sL -o /dev/null -w '%%{http_code}' --max-time 20 -A '%s' '%s'" % (UA, u))


print("=== CHECK %s ===" % LIST)
for tvg in TARGETS:
    url = entry(tvg)
    if not url:
        print("%-14s -> KHONG CO URL" % tvg)
        continue
    mpdc = code(url)
    segc = "-"
    if ".mpd" in url:
        body = sh("curl -sL --max-time 25 -A '%s' '%s'" % (UA, url))
        m = re.search(r"<BaseURL>([^<]*)</BaseURL>", body)
        bu = m.group(1) if m else ""
        it = re.search(r'initialization="([^"]+)"', body)
        reps = re.findall(r'<Representation id="([^"]+)"', body)
        if it and reps:
            root = url.rsplit("/", 1)[0]
            rel = it.group(1).replace("$RepresentationID$", reps[0])
            seg = urllib.parse.urljoin(root + "/" + bu, rel) if bu else urllib.parse.urljoin(root + "/", rel)
            segc = code(seg)
    print("%-14s MPD=%-5s SEG=%-5s  %s" % (tvg, mpdc, segc, time.strftime("%H:%M:%S")))
