#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Sinh / cap nhat list.m3u8 + Listphu.m3u8 tu TV360 truc tiep.

Nguon: api.tv360.vn -> /public/v1/composite/get-home-live (danh sach kenh)
       tv360.vn      -> /public/v1/composite/get-link    (link phat m3u8)

Khong con phu thuoc trung gian nao (khong qua vietanhtv.top).

Dung:
  python sync_tv360.py            # cap nhat 2 file
  python sync_tv360.py --dry      # xem truoc, khong ghi
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from tv360_client import client  # noqa: E402

# ------------------------------------------------------------------ config
CONFIG_PATH = os.path.join(HERE, "tv360_sync.json")

DEFAULT_CFG = {
    "playlist_files": ["list.m3u8", "Listphu.m3u8"],
    # Nhom TV360 -> group-title trong playlist
    "group_map": {
        "Kênh VTV": "VTV",
        "Kênh HTV": "HTV",
        "Kênh VTV Cab": "VTVcab",
        "Kênh SCTV": "SCTV",
        "Kênh Vĩnh Long": "Vĩnh Long",
        "Kênh địa phương": "Địa Phương",
        "Kênh quốc tế": "Quốc tế",
        "Kênh FM": "FM",
        "Kênh thiết yếu": "VTV",
        "Giải trí": "Giải trí",
        "Thể thao": "Thể thao",
        "Sự kiện trực tiếp": "Sự kiện",
    },
    # Chi lay cac kenh co needLogin = 0 (mien phi, khong can dang nhap).
    # Dat False de lay ca kenh can dang nhap (can token).
    "only_free_no_login": True,
    # Giu nguyen dong #EXTVLCOPT cua file goc (User-Agent)
    "keep_vlcopt": True,
    # Ghi dong #UPDATE
    "stamp_update": True,
    "user": "HaMaBu",
}


def load_cfg():
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    try:
        with io.open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


# ------------------------------------------------------------------ playlist io
def parse_playlist(path):
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    nl = "\r\n" if "\r\n" in text else "\n"
    trailing = text.endswith("\n")
    lines = text.replace("\r\n", "\n").split("\n")
    if trailing and lines and lines[-1] == "":
        lines.pop()
    entries = []
    i = 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            hdr = lines[i]
            url = None
            url_idx = None
            ua = None
            j = i + 1
            while j < len(lines):
                s = lines[j].strip()
                if s.startswith("#EXTVLCOPT:http-"):
                    ua = lines[j]
                    j += 1
                    continue
                if s.startswith("http://") or s.startswith("https://"):
                    url, url_idx = s, j
                    j += 1
                    break
                if s.startswith("#EXTINF"):
                    break
                j += 1
            if url is not None:
                m = re.search(r'tvg-id="([^"]*)"', hdr)
                g = re.search(r'group-title="([^"]*)"', hdr)
                k = hdr.find(",")
                entries.append({
                    "tvg": (m.group(1).strip() if m else ""),
                    "group": (g.group(1).strip() if g else ""),
                    "name": hdr[k + 1:].strip() if k >= 0 else "",
                    "url": url,
                    "ua": ua,
                    "url_idx": url_idx,
                })
                i = url_idx + 1
                continue
        i += 1
    return lines, entries, nl, trailing


def build_update_line(user="HaMaBu"):
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    except Exception:
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=7)
    return ("#UPDATE %02d:%02d⏱%02d/%02d | User: %s"
            % (now.hour, now.minute, now.day, now.month, user))


def stamp_update(lines, user="HaMaBu"):
    new_line = build_update_line(user)
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#UPDATE"):
            lines[i] = new_line
            return "da cap nhat"
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#EXTM3U"):
            lines.insert(i + 1, new_line)
            return "da chen"
    lines.insert(0, new_line)
    return "da chen"


# ------------------------------------------------------------------ matching
def norm(s):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9a]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def norm2(s):
    """Chuan hoa them: bo 'hd', 'tv', so thu tu, dau gach."""
    s = norm(s)
    s = re.sub(r"\b(hd|sd|uhd|fhd|4k|2k)\b", " ", s)
    s = re.sub(r"\b\d{1,2}\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_indexes(catalog):
    by_slug, by_name, by_norm, by_norm2 = {}, {}, {}, {}
    # Ten tinh/thanh pho (khong so) -> danh sach kenh dia phuong
    by_province = {}
    for ch in catalog:
        cid = str(ch.get("id"))
        by_slug[cid] = ch
        if ch.get("slug"):
            by_slug[str(ch["slug"]).lower()] = ch
        n = norm(ch.get("name"))
        if n:
            by_name.setdefault(n, ch)
        by_norm.setdefault(n, ch)
        n2 = norm2(ch.get("name"))
        if n2:
            by_norm2.setdefault(n2, ch)
        # "Quang Ninh 3" -> "quang ninh"
        base = re.sub(r"\s*\d+\s*$", "", n)
        if base and len(base) >= 4:
            by_province.setdefault(base, []).append(ch)
    return by_slug, by_name, by_norm, by_norm2, by_province


def strip_province(name):
    """'QTV1 HD | TH Quang Ninh' -> ('quang ninh', '1')"""
    s = (name or "").split("|")[-1].strip() if "|" in (name or "") else (name or "").strip()
    s = re.sub(r"^TH\s+", "", s, flags=re.I).strip()
    m = re.search(r"(\d+)\s*$", s)
    num = m.group(1) if m else ""
    base = re.sub(r"\s*\d+\s*(HD|SD)?\s*$", "", s, flags=re.I).strip()
    return norm(base), num


def match_channel(entry, idx):
    by_slug, by_name, by_norm, by_norm2, by_province = idx
    tvg = (entry["tvg"] or "").strip()
    name = (entry["name"] or "").strip()

    # 1. tvg-id la id TV360
    if tvg and tvg in by_slug:
        return by_slug[tvg], "tvg-id(id)"
    # 2. tvg-id la slug TV360
    if tvg and tvg.lower() in by_slug:
        return by_slug[tvg.lower()], "tvg-id(slug)"
    # 3. ten giong het
    if name and norm(name) in by_name:
        return by_name[norm(name)], "ten"
    # 4. ten chuan hoa
    if name:
        n = norm(name)
        if n in by_norm:
            return by_norm[n], "ten~"
        n2 = norm2(name)
        if n2 and n2 in by_norm2:
            return by_norm2[n2], "ten~~"
        # 5. Kenh dia phuong: "TH <tinh>" -> "Quang Ninh 1"
        prov, num = strip_province(name)
        if prov and len(prov) >= 4:
            cands = by_province.get(prov)
            if not cands:
                for k, v in by_province.items():
                    if len(k) >= 4 and (prov in k or k in prov):
                        cands = v
                        break
            if cands:
                if num:
                    for ch in cands:
                        if re.search(r"\b%s\b" % re.escape(num),
                                     str(ch.get("name") or "")):
                            return ch, "dia phuong+%s" % num
                return cands[0], "dia phuong"
        # 6. Viet tat: "THP HD" -> "THP"
        base = re.sub(r"^(TH|Q|HT)\s*", "", name, flags=re.I)
        b2 = norm2(base)
        if b2:
            for k, ch in by_norm2.items():
                if b2 == k or (len(b2) >= 3 and b2 in k):
                    return ch, "viet tat"
        # 7. kenh dia phuong dua vao slug
        if tvg:
            t = re.sub(r"\d+$", "", tvg.lower())
            for k, v in by_province.items():
                if t and t in k:
                    return v[0], "slug dia phuong"
    return None, None


# ------------------------------------------------------------------ main
def build(cfg, dry=False):
    c = client()
    catalog = c.catalog()
    if not catalog:
        print("!! khong lay duoc danh muc kenh tu TV360")
        return 2

    only_free = cfg.get("only_free_no_login", True)
    group_map = cfg.get("group_map", {})

    # Lay link cho tung kenh (chay nhieu luong cho nhanh)
    print("Dang lay link truc tiep tu TV360...")
    links = {}
    n_ok = n_fail = n_needlogin = 0
    todo = [ch for ch in catalog]
    workers = int(cfg.get("workers", 8))

    def fetch(ch):
        cid = str(ch["id"])
        url, err = c.get_stream(cid)
        needlogin = False
        if not url:
            s = str(err)
            if "ăng nhập" in s or "D-164" in s or "401" in s:
                needlogin = True
                if only_free and ch.get("need_login"):
                    return cid, None, err, needlogin
                c.ensure_token()
                url, err = c.get_stream(cid)
        return cid, url, err, needlogin

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (cid, url, err, nl) in enumerate(ex.map(fetch, todo), 1):
            if nl:
                n_needlogin += 1
            if url:
                links[cid] = url
                n_ok += 1
                if i % 25 == 0:
                    print("   ... %d/%d" % (i, len(todo)))
            else:
                n_fail += 1
                if n_fail <= 10:
                    print("   [FAIL] id=%-7s %s" % (cid, str(err)[:50]))
    print("   xong: %d ok, %d fail, %d can dang nhap" % (n_ok, n_fail, n_needlogin))
    if not links:
        print("!! khong co link nao lay duoc")
        return 2

    idx = build_indexes(catalog)
    worst = 0
    total_changed = 0

    for pl_name in cfg.get("playlist_files", []):
        path = os.path.join(HERE, pl_name)
        print("\n" + "#" * 66)
        print("# %s" % pl_name)
        print("#" * 66)
        if not os.path.exists(path):
            print("  (khong ton tai - bo qua)")
            continue

        lines, entries, nl, trailing = parse_playlist(path)
        print("  %d dong, %d muc" % (len(lines), len(entries)))

        url_map = {}
        updated = kept = 0
        nomap = []
        for e in entries:
            ch, how = match_channel(e, idx)
            if ch is None:
                kept += 1
                nomap.append((e["name"], e["group"]))
                continue
            new = links.get(str(ch["id"]))
            if not new:
                kept += 1
                nomap.append((e["name"], "khong lay duoc link (%s)" % ch.get("name")))
                continue
            if new == e["url"]:
                kept += 1
                continue
            url_map[e["url_idx"]] = new
            updated += 1

        print("  se doi %d dong URL | giu nguyen %d" % (updated, kept))
        if nomap and os.environ.get("TV360_VERBOSE"):
            print("  --- %d muc khong doi duoc ---" % len(nomap))
            for nm, why in nomap[:40]:
                print("      %-30s %s" % (str(nm)[:30], why))
        if not updated:
            continue
        if dry:
            print("  DRY-RUN: khong ghi")
            continue

        # ghi
        for idx_line, new in url_map.items():
            lines[idx_line] = new
        if cfg.get("stamp_update", True):
            act = stamp_update(lines, cfg.get("user", "HaMaBu"))
            print("  #UPDATE: %s" % act)

        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(nl.join(lines) + (nl if trailing else ""))
        os.replace(tmp, path)
        print("  DA GHI %d dong vao %s" % (updated, pl_name))
        total_changed += updated

    print("\n" + "=" * 66)
    print("HOAN TAT: %d dong URL thay doi" % total_changed)
    print("=" * 66)
    return worst


def main():
    dry = "--dry" in sys.argv
    cfg = load_cfg()
    print("=" * 66)
    print("  SYNC TV360 TRUC TIEP  |  che do: %s"
          % ("DRY-RUN" if dry else "GHI THAT"))
    print("=" * 66)
    return build(cfg, dry=dry)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nDa dung.")
        sys.exit(130)
