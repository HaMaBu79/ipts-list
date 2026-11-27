#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sua apply_v2.py: BO QUA kenh 6/7 (theo yeu cau - de den man).
Tat ca muc khac giong nhu ban cu.
"""
import io
import json
import os
import re
import shutil
import sys

QWEB = "/opt/qweb"
# --- Danh sach kenh: doc tu config chung tv360_channels.py ---
# Them kenh moi = them 1 dong vao tv360_channels.py, KHONG sua file nay.
try:
    import tv360_channels as TVC
    TARGET = TVC.order()
    SKIP = set(TVC.skips())
except Exception:
    TARGET = ["tv360plus%d" % i for i in range(1, 16)] + ["tv360", "livestream-01"]
    # Kenh 6/7: bo qua hoan toan - giu nguyen muc hien tai (den man).
    SKIP = {"tv360plus6", "tv360plus7"}
DATA = os.path.join(QWEB, "tv360_resolved_new.json")
LOG = "/var/log/qweb_apply_tv360.log"


def strip_port(u):
    return re.sub(r"^(https?://[^/:]+):443(?=/)", r"\1", u)


def url_exp(u):
    """exp (epoch) cua BAT KY loai token trong URL. 0 = khong doc duoc.

    Ho tro: cdntoken=<JWT>  |  /tok_<JWT>  |  ?auth=<JWT>
    """
    if not u:
        return 0
    import base64

    def jwt_exp(tok):
        try:
            p = tok.split(".")[1]
            p += "=" * (-len(p) % 4)
            d = json.loads(base64.urlsafe_b64decode(p))
            return int(d.get("exp", 0) or 0)
        except Exception:
            return 0

    m = re.search(r"/tok_([A-Za-z0-9_\-\.]+)", u)
    if m:
        e = jwt_exp(m.group(1))
        if e:
            return e
    m = re.search(r"cdntoken=([^&\s]+)", u)
    if m:
        e = jwt_exp(m.group(1))
        if e:
            return e
    m = re.search(r"[?&]auth=([A-Za-z0-9_\-\.]+)", u)
    if m:
        e = jwt_exp(m.group(1))
        if e:
            return e
    return 0


def url_alive(u, margin=0):
    """True neu token trong URL con han."""
    e = url_exp(u)
    if e <= 0:
        return False
    import time
    return (e - time.time()) > margin


ERROR_URL = "http://0.0.0.0/error.ts"


def load():
    try:
        return json.load(open(DATA, encoding="utf-8"))
    except Exception:
        return {}


def parse(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    lines = txt.split("\n")
    blocks, i = [], 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("#EXTINF"):
                j += 1
            blocks.append(lines[i:j])
            i = j
            continue
        blocks.append([lines[i]])
        i += 1
    return txt, blocks


def slug_of(blk):
    m = re.search(r'tvg-id="([^"]+)"', blk[0])
    return m.group(1).strip().lower() if m else ""


def build_block(blk, rec):
    url = (rec.get("url") or "").strip()
    key = (rec.get("key") or "").strip()
    if not url:
        return blk, "GIU NGUYEN (khong co URL)"
    url = strip_port(url)
    if key and ":" not in key:
        key = ""
    out = []
    for l in blk:
        s = l.strip()
        if s.startswith("#KODIPROP:inputstream.adaptive.license_key="):
            if key:
                out.append("#KODIPROP:inputstream.adaptive.license_key=%s" % key)
            continue
        if s.startswith("#KODIPROP:inputstream.adaptive.license_type="):
            if key:
                out.append("#KODIPROP:inputstream.adaptive.license_type=clearkey")
            continue
        if s.startswith("#KODIPROP:inputstream.adaptive.manifest_type="):
            out.append("#KODIPROP:inputstream.adaptive.manifest_type=mpd")
            continue
        if s.startswith("http"):
            out.append(url)
            continue
        out.append(l)
    if key and not any(x.startswith("#KODIPROP:inputstream.adaptive.license_key=") for x in out):
        idx = len(out)
        for k, x in enumerate(out):
            if x.startswith("#EXTINF"):
                idx = k + 1
                break
        out.insert(idx, "#KODIPROP:inputstream.adaptive.license_key=%s" % key)
        out.insert(idx, "#KODIPROP:inputstream.adaptive.license_type=clearkey")
    return out, "DA GHI"


def apply(path, data, dry=False):
    txt, blocks = parse(path)
    changed = 0
    report = []
    for b in blocks:
        if not b or not b[0].startswith("#EXTINF"):
            continue
        slug = slug_of(b)
        if slug not in TARGET:
            continue
        if slug in SKIP:
            report.append((slug, "BO QUA (theo yeu cau)"))
            continue
        rec = data.get(slug)
        if not rec:
            report.append((slug, "KHONG CO DU LIEU"))
            continue
        if rec.get("status") not in ("KHOP", "HLS"):
            # Resolve that bai (LECH/LOI). Neu muc hien tai dang tro toi
            # URL co token HET HAN -> XOA (error.ts) de KHONG push link chet
            # len GitHub. Con URL con han -> giu nguyen (van chay).
            cur_url = ""
            for l in b:
                s = l.strip()
                if s.startswith("http"):
                    cur_url = s
                    break
            if cur_url and not url_alive(cur_url, 0):
                nb = []
                for l in b:
                    s = l.strip()
                    if s.startswith("#KODIPROP:inputstream.adaptive.license_key="):
                        continue
                    if s.startswith("#KODIPROP:inputstream.adaptive.license_type="):
                        continue
                    if s.startswith("http"):
                        nb.append(ERROR_URL)
                        continue
                    nb.append(l)
                if nb != b:
                    b[:] = nb
                    changed += 1
                report.append((slug, "XOA LINK HET HAN -> error.ts (%s)"
                               % rec.get("status")))
            else:
                report.append((slug, "BO QUA (%s)" % rec.get("status")))
            continue
        nb, st = build_block(b, rec)
        if nb != b:
            b[:] = nb
            changed += 1
        report.append((slug, "%s %s" % (st, (rec.get("key") or rec.get("url", ""))[:34])))
    if changed and not dry:
        bak = path + ".bak_apply"
        if not os.path.exists(bak):
            shutil.copy2(path, bak)
        with io.open(path, "w", encoding="utf-8") as f:
            f.write("\n".join("\n".join(b) for b in blocks))
    return changed, report


def main():
    dry = "--dry" in sys.argv
    data = load()
    print("du lieu: %d muc, %d OK" % (
        len(data), sum(1 for v in data.values() if v.get("status") in ("KHOP", "HLS"))))
    lines = []
    for path in (os.path.join(QWEB, "list.m3u8"), os.path.join(QWEB, "Listphu.m3u8")):
        if not os.path.exists(path):
            continue
        changed, report = apply(path, data, dry)
        print("\n== %s : %d muc doi %s" % (os.path.basename(path), changed,
                                          "(DRY)" if dry else ""))
        lines.append("== %s : %d muc doi" % (os.path.basename(path), changed))
        for slug, st in report:
            print("   %-14s %s" % (slug, st))
            lines.append("   %-14s %s" % (slug, st))
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except Exception:
        pass


if __name__ == "__main__":
    main()
