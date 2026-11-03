#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RELAY HLS - giai ma DRM tren BB2, phat MPEG-TS sach cho moi thiet bi.

Vì sao can relay:
  11 kenh SCTV + ON SPORTS la DRM (DASH/CENC ClearKey). Playlist chi giu
  "#KODIPROP:inputstream.adaptive.license_key=<url>" -> THIET BI phai tu goi
  server vietanhtv.top de lay key. Server do:
    - chay chay chon (404 xen ke 200)
    - key xoay vong theo chu ky
  => may nao goi dung luc server song -> xem duoc; may nao gap luc 404 -> MAN DEN
  ma khong bao loi gi (player khong co key de giai ma).

Cach nay: BB2 lam "nguoi xem" duy nhat.
  1. Tu lay key tu clearkey.php (retry lien tuc vi server chay chay chon)
  2. ffmpeg giai ma CENC -> MPEG-TS thuan
  3. Phat lai qua HTTP, app xem link thuong (HLS .m3u8) - KHONG can DRM,
     KHONG can plugin ClearKey, KHONG phu thuoc endpoint luc phat.

Kenh nao DAI TRANG (direct) thi khong qua relay: xem pin_overrides.json.
"""
import base64
import gzip
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import http.server
import socketserver
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(HERE, "pin_overrides.json")
BASE_DIR = os.environ.get("RELAY_DIR", "/var/lib/hlsrelay")
PORT = int(os.environ.get("RELAY_PORT", "8099"))
FFMPEG = os.environ.get("RELAY_FFMPEG", "/opt/ff81/bin/ffmpeg")
IDLE_STOP = int(os.environ.get("RELAY_IDLE_STOP", "300"))
KEY_TTL = int(os.environ.get("RELAY_KEY_TTL", "1800"))  # 30 phut
UA = "Dalvik/2.1.0"
RELAY_HOST = os.environ.get("RELAY_HOST", "")

STATE = {}
STARTING = set()
LOCK = threading.RLock()
LAST_REQ = {}
KEYS = {}          # ch -> {key, kid, t}
KEY_LOCK = threading.Lock()


def log(m):
    s = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), m)
    print(s, flush=True)


# ------------------------------------------------------------------ http
def _http(url, ua=UA, timeout=20, method="GET"):
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
    req.get_method = lambda: method
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            b = r.read()
            if b[:2] == b"\x1f\x8b":
                try:
                    b = gzip.decompress(b)
                except Exception:
                    pass
            return r.getcode(), b, r.geturl(), dict(r.headers)
    except urllib.error.HTTPError as e:
        b = b""
        try:
            b = e.read()
        except Exception:
            pass
        if b[:2] == b"\x1f\x8b":
            try:
                b = gzip.decompress(b)
            except Exception:
                pass
        return e.code, b, url, dict(e.headers or {})
    except Exception as e:
        return 0, str(e).encode(), url, {}


def _b64url_to_hex(s):
    s = (s or "").strip()
    if not s:
        return ""
    s += "=" * (-len(s) % 4)
    try:
        return base64.urlsafe_b64decode(s).hex()
    except Exception:
        return ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Khong follow redirect - muon doc header Location de lay URL co token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def tokenize_url(url):
    """Lay URL manifest DA CO TOKEN tu header Location cua redirect CDN.

    CDN Broadpeak/Ateme redirect 307 sang URL khac chua token
    (bpk-token/... hoac tok_<jwt>/...). Segment trong MPD la DUONG DAN TUONG DOI,
    nen player phai lay manifest tren host co token - neu khong thi segment 404.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with opener.open(req, timeout=20) as r:
            code = r.getcode()
            hdrs = dict(r.headers or {})
    except urllib.error.HTTPError as e:
        code = e.code
        hdrs = dict(e.headers or {})
    except Exception:
        return url
    loc = hdrs.get("Location") or hdrs.get("location") or ""
    if code in (301, 302, 303, 307, 308) and loc:
        if loc.startswith("/"):
            p = urllib.parse.urlsplit(url)
            loc = "%s://%s%s" % (p.scheme, p.netloc, loc)
        log("  token hoa (%s): %s" % (code, loc[:160]))
        return loc
    log("  khong co redirect (%s), dung truc tiep" % code)
    return url


# ------------------------------------------------------------------ key
def _dec_key(kk):
    """Gia tri 'k' co the la hex 32 hoac base64url 16 byte."""
    kk = (kk or "").strip()
    if len(kk) == 32 and all(c in "0123456789abcdefABCDEF" for c in kk):
        return kk.lower()
    return _b64url_to_hex(kk)


def fetch_license_key(url, tries=8, gap=4):
    """Lay key tu endpoint clearkey.php. Server chay chay chon -> retry nhieu lan."""
    for i in range(tries):
        code, body, _, _ = _http(url, timeout=15)
        try:
            txt = body.decode("utf-8", "replace")
        except Exception:
            txt = ""
        if '"keys"' in txt:
            try:
                j = json.loads(txt)
                k0 = (j.get("keys") or [{}])[0]
                kid = _b64url_to_hex(k0.get("kid", ""))
                key = _dec_key(k0.get("k", ""))
                if key:
                    return {"kid": kid, "key": key, "type": j.get("type", "temporary")}
            except Exception:
                pass
        if i < tries - 1:
            time.sleep(gap)
    return None


def key_for(ch):
    """Lay key tu cache (TTL) hoac goi server."""
    with KEY_LOCK:
        e = KEYS.get(ch)
        if e and time.time() - e["t"] < KEY_TTL:
            return e
    spec = PINS.get(ch) or {}
    src = spec.get("license_key")
    if isinstance(src, (list, tuple)):
        src = src[0] if src else None
    if not src:
        return None
    if not str(src).startswith("http"):
        return None
    k = fetch_license_key(str(src))
    if not k:
        return None
    k["t"] = time.time()
    with KEY_LOCK:
        KEYS[ch] = k
    log("[%s] lay key moi: kid=%s type=%s" % (ch, k["kid"][:16], k["type"]))
    return k


# ------------------------------------------------------------------ pins
def load_pins():
    """Doc pin_overrides.json. Cac khoa bat dau bang '_' la metadata, bo qua."""
    try:
        with io.open(PIN_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        log("LOAI doc pin_overrides.json: %s" % e)
        return {}
    out = {}
    for k, v in raw.items():
        if k.startswith("_") or not isinstance(v, dict):
            continue
        out[k] = v
    return out


PINS = load_pins()
NAMES = {k: (v.get("name") or k.upper()) for k, v in PINS.items()}


# ------------------------------------------------------------------ ffmpeg
def chan_dir(ch):
    return os.path.join(BASE_DIR, ch)


def ffmpeg_cmd(ch, spec, d, pl, seg, keyhex):
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error",
           "-user_agent", UA, "-reconnect", "1", "-reconnect_streamed", "1",
           "-reconnect_delay_max", "8"]
    cmd += ["-cenc_decryption_key", keyhex]
    cmd += ["-i", spec["url"], "-c", "copy",
            "-f", "segment", "-segment_format", "mpegts",
            "-segment_time", "4", "-reset_timestamps", "1",
            "-segment_start_number", "0", seg]
    return cmd


def start_ffmpeg(ch):
    spec = PINS.get(ch) or {}
    url = spec.get("url")
    if not url:
        return False, "khong co url"
    d = chan_dir(ch)
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(d, exist_ok=True)
    pl = os.path.join(d, "live.m3u8")
    seg = os.path.join(d, "seg%03d.ts")
    k = key_for(ch)
    if not k:
        return False, "khong lay duoc key DRM"
    # Manifest phai la URL da token-hoa (redirect 307 -> bpk-token / tok_jwt)
    tok_url = tokenize_url(url)
    cmd = ffmpeg_cmd(ch, spec, d, pl, seg, k["key"])
    cmd[cmd.index("-i") + 1] = tok_url
    try:
        logf = open(os.path.join(d, "ffmpeg.log"), "wb")
        proc = subprocess.Popen(cmd, stdout=logf, stderr=logf,
                                cwd=os.path.dirname(FFMPEG))
    except Exception as e:
        return False, "ffmpeg loi: %s" % e
    with LOCK:
        STATE[ch] = {"proc": proc, "last": time.time(), "dir": d, "pl": pl, "seg": seg}
    return True, "ok"


def cleanup_files(ch):
    """Xoa segment cu de khong phinh to. Giu 20 segment moi nhat."""
    d = chan_dir(ch)
    try:
        segs = [f for f in os.listdir(d) if f.endswith(".ts") and f.startswith("seg")]
    except Exception:
        return
    segs.sort(key=_seg_idx)
    for f in segs[:-20]:
        try:
            os.remove(os.path.join(d, f))
        except Exception:
            pass


def _seg_idx(fname):
    """seg000.ts -> 0 ; tra -1 neu khong parse duoc."""
    try:
        return int(fname[3:-3])
    except Exception:
        return -1


def build_playlist(ch):
    """Tu sinh HLS playlist tu cac file .ts tren dia.

    Khong dung -segment_list cua ffmpeg vi doi voi live stream, file .m3u8 chi
    duoc ghi khi ffmpeg ket thuc (tuc la khi stream die) -> khong dung duoc.
    Vay tao tu doc thu muc va lam playlist theo thu tu segment.
    """
    d = chan_dir(ch)
    try:
        segs = sorted([f for f in os.listdir(d)
                       if f.endswith(".ts") and f.startswith("seg")])
    except Exception:
        return None
    segs = [f for f in segs if _seg_idx(f) >= 0]
    segs.sort(key=_seg_idx)
    if not segs:
        return None
    # giu 12 segment moi nhat
    keep = segs[-12:]
    out = ["#EXTM3U", "#EXT-X-VERSION:3", "#EXT-X-TARGETDURATION:5",
           "#EXT-X-MEDIA-SEQUENCE:%d" % max(0, _seg_idx(keep[0]) - 1)]
    for f in keep:
        out.append("#EXTINF:4.000,")
        out.append("%s/%s" % (ch, f))
    return ("\n".join(out) + "\n").encode("utf-8")


def ensure_running(ch):
    with LOCK:
        st = STATE.get(ch)
        if st and st["proc"].poll() is None:
            st["last"] = time.time()
            return True, "ok"
        if st:
            try:
                st["proc"].kill()
            except Exception:
                pass
        if ch in STARTING:
            return False, "dang khoi dong"
        if ch not in PINS:
            return False, "khong co pin cho %s" % ch
        STARTING.add(ch)
    try:
        ok, msg = start_ffmpeg(ch)
    finally:
        with LOCK:
            STARTING.discard(ch)
    if not ok:
        return False, msg
    d = chan_dir(ch)
    for _ in range(90):
        time.sleep(1)
        try:
            segs = [f for f in os.listdir(d)
                    if f.endswith(".ts") and f.startswith("seg")]
        except Exception:
            segs = []
        if len(segs) >= 3:
            return True, "ok"
        st = STATE.get(ch)
        if not st or st["proc"].poll() is not None:
            return False, "ffmpeg tho ngay"
    return False, "timeout"


# ------------------------------------------------------------------ http server
class H(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_HEAD(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
        self.end_headers()

    def do_GET(self):
        try:
            self._get()
        except Exception as e:
            try:
                self._send(500, "text/plain", ("loi relay: %s" % e).encode("utf-8", "replace"))
            except Exception:
                pass

    def _get(self):
        path = self.path.split("?")[0].lstrip("/")

        if path.endswith(".ts") and "/" in path:
            ch = path.split("/")[0]
            fn = os.path.basename(path)
            fp = os.path.join(chan_dir(ch), fn)
            if not os.path.exists(fp):
                self._send(404, "text/plain", b"not found")
                return
            try:
                with open(fp, "rb") as f:
                    data = f.read()
            except Exception:
                self._send(404, "text/plain", b"not found")
                return
            self._send(200, "video/mp2t", data)
            return

        if path.endswith(".m3u8"):
            ch = path[:-5]
            if ch not in PINS:
                body = ("#EXTM3U\n" + "".join(
                    '#EXTINF:-1 tvg-id="%s",%s\nhttp://%s/%s.m3u8\n'
                    % (c, NAMES.get(c, c.upper()),
                       self.headers.get("Host", "127.0.0.1"), c)
                    for c in sorted(PINS))).encode()
                self._send(200, "application/vnd.apple.mpegurl", body)
                return
            LAST_REQ[ch] = time.time()
            ok, msg = ensure_running(ch)
            body = build_playlist(ch)
            if body is None:
                for _ in range(20):
                    time.sleep(2)
                    body = build_playlist(ch)
                    if body is not None:
                        break
            if body is None:
                self.send_response(503)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Retry-After", "3")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            cleanup_files(ch)
            self._send(200, "application/vnd.apple.mpegurl", body)
            return

        self._send(404, "text/plain", b"not found")


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def watchdog():
    while True:
        time.sleep(30)
        now = time.time()
        for ch in list(STATE.keys()):
            st = STATE.get(ch)
            if not st:
                continue
            dead = st["proc"].poll() is not None
            idle = now - st["last"] > IDLE_STOP
            if dead or idle:
                try:
                    st["proc"].kill()
                except Exception:
                    pass
                with LOCK:
                    STATE.pop(ch, None)
                if idle:
                    log("[%s] dung ffmpeg (khong ai xem %ds)" % (ch, IDLE_STOP))


def cleanup(*a):
    for ch in list(STATE.keys()):
        st = STATE.get(ch)
        if st:
            try:
                st["proc"].kill()
            except Exception:
                pass
    sys.exit(0)


if __name__ == "__main__":
    try:
        os.makedirs(BASE_DIR, exist_ok=True)
    except Exception:
        pass
    threading.Thread(target=watchdog, daemon=True).start()
    srv = S(("127.0.0.1", PORT), H)
    log("relay HLS: %d kenh DRM, port %d, dir %s" % (len(PINS), PORT, BASE_DIR))
    log("  %s" % ", ".join(sorted(PINS)))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()
