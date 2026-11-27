#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stable_hls.py - LAY URL ON DINH CHO KENH HLS (KHONG DRM) NHOM "SU KIEN TV360".

NGUON SU THAT: tv360_channels.json (dung chung cho moi module).
  -> them kenh moi = them 1 dong vao tv360_channels.py, KHONG sua file nay.

HAI KIEU LAY URL (tu config "kind"):
  "eds" -> URL dang /live/eds/<eds>/HLS_Clean_MHL_2s/index.m3u8?cdntoken=..
           + cdntoken lay tu TV360 API (get_stream(cid)) -> thu nhieu host
           ateme cho den khi master/media/segment deu OK.
  "api" -> URL lay TRUC TIEP tu get_stream(cid) (khong co duong /eds/).

HAI CHI TIET QUAN TRONG:
  1. TOKEN REFRESH: cdntoken het han sau ~6h -> phai refresh truoc 10 phut.
     Cache (stable_hls_cache.json) giu URL da xac minh + exp.
  2. AUTO EDS: neu config khong khai bao "eds", resolver TU SUY RA tu URL
     lay duoc tu API (quet /eds/<n>/) -> TV360 doi ha tang khong can sua code.

KHONG sua gi ca ngoai ra -> chi ghi khi URL da kiem chung chay duoc.
"""
import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.request
from urllib.parse import urljoin

sys.path.insert(0, "/opt/qweb")

QWEB = "/opt/qweb"
CACHE = os.path.join(QWEB, "stable_hls_cache.json")
UA_D = "Dalvik/2.1.0"
UA_B = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# Token het han sau ~6h -> refresh truoc 10 phut.
TOKEN_REFRESH_MARGIN = 1800

try:
    import tv360_channels as TVC
except Exception:
    TVC = None

# Fallback neu khong import duoc (config cu).
HLS_CHANNELS = {
    "tv360plus9":  {"cid": "9887", "eds": "379"},
    "tv360plus10": {"cid": "9957", "eds": "449"},
    "tv360plus11": {"cid": "9958", "eds": "450"},
    "tv360":       {"cid": "9904", "eds": "427"},
}
API_CHANNELS = {
    "livestream-01": {"cid": "9965"},
}
ATEME_HOSTS = list(TVC.ATEME_HOSTS) if TVC else [
    "rr1-ateme.tv360.vn",
    "pop14-ec1-ateme.tv360.vn",
    "pop14-ec2-ateme.tv360.vn",
    "pop14-ec3-ateme.tv360.vn",
]
HLS_PATH = TVC.HLS_PATH if TVC else "HLS_Clean_MHL_2s"
EDS_RE = TVC.EDS_RE if TVC else re.compile(r"/eds/(\d+)/")


def log(m):
    print("   %s" % m, flush=True)


def get(u, timeout=20, ua=UA_B):
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            u, headers={"User-Agent": ua, "Accept": "*/*"}),
            timeout=timeout, context=CTX)
        return r.getcode(), r.read(), r.geturl()
    except Exception as e:
        return 0, str(e).encode(), u


def b64d(s):
    s = (s or "").strip()
    s += "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s)
    except Exception:
        return b""


def token_exp(cdn):
    """exp trong payload JWT (giay epoch)."""
    try:
        parts = cdn.split(".")
        if len(parts) < 2:
            return 0
        pay = json.loads(b64d(parts[1]))
        return int(pay.get("exp", 0) or 0)
    except Exception:
        return 0



# --- FORCE REFRESH (them 27/11) ---------------------------------------------
# Ly do: token cu trong cache co the het han -> giu URL chet -> push len
# GitHub -> kenh chet sau ~6-9h. FORCE_REFRESH = moi chu ky LUON lay
# cdntoken MOI tu TV360 API (1.3-11s/kenh, khong ton nhieu).
# Bien env TV360_FORCE_REFRESH=0 de tat khi can.
FORCE_REFRESH = os.environ.get("TV360_FORCE_REFRESH", "1") != "0"


def url_exp(url):
    """exp (epoch) cua BAT KY loai token trong URL. 0 = khong doc duoc.

    Ho tro: cdntoken=<JWT>  |  /tok_<JWT>  |  ?auth=<JWT>
    """
    if not url:
        return 0
    m = re.search(r"/tok_([A-Za-z0-9_\-\.]+)", url)
    if m:
        e = token_exp(m.group(1))
        if e:
            return e
    m = re.search(r"cdntoken=([^&]+)", url)
    if m:
        e = token_exp(m.group(1))
        if e:
            return e
    m = re.search(r"[?&]auth=([A-Za-z0-9_\-\.]+)", url)
    if m:
        e = token_exp(m.group(1))
        if e:
            return e
    return 0


def url_alive(url, margin=0):
    """True neu token trong URL con han (con > margin giay)."""
    e = url_exp(url)
    if e <= 0:
        return False
    return (e - time.time()) > margin


def build_url(host, eds, cdn):
    return ("https://%s/live/eds/%s/%s/index.m3u8?cdntoken=%s&screen=MHL&uid=290557915"
            % (host, eds, HLS_PATH, cdn))


def verify(url):
    """Kiem chung THAT: master -> media playlist -> 1 segment.
    Tra (ok, mo_ta)."""
    c, b, eff = get(url, 20, UA_D)
    if c != 200:
        return False, "master HTTP%d" % c
    t = b.decode("utf-8", "replace")
    if "#EXTM3U" not in t[:2000]:
        return False, "khong phai M3U"
    subs = [l for l in t.splitlines() if l.strip() and not l.startswith("#")]
    if not subs:
        return False, "khong co media playlist"
    c2, m, eff2 = get(urljoin(eff, subs[0].strip()), 20, UA_D)
    if c2 != 200:
        return False, "media HTTP%d" % c2
    segs = [l for l in m.decode("utf-8", "replace").splitlines()
            if l.strip() and not l.startswith("#")]
    if not segs:
        return False, "media khong co segment"
    c3, s, _ = get(urljoin(eff2, segs[-1].strip()), 20, UA_D)
    if c3 != 200:
        return False, "segment HTTP%d" % c3
    if len(s) < 10000:
        return False, "segment qua nho (%d bytes)" % len(s)
    return True, "OK %d bytes seg | %s" % (len(s), eff2.split("/")[0][:50])


def fetch_cdntoken(cid, tries=8, deadline=None):
    """Lay cdntoken tu TV360 API (co token dang nhap, fallback anon)."""
    try:
        from tv360_client import anon_client, client
    except Exception as e:
        log("khong import duoc tv360_client: %s" % e)
        return None, None
    for i in range(tries):
        if deadline and time.time() > deadline:
            return None, None
        for use_auth in (True, False):
            try:
                cl = client() if use_auth else anon_client()
                if use_auth and not getattr(cl, "access_token", None):
                    continue
                u, _ = cl.get_stream(cid, timeout=18)
            except Exception:
                continue
            if u and "cdntoken=" in u:
                m = re.search(r"cdntoken=([^&]+)", u)
                if m:
                    return m.group(1), u
        time.sleep(1.0)
    return None, None


def load_cache():
    try:
        with open(CACHE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_cache(d):
    tmp = CACHE + ".tmp.%d" % os.getpid()
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


# ------------------------------------------------------------------
# CAC KENH: doc tu config chung (tv360_channels.json)
# ------------------------------------------------------------------
def _cfg(slug):
    """Tra (cid, kind, eds) cho 1 slug - doc tu config chung."""
    if TVC:
        i = TVC.info(slug)
        if i:
            return i.get("cid", ""), i.get("kind", "eds"), i.get("eds")
    if slug in HLS_CHANNELS:
        h = HLS_CHANNELS[slug]
        return h["cid"], "eds", h.get("eds")
    if slug in API_CHANNELS:
        return API_CHANNELS[slug]["cid"], "api", None
    return "", "eds", None


def all_slugs():
    """Tat ca slug kenh HLS trong nhom (theo config chung)."""
    if TVC:
        return [s for s in TVC.order()
                if TVC.kind_of(s) in ("eds", "api")
                and not TVC.info(s).get("skip")]
    return list(HLS_CHANNELS) + list(API_CHANNELS)


def resolve_hls(slug, deadline=None):
    """Kienh dang 'eds': lay cdntoken moi + thu host -> xac minh that."""
    cid, _kind, eds = _cfg(slug)
    if not cid:
        return None
    cache = load_cache()
    old = cache.get(slug) or {}

    # --- 1. URL trong cache ------------------------------------------------
    # FORCE_REFRESH=1 (mac dinh): BO QUA cache, luon lay cdntoken moi.
    # Ly do: giu cache token het han = push link chet len GitHub.
    # FORCE_REFRESH=0: dung cache khi token con >= TOKEN_REFRESH_MARGIN.
    old_url = old.get("url") or ""
    if old_url and not FORCE_REFRESH:
        if url_alive(old_url, TOKEN_REFRESH_MARGIN):
            ok, why = verify(old_url)
            if ok:
                log("%s: dung lai URL da xac minh (%s)" % (slug, why))
                return {"url": old_url, "key": None, "status": "HLS",
                        "test": "cached-ok", "kid": "", "keysrc": "-"}
            log("%s: URL cache hong (%s) -> lay moi" % (slug, why))
    elif old_url:
        log("%s: FORCE_REFRESH -> bo qua cache, lay cdntoken moi" % slug)

    # --- 2. Lay cdntoken moi tu TV360 API ---
    cdn, raw_url = fetch_cdntoken(cid, deadline=deadline)
    if not cdn:
        # KHONG giu URL cu neu token da het han -> se push link chet.
        if old_url and url_alive(old_url, 0):
            log("%s: khong lay duoc cdntoken -> giu URL con han" % slug)
            return {"url": old_url, "key": None, "status": "HLS",
                    "test": "keep-old (no-token, still-alive)", "kid": "", "keysrc": "-"}
        log("%s: khong lay duoc cdntoken + URL cu HET HAN -> fail" % slug)
        return {"url": "", "key": None, "status": "LECH",
                "test": "no-token & old-expired", "kid": "", "keysrc": "-"}
    exp = token_exp(cdn)
    log("%s: cdntoken moi (exp con %s giay)" % (slug, int(exp - time.time())))

    # --- 2b. AUTO EDS: suy ra duong dan /eds/<n>/ tu URL API ---
    if not eds:
        eds = None
        if raw_url:
            m = EDS_RE.search(raw_url)
            if m:
                eds = m.group(1)
        if eds:
            log("%s: auto suy ra eds=%s tu URL API" % (slug, eds))
        else:
            log("%s: API tra URL khong chua /eds/ -> thu URL truc tiep" % slug)
            ok, why = verify(raw_url)
            if ok:
                cache[slug] = {"url": raw_url, "exp": exp, "ts": int(time.time())}
                save_cache(cache)
                return {"url": raw_url, "key": None, "status": "HLS",
                        "test": "API-verified", "kid": "", "keysrc": "-"}
            if old_url and url_alive(old_url, 0):
                log("%s: URL API that bai (%s) -> giu URL con han" % (slug, why))
                return {"url": old_url, "key": None, "status": "HLS",
                        "test": "keep-old (api-fail, still-alive)",
                        "kid": "", "keysrc": "-"}
            log("%s: URL API that bai (%s) + URL cu HET HAN -> fail" % (slug, why))
            return {"url": "", "key": None, "status": "LECH",
                    "test": "api-fail & old-expired", "kid": "", "keysrc": "-"}

    # --- 3. Thu host ateme, xac minh that ---
    best = None
    for host in ATEME_HOSTS:
        if deadline and time.time() > deadline:
            break
        u = build_url(host, eds, cdn)
        ok, why = verify(u)
        if ok:
            log("%s: OK host=%s (%s)" % (slug, host, why))
            best = u
            break
        log("%s: host=%s that bai (%s)" % (slug, host, why))
    if not best:
        # Thu URL goc tu API (co the dang dung) truoc khi bo qua.
        if raw_url:
            ok, why = verify(raw_url)
            if ok:
                log("%s: OK bang URL goc tu API (%s)" % (slug, why))
                best = raw_url
    if not best:
        if old_url and url_alive(old_url, 0):
            log("%s: moi host deu that bai -> giu URL con han" % slug)
            return {"url": old_url, "key": None, "status": "HLS",
                    "test": "keep-old (all-host-fail, still-alive)",
                    "kid": "", "keysrc": "-"}
        log("%s: moi host deu that bai + URL cu HET HAN -> fail" % slug)
        return {"url": "", "key": None, "status": "LECH",
                "test": "all-host-fail & old-expired", "kid": "", "keysrc": "-"}

    cache[slug] = {"url": best, "exp": exp, "ts": int(time.time())}
    save_cache(cache)
    return {"url": best, "key": None, "status": "HLS",
            "test": "HLS-verified", "kid": "", "keysrc": "-"}


def resolve_api_direct(slug, deadline=None):
    """Kenh lay URL truc tiep tu TV360 API (khong phai duong /eds/)."""
    cid, kind, _eds = _cfg(slug)
    if not cid:
        return None
    try:
        from tv360_client import anon_client, client
    except Exception as e:
        log("%s: khong import duoc tv360_client: %s" % (slug, str(e)[:60]))
        return None

    u = None
    for use_auth in (True, False):
        if deadline and time.time() > deadline:
            return None
        try:
            cl = client() if use_auth else anon_client()
            if use_auth and not getattr(cl, "access_token", None):
                continue
            cand, _ = cl.get_stream(cid, timeout=18)
        except Exception:
            continue
        if cand and cand.startswith("http"):
            u = cand
            log("%s: URL moi tu API (len=%d)" % (slug, len(u)))
            break
    if not u:
        log("%s: khong lay duoc URL tu API" % slug)
        return None

    ok, why = verify(u)
    if not ok:
        log("%s: URL moi that bai (%s)" % (slug, why))
        return None
    log("%s: OK (%s)" % (slug, why))
    return {"url": u, "key": None, "status": "HLS",
            "test": "API-verified", "kid": "", "keysrc": "-"}


def is_hls(slug):
    """True NEU kenh duoc xu ly boi module nay (HLS khong DRM).

    Kenh DRM DASH -> False (KHONG duoc lay HLS URL, se mat ClearKey).
    """
    _cid, kind, _eds = _cfg(slug)
    return kind in ("eds", "api")


def resolve_any(slug, deadline=None):
    """Diem entry dung chung - tu quyet dinh theo kind trong config.

    TRAVE: neu kenh khong phai HLS -> None (de resolve_v2.py xu ly DASH).
    """
    if not is_hls(slug):
        return None
    _cid, kind, _eds = _cfg(slug)
    if kind == "api":
        return resolve_api_direct(slug, deadline=deadline)
    return resolve_hls(slug, deadline=deadline)


def main():
    want = sys.argv[1:] or all_slugs()
    budget = float(os.environ.get("TV360_RESOLVE_BUDGET", "600"))
    deadline = time.time() + budget
    out = {}
    for slug in want:
        if slug not in all_slugs():
            continue
        try:
            r = resolve_any(slug, deadline=deadline)
        except Exception as exc:
            r = None
            log("%s: LOI %s" % (slug, str(exc)[:80]))
        out[slug] = r
    print(json.dumps({k: v for k, v in out.items() if v},
                     ensure_ascii=False)[:4000])
    return 0


if __name__ == "__main__":
    sys.exit(main())
