#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
token_guard.py - LUOI AN TOAN CUOI CUNG (them 27/11).

MUC TICH: ngay ca khi pipeline chinh (resolve_v2/apply_v2) bi loi, treo,
het deadline hay session chet -> link cu (token het han) van nam trong
list.m3u8 va duoc PUSH len GitHub -> kenh chet o may nguoi xem.

MODULE NAY chay DOC LAP (cron */5), khong phu thuoc pipeline:
  1. Doc list.m3u8 + Listphu.m3u8
  2. Tim moi muc co token (cdntoken / tok_ / auth)
  3. Token het han hoac sap het (< GUARD_MARGIN) -> can lam moi
  4. Lam moi: goi TV360 API -> lay URL moi -> XAC MINH THAT (master/media/
     segment) -> ghi vao playlist
  5. Khong lay duoc URL moi -> dat error.ts (KHONG push link chet)

Bien moi truong:
  TV360_GUARD_MARGIN   - refresh truoc khi het (giay). Mac dinh 3600 (1h).
  TV360_GUARD_DRY      - =1 thi chi bao cao, khong sua file.
"""
import base64
import io
import json
import os
import re
import shutil
import sys
import time

QWEB = "/opt/qweb"
sys.path.insert(0, QWEB)

LOG = "/var/log/qweb_token_guard.log"
GUARD_MARGIN = int(os.environ.get("TV360_GUARD_MARGIN", "3600"))
DRY = os.environ.get("TV360_GUARD_DRY", "0") == "1"
PLAYLISTS = (os.path.join(QWEB, "list.m3u8"),
             os.path.join(QWEB, "Listphu.m3u8"))
ERROR_URL = "http://0.0.0.0/error.ts"

UA_D = "Dalvik/2.1.0"
CTX = None
try:
    import ssl
    CTX = ssl.create_default_context()
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE
except Exception:
    pass


def log(m):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), m)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def jwt_exp(tok):
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        d = json.loads(base64.urlsafe_b64decode(p))
        return int(d.get("exp", 0) or 0)
    except Exception:
        return 0


def url_exp(u):
    """exp cua BAT KY loai token: cdntoken / tok_ / auth. 0 = khong doc."""
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


def get(u, timeout=20, ua=UA_D):
    import urllib.request
    try:
        r = urllib.request.urlopen(urllib.request.Request(
            u, headers={"User-Agent": ua, "Accept": "*/*"}),
            timeout=timeout, context=CTX)
        return r.getcode(), r.read(), r.geturl()
    except Exception as e:
        return 0, str(e).encode(), u


def verify(url):
    """master -> media -> segment. Tra (ok, mo_ta)."""
    from urllib.parse import urljoin
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
    return True, "OK %d bytes" % len(s)


def parse_blocks(path):
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


def url_of(blk):
    for l in blk:
        s = l.strip()
        if s.startswith("http"):
            return s
    return ""


def set_error(blk):
    """Dat URL -> error.ts va goi bo license_key (de khong man den)."""
    out = []
    for l in blk:
        s = l.strip()
        if s.startswith("#KODIPROP:inputstream.adaptive.license_key="):
            continue
        if s.startswith("#KODIPROP:inputstream.adaptive.license_type="):
            continue
        if s.startswith("http"):
            out.append(ERROR_URL)
            continue
        out.append(l)
    return out


def refresh_url(slug, cid, kind, eds):
    """Lay URL moi tu TV360 API + xac minh that. Tra (url, mo_ta)."""
    try:
        from tv360_client import client, anon_client
    except Exception as e:
        return None, "import loi: %s" % str(e)[:60]
    raw = None
    for use_auth in (True, False):
        try:
            cl = client() if use_auth else anon_client()
            if use_auth and not getattr(cl, "access_token", None):
                continue
            u, _ = cl.get_stream(cid, timeout=20)
        except Exception:
            continue
        if u:
            raw = u
            break
    if not raw:
        return None, "API khong tra URL"

    cands = [raw]
    # Tu suy ra /eds/<n>/ va doi host -> thu nhieu host ateme
    m = re.search(r"/eds/(\d+)/", raw)
    eds_v = eds or (m.group(1) if m else None)
    if eds_v:
        try:
            import tv360_channels as TVC
            hosts = list(TVC.ATEME_HOSTS)
        except Exception:
            hosts = ["rr1-ateme.tv360.vn", "pop14-ec1-ateme.tv360.vn",
                     "pop14-ec2-ateme.tv360.vn", "pop14-ec3-ateme.tv360.vn"]
        for h in hosts:
            cands.append("https://%s/live/eds/%s/%s/index.m3u8?cdntoken=%s"
                         % (h, eds_v, "HLS_Clean_MHL_2s",
                            re.search(r"cdntoken=([^&\s]+)", raw).group(1)
                            if re.search(r"cdntoken=([^&\s]+)", raw) else ""))
    for u in cands:
        if not u or "cdntoken=" not in u and "/eds/" not in u:
            continue
        ok, why = verify(u)
        if ok:
            return u, why
    return None, "moi host deu that bai"


def main():
    log("=== TOKEN GUARD BAT DAU (margin=%ds, dry=%s) ==="
        % (GUARD_MARGIN, DRY))
    now = int(time.time())
    try:
        import tv360_channels as TVC
    except Exception:
        TVC = None

    fixed = 0
    checked = 0
    for path in PLAYLISTS:
        if not os.path.exists(path):
            continue
        _txt, blocks = parse_blocks(path)
        changed = False
        for b in blocks:
            if not b or not b[0].startswith("#EXTINF"):
                continue
            slug = slug_of(b)
            cur = url_of(b)
            if not cur or "0.0.0.0" in cur:
                continue
            exp = url_exp(cur)
            if exp <= 0:
                continue
            checked += 1
            left = exp - now
            if left > GUARD_MARGIN:
                continue
            state = "HET HAN" if left <= 0 else "sap het (%.0f ph)" % (left / 60.0)
            log("%-16s token %s -> can lam moi" % (slug, state))
            if DRY:
                continue
            cid = kind = eds = None
            if TVC:
                cid = TVC.cid_of(slug)
                kind = TVC.kind_of(slug)
                eds = TVC.eds_of(slug)
            if not cid:
                log("%-16s bo qua (khong biet cid)" % slug)
                continue
            newu, why = refresh_url(slug, cid, kind, eds)
            if not newu:
                log("%-16s KHONG lay duoc URL moi (%s) -> error.ts" % (slug, why))
                nb = set_error(b)
                if nb != b:
                    b[:] = nb
                    changed = True
                    fixed += 1
                continue
            log("%-16s da lam moi URL (%s)" % (slug, why))
            nb = []
            for l in b:
                s = l.strip()
                if s.startswith("http"):
                    nb.append(newu)
                    continue
                nb.append(l)
            if nb != b:
                b[:] = nb
                changed = True
                fixed += 1
        if changed:
            bak = path + ".bak_guard"
            if not os.path.exists(bak):
                shutil.copy2(path, bak)
            with io.open(path, "w", encoding="utf-8") as f:
                f.write("\n".join("\n".join(b) for b in blocks))
            log("%s -> da ghi lai (%d muc)" % (os.path.basename(path), fixed))

    log("=== TOKEN GUARD XONG (checked=%d, fixed=%d) ===" % (checked, fixed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
