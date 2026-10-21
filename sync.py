# -*- coding: utf-8 -*-
"""Dong bo URL kenh tu playlist nguon vao list.m3u8 cua ban.

Nguyen tac:
  - Chi thay DONG URL cua muc thuoc nhom theo doi va doi chieu thanh cong.
  - Moi dong khac giu nguyen byte-for-byte: ten, logo, nhom, thu tu, comment,
    dong #EXTVLCOPT (User-Agent cua ban khong bao gio bi thay).
  - Kenh khong co trong nguon -> giu nguyen URL cu.
  - Khong tim thay / URL moi khong xac minh duoc -> giu nguyen URL cu.
  - Ghi file tam, kiem tra, roi moi thay the. Loi -> giu nguyen file goc.

Khong can dang nhap. Khong co tai khoan, khong co mat khau.
"""
import datetime
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    from zoneinfo import ZoneInfo
except Exception:                  # Python < 3.9 fallback
    ZoneInfo = None

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Dam bao in duoc tieng Viet tren Windows console (cp1252)
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

from playlist import parse_playlist, write_playlist, index_by  # noqa: E402

CONFIG = os.path.join(HERE, "source.json")
REPORT = os.path.join(HERE, "last_report.json")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/154.0.0.0 Safari/537.36")

# Khong ghi token/expires vao log hay bao cao.
SECRET_RE = re.compile(
    r"((?:token|expires|cdntoken|access[-_]?token|refresh[-_]?token|jwt|sig|otp)"
    r"=)[^&\s\"]+", re.I)


def mask(u):
    return SECRET_RE.sub(lambda m: m.group(1) + "***", u or "")


def short(u, keep=54):
    if not u:
        return ""
    p = urllib.parse.urlparse(u)
    host = p.netloc or (p.scheme + "://")
    last = p.path.rsplit("/", 1)[-1]
    tail = urllib.parse.urlparse(mask(u)).query
    s = "%s.../%s" % (host, last[:26])
    if tail:
        s += "?" + tail[:40]
    return s[:keep + 30]


# ------------------------------------------------------------------ dong UPDATE
UPDATE_RE = re.compile(r"^\s*#UPDATE\b.*$", re.M)


def build_update_text(now=None, user="HaMaBu"):
    """Sinh dong #UPDATE moi theo mau 'HH:MM⏱DD/MM | User: <user>' (gio Viet Nam)."""
    if now is None:
        if ZoneInfo is not None:
            try:
                now = datetime.datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
            except Exception:
                pass
        if now is None:                       # tzdata khong co: +07 truc tiep
            now = (datetime.datetime.utcnow() + datetime.timedelta(hours=7))
    return ("#UPDATE %02d:%02d⏱%02d/%02d | User: %s"
            % (now.hour, now.minute, now.day, now.month, user))


def stamp_update(playlist_path, user="HaMaBu"):
    """Thay the dong #UPDATE trong playlist neu co, hoac chen vao sau dong #EXTM3U."""
    with io.open(playlist_path, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    new_line = build_update_text(user=user)

    if UPDATE_RE.search(text):
        new_text, count = UPDATE_RE.subn(new_line, text, count=1)
        action = "da cap nhat"
    else:
        # chen ngay sau dong #EXTM3U ... dau tien (neu co), hoac tren cung
        lines = text.split("\n")
        insert_at = 0
        for i, ln in enumerate(lines):
            if ln.strip().startswith("#EXTM3U"):
                insert_at = i + 1
                break
        lines.insert(insert_at, new_line)
        new_text = "\n".join(lines)
        count = 1
        action = "da chen"

    if new_text != text:
        tmp = playlist_path + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)
        os.replace(tmp, playlist_path)
    return action, count


# ------------------------------------------------------------------ tai du lieu
def fetch_text(url, timeout=30, ua=USER_AGENT):
    req = urllib.request.Request(url, headers={
        "User-Agent": ua,
        "Accept": "*/*",
        "Accept-Language": "vi,en;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw.decode("utf-8", errors="replace")


def parse_text_playlist(text):
    """Parse playlist tu chuoi (khong doc file). Tra ve danh sach muc.

    Chuong hoa xuong dong truoc: nhieu nguon tron nhung chuoi CRLF lam
    con, tach theo "\\r\\n" se lam cac dong bi gop lai.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
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
                })
                i = url_idx + 1
                continue
        i += 1
    return entries


# ------------------------------------------------------------------ kiem tra
def looks_like_stream(url):
    if not isinstance(url, str) or not url:
        return False, "rong"
    low = url.lower()
    if low.endswith(".mpd"):
        return False, "DASH khong ho tro"
    if not (low.startswith("http://") or low.startswith("https://")):
        if low.startswith("#extm3u"):
            return True, "playlist noi bo"
        return False, "khong phai URL http"
    if ".m3u8" in low or "playlist" in low or "manifest" in low or "tv360.php" in low:
        return True, "ok"
    return False, "khong phai duong dan HLS"


def _fetch_bytes(url, ua, timeout=25, read=400000):
    req = urllib.request.Request(url, headers={"User-Agent": ua})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(read).decode("utf-8", errors="replace")


def _join(base, ref):
    return urllib.parse.urljoin(base, ref)


def _probe(seg_url, ua, timeout=20):
    try:
        req = urllib.request.Request(seg_url, headers={"User-Agent": ua})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            n = len(r.read(150000))
    except Exception as e:
        return False, type(e).__name__
    if n < 1024:
        return False, "qua nho (%d byte)" % n
    return True, "%d byte" % n


def verify_stream(url, ua_line, timeout=25):
    """HTTP 200 khong du. Phai xuong den segment that.

    Tra (ok, mo_ta).
    """
    ua = USER_AGENT
    if ua_line and "http-user-agent=" in ua_line:
        ua = ua_line.split("http-user-agent=", 1)[1].strip()

    try:
        body = _fetch_bytes(url, ua, timeout=timeout)
    except urllib.error.HTTPError as e:
        return False, "HTTP %s" % e.code
    except Exception as e:
        return False, "khong tai duoc (%s)" % type(e).__name__

    if "EXTM3U" not in body:
        return False, "khong phai playlist HLS"
    if "SAMPLE-AES" in body:
        return False, "co DRM SAMPLE-AES"

    bl = body.splitlines()
    variants = [i for i, l in enumerate(bl) if l.startswith("#EXT-X-STREAM-INF")]

    if variants:
        best, bw, bh = None, -1, 0
        for i in variants:
            m = re.search(r"RESOLUTION=(\d+)x(\d+)", bl[i])
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                if w * h > bw and i + 1 < len(bl):
                    bw, bh, best = w * h, h, bl[i + 1].strip()
        if not best:
            return False, "khong tim duoc bien thien"
        vurl = _join(url, best)
        try:
            vb = _fetch_bytes(vurl, ua, timeout=timeout)
        except urllib.error.HTTPError as e:
            return False, "bien thien HTTP %s" % e.code
        except Exception as e:
            return False, "bien thien khong tai duoc (%s)" % type(e).__name__
        segs = [l.strip() for l in vb.splitlines()
                if l.strip() and not l.startswith("#")
                and (".ts" in l or ".m4s" in l or ".aac" in l)]
        if not segs:
            return False, "bien thien khong co segment"
        ok, why = _probe(_join(vurl, segs[-1]), ua)
        return ((True, "OK %dp, segment %s" % (bh, why)) if ok
                else (False, "segment that khong tai duoc"))

    segs = [l.strip() for l in bl if l.strip() and not l.startswith("#")
            and (".ts" in l or ".m4s" in l)]
    if segs:
        ok, why = _probe(_join(url, segs[-1]), ua)
        return (True, "OK segment " + why) if ok else (False, "segment that khong tai duoc")
    return False, "playlist khong co segment"


# ------------------------------------------------------------------ doi chieu
def build_source_index(src_entries):
    """Tra (by_tvg, by_name) cua playlist nguon."""
    by_tvg, by_name = {}, {}
    for e in src_entries:
        if e["tvg"]:
            by_tvg.setdefault(e["tvg"].strip().lower(), []).append(e)
        if e["name"]:
            by_name.setdefault(e["name"].strip().lower(), []).append(e)
    return by_tvg, by_name


def resolve(entry, groups, s_tvg, s_name):
    """Tra (muc_nguon, cach_doi_chieu) hoac (None, ly_do)."""
    if entry.group not in groups:
        return None, "nhom '%s' khong theo doi" % entry.group

    tvg = (entry.tvg_id or "").strip()
    name = (entry.name or "").strip()

    if tvg:
        cand = s_tvg.get(tvg.lower(), [])
        if len(cand) == 1:
            return cand[0], "tvg-id"
        if len(cand) > 1:
            ids = {_src_id(c["url"]) for c in cand}
            if len(ids) == 1 and entry.url:
                return cand[0], "tvg-id lap (%d ban)" % len(cand)

    if name:
        ncand = s_name.get(name.strip().lower(), [])
        if len(ncand) == 1:
            return ncand[0], "ten"

    return None, "khong doi chieu duoc (tvg-id/ten khong khop nguon)"


_SRC_ID_RE = re.compile(r"[?&]id=(\d+)")


def _src_id(url):
    m = _SRC_ID_RE.search(url or "")
    return m.group(1) if m else None


# ------------------------------------------------------------------ chay
def sync_one(cfg, pl_name, apply_mode, only, verify, timeout, cached_src=None):
    """Dong bo MOT file playlist dich. Tra ve (status_code, so_dong_doi).

    Neu cached_src duoc truyen vao, KHONG tai nguon lai (dung de chay nhieu file).
    """
    src_url = (cfg.get("source_url") or "").strip()
    groups = set(cfg.get("groups") or [])

    print("\n" + "#" * 68)
    print("# PLAYLIST DICH: %s" % pl_name)
    print("#" * 68)

    if not groups:
        print("DUNG: danh sach groups trong source.json rong.")
        return 2, 0

    playlist = os.path.join(HERE, pl_name)
    if not os.path.exists(playlist):
        print("DUNG: khong tim thay %s" % playlist)
        return 2, 0

    # ---- tai playlist nguon (hoac dung cache)
    if cached_src is not None:
        src_entries = cached_src
        print("\n[1] Dung playlist nguon (cached)")
        print("    %d muc" % len(src_entries))
    else:
        print("\n[1] Tai playlist nguon")
        print("    %s" % short(src_url, 90))
        try:
            text = fetch_text(src_url, timeout=timeout + 15)
        except urllib.error.HTTPError as e:
            print("    -> HTTP %s. KHONG doi URL. Giu nguyen playlist." % e.code)
            save_report(pl_name, src_url, [], [], [("nguon", "HTTP %s" % e.code)], 2)
            return 2, 0
        except Exception as e:
            print("    -> loi (%s). KHONG doi URL. Giu nguyen playlist." % type(e).__name__)
            save_report(pl_name, src_url, [], [], [("nguon", type(e).__name__)], 2)
            return 2, 0

        src_entries = parse_text_playlist(text)
        if not src_entries:
            print("    -> khong doc duoc muc nao tu nguon. KHONG doi URL.")
            save_report(pl_name, src_url, [], [], [("nguon", "khong co muc")], 2)
            return 2, 0
        print("    -> %d muc" % len(src_entries))

    # ---- doc playlist dich
    print("\n[2] Doc playlist dich")
    lines, entries, nl, trailing = parse_playlist(playlist)
    print("    %s: %d muc, %d dong" % (pl_name, len(entries), len(lines)))

    s_tvg, s_name = build_source_index(src_entries)
    targets = [e for e in entries if e.group in groups]
    print("\n[3] Kenh thuoc %d nhom theo doi: %d / %d muc"
          % (len(groups), len(targets), len(entries)))
    print("    nhom: %s" % ", ".join(sorted(groups)))

    if only:
        targets = [e for e in targets
                   if only in (e.name or "").lower() or only in (e.tvg_id or "").lower()]
        print("    loc '--only %s': %d muc" % (only, len(targets)))
    if not targets:
        print("\nKhong co muc nao de xu ly. KHONG ghi file.")
        save_report(pl_name, src_url, [], [], [], 0)
        return 0, 0

    # ---- so sanh + thay
    print("\n[4] So sanh URL")
    url_map, updated, kept, errors = {}, [], [], []

    for e in targets:
        src, how = resolve(e, groups, s_tvg, s_name)
        if src is None:
            kept.append((e.name, how))
            print("  [GIU]    %-30s %s" % (e.name[:30], how))
            continue

        new = src["url"]
        if new == e.url:
            kept.append((e.name, "URL giong nhau"))
            print("  [GIONG]  %-30s" % e.name[:30])
            continue

        ok, why = looks_like_stream(new)
        if not ok:
            kept.append((e.name, "URL nguon sai: " + why))
            print("  [GIU]    %-30s URL nguon sai (%s)" % (e.name[:30], why))
            continue

        if verify:
            ok2, why2 = verify_stream(new, e.ua_line, timeout=timeout)
            if not ok2:
                kept.append((e.name, "khong xac minh: " + why2))
                print("  [GIU]    %-30s %s" % (e.name[:30], why2))
                continue

        url_map[e.url_idx] = new
        updated.append((e.name, e.url, new, how))
        print("  [MOI]    %-30s %s  (%s)" % (e.name[:30], short(new), how))

    # gom theo URL giong nhau: 1 muc nguon co the phuc vu nhieu ban
    print("\n" + "-" * 68)
    print("Tong ket: %d se doi | %d giu nguyen | %d loi"
          % (len(updated), len(kept), len(errors)))
    print("-" * 68)

    if not updated:
        print("\nKhong co thay doi nao. KHONG ghi file.")
        save_report(pl_name, src_url, updated, kept, errors, 0)
        return 0, 0

    if not apply_mode:
        print("\nDRY-RUN: se ghi %d dong URL vao %s" % (len(url_map), pl_name))
        print("Chay 'python sync.py --apply' de ghi that.")
        save_report(pl_name, src_url, updated, kept, errors, 0)
        return 0, len(url_map)

    # ---- ghi file tam -> kiem tra -> moi the
    tmp = playlist + ".tmp"
    n = write_playlist(tmp, lines, url_map, newline=nl, trailing=trailing)
    t_lines, t_entries, _, _ = parse_playlist(tmp)

    problems = []
    if len(t_lines) != len(lines):
        problems.append("so dong %d -> %d" % (len(lines), len(t_lines)))
    if len(t_entries) != len(entries):
        problems.append("so muc %d -> %d" % (len(entries), len(t_entries)))
    if n != len(url_map):
        problems.append("chi %d/%d dong duoc doi" % (n, len(url_map)))
    if not t_entries:
        problems.append("ket qua rong")
    # chi xac nhan nhung dong duoc phep doi co that su doi
    for idx, new in url_map.items():
        if t_lines[idx] != new:
            problems.append("dong %d khong ghi dung" % idx)
            break
    # xac nhan phan con lai giu nguyen byte-for-byte
    allowed = set(url_map.keys())
    for i in range(min(len(lines), len(t_lines))):
        if i not in allowed and lines[i] != t_lines[i]:
            problems.append("dong %d bi doi ngoai danh sach" % i)
            break

    if problems:
        os.remove(tmp)
        print("\nKIEM TRA that bai -> KHONG ghi:")
        for p in problems:
            print("   - " + p)
        print("Giu nguyen file goc.")
        save_report(pl_name, src_url, updated, kept,
                    errors + [("kiem tra file", "; ".join(problems))], 1)
        return 1, 0

    os.replace(tmp, playlist)

    # Cap nhat dong #UPDATE (gio Viet Nam)
    action, cnt = stamp_update(playlist)
    print("\nDa ghi %d dong vao %s" % (n, pl_name))
    print("Dong #UPDATE: %s (%d lan)" % (action, cnt))
    save_report(pl_name, src_url, updated, kept, errors, 0)
    return 0, n


REPORTS_DIR = os.path.join(HERE, "reports")


def save_report(pl_name, src_url, updated, kept, errors, status):
    data = {
        "playlist": pl_name,
        "status": status,
        "source": short(src_url, 90),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "updated": [{"channel": c, "old": short(o), "new": short(x), "matched_by": h}
                    for c, o, x, h in updated],
        "kept": [{"channel": c, "reason": r} for c, r in kept],
        "errors": [{"channel": c, "error": e} for c, e in errors],
    }
    # bao cao rieng cho tung playlist + bao cao tong
    if not os.path.isdir(REPORTS_DIR):
        os.makedirs(REPORTS_DIR)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", pl_name)
    with io.open(os.path.join(REPORTS_DIR, safe + ".json"),
                 "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("\nBao cao: reports/%s.json" % safe)


def main():
    apply_mode = "--apply" in sys.argv
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1].lower()
    no_verify = "--no-verify" in sys.argv

    print("=" * 68)
    print("  DONG BO URL TU PLAYLIST NGUON   |   che do: %s"
          % ("GHI THAT" if apply_mode else "DRY-RUN (khong ghi)"))
    print("=" * 68)

    try:
        with io.open(CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        print("DUNG: khong doc duoc %s (%s)." % (CONFIG, e))
        return 2

    src_url = (cfg.get("source_url") or "").strip()
    if not src_url:
        print("DUNG: thieu source_url trong source.json.")
        return 2

    verify = cfg.get("verify", True) and not no_verify
    timeout = int(cfg.get("timeout") or 25)

    # danh sach file dich: hoac "playlist_file", hoac "playlist_files"
    names = cfg.get("playlist_files")
    if not names:
        names = [cfg.get("playlist_file") or "list.m3u8"]
    names = [n.strip() for n in names if n and n.strip()]
    seen, targets_files = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            targets_files.append(n)

    print("\nSe dong bo %d file: %s"
          % (len(targets_files), ", ".join(targets_files)))

    # Tai playlist nguon DUNG 1 LAN cho moi file (gia cac khac giong nhau)
    print("\n" + "*" * 68)
    print("* Tai playlist nguon (dung chung cho cac file dich)")
    print("*  %s" % short(src_url, 90))
    try:
        text = fetch_text(src_url, timeout=timeout + 15)
    except Exception as e:
        print("*  -> loi (%s). Huy luon." % type(e).__name__)
        return 2
    cached_src = parse_text_playlist(text)
    if not cached_src:
        print("*  -> khong doc duoc muc nao. Huy luon.")
        return 2
    print("*  -> %d muc (cached)" % len(cached_src))
    print("*" * 68)

    worst = 0
    total_changed = 0
    for pl_name in targets_files:
        st, n = sync_one(cfg, pl_name, apply_mode, only, verify, timeout,
                         cached_src=cached_src)
        if st > worst:
            worst = st
        total_changed += n

    print("\n" + "=" * 68)
    print("HOAN TAT: %d file da xu ly | %d dong URL thay doi"
          % (len(targets_files), total_changed))
    if apply_mode:
        print("Da ghi file. Workflow se commit.")
    else:
        print("DRY-RUN: chua ghi gi. Chay voi --apply de ghi that.")
    print("=" * 68)
    return worst


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nDa dung.")
        sys.exit(130)