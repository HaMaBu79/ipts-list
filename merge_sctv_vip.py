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
import base64
import io
import json
import os
import re
import sys
import time
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

# Cac kenh DRM -> lay key tu vietanhtv ROI va nhung thang KID:KEY vao playlist.
#
# LY DO KHONG DUNG RELAY: upload cua BB2 chi ~500KB/s (4Mbps). Kenh relay
# phai chay qua Cloudflare tunnel cua BB2 -> lag, load hinh lau, nhieu kenh
# cung luc khong nang. Kenh lay TRUC TIEP (liveatmvng.vtvprime.vn) thi thiet bi
# tu tai ve -> khong qua BB2 -> muot.
#
# LY DO PHAI NHUNG KEY: server clearkey.php cua vietanhtv chay chay chon
# (404 xen ke 200) + key xoay vong. Neu de nguyen URL trong playlist, may nao
# gap luc 404 se MAN DEN. Nhung KID:KEY thi thiet bi khong can goi server
# nua -> luon xem duoc. Key duoc lam moi moi lan sync (cron ~47 phut).
RELAY_CHANNELS = ["sctv1hd", "sctv8hd", "sctv15", "sctv17", "sctv21hd",
                  "sctv22hd", "onsports", "onsportsplus", "onfootball",
                  "onsportsnews"]
TUNNEL_FILE = "/opt/qweb/tunnel_url.txt"
UA_DL = "Dalvik/2.1.0"


def fetch_kidkey(lic_url, tries=6, gap=4):
    """Lay 'KID:KEY' (hex) tu clearkey endpoint cua vietanhtv.

    Server CHI tra key khi UA la 'Dalvik/2.1.0' (nhu app Android TV).
    Server chay chay chon -> retry nhieu lan.

    Neu het retry van khong duoc -> dung key da CACHE (drm_cache.py).
    Cache giup kenh khong bi MAN DEN khi endpoint dung luc dong bo.
    """
    if not lic_url or not str(lic_url).startswith("http"):
        return None
    cached = None
    try:
        sys.path.insert(0, "/opt/qweb")
        import drm_cache
        cached = drm_cache.get(lic_url)
    except Exception:
        cached = None
    for i in range(tries):
        try:
            req = urllib.request.Request(lic_url,
                                         headers={"User-Agent": UA_DL})
            with urllib.request.urlopen(req, timeout=20) as r:
                body = r.read().decode("utf-8", "replace")
            j = json.loads(body)
            ks = j.get("keys") or []
            if ks:
                k = ks[0]
                kid = k.get("kid", "")
                key = k.get("k", "")
                if not key:
                    return None
                if len(key) != 32 or not all(
                        c in "0123456789abcdefABCDEF" for c in key):
                    # base64url -> hex
                    raw = base64.urlsafe_b64decode(key + "=" * (-len(key) % 4))
                    key = raw.hex()
                try:
                    raw = base64.urlsafe_b64decode(kid + "=" * (-len(kid) % 4))
                    kid = raw.hex()
                except Exception:
                    pass
                kk = "%s:%s" % (kid, key)
                # luu vao cache de dung lai khi endpoint chet
                try:
                    import drm_cache
                    drm_cache.put(lic_url, kk)
                except Exception:
                    pass
                return kk
        except Exception:
            pass
        if i < tries - 1:
            time.sleep(gap)
    # Het retry -> dung key da cache (neu co)
    if cached:
        return cached
    return None


def inline_keys(path):
    """Thay moi dong license_key=<URL> bang license_key=<KID:KEY> that."""
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    lines = text.replace("\r\n", "\n").split("\n")
    out = []
    n_ok = 0
    n_fail = []
    for l in lines:
        s = l.strip()
        if s.startswith("#KODIPROP:inputstream.adaptive.license_key="):
            val = s.split("license_key=", 1)[1].strip()
            if val.startswith("http"):
                kk = fetch_kidkey(val)
                if kk:
                    out.append("#KODIPROP:inputstream.adaptive.license_key=" + kk)
                    n_ok += 1
                    continue
                n_fail.append(val[:70])
            out.append(l)
            continue
        out.append(l)
    newtext = nl.join(out)
    if newtext != text:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(newtext)
    return n_ok, n_fail


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
    """KHONG DUNG RELAY - tra ve nguyên link goc cua muc do.

    Ly do: upload cua BB2 chi ~500KB/s. Kenh di qua relay se chay qua
    Cloudflare tunnel cua BB2 -> lag va load hinh lau. Cac kenh lay truc tiep
    (liveatmvng.vtvprime.vn / vtvgolive) thi thiet bi tu tai -> khong qua BB2.
    """
    return 0, []


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

    # Buoc 2: nhung KID:KEY that cho kenh DRM (thiet bi khong goi vietanhtv)
    total_ok = 0
    for p in files:
        if not os.path.exists(p):
            continue
        ok, fail = inline_keys(p)
        total_ok += ok
        print("[key] %s: nhung %d key that" % (p, ok))
        for v in fail:
            print("        FAIL (server chua tra key): %s" % v)
    print("[key] tong %d key da nhung" % total_ok)
    return 0


if __name__ == "__main__":
    sys.exit(main())
