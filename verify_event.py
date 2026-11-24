#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KICH HOAT TRUOC KHI PUSH: kiem tra 17 kenh nhom 'Su kien TV360' trong
list.m3u8 co THAT SU giai ma duoc khong.

Kiem tra 2 tang:
  1. HLS  (tv360plus9/10/11, tv360, livestream-01):
     master -> media -> segment, segment > 10KB
  2. DASH (tv360plus1-5, 8, 12-15):
     MPD -> lay default_KID -> so sanh voi KID trong license_key
     -> tai init + segment -> giai ma bang ClearKey -> anh THAT (khong xam 818181)

Exit 0 = OK (duoc push). Exit 1 = co kenh hong -> KHONG push.

CHI kiem tra nhom 'Su kien TV360'. KHONG dua dung nhom nao khac vao day.

CHONG RATE-LIMIT:
  - list.m3u8 va Listphu.m3u8 cung 17 kenh, cung URL/key -> goi CDN
    1 LAN cho moi cap (url, key) duy nhat (dedupe) thay vi 34 lan.
  - ket qua dung chung cho ca 2 file.
  - retry 2 lan neu that bai (loi tam thoi 403/429/timeout).
  - CACHE (TTL 420s) de buoc 3 doc ket qua buoc 2, khong chay lai.
"""
import base64
import json
import os
import re
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urljoin

QWEB = "/opt/qweb"
FILES = [os.path.join(QWEB, "list.m3u8"), os.path.join(QWEB, "Listphu.m3u8")]
CACHE = os.path.join(QWEB, "verify_event_cache.json")
CACHE_TTL = 420
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
SKIP_IDS = {"tv360plus6", "tv360plus7"}
RETRY = 3


def get(u, timeout=18):
    try:
        rq = urllib.request.Request(u, headers={"User-Agent": UA, "Accept": "*/*"})
        with urllib.request.urlopen(rq, timeout=timeout, context=CTX) as r:
            return r.getcode(), r.read(), r.geturl()
    except Exception as e:
        return getattr(e, "code", 0), str(e).encode(), u


def kids_of(body):
    out = []
    for k in re.findall(r'default_KID="([^"]+)"', body):
        out.append(k.replace("-", "").lower())
    for p in re.findall(r'<(?:cenc:)?pssh[^>]*>(.*?)</(?:cenc:)?pssh>', body, re.S):
        s = re.sub(r"\s+", "", p)
        s += "=" * (-len(s) % 4)
        try:
            raw = base64.urlsafe_b64decode(s)
        except Exception:
            continue
        if len(raw) < 32:
            continue
        dlen = int.from_bytes(raw[28:32], "big")
        data = raw[32:32 + dlen]
        if len(data) >= 20 and data[:4] == bytes.fromhex("08011210"):
            out.append(data[4:20].hex())
    return [x for x in out if x]


def seg_urls(body, mpd_url):
    base = urljoin(mpd_url, ".")
    blocks = re.findall(r"<AdaptationSet\b.*?</AdaptationSet>", body, re.S) or [body]
    ordered = sorted(blocks,
                     key=lambda b: 0 if ('contentType="video"' in b or "avc" in b) else 1)
    for blk in ordered:
        tm = re.search(r"<SegmentTemplate[^>]*>", blk)
        if not tm:
            continue
        t = tm.group(0)
        mi = re.search(r'initialization="([^"]+)"', t)
        me = re.search(r'media="([^"]+)"', t)
        if not (mi or me):
            continue
        rids = re.findall(r'<Representation[^>]*id="([^"]+)"', blk)
        use = [r for r in rids if r.startswith("avc") or "video" in r] or rids
        if not use:
            continue
        times, cur = [], None
        for sm in re.findall(r"<S\b[^>]*/?>", blk):
            tm2 = re.search(r't="(\d+)"', sm)
            if tm2:
                cur = tm2.group(1)
            dm = re.search(r'd="(\d+)"', sm)
            d = int(dm.group(1)) if dm else 0
            rm = re.search(r'r="(\d+)"', sm)
            rep = int(rm.group(1)) if rm else 0
            if cur is None:
                continue
            times.append(cur)
            times.extend([str(int(cur) + d * k) for k in range(1, rep + 1)])
        sn = re.search(r'startNumber="(\d+)"', t)
        start = sn.group(1) if sn else "0"
        for rid in use[:3]:
            init2 = None
            if mi:
                init2 = re.sub(r"\$[^$]*\$", "0",
                               mi.group(1).replace("$RepresentationID$", rid)
                               .replace("$Number$", start))
            segs2 = []
            if me:
                s = me.group(1).replace("$RepresentationID$", rid)
                if "$Number$" in s:
                    nxt = str(int(start) + 1) if start.isdigit() else "1"
                    for n in (start, nxt):
                        segs2.append(re.sub(r"\$[^$]*\$", "0", s.replace("$Number$", n)))
                else:
                    for tt in times[:6]:
                        segs2.append(re.sub(r"\$[^$]*\$", "0", s.replace("$Time$", tt)))
            if init2 and segs2:
                return urljoin(base, init2), [urljoin(base, s) for s in segs2]
    return None, []


def decode(init_u, seg_u, keyhex):
    d = tempfile.mkdtemp(prefix="vg_")
    mp4 = os.path.join(d, "a.mp4")
    png = os.path.join(d, "f.png")
    try:
        c1, i, _ = get(init_u, 20)
        c2, s, _ = get(seg_u, 20)
        if c1 != 200 or c2 != 200:
            return False, "HTTP%d/%d" % (c1, c2)
        with open(mp4, "wb") as f:
            f.write(i + s)
        subprocess.run(["ffmpeg", "-v", "error", "-decryption_key", keyhex,
                        "-i", mp4, "-frames:v", "1", "-y", png],
                       capture_output=True, timeout=60)
        if not os.path.exists(png) or os.path.getsize(png) < 15000:
            return False, "NOFRAME"
        r2 = subprocess.run(["ffmpeg", "-v", "error", "-i", png, "-vf", "scale=1:1",
                             "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                            capture_output=True)
        h = r2.stdout[:3].hex()
        if h == "818181":
            return False, "GRAY"
        return True, h
    except Exception as e:
        return False, "ERR " + str(e)[:40]
    finally:
        subprocess.run(["rm", "-rf", d])


def events_of(path):
    out = {}
    if not os.path.exists(path):
        return out
    txt = open(path, "rb").read().decode("utf-8", "replace")
    lines = txt.split("\n")
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("#EXTINF"):
                j += 1
            blk = lines[i:j]
            if 'group-title="Sự kiện TV360"' in blk[0]:
                m = re.search(r'tvg-id="([^"]*)"', blk[0])
                slug = (m.group(1) if m else "?").lower()
                url = None
                key = None
                for x in blk:
                    s = x.strip()
                    if s.startswith("http") and url is None:
                        url = s
                    if s.startswith("#KODIPROP:inputstream.adaptive.license_key="):
                        key = s.split("license_key=", 1)[1].strip()
                out[slug] = (url, key)
            i = j
        else:
            i += 1
    return out


def test_pair(url, key):
    """Tra (ok, info). Tuần tự, retry khi that bai tam thoi."""
    last = ""
    for attempt in range(RETRY):
        if ".mpd" in url.lower():
            if not key or ":" not in key:
                return False, "thieu ClearKey"
            kid = key.split(":")[0].lower()
            c, body, eff = get(url)
            if c != 200:
                last = "MPD HTTP%d" % c
                time.sleep(2 + attempt * 2)
                continue
            body = body.decode("utf-8", "replace")
            sk = kids_of(body)
            if kid not in sk:
                # token het han -> thu lai (URL co the da doi)
                last = "KID KHONG KHOP (%s vs %s)" % (kid[:8], ",".join(sorted(sk))[:20])
                time.sleep(2 + attempt * 2)
                continue
            init_u, segs = seg_urls(body, eff)
            if not init_u or not segs:
                last = "khong lay duoc segment"
                time.sleep(1)
                continue
            ok, info = decode(init_u, segs[0], key.split(":", 1)[1])
            if ok:
                return True, "giai ma that (%s)" % info
            last = "giai ma that FAIL (%s)" % info
            time.sleep(2 + attempt * 2)
        else:
            c, b, eff = get(url)
            if c != 200:
                last = "master HTTP%d" % c
                time.sleep(2 + attempt * 2)
                continue
            subs = [l for l in b.decode("utf-8", "replace").splitlines()
                    if l.strip() and not l.startswith("#")]
            if not subs:
                last = "khong co media playlist"
                time.sleep(1)
                continue
            c2, m, eff2 = get(urljoin(eff, subs[0].strip()))
            if c2 != 200:
                last = "media HTTP%d" % c2
                time.sleep(2 + attempt * 2)
                continue
            segs = [l for l in m.decode("utf-8", "replace").splitlines()
                    if l.strip() and not l.startswith("#")]
            if not segs:
                last = "media khong co segment"
                time.sleep(1)
                continue
            c3, s, _ = get(urljoin(eff2, segs[-1].strip()))
            if c3 != 200 or len(s) < 10000:
                last = "segment HTTP%d / %d bytes" % (c3, len(s))
                time.sleep(2 + attempt * 2)
                continue
            return True, "segment %d bytes" % len(s)
    return False, last


def run_all():
    """Verify 1 LAN cho moi cap (url,key) duy nhat. Tra (lines, bad)."""
    per_file = {}
    pairs = {}
    for p in FILES:
        blocks = events_of(p)
        per_file[p] = blocks
        for slug, (url, key) in blocks.items():
            if slug in SKIP_IDS or not url:
                continue
            pairs.setdefault((url, key), slug)

    print("== VERIFY %d cap (url,key) duy nhat ==" % len(pairs))
    result = {}
    for (url, key), slug in sorted(pairs.items(),
                                   key=lambda x: len(x[0][1] or "")):
        ok, info = test_pair(url, key)
        result[(url, key)] = (ok, info)
        print("   %-14s %-5s %s" % (slug, "OK" if ok else "LOI", info[:60]))
        if not ok:
            time.sleep(3)

    lines = []
    bad = []
    for p in FILES:
        lines.append("== %s (%d muc) ==" % (os.path.basename(p), len(per_file[p])))
        for slug in sorted(per_file[p], key=lambda s: (len(s), s)):
            url, key = per_file[p][slug]
            if slug in SKIP_IDS:
                lines.append("   %-14s BO QUA (khong cap nhat)" % slug)
                continue
            if not url:
                bad.append(slug)
                lines.append("   %-14s LOI - khong co URL" % slug)
                continue
            ok, info = result.get((url, key), (False, "chua kiem tra"))
            if ok:
                lines.append("   %-14s OK  %s" % (slug, info))
            else:
                bad.append(slug)
                lines.append("   %-14s LOI - %s" % (slug, info))
    return lines, sorted(set(bad))


def load_cache():
    try:
        c = json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        return None
    if time.time() - c.get("ts", 0) > CACHE_TTL:
        return None
    return c


def save_cache(lines, bad):
    tmp = CACHE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "bad": bad, "lines": lines}, f)
        os.replace(tmp, CACHE)
    except Exception:
        pass


def main():
    force = "--force" in sys.argv
    cached = None if force else load_cache()
    if cached:
        print("[CACHE %ds tu] ket qua: %s" % (
            int(time.time() - cached["ts"]),
            "OK" if not cached["bad"] else "LOI: " + ", ".join(cached["bad"])))
        for l in cached.get("lines", []):
            print(l)
        print()
        if cached["bad"]:
            print("KET QUA: %d KENH LOI -> KHONG PUSH: %s"
                  % (len(cached["bad"]), ", ".join(cached["bad"])))
            return 1
        print("KET QUA: TAT CA KENH 'Su kien TV360' OK -> DUOC PUSH")
        return 0

    t0 = time.time()
    lines, bad = run_all()
    for l in lines:
        print(l)
    print("\n(verify took %.0fs)" % (time.time() - t0))
    save_cache(lines, bad)
    print()
    if bad:
        print("KET QUA: %d KENH LOI -> KHONG PUSH: %s" % (len(bad), ", ".join(bad)))
        return 1
    print("KET QUA: TAT CA KENH 'Su kien TV360' OK -> DUOC PUSH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
