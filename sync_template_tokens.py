#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sync_template_tokens.py (them 27/11) - Dong bo token CON HAN tu list.m3u8
(da xac minh) sang file mau template/list.m3u8 + template/Listphu.m3u8.

LY DO: template keo tu GitHub luon chua token CU (het han sau ~6-9h).
sync_tv360.py bo qua nhom "Su kien TV360" -> giu nguyen token cu trong
template -> ghi vao list.m3u8 -> link het han push len GitHub -> kenh chet.

CACH: doi URL trong template bang URL cung slug tren list.m3u8 CHI KHI
token cu trong template da het han (hoac sap het < 30 phut).
KHONG doi URL con han -> giu nguyen phien hien tai.
"""
import base64
import io
import json
import os
import re
import sys
import time

QWEB = "/opt/qweb"
MARGIN = int(os.environ.get("TV360_SYNC_TEMPLATE_MARGIN", "1800"))


def jwt_exp(tok):
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        d = json.loads(base64.urlsafe_b64decode(p))
        return int(d.get("exp", 0) or 0)
    except Exception:
        return 0


def url_exp(u):
    if not u:
        return 0
    for pat in (r"/tok_([A-Za-z0-9_\-\.]+)",
                r"cdntoken=([^&\s]+)",
                r"[?&]auth=([A-Za-z0-9_\-\.]+)"):
        m = re.search(pat, u)
        if m:
            e = jwt_exp(m.group(1))
            if e:
                return e
    return 0


def slug_of(block):
    m = re.search(r'tvg-id="([^"]+)"', block[0])
    return m.group(1).strip().lower() if m else ""


def url_of(lines):
    for l in lines:
        s = l.strip()
        if s.startswith("http"):
            return s
    return ""


def index_by_slug(path):
    """{slug: (start, end, url_idx)} theo tung block EXTINF."""
    try:
        lines = io.open(path, encoding="utf-8", errors="replace").read().split("\n")
    except Exception:
        return {}, []
    out = {}
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            j = i + 1
            while j < len(lines) and not lines[j].startswith("#EXTINF"):
                j += 1
            blk = lines[i:j]
            slug = slug_of(blk)
            if slug:
                uidx = None
                for k, l in enumerate(blk):
                    if l.strip().startswith("http"):
                        uidx = i + k
                        break
                out[slug] = (i, j, uidx)
            i = j
            continue
        i += 1
    return out, lines


def main():
    now = int(time.time())
    src_path = os.path.join(QWEB, "list.m3u8")
    if not os.path.exists(src_path):
        print("khong co list.m3u8 - bo qua")
        return 0

    src_idx, src_lines = index_by_slug(src_path)
    if not src_idx:
        print("list.m3u8 rong - bo qua")
        return 0

    total = 0
    for name in ("list.m3u8", "Listphu.m3u8"):
        tpl = os.path.join(QWEB, "template", name)
        if not os.path.exists(tpl):
            continue
        tpl_idx, tpl_lines = index_by_slug(tpl)
        changed = 0
        for slug, (s, e, uidx) in tpl_idx.items():
            if uidx is None:
                continue
            tpl_url = tpl_lines[uidx].strip()
            t_exp = url_exp(tpl_url)
            if t_exp and (t_exp - now) > MARGIN:
                # token trong template con han -> giu nguyen
                continue
            s_info = src_idx.get(slug)
            if not s_info:
                continue
            _s2, _e2, s_uidx = s_info
            if s_uidx is None:
                continue
            new_url = src_lines[s_uidx].strip()
            n_exp = url_exp(new_url)
            if not new_url or new_url == tpl_url:
                continue
            if "0.0.0.0" in new_url:
                # nguon da error.ts -> giu nguyen template (khong ghi over)
                continue
            if t_exp and n_exp and n_exp <= t_exp:
                # nguon cung het han -> giu nguyen
                continue
            tpl_lines[uidx] = new_url
            changed += 1
            print("  %-16s template token het han -> lay URL moi tu list.m3u8"
                  % slug)
        if changed:
            with io.open(tpl, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(tpl_lines))
            print("%s: %d dong URL dong bo" % (name, changed))
            total += changed
    print("sync_template_tokens: tong %d dong" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
