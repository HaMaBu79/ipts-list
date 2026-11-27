#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resolver TV360 THAT (phien 2) - kiem chung bang ANH THAT (khong phai xam).

Tieu chi giai ma dung:
  - ffmpeg decrypt bang content key -> xuat PNG
  - PNG phai khac mau xam dong nhat (818181) va khac mau den thuan
  => "PLAY" that, khong con nham voi "anh tinh" (scenary toi)

Nguon: clearkey.php (vietanhtv) cho KID:KEY; lap URL goc cho den khi
default_KID cua stream == KID cua key.
"""
import base64
from concurrent.futures import ThreadPoolExecutor
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from urllib.parse import urljoin

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
UA_D = "Dalvik/2.1.0"
UA_B = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
QWEB = "/opt/qweb"
SRC_URL = "https://tv.vietanhtv.top/tivi/index.php?u=nguyenduhan&p=nguyenduhan"
CK_URL = "https://tv.vietanhtv.top/tivi/cleankey.php?id=%s&token=3d92fcd055275635ca615aa9ee5fea0a"
# --- DANH SACH KENH: doc tu tv360_channels.py (NGUON SU THAT CHUNG) ---
# Them kenh moi = them 1 dong vao tv360_channels.py, KHONG sua file nay.
try:
    import tv360_channels as TVC
except Exception:
    TVC = None

if TVC:
    ORDER = TVC.order()
    SKIP = set(TVC.skips())
else:
    ORDER = ["tv360plus%d" % i for i in range(1, 16)] + ["tv360", "livestream-01"]
    SKIP = {"tv360plus6", "tv360plus7"}
# "tv360" -> phai refresh cdntoken moi moi chu ky, dung URL cache cu
#    (token het han sau ~6h -> master 403 -> khong push duoc).
# HAN THOI GIAN cho 1 lan resolve - de chu ky 5 phut khong bi truot.
DEADLINE = 0.0
BUDGET = 150.0


def get(u, timeout=15, ua=UA_D):
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            u, headers={"User-Agent": ua, "Accept": "*/*"}), timeout=timeout, context=CTX)
        return r.getcode(), r.read(), r.geturl()
    except Exception as exc:
        return 0, str(exc).encode(), u


def b64h(s):
    s = (s or "").strip()
    if not s:
        return ""
    s += "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s).hex()
    except Exception:
        return ""


def strip_port(u):
    return re.sub(r"^(https?://[^/:]+):443(?=/)", r"\1", u)


def parse_source(raw):
    lines = raw.split("\n")
    out, i = {}, 0
    while i < len(lines):
        if lines[i].startswith("#EXTINF"):
            blk, j = [], i + 1
            while j < len(lines) and not lines[j].startswith("#EXTINF"):
                blk.append(lines[j])
                j += 1
            m = re.search(r'tvg-id="([^"]+)"', lines[i])
            if m:
                slug = m.group(1).strip().lower()
                lic = url = None
                for x in blk:
                    s = x.strip()
                    if "license_key=" in s:
                        lic = s.split("license_key=", 1)[1].strip()
                    if s.startswith("http") and not s.startswith("#"):
                        url = s
                out[slug] = {"lic": lic, "url": url}
            i = j
            continue
        i += 1
    return out


def kids_of(body):
    out = [k.replace("-", "").lower() for k in re.findall(r'default_KID="([^"]+)"', body)]
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
    """SUA: chon rid VIDEO + lay <S> RIENG theo AdaptationSet.

    MPD co nhieu AdaptationSet (audio + video), moi cai co SegmentTimeline
    rieng. Ban cu lay <S> toan cuc body + rids[-1] (audio) -> URL segment sai
    -> ffmpeg NOFRAME -> kenh khong bao gio giai ma duoc.
    """
    base = urljoin(mpd_url, ".")
    init, segs = None, []
    blocks = re.findall(r"<AdaptationSet\b.*?</AdaptationSet>", body, re.S) or [body]
    # VIDEO truoc, AUDIO sau
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
        vrids = [r for r in rids if r.startswith("avc") or "video" in r]
        use = vrids or rids
        if not use:
            continue
        # <S> RIENG cua AdaptationSet nay + ho tro r= repeat
        times = []
        cur = None
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
            for _ in range(rep):
                cur = str(int(cur) + d)
                times.append(cur)
        if not times:
            times = re.findall(r'<S[^>]*t="(\d+)"', blk)
        if not times:
            continue
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
    # fallback: SegmentList
    sl = re.search(r"<SegmentList[^>]*>(.*?)</SegmentList>", body, re.S)
    if sl:
        inner = sl.group(1)
        mi = re.search(r'<Initialization[^>]*sourceURL="([^"]+)"', inner)
        init3 = mi.group(1) if mi else None
        segs3 = re.findall(r'<SegmentURL[^>]*media="([^"]+)"', inner)[:2]
        if init3 and segs3:
            return urljoin(base, init3), [urljoin(base, s) for s in segs3]
    return None, []
def decode_png(init_u, seg_u, keyhex):
    """Giai ma 1 frame -> (ok, avg_hex). ok=True neu that su khong phai xam."""
    d = tempfile.mkdtemp(prefix="dv_")
    mp4 = os.path.join(d, "a.mp4")
    png = os.path.join(d, "f.png")
    try:
        c1, i, _ = get(init_u, 20, UA_B)
        c2, s, _ = get(seg_u, 20, UA_B)
        if c1 != 200 or c2 != 200:
            return False, "HTTP%d/%d" % (c1, c2)
        with open(mp4, "wb") as f:
            f.write(i)
            f.write(s)
        p = subprocess.run(["ffmpeg", "-v", "error", "-decryption_key", keyhex,
                            "-i", mp4, "-frames:v", "1", "-y", png],
                           capture_output=True, timeout=60)
        if not os.path.exists(png) or os.path.getsize(png) < 15000:
            return False, "NOFRAME"
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", png, "-vf", "scale=1:1",
                            "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                           capture_output=True)
        h = r.stdout[:3].hex()
        if h == "818181":
            return False, "GRAY"
        return True, h
    except Exception as exc:
        return False, "ERR " + str(exc)[:40]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def get_source():
    for _ in range(2):
        if DEADLINE and time.time() > DEADLINE:
            return {}
        c, raw, _ = get(SRC_URL, 25)
        if c == 200 and len(raw) > 10000:
            return parse_source(raw.decode("utf-8", "replace"))
        time.sleep(3)
    return {}


def ck_keys(slug):
    # 1 lan goi duy nhat: het deadline -> tra ve rong ngay (resolve() se dung
    # key trong key_pool). Truoc day 4 attempts x 6s sleep x 17 kenh = 408s.
    for attempt in range(1):
        if DEADLINE and time.time() > DEADLINE:
            return []
        c, b, _ = get(CK_URL % slug, 8)
        try:
            j = json.loads(b.decode("utf-8", "replace"))
            out = []
            for k0 in (j.get("keys") or []):
                kid, key = b64h(k0.get("kid")), b64h(k0.get("k"))
                if kid and key:
                    out.append((kid, key))
            if out:
                return out
        except Exception:
            pass
    return []


def pool_keys():
    p = {}
    for f in ("key_pool.json.ch", "key_pool.json"):
        try:
            p.update(json.load(open(os.path.join(QWEB, f), encoding="utf-8")))
        except Exception:
            pass
    return p


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
    return (e - time.time()) > margin


def resolve(slug, src, pool, tries=60):
    e = src.get(slug) or {}
    keys = []
    for kid, key in ck_keys(slug):
        keys.append((kid, key, "vietanhtv"))
    pv = pool.get(slug, "")
    if ":" in pv:
        k, v = pv.split(":", 1)
        if not any(k == kk for kk, _, _ in keys):
            keys.append((k, v, "pool"))

    # --- KENH HLS (KHONG DRM): lay URL ON DINH + XAC MINH that ---
    # Nguon ngoai redirect lo xoay giua nhieu host, host nao het token
    # (netcdn/bpk/fo-hlc) tra 403 o SEGMENT -> player MAN DEN.
    # stable_hls.py lay cdntoken tu TV360 API + kiem chung master/media/
    # segment truoc khi ghi -> chi ghi URL CHAY DUOC.
    #
    # resolve_any() tu QUET kind trong config (eds/api) -> them kenh moi
    # khong can sua file nay.
    # CHI kenh HLS (kind eds/api) moi di qua stable_hls. Kenh DRM DASH
    # phai xu ly o duoi (ClearKey) - lay nham HLS URL se lam MAN DEN.
    if TVC is None or TVC.is_hls(slug):
        try:
            sys.path.insert(0, QWEB)
            import stable_hls
            r = stable_hls.resolve_any(slug, deadline=DEADLINE or None)
            if r and r.get("url"):
                return r
        except Exception as exc:
            print("   %s stable_hls LOI: %s" % (slug, str(exc)[:90]), flush=True)

    stm = e.get("url")
    if not stm:
        return {"url": "", "key": None, "status": "LOI", "test": "no-src"}

    seen_hosts = set()
    for attempt in range(tries):
        if DEADLINE and time.time() > DEADLINE:
            break
        sc, sb, sf = get(stm, 20, UA_B)
        if sc != 200:
            time.sleep(1.0)
            continue
        body = sb.decode("utf-8", "replace")
        if "#EXTM3U" in body[:2000]:
            return {"url": strip_port(sf), "key": None, "status": "HLS", "test": "HLS",
                    "kid": "", "keysrc": "-"}
        if "<MPD" not in body[:6000]:
            time.sleep(0.8)
            continue
        final = strip_port(sf)
        skids = set(kids_of(body))
        init_u, segs = seg_urls(body, final)
        host = final.split("/")[2]
        for kid, key, src_name in keys:
            if kid not in skids or not init_u or not segs:
                continue
            ok, info = decode_png(init_u, segs[0], key)
            print("   %s try%d host=%s KID=%s -> %s (%s)" %
                  (slug, attempt, host, kid[:12], "OK" if ok else "no", info), flush=True)
            if ok:
                return {"url": final, "key": "%s:%s" % (kid, key), "status": "KHOP",
                        "test": "IMG " + info, "kid": kid, "keysrc": src_name}
        seen_hosts.add(host)
        time.sleep(1.0)
    return {"url": "", "key": None, "status": "LECH",
            "test": "no-match (%d tries, hosts=%s)" % (tries, ",".join(sorted(seen_hosts))[:60]),
            "kid": "", "keysrc": "-"}


def main():
    global DEADLINE
    budget = float(os.environ.get("TV360_RESOLVE_BUDGET", "600"))
    DEADLINE = time.time() + budget
    src = get_source()
    pool = pool_keys()
    want = sys.argv[1:] or ORDER
    out = {}
    path = os.path.join(QWEB, "tv360_resolved_new.json")
    try:
        old = json.load(open(path, encoding="utf-8"))
    except Exception:
        old = {}
    # --- CHIA LOAI: HLS chay TRUOC, DASH chay SONG SONG -------------------
    # Ly do (27/11): 10 kenh DASH xu ly TUAN TU an het budget 90s ->
    # 5 kenh HLS (9,10,11,tv360,livestream) khong kip chay -> LECH ->
    # giu link cu co token HET HAN -> push len GitHub -> kenh chet sau ~6-9h.
    # Fix: HLS chay truoc (nhanh) -> luon kip lay cdntoken moi.
    hls_slugs = [s for s in want
                 if s not in SKIP and TVC is not None and TVC.is_hls(s)]
    dash_slugs = [s for s in want
                  if s not in SKIP and s not in hls_slugs]

    def do_one(slug):
        if DEADLINE and time.time() > DEADLINE:
            return {"url": "", "key": None, "status": "LECH",
                    "test": "het deadline", "kid": "", "keysrc": "-"}
        try:
            return resolve(slug, src, pool)
        except Exception as exc:
            return {"url": "", "key": None, "status": "LOI",
                    "test": str(exc)[:60], "kid": "", "keysrc": "-"}

    def save_one(slug, r):
        # CHI ghi de khi resolve THANH CONG. Neu lan nay that bai (nguon ban
        # IP, API loi...) thi GIU nguyen cap key/URL da verify truoc do ->
        # tranh ghi de key tot bang KID tran (man den).
        if r.get("status") in ("KHOP", "HLS"):
            old[slug] = r
        else:
            prev = old.get(slug) or {}
            p_url = prev.get("url", "")
            # KHONG giu URL co token HET HAN -> se push link chet len GitHub.
            # Giu chi khi URL con han (hoac khong co token de check).
            if p_url and not url_alive(p_url, 0):
                out[slug] = {"url": "", "key": None,
                             "logo": prev.get("logo", ""),
                             "name": prev.get("name", slug),
                             "status": "LECH",
                             "test": "expired-previous dropped (%s)"
                                     % r.get("test", "")}
            else:
                out[slug] = {"url": p_url, "key": prev.get("key"),
                             "logo": prev.get("logo", ""),
                             "name": prev.get("name", slug),
                             "status": prev.get("status", r.get("status")),
                             "test": "keep-previous (%s)" % r.get("test", "")}
        print("%-14s %-6s %-70s %s" % (slug, r["status"],
              (r.get("key") or "-"), r.get("test")), flush=True)
        json.dump(old, open(path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)

    # --- 2a) HLS truoc: nhanh, luon kip lam moi token ---------------------
    for slug in hls_slugs:
        r = do_one(slug)
        out[slug] = r
        save_one(slug, r)

    # --- 2b) DASH song song (ThreadPoolExecutor) --------------------------
    if dash_slugs:
        with ThreadPoolExecutor(max_workers=min(6, len(dash_slugs))) as ex:
            futs = {ex.submit(do_one, s): s for s in dash_slugs}
            for fut, slug in futs.items():
                r = fut.result()
                out[slug] = r
                save_one(slug, r)

    for slug in want:
        if slug in SKIP:
            continue
        if slug in out:
            continue
    ok = sum(1 for v in out.values() if v.get("status") in ("KHOP", "HLS"))
    print("=> %d/%d OK (%.0fs)" % (ok, len(out), budget))


if __name__ == "__main__":
    main()
