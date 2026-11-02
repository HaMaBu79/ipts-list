#!/usr/bin/env python3
"""
RELAY GIAI MA -> HLS SACH (chay tren BB2 / Mecool BB2)

Muc tieu: MOI kenh trong playlist deu phat duoc tren MOI thiet bi (app IPTV
thong thuong khong co engine DRM).

  - Kenh VTV / HTV / Dia phuong : HLS thuan                    -> phat duoc san
  - SCTV 15/17/22, ON SPORTS, Quoc te (HBO/CNN/...): MPEG-DASH + ClearKey
                                                          -> app khong giai ma -> MAN DEN
  -> Relay nay dung ffmpeg 8.1 giai ma ClearKey roi xuat ra HLS MPEG-TS (HLS thuan).

Link cho app (moi thiet bi dung chung 1 link):
  http://<host>:8099/<tvg-id>.m3u8
"""
import base64
import http.server
import io
import json
import os
import re
import shutil
import signal
import socketserver
import subprocess
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(HERE, "pin_overrides.json")
BASE_DIR = os.environ.get("RELAY_DIR", "/var/lib/hlsrelay")
PORT = int(os.environ.get("RELAY_PORT", "8099"))
SEG_SEC = 4
LIST_SIZE = 20
KEEP_FILES = 40
IDLE_STOP = 300
UA = "Dalvik/2.1.0"
FFMPEG = os.environ.get("RELAY_FFMPEG", "/opt/ff81/bin/ffmpeg")
LOG = "/tmp/relay_hls.log"

WIDEVINE_SYSID = "edef8ba979d64acea3c827dcd51d21ed"

NAMES = {
    "onsports": "ON SPORTS",
    "onsportsplus": "ON SPORTS+",
    "onfootball": "ON FOOTBALL",
    "onsportsnews": "ON SPORTS NEWS",
}


def log(m):
    s = "%s %s" % (time.strftime("%H:%M:%S"), m)
    print(s, flush=True)
    try:
        with io.open(LOG, "a", encoding="utf-8") as f:
            f.write(s + "\n")
    except Exception:
        pass


def load_pins():
    try:
        with io.open(PIN_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception as e:
        log("LOAI doc pins: %s" % e)
        return {}
    return {k: v for k, v in raw.items()
            if not k.startswith("_") and isinstance(v, dict)}


PINS = load_pins()
STATE = {}
STARTING = set()
LAST_REQ = {}
LOCK = threading.RLock()


# ---------------------------------------------------------------- helpers
def _b64url_to_hex(s):
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    return base64.b64decode(s).hex()


def _http_text(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _http_json(url, timeout=15):
    return json.loads(_http_text(url, timeout))


# ---------------------------------------------------------------- key
def _key_from_pssh_b64(b64):
    """Trich 16 byte key tu 1 pssh base64.

    ClearKey (box 0x22 ben trong pssh data): payload = type(1) size(1) KID(36 ascii) KEY(16)
    Widevine (box 0x25): protobuf, field 3 hoac 4 = string chua key base64
    """
    try:
        raw = base64.b64decode(b64)
    except Exception:
        return None
    if len(raw) < 32 or raw[4:8] != b"pssh":
        return None
    dlen = int.from_bytes(raw[28:32], "big")
    data = raw[32:32 + dlen]

    # --- ClearKey inline (0x22)
    i = 0
    while i + 2 <= len(data):
        btype = data[i]
        bsize = data[i + 1]
        if bsize == 0x24 and btype == 0x22 and i + 54 <= len(data):
            key = data[i + 38:i + 54]
            if key != b"\x00" * 16:
                return key.hex()
        i += 1

    # --- Widevine protobuf
    i = 0
    while i + 2 <= len(data):
        field = data[i]
        wt = field & 0x07
        i += 1
        if wt == 0:
            continue
        ln = data[i]
        i += 1
        val = data[i:i + ln]
        i += ln
        if (field >> 3) in (3, 4) and wt == 2 and len(val) >= 20:
            try:
                kb = base64.b64decode(val.rstrip(b"\x00") + b"==")
                if len(kb) >= 16:
                    return kb[:16].hex()
            except Exception:
                pass
    return None


def _key_from_mpd(mpd_url):
    """Trich key tu pssh trong MPD (uu tien Widevine, bo PlayReady)."""
    try:
        txt = _http_text(mpd_url, 20)
    except Exception as e:
        log("  LOAI tai MPD: %s" % e)
        return None
    for b64 in re.findall(r"<cenc:pssh>([^<]+)</cenc:pssh>", txt):
        try:
            raw = base64.b64decode(b64)
        except Exception:
            continue
        if raw[4:8] != b"pssh":
            continue
        if raw[12:28].hex() != WIDEVINE_SYSID:
            continue
        k = _key_from_pssh_b64(b64)
        if k:
            return k
    return None


def _key_from_hls(url):
    """Tim key trong EXT-X-KEY cua media playlist HLS (pssh base64)."""
    try:
        txt = _http_text(url, 20)
    except Exception:
        return None
    for b64 in re.findall(r"base64,([A-Za-z0-9+/=]+)", txt):
        k = _key_from_pssh_b64(b64)
        if k:
            return k
    return None


def resolve_key(spec, url=None):
    """Tra về hex key 32 ky tu (16 byte) cho ffmpeg.

    spec co the la LIST (nhieu nguon khoa) -> thu lan luot, lay nguon dau tien
    ra key hop le.
    """
    if not spec:
        return None
    if isinstance(spec, (list, tuple)):
        for one in spec:
            k = resolve_key(one, url)
            if k:
                return k
        return None
    spec = str(spec).strip()

    if spec == "auto":
        if not url:
            return None
        k = _key_from_mpd(url) if url.endswith(".mpd") else None
        if not k:
            k = _key_from_hls(url)
        if k:
            log("  key tu pssh: %s" % k)
        return k

    if spec.startswith("http"):
        try:
            j = _http_json(spec)
            k = (j.get("keys") or [])[0]
            kid = _b64url_to_hex(k.get("kid", ""))
            key = k.get("k", "")
            if len(key) == 32 and all(c in "0123456789abcdefABCDEF" for c in key):
                keyhex = key.lower()
            else:
                keyhex = _b64url_to_hex(key)
            log("  key tu server: kid=%s key=%s" % (kid[:12], keyhex))
            return keyhex
        except Exception as e:
            log("  LOAI lay key: %s" % e)
            return None

    if spec.startswith("{"):
        try:
            j = json.loads(spec)
            k = (j.get("keys") or [])[0]
            kid = _b64url_to_hex(k.get("kid", ""))
            key = k.get("k", "")
            if len(key) == 32 and all(c in "0123456789abcdefABCDEF" for c in key):
                keyhex = key.lower()
            else:
                keyhex = _b64url_to_hex(key)
            log("  key tu json: kid=%s key=%s" % (kid[:12], keyhex))
            return keyhex
        except Exception as e:
            log("  LOAI parse json key: %s" % e)
            return None

    if ":" in spec:
        _kid, key = spec.split(":", 1)
        key = key.strip()
        if len(key) == 32 and all(c in "0123456789abcdefABCDEF" for c in key):
            return key.lower()
        try:
            return _b64url_to_hex(key)
        except Exception:
            return None
    return None


def refresh_key(ch):
    """Lay lai key moi (cho case 'temporary' xoay vong)."""
    spec = PINS[ch].get("license_key")
    if not spec:
        return None
    keyhex = resolve_key(spec, PINS[ch].get("url"))
    if keyhex:
        try:
            with io.open(os.path.join(BASE_DIR, ch + ".key"), "w", encoding="utf-8") as f:
                f.write(keyhex)
        except Exception:
            pass
    return keyhex


# ---------------------------------------------------------------- ffmpeg
def chan_dir(ch):
    return os.path.join(BASE_DIR, ch)


def ffmpeg_cmd(ch, spec, d, pl, seg, keyhex):
    cmd = [FFMPEG, "-loglevel", "error",
           "-user_agent", UA,
           "-allowed_extensions", "ALL",
           "-protocol_whitelist", "file,http,https,tcp,tls,crypto,data",
           "-reconnect", "1", "-reconnect_streamed", "1",
           "-reconnect_delay_max", "5", "-rw_timeout", "30000000",
           "-ss", "8",
           "-i", spec["url"]]
    if keyhex:
        # option cua demuxer mp4/cenc -> phai dat TRUOC -i
        cmd += ["-cenc_decryption_key", keyhex]
    cmd += [
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "128k", "-ac", "2", "-ar", "48000",
        "-fflags", "+genpts",
        "-avoid_negative_ts", "make_zero",
        "-f", "hls",
        "-hls_time", str(SEG_SEC),
        "-hls_list_size", str(LIST_SIZE),
        "-hls_flags", "append_list+omit_endlist+independent_segments+temp_file",
        "-hls_segment_filename", seg,
        pl,
    ]
    return cmd


def start_ffmpeg(ch):
    spec = PINS[ch]
    d = chan_dir(ch)
    os.makedirs(d, exist_ok=True)
    pl = os.path.join(d, "live.m3u8")
    seg = os.path.join(d, "seg%03d.ts")
    for fn in os.listdir(d):
        if fn.endswith(".ts") or fn == "live.m3u8":
            try:
                os.remove(os.path.join(d, fn))
            except Exception:
                pass
    keyhex = refresh_key(ch)
    cmd = ffmpeg_cmd(ch, spec, d, pl, seg, keyhex)
    log("[%s] khoi dong ffmpeg (key=%s)" % (ch, (keyhex or "NONE")[:20]))
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=open(os.path.join(d, "ffmpeg.log"), "w"))
    STATE[ch] = {"proc": p, "last": time.time(), "nreq": 0, "key": keyhex}
    return True, "ok"


def cleanup_files(ch):
    d = chan_dir(ch)
    try:
        files = sorted([f for f in os.listdir(d) if f.endswith(".ts")])
    except Exception:
        return
    for f in files[:-KEEP_FILES]:
        try:
            os.remove(os.path.join(d, f))
        except Exception:
            pass


def ensure_running(ch):
    with LOCK:
        st = STATE.get(ch)
        if st and st["proc"].poll() is None:
            st["last"] = time.time()
            st["nreq"] += 1
            LAST_REQ[ch] = time.time()
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

    ok, msg = start_ffmpeg(ch)
    with LOCK:
        STARTING.discard(ch)
    if not ok:
        return False, msg

    pl = os.path.join(chan_dir(ch), "live.m3u8")
    for _ in range(90):
        time.sleep(1)
        if os.path.exists(pl):
            try:
                if io.open(pl, encoding="utf-8").read().count(".ts") >= 3:
                    log("[%s] san sang" % ch)
                    return True, "ok"
            except Exception:
                pass
        st = STATE.get(ch)
        if not st or st["proc"].poll() is not None:
            return False, "ffmpeg tho ngay"
    return False, "timeout"


def build_playlist(ch):
    pl = os.path.join(chan_dir(ch), "live.m3u8")
    if not os.path.exists(pl):
        return None
    try:
        txt = io.open(pl, encoding="utf-8").read()
    except Exception:
        return None
    out = []
    n = 0
    for ln in txt.split("\n"):
        s = ln.strip()
        if not s:
            continue
        if not s.startswith("#") and s.endswith(".ts"):
            if not os.path.exists(os.path.join(chan_dir(ch), s)):
                continue
            out.append("/%s/%s" % (ch, s))
            n += 1
        else:
            out.append(s)
    if n == 0:
        return None
    return ("\n".join(out) + "\n").encode("utf-8")


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
            ensure_running(ch)
            body = build_playlist(ch)
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
                reason = "ffmpeg chet" if dead else "khong ai xem %ds" % IDLE_STOP
                try:
                    st["proc"].kill()
                except Exception:
                    pass
                with LOCK:
                    STATE.pop(ch, None)
                if dead:
                    if now - LAST_REQ.get(ch, 0) < 60:
                        log("[%s] %s -> khoi dong lai" % (ch, reason))
                        try:
                            start_ffmpeg(ch)
                        except Exception as e:
                            log("[%s] khoi dong lai that bai: %s" % (ch, e))
                    else:
                        log("[%s] %s (khong ai xem)" % (ch, reason))
                        shutil.rmtree(chan_dir(ch), ignore_errors=True)


def cleanup(*a):
    for st in STATE.values():
        try:
            st["proc"].kill()
        except Exception:
            pass
    os._exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    os.makedirs(BASE_DIR, exist_ok=True)
    log("=== RELAY HLS start port %d ===" % PORT)
    log("    ffmpeg: %s" % FFMPEG)
    log("    kenh: %d" % len(PINS))
    for c in sorted(PINS):
        log("  %-16s %s" % (c, PINS[c]["url"][:88]))
    threading.Thread(target=watchdog, daemon=True).start()
    srv = S(("0.0.0.0", PORT), H)
    log("=== RELAY HLS san sang ===")
    srv.serve_forever()
