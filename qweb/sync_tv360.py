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
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from tv360_client import client, anon_client  # noqa: E402

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
    # Cac nhom kenh TV360 KHONG co (vd SCTV) -> lay tu nguon thay the
    # (xem "external_sources" ben duoi) thay vi lay sai kenh khac.
    # QUY TAC: TV360 co kenh -> lay TV360; TV360 khong co -> lay vietanhtv.
    # NHOM QUOC TE: giu nguyen 100% (khong dung).
    "skip_groups": ["SCTV", "Quốc tế"],
    # Nguon thay the cho nhom skip: {group: [url, ...]}
    # Moi lan sync se tai playlist nay va lay URL cho cac kenh trong nhom.
    "external_sources": {
        "SCTV": [
            "https://tv.vietanhtv.top/tivi/index.php?u=nguyenduhan&p=nguyenduhan",
        ],
    },
    # Nguon FALLBACK: dung khi TV360 khong co kenh do, hoac khong tra link
    # (HTV5, KTV, QuangNgaiTV, DaNangTV, ON FOOTBALL, ...).
    "external_fallback": [
        "https://tv.vietanhtv.top/tivi/index.php?u=nguyenduhan&p=nguyenduhan",
    ],
    # Giu nguyen dong #EXTVLCOPT cua file goc (User-Agent)
    "keep_vlcopt": True,
    # Ghi dong #UPDATE
    "stamp_update": True,
    "user": "HaMaBu",
}

# Ten kenh trong playlist (nguon cu) khac ten tren TV360.
# KHUYEN -> ten TV360 tuong ung (so sanh theo khoa chuan hoa, prefix deu chap).
ALIAS = {
    # VTVcab (ON*)
    "on vie giai tri": "vtvcab 1 vie giai tri hd",
    "on e channel": "vtvcab 5 e channel hd",
    "on biebi": "vtvcab 8 bibi sd",
    "on bibi": "vtvcab 8 bibi sd",
    # Da Nang: TV360 goi la "Da Nang 1/2" (slug da-nang-1/2)
    "danangtv1 hd": "da nang 1",
    "danangtv2 hd": "da nang 2",
    "danang1": "da nang 1",
    "danang2": "da nang 2",
    # Vinh Long: TV360 goi la "Vinh Long 1..5"
    "thvl1 hd th vinh long": "vinh long 1",
    "thvl2 hd th vinh long": "vinh long 2",
    "thvl3 hd th vinh long": "vinh long 3",
    "thvl4 hd th vinh long": "vinh long 4",
    "thvl5 hd th vinh long": "vinh long 5",
    "thvl1": "vinh long 1",
    "thvl2": "vinh long 2",
    "thvl3": "vinh long 3",
    "thvl4": "vinh long 4",
    "thvl5": "vinh long 5",
    "on info tv": "vtvcab 9 infotv hd",
    "on style tv": "vtvcab 12 styletv",
    "on trending tv": "vtvcab17 trending tv",
    "on vie dramas": "vtvcab19 vie dramas",
    "on v family": "vtvcab 20 v family hd",
    "on life": "vtvcab 22 life tv hd",
    "on golf": "vtvcab 23 golf channel",
    "on sports": "vtvcab 3 on sports",
    "on sports plus": "vtvcab 6 on sports plus",
    "on sports+": "vtvcab 6 on sports plus",
    "on sports news": "vtvcab 18 on sports news",
    "on football": "vtvcab 16 on football hd",
    "on cine": "vtvcab10 on cine hd",
    "on music": "vtvcab15 on music",
    "on movies": "vtvcab 4 on movies",
    "cartoon kids": "vtvcab 21 cartoon kids",
    # SCTV: TV360 khong co kenh SCTV that -> giu nguyen link vietanhtv goc
    # (khong map sang "SCTV2 - Today TV" vi sai noi dung).
    # HTV
    "htv the thao": "htv the thao",
    "htvc plus": "htvc plus channel b",
    "htvc plus hd": "htvc plus channel b",
    "htvc du lich hd": "htvc du lich cuoc song",
    "htvc phim hd": "htvc phim truyen hd",
    # Dia phuong
    "hanoitv1": "ha noi 1",
    "hanoitv2": "ha noi 2",
    "youtv": "youtv",
}


def _norm_alias(s):
    """Chuan hoa ten: bo dau, bo HD/SD, chi giu a-z0-9.

    Chuyen 'đ' -> 'd' TRUOC khi bo dau (neu bo dau NFD, 'đ' -> 'd' + 'SAC',
    bo diacritic se thanh 'd' nhung 'Đà' -> 'a' vi 'à' -> 'a').
    """
    s = (s or "").lower()
    s = s.replace("đ", "d").replace("Đ", "D")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower()
    # GIU dau '+' (phan biet 'ON SPORTS' va 'ON SPORTS+')
    s = s.replace("+", " plus ")
    s = re.sub(r"[^a-z0-9+]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def load_cfg():
    cfg = json.loads(json.dumps(DEFAULT_CFG))
    try:
        with io.open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        pass
    return cfg


# ------------------------------------------------------------------ nguon ngoai
def fetch_external_playlist(url, timeout=45):
    """Tai mot playlist m3u8 tu nguon ngoai (vd vietanhtv.top).

    Tra ve dict: {tvg-id -> (url, ua)} cho cac muc co group-title khop.
    """
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/154.0.0.0 Safari/537.36",
        "Accept": "*/*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    out = {}
    lines = raw.split("\n")
    i = 0
    while i < len(lines):
        ln = lines[i]
        if ln.startswith("#EXTINF"):
            m = re.search(r'tvg-id="([^"]*)"', ln)
            g = re.search(r'group-title="([^"]*)"', ln)
            tvg = m.group(1).strip() if m else ""
            grp = g.group(1).strip() if g else ""
            ua = None
            j = i + 1
            url_line = None
            while j < len(lines):
                s = lines[j].strip()
                if s.startswith("#EXTVLCOPT:http-user-agent"):
                    ua = lines[j]  # giu nguyen ca dong
                    j += 1
                    continue
                if s.startswith("http://") or s.startswith("https://"):
                    url_line = s
                    j += 1
                    break
                if s.startswith("#EXTINF"):
                    break
                j += 1
            if url_line:
                key = (tvg or "").lower()
                out.setdefault(key, {"url": url_line, "ua": ua,
                                    "group": grp, "name": ln.split(",")[-1].strip()})
            i = j
            continue
        i += 1
    return out


def build_external_index(cfg):
    """Tai cac nguon ngoai va tra ve {group: {tvg-id: {url, ua}}}."""
    idx = {}
    for group, urls in (cfg.get("external_sources") or {}).items():
        acc = {}
        for u in urls:
            print("   [ext] tai %s (%s)" % (group, u))
            try:
                d = fetch_external_playlist(u)
            except Exception as e:
                print("   [ext] LOAI %s: %s" % (group, e))
                continue
            for k, v in d.items():
                if k and v.get("group") == group:
                    acc.setdefault(k, v)
        if acc:
            print("   [ext] %s: lay duoc %d muc" % (group, len(acc)))
            idx[group] = acc
        else:
            print("   [ext] %s: KHONG lay duoc muc nao" % group)
    return idx


def build_external_fallback(cfg):
    """Nguon fallback: {tvg-id: {url, ua}} cho kenh TV360 khong lay duoc link."""
    acc = {}
    for u in cfg.get("external_fallback") or []:
        print("   [fb] tai %s" % u)
        try:
            d = fetch_external_playlist(u)
        except Exception as e:
            print("   [fb] LOAI: %s" % e)
            continue
        for k, v in d.items():
            if k:
                acc.setdefault(k, v)
    if acc:
        print("   [fb] fallback: %d muc" % len(acc))
    return acc


# ------------------------------------------------------------------ playlist io
# Regex phan "Update: HH:MM⏱DD/MM" trong ten kenh (nguon goc them vao).
_RE_STAMP = re.compile(r"\s*Update:\s*\d{2}:\d{2}⏱\d{2}/\d{2}")


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
            ua_idx = None
            j = i + 1
            while j < len(lines):
                s = lines[j].strip()
                if s.startswith("#EXTVLCOPT:http-"):
                    ua = lines[j]
                    ua_idx = j
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
                raw_name = hdr[k + 1:].strip() if k >= 0 else ""
                # Ten kenh co the chua danh dau "Update: HH:MM⏱DD/MM" -> tach
                # ra de viec khop ten khong bi anh huong.
                clean_name = _RE_STAMP.sub("", raw_name).strip()
                entries.append({
                    "tvg": (m.group(1).strip() if m else ""),
                    "group": (g.group(1).strip() if g else ""),
                    "name": raw_name,
                    "clean_name": clean_name,
                    "url": url,
                    "ua": ua,
                    "ua_idx": ua_idx,
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


def build_stamp():
    """Chuoi danh dau thoi gian: HH:MM⏱DD/MM (dung cho ten kenh)."""
    import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
    except Exception:
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=7)
    return "%02d:%02d⏱%02d/%02d" % (now.hour, now.minute, now.day, now.month)


# Regex phan "Update: HH:MM⏱DD/MM" trong ten kenh (co the do nguon goc them).
# Chi kenh nay moi giu danh dau thoi gian trong ten (de check nhanh khi bat len).
STAMP_TVG_ID = "vtv1hd"


def stamp_update(lines, user="HaMaBu"):
    """Cap nhat dong #UPDATE + them moi danh dau thoi gian vao TEN KENH.

    Nham de nhan biet BB2 da chay dong bo tu dong hay chua: xem dong
    '#UPDATE HH:MM⏱DD/MM' va ten kenh dang co 'Update: HH:MM⏱DD/MM'.
    """
    new_line = build_update_line(user)
    stamp = build_stamp()
    # 1. Cap nhat dong #UPDATE
    act = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("#UPDATE"):
            lines[i] = new_line
            act = "da cap nhat"
            break
    if act is None:
        for i, ln in enumerate(lines):
            if ln.strip().startswith("#EXTM3U"):
                lines.insert(i + 1, new_line)
                act = "da chen"
                break
        if act is None:
            lines.insert(0, new_line)
            act = "da chen"
    # 2. Cap nhat phan "Update: ..." trong ten kenh.
    #    CHI danh dau tren kenh "VTV1" (tvg-id="vtv1hd") de khi bat kenh
    #    do la biet BB2 da chay dong bo chua.
    n_name = 0
    for i, ln in enumerate(lines):
        if not ln.startswith("#EXTINF"):
            continue
        k = ln.find(",")
        if k < 0:
            continue
        tvg = re.search(r'tvg-id="([^"]*)"', ln)
        tid = (tvg.group(1) if tvg else "").strip().lower()
        name = ln[k + 1:]
        if tid == STAMP_TVG_ID:
            # kenh can danh dau -> them hoac thay
            new_name = _RE_STAMP.sub("", name).rstrip()
            new_name = "%s Update: %s" % (new_name, stamp)
        else:
            # kenh khac -> XOA danh dau (neu co)
            new_name = _RE_STAMP.sub("", name).rstrip()
        if new_name != name:
            lines[i] = ln[:k + 1] + new_name
            n_name += 1
    return "%s | %d ten kenh" % (act, n_name)


# ------------------------------------------------------------------ matching
def norm(s):
    """Chuan hoa ten: bo dau, chi giu a-z0-9.

    'đ' -> 'd' truoc khi bo dau (neu bo dau truoc, 'Đà' se thanh 'a').
    """
    s = (s or "").lower()
    s = s.replace("đ", "d")
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", " ", s)
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
    by_alias = {}
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
        # khoa alias: ten TV360 -> kenh
        key = _norm_alias(ch.get("name"))
        if key:
            by_alias.setdefault(key, ch)
    return by_slug, by_name, by_norm, by_norm2, by_province, by_alias


def strip_province(name):
    """'QTV1 HD | TH Quang Ninh' -> ('quang ninh', '1')

    Voi ten dang 'THVL1 | TH Vinh Long': so thu tu nam O DAU ten
    (truoc dau '| TH ...') -> phai lay tu phan dau.
    """
    s = (name or "").strip()
    # phan dau (truoc '|') chua so thu tu: 'THVL1 HD'
    head = s.split("|")[0].strip() if "|" in s else ""
    # phan sau '| TH ' chua ten tinh
    tail = s.split("|")[-1].strip() if "|" in s else s
    tail = re.sub(r"^TH\s+", "", tail, flags=re.I).strip()
    # so thu tu: uu tien phan dau ('THVL1' -> 1), sau do phan cuoi
    num = ""
    # phan dau co thể ket thuc bang 'HD'/'SD': 'ATV1 HD' -> 1
    m = re.search(r"(\d+)\s*(?:HD|SD|UHD|FHD)?\s*$", head, flags=re.I)
    if not m:
        m = re.search(r"(\d+)\s*$", tail)
    if m:
        num = m.group(1)
    # ten tinh: bo so thu tu va HD/SD o cuoi
    base = re.sub(r"\s*\d+\s*(HD|SD)?\s*$", "", tail, flags=re.I).strip()
    return norm(base), num


def match_channel(entry, idx):
    by_slug, by_name, by_norm, by_norm2, by_province, by_alias = idx
    tvg = (entry["tvg"] or "").strip()
    # Dung ten DA BO danh dau "Update: ..." de khop kenh chinh xac.
    name = (entry.get("clean_name") or entry["name"] or "").strip()

    # 0. Ban do khuyen (ten playlist -> ten TV360)
    akey = _norm_alias(name)
    if akey:
        # Pass 1: khop CHINH XAC (uu tien khoa dai nhat -> 'on sports plus'
        # phai dung 'on sports plus', khong phai 'on sports').
        exact = []
        for k, target in ALIAS.items():
            nk = _norm_alias(k)
            if nk == akey:
                exact.append((len(nk), len(k), k, target))
        exact.sort(reverse=True)
        for _, _, k, target in exact:
            if target in by_alias:
                return by_alias[target], "alias"
            for kk, ch in by_alias.items():
                if kk.startswith(target) or target.startswith(kk):
                    return ch, "alias~"
        # Pass 2: khop prefix - chi lay khi khoa target DUNG PREFIX dau
        # (tranh 'on sports' -> 'vtvcab 3 on sports' khi can 'on sports news').
        for k, target in ALIAS.items():
            nk = _norm_alias(k)
            if nk.startswith(akey) or akey.startswith(nk):
                if target in by_alias:
                    return by_alias[target], "alias~"
                for kk, ch in by_alias.items():
                    if kk.startswith(target) or target.startswith(kk):
                        return ch, "alias~~"
    if tvg and tvg in by_slug:
        return by_slug[tvg], "tvg-id(id)"
    # 2. tvg-id la slug TV360
    if tvg and tvg.lower() in by_slug:
        return by_slug[tvg.lower()], "tvg-id(slug)"
    # 2b. tvg-id -> slug TV360 them dau gach: 'danang1' -> 'da-nang-1'
    if tvg:
        cand = tvg.lower().replace("_", "-")
        m = re.match(r"^([a-z]+)(\d+)$", cand)
        tries = [cand]
        if m:
            tries.append("%s-%s" % (m.group(1), m.group(2)))
            tries.append("%s-%d" % (m.group(1)[:-1], int(m.group(2))) if len(m.group(1)) > 1 else cand)
        for t2 in tries:
            if t2 in by_slug:
                return by_slug[t2], "tvg-id(slug~)"
        # 2c. tvg-id co 'hd'/'sd' nhung TV360 khong co: 'vtv5hd' -> 'vtv5'
        t3 = re.sub(r"(hd|sd|uhd|fhd)$", "", cand).strip("-")
        if t3 and t3 in by_slug:
            return by_slug[t3], "tvg-id(slug-bỏ-hd)"
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
                    # Phai khop DUNG so thu tu -> khong duoc tra kenh so 1
                    # cho moi kenh cung tinh.
                    for ch in cands:
                        if re.search(r"\b%s\b" % re.escape(num),
                                     str(ch.get("name") or "")):
                            return ch, "dia phuong+%s" % num
                    # Khong co kenh nao dung so -> thu tim kenh co cung ten
                    # tinh nhung khac so (vi du 'HTV5' -> 'HTV5').
                    return None, None
                # Khong co so -> chi lay kenh duy nhat cua tinh do
                if len(cands) == 1:
                    return cands[0], "dia phuong"
                # Tinh co nhieu kenh TV360 nhung khong co dung so (vd An Giang
                # chi co 1,2 -> ATV3 khong co) -> giu link goc.
                return None, None
        # 6. Viet tat: "THP HD" -> "THP"
        base = re.sub(r"^(TH|Q|HT)\s*", "", name, flags=re.I)
        b2 = norm2(base)
        if b2 and len(b2) >= 3:
            # Uu tien khop chinh xac
            if b2 in by_norm2:
                return by_norm2[b2], "viet tat"
            # Khong khop chinh xac -> chi lay khi khoa co chuoi chung du
            # nhat (tranh khop nham 'htv5' -> 'htv7').
            for k, ch in by_norm2.items():
                if len(k) >= 4 and (b2 in k or k in b2) and k[:3] == b2[:3]:
                    return ch, "viet tat~"
            # 6b. 'HTVC Plus HD' -> 'HTVC+ Channel B' (chi nhom HTVC)
            if b2.startswith("htvc"):
                for k, ch in by_norm2.items():
                    if k.startswith(b2) or b2.startswith(k):
                        return ch, "viet tat~~"
        # 7. kenh dia phuong dua vao slug
        if tvg:
            t = re.sub(r"\d+$", "", tvg.lower())
            num_tvg = re.search(r"(\d+)$", tvg.lower())
            if len(t) >= 4:
                for k, v in by_province.items():
                    if t and t in k:
                        if num_tvg:
                            n2 = num_tvg.group(1)
                            for ch in v:
                                if re.search(r"\b%s\b" % re.escape(n2),
                                             str(ch.get("name") or "")):
                                    return ch, "slug dia phuong+%s" % n2
                        else:
                            return v[0], "slug dia phuong"
    return None, None


# ------------------------------------------------------------------ main
def build(cfg, dry=False):
    c = client()
    anon = anon_client()
    catalog = c.catalog()
    if not catalog:
        print("!! khong lay duoc danh muc kenh tu TV360")
        return 2

    only_free = cfg.get("only_free_no_login", True)
    group_map = cfg.get("group_map", {})
    skip_groups = set(cfg.get("skip_groups", []) or [])

    # Nguon thay the cho nhom kenh TV360 khong co (vd SCTV)
    ext_idx = build_external_index(cfg)
    # Nguon fallback cho kenh TV360 khong tra link
    fb_idx = build_external_fallback(cfg)

    # Lay link cho tung kenh (chay nhieu luong cho nhanh)
    print("Dang lay link truc tiep tu TV360...")
    links = {}
    n_ok = n_fail = n_needlogin = 0
    todo = [ch for ch in catalog]
    workers = int(cfg.get("workers", 8))

    # PASS 1: client 'khach' (device rieng, khong gui access-token) chay
    # song song -> lay duoc kenh free ma khong bi ap goi thue bao.
    import threading
    _local = threading.local()

    def _anon_for_thread():
        a = getattr(_local, "a", None)
        if a is None:
            from tv360_client import Tv360Client, STATE_FILE
            tid = threading.current_thread().name.replace(" ", "_")
            a = Tv360Client("%s.anon.%s" % (STATE_FILE, tid))
            a.ping()
            _local.a = a
        return a

    def fetch_free(ch):
        cid = str(ch["id"])
        url, err = _anon_for_thread().get_stream(cid)
        return cid, url, err

    from concurrent.futures import ThreadPoolExecutor
    pending = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, (cid, url, err) in enumerate(ex.map(fetch_free, todo), 1):
            if url:
                links[cid] = url
                n_ok += 1
                if i % 40 == 0:
                    print("   ... %d/%d" % (i, len(todo)))
            else:
                pending.append((cid, err))
                n_needlogin += 1
    print("   pass 1 (khach): %d ok, %d can dang nhap" % (n_ok, len(pending)))

    # PASS 2: kenh con thieu -> thu voi client DA DANG NHAP (gói thue bao).
    # Chay TUAN TU: nhieu thread cung dung 1 phien dang nhap se bi server
    # loai session (link tra D-164).
    if pending:
        # KHONG auto-login (cho OTP) -> se treo. Chi thu refresh-token.
        c.ensure_token_safe(allow_login=False)
        n2 = 0
        # Moi thread mot client rieng (tranh tranh chap cookie jar khi
        # get_stream xoa/khoi phuc cookie access-token).
        from concurrent.futures import ThreadPoolExecutor
        from tv360_client import Tv360Client, STATE_FILE

        def fetch_auth(item):
            cid, err0 = item
            import threading as _t
            tid = _t.current_thread().name.replace(" ", "_")
            path = "%s.auth.%s" % (STATE_FILE, tid)
            try:
                if os.path.exists(STATE_FILE):
                    with open(STATE_FILE, "r", encoding="utf-8") as f:
                        data = f.read()
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(data)
            except Exception:
                pass
            cl = Tv360Client(path)
            if not cl.access_token:
                return cid, None, "khong co access-token"
            try:
                cl.ensure_token_safe(allow_login=False)
                u, e = cl.get_stream(cid, timeout=15)
            except Exception as ex:
                u, e = None, str(ex)
            return cid, u, e

        with ThreadPoolExecutor(max_workers=4) as ex:
            for i, (cid, url, err) in enumerate(ex.map(fetch_auth, pending), 1):
                if url:
                    links[cid] = url
                    n2 += 1
                else:
                    n_fail += 1
                    if n_fail <= 10:
                        print("   [FAIL] id=%-7s %s" % (cid, str(err)[:50]))
        print("   pass 2 (dang nhap): %d ok, %d fail" % (n2, n_fail))
    print("   xong: %d ok, %d fail" % (n_ok, n_fail))
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
            # Nhom kenh TV360 khong co (vd SCTV) -> lay tu nguon thay the.
            if e.get("group") in skip_groups:
                ext = ext_idx.get(e["group"], {})
                hit = ext.get((e.get("tvg") or "").lower())
                if hit:
                    new = hit["url"]
                    if new != e["url"]:
                        url_map[e["url_idx"]] = new
                        updated += 1
                    else:
                        kept += 1
                    # dong #EXTVLCOPT cua nguon goc
                    if cfg.get("keep_vlcopt", True) and hit.get("ua") \
                            and e.get("ua_idx") is not None:
                        lines[e["ua_idx"]] = hit["ua"]
                else:
                    kept += 1
                    nomap.append((e["name"], "nhom %s: khong tim thay o nguon thay the"
                                  % e["group"]))
                continue
            ch, how = match_channel(e, idx)
            if ch is None:
                # TV360 khong co kenh nay -> thu nguon fallback
                fb = fb_idx.get((e.get("tvg") or "").lower())
                if fb:
                    new = fb["url"]
                    if new != e["url"]:
                        url_map[e["url_idx"]] = new
                        updated += 1
                    else:
                        kept += 1
                    if cfg.get("keep_vlcopt", True) and fb.get("ua") \
                            and e.get("ua_idx") is not None:
                        lines[e["ua_idx"]] = fb["ua"]
                else:
                    kept += 1
                    nomap.append((e["name"], e["group"]))
                continue
            new = links.get(str(ch["id"]))
            if not new:
                # TV360 khong cho link kenh nay -> thu nguon fallback
                fb = fb_idx.get((e.get("tvg") or "").lower())
                if fb:
                    new = fb["url"]
                    if cfg.get("keep_vlcopt", True) and fb.get("ua") \
                            and e.get("ua_idx") is not None:
                        lines[e["ua_idx"]] = fb["ua"]
                else:
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
        if not updated and not cfg.get("stamp_update", True):
            continue
        if dry:
            print("  DRY-RUN: khong ghi")
            continue

        # ghi
        for idx_line, new in url_map.items():
            lines[idx_line] = new
        if cfg.get("stamp_update", True):
            # LUON cap nhat dong #UPDATE + danh dau thoi gian trong ten kenh,
            # ke ca khi khong co URL nao doi -> nhan biet BB2 da chay.
            act = stamp_update(lines, cfg.get("user", "HaMaBu"))
            print("  #UPDATE: %s" % act)

        tmp = path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(nl.join(lines) + (nl if trailing else ""))
        os.replace(tmp, path)
        print("  DA GHI %d dong URL vao %s" % (updated, pl_name))
        total_changed += updated

    print("\n" + "=" * 66)
    print("HOAN TAT: %d dong URL thay doi" % total_changed)
    print("=" * 66)
    return worst


def main():
    # Mac dinh: GHI THAT (cho cron tren box).
    # --dry: chi xem truoc, khong ghi.
    # --apply: ghi that (tuong duong mac dinh, cho workflow GitHub).
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
