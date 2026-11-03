#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MERGE SCTV - doi nguon kenh SCTV sang list "vip_full".

QUY TAC (theo yeu cau):
  1. Moi muc trong nhom SCTV cua playlist tao, neu co ten tuong ung trong
     vip_full -> lay URL tu vip_full.
  2. Neu vip_full KHONG co kenh do -> GIU NGUYEN, van lay tu vietanhtv.
  3. Neu URL lay tu vip_full bi 404 (nguon da chet) -> GIU NGUYEN muc cu,
     vi lay link chet thi kenh chan chay.
  4. Khi doi sang nguon khong DRM -> xoa cac dong #KODIPROP adaptive
     (manifest_type / license_type / license_key) di kem.

Chay SAU sync_tv360.py (khong sua file sync) -> cron khong ghi de ket qua.
"""
import io
import os
import re
import sys
import urllib.request
import urllib.error

VIP_URL = ("https://raw.githubusercontent.com/hai-crypto2026/listmytv/"
           "refs/heads/main/vip_full")
DEFAULT_FILES = ["/opt/ipts-list/list.m3u8", "/opt/ipts-list/Listphu.m3u8"]
UA = ("Mozilla/5.0 (SMART-TV; LINUX; Tizen 10.0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) 130.0.6723.116/10.0 TV Safari/537.36")

# Map tvg-id cua tao -> ten kenh trong vip_full.
# Khoa khong co trong vip_full -> giu nguyen (lay tu vietanhtv).
TVG2VIP = {
    "sctv1hd": "SCTV1 HD",
    "sctv2hd": "SCTV2 HD",
    "sctv3hd": "SCTV3 HD",
    "sctv4hd": "SCTV4 HD",
    "sctv6hd": "SCTV6 HD",
    "sctv7hd": "SCTV7 HD",
    "sctv8hd": "SCTV8 HD",
    "sctv9": "SCTV9 HD",
    "sctv11hd": "SCTV11 HD",
    "sctv12hd": "SCTV12 HD",
    "sctv13hd": "SCTV13 HD",
    "sctv14hd": "SCTV14 HD",
    "sctv16hd": "SCTV16 HD",
    "sctv18hd": "SCTV18 HD",
    "sctv19hd": "SCTV19 HD",
    "sctv20hd": "SCTV20 HD",
    "sctv21hd": "SCTV21 HD",
    "sctvhdpth": "SCTV-PTH HD",
    "sctv4k": "SCTV4K",
}

DRM_PREFIXES = (
    "#KODIPROP:inputstream.adaptive.manifest_type=",
    "#KODIPROP:inputstream.adaptive.license_type=",
    "#KODIPROP:inputstream.adaptive.license_key=",
)

# Cac kenh DRM -> dua qua relay BB2 (BB2 tu lay key, giai ma, phat MPEG-TS).
# Thiet bi KHONG con phai goi clearkey.php cua vietanhtv -> het man den.
RELAY_CHANNELS = ["sctv1hd", "sctv8hd", "sctv15", "sctv17", "sctv21hd",
                  "sctv22hd", "onsports", "onsportsplus", "onfootball",
                  "onsportsnews"]
TUNNEL_FILE = "/opt/qweb/tunnel_url.txt"


def tunnel_base():
    """URL public cua BB2 (cloudflare tunnel hien tai)."""
    try:
        with io.open(TUNNEL_FILE, encoding="utf-8") as f:
            u = f.read().strip()
        return u.rstrip("/") if u else ""
    except Exception:
        return ""


def fetch(url, timeout=45, ua=UA):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.getcode(), r.read()
    except urllib.error.HTTPError as e:
        try:
            e.read()
        except Exception:
            pass
        return e.code, b""
    except Exception:
        return 0, b""


def alive(url):
    """URL co phai con song khong (HTTP 200 + body khong rong) khong?"""
    code, body = fetch(url, timeout=20)
    return code == 200 and len(body.strip()) > 0, code


def parse_vip(text):
    """{ten kenh: url} cho nhom SCTV trong vip_full."""
    out = {}
    cur = None
    for raw in text.split("\n"):
        l = raw.strip()
        if not l:
            continue
        if l.startswith("#EXTINF"):
            cur = l
        elif cur and not l.startswith("#"):
            if 'group-title="SCTV"' in cur:
                out.setdefault(cur.split(",")[-1].strip(), l)
            cur = None
    return out


def merge_file(path, vip, alive_cache):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith("\n")
    lines = text.replace("\r\n", "\n").split("\n")
    if trailing and lines and lines[-1] == "":
        lines.pop()

    out = []
    i = 0
    changed = 0
    kept = 0
    report = []
    while i < len(lines):
        l = lines[i]
        if not l.startswith("#EXTINF"):
            out.append(l)
            i += 1
            continue
        block = [l]
        j = i + 1
        url_idx = None
        while j < len(lines):
            s = lines[j].strip()
            if s.startswith("#"):
                block.append(lines[j])
                j += 1
                continue
            if s:
                url_idx = len(block)
                block.append(lines[j])
                j += 1
            break
        i = j

        if 'group-title="SCTV"' not in block[0]:
            out.extend(block)
            continue

        name = block[0].split(",")[-1].strip()
        m = re.search(r'tvg-id="([^"]*)"', block[0])
        tvg = (m.group(1) if m else "").lower()

        vip_name = TVG2VIP.get(tvg)
        if not vip_name:
            out.extend(block)
            kept += 1
            report.append(("GIU (khong co trong vip_full)", tvg, name, ""))
            continue

        vip_url = vip.get(vip_name)
        if not vip_url:
            out.extend(block)
            kept += 1
            report.append(("GIU (vip_full khong co muc nay)", tvg, name, ""))
            continue

        if vip_url not in alive_cache:
            alive_cache[vip_url] = alive(vip_url)
        ok, code = alive_cache[vip_url]
        if not ok:
            out.extend(block)
            kept += 1
            report.append(("GIU (link vip_full bi %s - nguon chet)" % code,
                           tvg, name, vip_url))
            continue

        newblock = [block[0]]
        for extra in block[1:]:
            s = extra.strip()
            if any(s.startswith(p) for p in DRM_PREFIXES):
                continue
            newblock.append(extra)
        # tim lai chi so URL trong newblock (da co the bi lech do bo DRM)
        nu = None
        for k2 in range(len(newblock) - 1, 0, -1):
            if not newblock[k2].strip().startswith("#"):
                nu = k2
                break
        if nu is not None:
            newblock[nu] = vip_url
        else:
            newblock.append(vip_url)
        out.extend(newblock)
        changed += 1
        report.append(("DA DOI -> vip_full", tvg, name, vip_url))

    newtext = nl.join(out)
    if trailing:
        newtext += nl
    if newtext != text:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(newtext)
    return changed, kept, report


def apply_relay(path, base):
    """Doi URL cua cac kenh DRM sang link relay cua BB2.

    Ly do: 10 kenh nay la DASH/CENC ClearKey. Neu de nguyen license_key,
    moi thiet bi phai tu goi clearkey.php cua vietanhtv - server do chay chay
    chon (404 xen ke 200) nen may nao gap luc 404 se MAN DEN khong bao loi.
    Relay giai ma o phia BB2 -> moi thiet bi deu xem duoc.
    """
    if not base:
        return 0, []
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith("\n")
    lines = text.replace("\r\n", "\n").split("\n")
    if trailing and lines and lines[-1] == "":
        lines.pop()

    out = []
    i = 0
    changed = []
    while i < len(lines):
        l = lines[i]
        if not l.startswith("#EXTINF"):
            out.append(l)
            i += 1
            continue
        block = [l]
        j = i + 1
        while j < len(lines):
            s = lines[j].strip()
            if s.startswith("#"):
                block.append(lines[j])
                j += 1
                continue
            if s:
                block.append(lines[j])
                j += 1
            break
        i = j

        m = re.search(r'tvg-id="([^"]*)"', block[0])
        tvg = (m.group(1) if m else "").lower()
        if tvg not in RELAY_CHANNELS:
            out.extend(block)
            continue

        newblock = [block[0]]
        for extra in block[1:]:
            s = extra.strip()
            if any(s.startswith(p) for p in DRM_PREFIXES):
                continue
            newblock.append(extra)
        nu = None
        for k2 in range(len(newblock) - 1, 0, -1):
            if not newblock[k2].strip().startswith("#"):
                nu = k2
                break
        relay_url = "%s/relay/%s.m3u8" % (base, tvg)
        if nu is not None:
            newblock[nu] = relay_url
        else:
            newblock.append(relay_url)
        out.extend(newblock)
        changed.append((tvg, relay_url))

    newtext = nl.join(out)
    if trailing:
        newtext += nl
    if newtext != text:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(newtext)
    return len(changed), changed


def main():
    files = sys.argv[1:] or DEFAULT_FILES
    code, body = fetch(VIP_URL, timeout=60)
    if code != 200 or not body:
        print("LOAI tai vip_full (HTTP %s) - giu nguyen playlist" % code)
        return 1
    vip = parse_vip(body.decode("utf-8", "replace"))
    print("vip_full: %d muc SCTV" % len(vip))

    alive_cache = {}
    total_ch = total_k = 0
    for p in files:
        if not os.path.exists(p):
            print("BO QUA (khong ton tai): %s" % p)
            continue
        ch, k, rep = merge_file(p, vip, alive_cache)
        print("\n%s: doi %d, giu %d" % (p, ch, k))
        for st, tvg, name, url in rep:
            print("   %-34s %-12s %-24s %s" % (st, tvg, name[:24], url[:70]))
        total_ch += ch
        total_k += k
    print("\nTONG: doi %d muc, giu %d muc" % (total_ch, total_k))

    # Buoc 2: dua kenh DRM qua relay BB2
    base = tunnel_base()
    if not base:
        print("\n[relay] khong doc duoc tunnel URL -> giu nguyen kenh DRM")
        return 0
    print("\n[relay] dung tunnel: %s" % base)
    for p in files:
        if not os.path.exists(p):
            continue
        n, ch2 = apply_relay(p, base)
        print("[relay] %s: %d kenh -> relay" % (p, n))
        for tvg, u in ch2:
            print("        %-13s %s" % (tvg, u))
    return 0


if __name__ == "__main__":
    sys.exit(main())
