# -*- coding: utf-8 -*-
"""Doc va ghi playlist M3U8 theo nguyen tac: chi doi DONG URL.

Moi muc giu lai chi so dong goc. Khi cap nhat, chi thay theo chi so do
va giu nguyen byte-for-byte tat ca dong khac.
"""
import re


class Entry:
    """Mot kenh, kem chi so dong de sua cho dung cho."""

    __slots__ = ("hdr", "name", "tvg_id", "group", "url", "url_idx",
                 "hdr_idx", "ua_line")

    def __init__(self, hdr, hdr_idx, name, tvg_id, group, url, url_idx, ua_line):
        self.hdr = hdr
        self.hdr_idx = hdr_idx
        self.name = name
        self.tvg_id = tvg_id
        self.group = group
        self.url = url
        self.url_idx = url_idx
        self.ua_line = ua_line

    def __repr__(self):
        return "<Entry %s id=%s url=%s>" % (self.name[:24], self.tvg_id, self.url[:40])


def _split_name(hdr):
    # "...,Ten kenh" -> "Ten kenh"
    i = hdr.find(",")
    return hdr[i + 1:].strip() if i >= 0 else ""


def _attr(hdr, key):
    m = re.search(r'%s="([^"]*)"' % re.escape(key), hdr)
    return m.group(1) if m else ""


def parse_playlist(path):
    """Tra ve (lines, entries). lines la list chuoi nguyen ban."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        raw = f.read()

    # giu nguyen bo tach dong goc
    nl = "\r\n" if "\r\n" in raw else "\n"
    lines = raw.split(nl)
    if lines and lines[-1] == "":
        lines.pop()
        trailing = True
    else:
        trailing = False

    entries = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("#EXTINF"):
            hdr = ln
            url_idx = None
            url = None
            ua = None
            j = i + 1
            while j < len(lines):
                s = lines[j].strip()
                if s.startswith("#EXTVLCOPT:http-"):
                    ua = lines[j]
                    j += 1
                    continue
                if s.startswith("http://") or s.startswith("https://"):
                    url_idx = j
                    url = s
                    j += 1
                    break
                if s.startswith("#EXTINF"):
                    break            # khong co URL, khong phai muc hop le
                j += 1            # dong trong hoac comment -> bo qua
            if url is not None:
                entries.append(Entry(
                    hdr=hdr, hdr_idx=i,
                    name=_split_name(hdr),
                    tvg_id=_attr(hdr, "tvg-id"),
                    group=_attr(hdr, "group-title"),
                    url=url, url_idx=url_idx, ua_line=ua))
                i = url_idx + 1
                continue
        i += 1

    return lines, entries, nl, trailing


def write_playlist(path, lines, url_map, newline="\n", trailing=True):
    """Chi thay cac dong URL co trong url_map {chi_so_dong: url_moi}.

    Tra ve so dong da doi. Khong them, khong xoa, khong sap xep lai.
    """
    out = list(lines)
    changed = 0
    for idx, new in url_map.items():
        if out[idx] != new:
            out[idx] = new
            changed += 1
    text = newline.join(out)
    if trailing:
        text += newline
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return changed


def index_by(entries):
    """Tao chi muc tra nhanh: tvg-id, ten."""
    by_tvg, by_name = {}, {}
    for e in entries:
        if e.tvg_id:
            by_tvg.setdefault(e.tvg_id.strip().lower(), []).append(e)
        if e.name:
            by_name.setdefault(e.name.strip().lower(), []).append(e)
    return by_tvg, by_name
