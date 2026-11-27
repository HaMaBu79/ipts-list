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
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from urllib.parse import urljoin

QWEB = "/opt/qweb"
FILES = [os.path.join(QWEB, "list.m3u8"), os.path.join(QWEB, "Listphu.m3u8")]
CACHE = os.path.join(QWEB, "verify_event_cache.json")
sys.path.insert(0, QWEB)
try:
    import channel_state as CS
except Exception:
    CS = None
try:
    import tv360_channels as TVC_HLS
except Exception:
    TVC_HLS = None
CACHE_TTL = 420

# --- PATCH FAST: cau hinh song song (env co the override) ---
MAX_WORKERS = max(1, int(os.environ.get("VERIFY_WORKERS", "8")))
# Deadline rieng cho MOI cap (url,key): 1 kenh treo khong chan tat ca.
PAIR_DEADLINE = max(20, int(os.environ.get("VERIFY_PAIR_DEADLINE", "75")))
# Deadline cho 1 kenh trong auto_recover (resolve lau -> gioi han).
RECOVER_DEADLINE = max(20, int(os.environ.get("VERIFY_RECOVER_DEADLINE", "50")))
# Backoff retry: 1/3/5 giay thay vi 2/4/6.
RETRY_SLEEP = [1, 3, 5]
# Lock cho resolve_v2 (module nay dung global DEADLINE -> race neu
# nhieu thread doi DEADLINE cung luc).
_RESOLVE_LOCK = Lock()
# URL man den - kenh chet gan fallback, khong chan phan con lai.
FALLBACK_URL = "http://0.0.0.0/error.ts"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
# Kenh bi BO QUA (khong cap nhat) - doc tu config chung tv360_channels.py
try:
    import tv360_channels as TVC
    SKIP_IDS = set(TVC.skips())
except Exception:
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
        # PATCH FAST: 1 LENH ffmpeg thay vi 2 (decode->png, roi png->1x1).
        # scale=1:1 + rawvideo rgb24 -> stdout = 3 byte (1 pixel RGB).
        # Ket qua y het ban cu (cung 3 byte hex), nhanh gap ~2 lan.
        r2 = subprocess.run(["ffmpeg", "-v", "error", "-decryption_key", keyhex,
                             "-i", mp4, "-frames:v", "1",
                             "-vf", "scale=1:1", "-f", "rawvideo",
                             "-pix_fmt", "rgb24", "-"],
                            capture_output=True, timeout=40)
        if len(r2.stdout) < 3:
            return False, "NOFRAME"
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


def test_pair(url, key, deadline=None):
    """Tra (ok, info). Tuần tự, retry khi that bai tam thoi.

    PATCH FAST:
      - `deadline` (epoch): dung truoc moi attempt -> 1 kenh treo
        khong lam chan cac kenh khac.
      - backoff ngan (1/3/5 giay).
    """
    last = ""
    for attempt in range(RETRY):
        if deadline is not None and time.time() > deadline:
            last = (last + " | " if last else "") + "het deadline"
            break
        if ".mpd" in url.lower():
            if not key or ":" not in key:
                return False, "thieu ClearKey"
            kid = key.split(":")[0].lower()
            c, body, eff = get(url)
            if c != 200:
                last = "MPD HTTP%d" % c
                time.sleep(RETRY_SLEEP[min(attempt, len(RETRY_SLEEP) - 1)])
                continue
            body = body.decode("utf-8", "replace")
            sk = kids_of(body)
            if kid not in sk:
                # token het han -> thu lai (URL co the da doi)
                last = "KID KHONG KHOP (%s vs %s)" % (kid[:8], ",".join(sorted(sk))[:20])
                time.sleep(RETRY_SLEEP[min(attempt, len(RETRY_SLEEP) - 1)])
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
            time.sleep(RETRY_SLEEP[min(attempt, len(RETRY_SLEEP) - 1)])
        else:
            c, b, eff = get(url)
            if c != 200:
                last = "master HTTP%d" % c
                time.sleep(RETRY_SLEEP[min(attempt, len(RETRY_SLEEP) - 1)])
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
                time.sleep(RETRY_SLEEP[min(attempt, len(RETRY_SLEEP) - 1)])
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
                time.sleep(RETRY_SLEEP[min(attempt, len(RETRY_SLEEP) - 1)])
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

    print("== VERIFY %d cap (url,key) duy nhat | SONG SONG %d luong | "
          "deadline %ds/cap ==" % (len(pairs), MAX_WORKERS, PAIR_DEADLINE))
    result = {}
    t0 = time.time()
    items = sorted(pairs.items(), key=lambda x: len(x[0][1] or ""))

    # PATCH FAST: chay SONG SONG thay vi tuần tự.
    # Mỗi cap co deadline rieng -> kenh treo khong keo dai toan bo.
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(items)))) as ex:
        futs = {}
        for (url, key), slug in items:
            futs[ex.submit(test_pair, url, key, t0 + PAIR_DEADLINE)] = (url, key, slug)
        for fu in as_completed(futs):
            url, key, slug = futs[fu]
            try:
                ok, info = fu.result()
            except Exception as exc:
                ok, info = False, "ERR " + str(exc)[:50]
            result[(url, key)] = (ok, info)
            print("   %-14s %-5s %s" % (slug, "OK" if ok else "LOI", info[:60]))

    n_ok = sum(1 for v in result.values() if v[0])
    print("   -> %d/%d cap OK (%.0fs)"
          % (n_ok, len(items), time.time() - t0))

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
                # Luu URL da xac minh -> dung lam du phong khi kenh fail
                # sau do (khong phai doi auto_recover moi co).
                if CS and url and url != FALLBACK_URL:
                    try:
                        CS.mark_ok(slug, url, info)
                    except Exception:
                        pass
            else:
                bad.append(slug)
                lines.append("   %-14s LOI - %s" % (slug, info))
    return lines, sorted(set(bad))


def current_pairs():
    """Tap doi (url,key) hien tai trong playlist - de so sanh voi cache."""
    pairs = set()
    for p in FILES:
        try:
            blocks = events_of(p)
        except Exception:
            continue
        for slug, (url, key) in blocks.items():
            if slug in SKIP_IDS or not url:
                continue
            pairs.add((url, key))
    return pairs


def load_cache():
    try:
        c = json.load(open(CACHE, encoding="utf-8"))
    except Exception:
        return None
    if time.time() - c.get("ts", 0) > CACHE_TTL:
        return None
    # Cache chi dung khi TAP DOI (url,key) khong doi so voi luc cache tao.
    # Neu URL da duoc lam moi (vd lay cdntoken moi) -> phai test lai.
    old = c.get("pairs")
    if old is not None:
        try:
            now = current_pairs()
            if now != set(tuple(x) for x in old):
                print("[CACHE] tap doi (url,key) da doi -> bo qua cache, test lai")
                return None
        except Exception:
            return None
    return c


def save_cache(lines, bad):
    tmp = CACHE + ".tmp"
    try:
        pairs = sorted(list(x) for x in current_pairs())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"ts": time.time(), "bad": bad, "lines": lines,
                       "pairs": pairs}, f)
        os.replace(tmp, CACHE)
    except Exception:
        pass



def key_of_slug(slug):
    """Lay ClearKey (KID:KEY) cua kenh tu playlist."""
    for p in FILES:
        try:
            blocks = events_of(p)
        except Exception:
            continue
        if slug in blocks:
            return blocks[slug][1]
    return None


def write_url(slug, new_url):
    """Ghi URL moi cho 1 muc trong playlist (list/Listphu/template)."""
    files = [os.path.join(QWEB, "list.m3u8"),
             os.path.join(QWEB, "Listphu.m3u8"),
             os.path.join(QWEB, "template", "list.m3u8"),
             os.path.join(QWEB, "template", "Listphu.m3u8")]
    n_ok = 0
    for path in files:
        if not os.path.exists(path):
            continue
        try:
            txt = open(path, "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        ls = txt.split("\n")
        changed = False
        i = 0
        while i < len(ls):
            if ls[i].startswith("#EXTINF"):
                j = i + 1
                while j < len(ls) and not ls[j].startswith("#EXTINF"):
                    j += 1
                m = re.search(r'tvg-id="([^"]*)"', ls[i])
                if m and m.group(1).strip().lower() == slug:
                    for k in range(i, j):
                        if ls[k].strip().startswith("http"):
                            ls[k] = new_url
                            changed = True
                            break
                i = j
            else:
                i += 1
        if changed:
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(ls))
                n_ok += 1
            except Exception:
                pass
    return n_ok


def write_entry(slug, new_url, new_key=None):
    """Ghi URL moi (va ClearKey moi neu co) cho 1 muc trong playlist.

    Ghi vao: list.m3u8, Listphu.m3u8, template/list.m3u8, template/Listphu.m3u8
    """
    files = [os.path.join(QWEB, "list.m3u8"),
             os.path.join(QWEB, "Listphu.m3u8"),
             os.path.join(QWEB, "template", "list.m3u8"),
             os.path.join(QWEB, "template", "Listphu.m3u8")]
    n_ok = 0
    for path in files:
        if not os.path.exists(path):
            continue
        try:
            txt = open(path, "rb").read().decode("utf-8", "replace")
        except Exception:
            continue
        ls = txt.split("\n")
        changed = False
        i = 0
        while i < len(ls):
            if ls[i].startswith("#EXTINF"):
                j = i + 1
                while j < len(ls) and not ls[j].startswith("#EXTINF"):
                    j += 1
                m = re.search(r'tvg-id="([^"]*)"', ls[i])
                if m and m.group(1).strip().lower() == slug:
                    for k in range(i, j):
                        s = ls[k].strip()
                        if s.startswith("http"):
                            ls[k] = new_url
                            changed = True
                        elif (new_key and
                              s.startswith("#KODIPROP:inputstream.adaptive.license_key=")):
                            ls[k] = ("#KODIPROP:inputstream.adaptive.license_key="
                                     + new_key)
                            changed = True
                i = j
            else:
                i += 1
        if changed:
            try:
                with open(path, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(ls))
                n_ok += 1
            except Exception:
                pass
    return n_ok


def is_source_down(bad_slugs):
    """Tra True neu nghi NGUON TV360 down (>=50% kenh fail).

    Khi do: KHONG gan fallback cho tung kenh (tranh 17 muc man den).
    Giu nguyen playlist cu - player bao loi rieng tung kenh.
    """
    if not bad_slugs:
        return False
    try:
        import subprocess as _sp
        import sys as _sy
        _sp.run([_sy.executable, os.path.join(QWEB, "circuit_breaker.py"),
                 "--bad=%s" % ",".join(bad_slugs)],
                capture_output=True, timeout=60,
                cwd=QWEB)
        # Doc ket qua tu state file (circuit_breaker ghi ra).
        p = os.path.join(QWEB, "breaker_state.json")
        d = json.load(open(p, encoding="utf-8"))
        return bool(d.get("down"))
    except Exception as exc:
        print("   circuit_breaker loi: %s" % str(exc)[:60])
        return False


def auto_recover(bad_slugs):
    """Soft-failure: thu phuc hoi tung kenh. KHONG bao gio chan danh sach.

    Voi moi kenh fail, theo thu tu:
      1. Kenh HLS  : stable_hls.resolve_any() -> URL moi + xac minh.
         Kenh DASH : resolve_v2.resolve() -> URL moi + CLEARKEY moi + xac minh.
      2. Neu that bai -> thu last_known_good (URL da xac minh gan nhat).
      3. Neu van that bai -> gan FALLBACK (man den) cho rieng kenh do.

    Tra (fixed, still_bad).
    """
    if not bad_slugs:
        return [], [], False
    print("[AUTO-RECOVER] %d kenh loi -> thu phuc hoi tung kenh" % len(bad_slugs))

    # Tai cac module resolve (lazy - chi khi that su can).
    _rc = None
    _src = None
    _pool = None

    def resolve_dash(slug):
        """Resolve lai kenh DRM DASH -> (url, key) moi."""
        nonlocal _rc, _src, _pool
        # PATCH FAST: resolve_v2 dung global DEADLINE -> phai LOCK,
        # neu khong 2 thread se gan DEADLINE chung va race.
        with _RESOLVE_LOCK:
            import resolve_v2
            _rc = resolve_v2
            if _src is None:
                _src = _rc.get_source()
            if _pool is None:
                _pool = _rc.pool_keys()
            _rc.DEADLINE = time.time() + RECOVER_DEADLINE
            r = _rc.resolve(slug, _src, _pool)
        if r and r.get("url") and r.get("key"):
            return r["url"], r["key"]
        return None, None

    fixed = []
    still_bad = []

    # PATCH FAST: tach 2 pha de chay SONG SONG an toan.
    #   Pha A (song song) - resolve + xac minh lau, KHONG ghi file.
    #   Pha B (tuan tu)   - ghi .m3u8, tranh 2 thread ghi cung 1 file.
    def resolve_one(slug):
        """Tra (slug, new_url, new_key). Chi resolve, khong ghi file."""
        try:
            if TVC_HLS and TVC_HLS.is_hls(slug):
                import stable_hls
                r = stable_hls.resolve_any(slug, deadline=time.time() + RECOVER_DEADLINE)
                if r and r.get("url"):
                    return slug, r["url"], None
            else:
                u, k = resolve_dash(slug)
                if u:
                    return slug, u, k
        except Exception as exc:
            print("   %s recover LOI: %s" % (slug, str(exc)[:70]))
        return slug, None, None

    print("   [AUTO-RECOVER] resolve %d kenh song song..." % len(bad_slugs))
    t_rec = time.time()
    cand = {}
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(bad_slugs)))) as ex:
        futs = [ex.submit(resolve_one, s) for s in bad_slugs]
        for fu in as_completed(futs):
            slug, new_url, new_key = fu.result()
            if not new_url:
                continue
            ok, why = test_pair(new_url, new_key or key_of_slug(slug),
                                time.time() + PAIR_DEADLINE)
            if ok:
                cand[slug] = (new_url, new_key, why)
            else:
                print("   %s URL moi xac minh FAIL (%s)" % (slug, why))
    print("   [AUTO-RECOVER] resolve+xac minh xong (%.0fs), %d kenh co URL moi"
          % (time.time() - t_rec, len(cand)))

    for slug in bad_slugs:
        got = False

        # --- 1. URL MOI (da xac minh o pha A) ---
        if slug in cand:
            new_url, new_key, why = cand[slug]
            if write_entry(slug, new_url, new_key):
                if CS:
                    CS.mark_ok(slug, new_url, why)
                fixed.append(slug)
                print("   %s -> URL MOI da xac minh (%s)" % (slug, why))
                got = True

        # --- 2. last_known_good ---
        if not got and CS:
            lg = CS.last_good(slug)
            if lg and lg != FALLBACK_URL:
                ok, why = test_pair(lg, key_of_slug(slug),
                                    time.time() + PAIR_DEADLINE)
                if ok:
                    if write_entry(slug, lg, None):
                        CS.mark_ok(slug, lg, why)
                        fixed.append(slug)
                        print("   %s -> dung lai URL da xac minh (%s)" % (slug, why))
                    got = True

        # --- 3. FALLBACK: chan rieng kenh, KHONG chan danh sach ---
        if not got:
            still_bad.append(slug)
            print("   %s -> CHET" % slug)

    # --- 3b. CIRCUIT BREAKER: neu >=50% kenh fail -> nguon down ---
    # Giu nguyen playlist cu (khong gan fallback) de tranh "mua" man den.
    if still_bad and is_source_down(still_bad):
        print("!! NGUON TV360 DOWN (%d kenh chet) - GIU NGUYEN playlist cu,"
              " KHONG gan fallback" % len(still_bad))
        return fixed, still_bad, True

    # --- 4. Gan fallback cho tung kenh chet ---
    for slug in still_bad:
        if CS:
            CS.mark_offline(slug, "khong lay duoc URL chay")
            n = CS.apply_fallback(slug)
            if n:
                print("   %s -> gan FALLBACK (man den), %d file" % (slug, n))
        print("   %s -> da gan fallback (KHONG chan push)" % slug)
    return fixed, still_bad, False


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
            # SOFT-FAILURE: thu phuc hoi tung kenh. KHONG chan danh sach.
            fixed, still_bad, down = [], [], False
            if not force:
                fixed, still_bad, down = auto_recover(cached["bad"])
                if fixed:
                    print("[AUTO-RECOVER] da phuc hoi: %s -> verify lai"
                          % ", ".join(fixed))
                    try:
                        os.unlink(CACHE)
                    except Exception:
                        pass
                    return main()
            if still_bad:
                if down:
                    print("KET QUA: NGUON TV360 DOWN - %d kenh khong lay duoc URL."
                          % len(still_bad))
                    print("         >>> GIU NGUYEN playlist cu (khong man den het)")
                    print("         >>> PUSH VAN DIEN RA (188 kenh khac khong bi chan)")
                else:
                    print("KET QUA: %d kenh CHET -> da gan fallback cho rieng kenh do."
                          % len(still_bad))
                    print("         %s" % ", ".join(still_bad))
                    print("         >>> PUSH VAN DIEN RA (188 kenh khac khong bi chan)")
                return 2
            print("KET QUA: %d kenh loi -> da phuc hoi xong" % len(cached["bad"]))
            return 0
        print("KET QUA: TAT CA KENH 'Su kien TV360' OK -> DUOC PUSH")
        return 0

    t0 = time.time()
    lines, bad = run_all()
    for l in lines:
        print(l)
    print("\n(verify took %.0fs)" % (time.time() - t0))
    print("[SUMMARY] %d kenh | %d OK | %d LOI | %.0fs | %d luong"
          % (len(bad) + sum(1 for l in lines if " OK  " in l),
             sum(1 for l in lines if " OK  " in l),
             len(bad),
             time.time() - t0, MAX_WORKERS))
    save_cache(lines, bad)
    print()
    if bad:
        # SOFT-FAILURE: khong chan danh sach.
        fixed, still_bad, down = [], [], False
        if not force:
            fixed, still_bad, down = auto_recover(bad)
            if fixed:
                print("[AUTO-RECOVER] da phuc hoi: %s -> verify lai"
                      % ", ".join(fixed))
                try:
                    os.unlink(CACHE)
                except Exception:
                    pass
                return main()
        if down:
            print("KET QUA: NGUON TV360 DOWN - %d kenh khong lay duoc URL."
                  % len(still_bad or bad))
            print("         >>> GIU NGUYEN playlist cu (khong man den het)")
        else:
            print("KET QUA: %d kenh CHET -> da gan fallback cho rieng kenh do."
                  % len(still_bad or bad))
            print("         %s" % ", ".join(still_bad or bad))
        print("         >>> PUSH VAN DIEN RA (188 kenh khac khong bi chan)")
        return 2
    print("KET QUA: TAT CA KENH 'Su kien TV360' OK -> DUOC PUSH")
    return 0


if __name__ == "__main__":
    sys.exit(main())
