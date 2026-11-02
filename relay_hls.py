#!/usr/bin/env python3
"""
RELAY GIAI MA -> HLS SACH (chay tren BB2 / Mecool BB2)

Vong doi:
  4 kenh the thao ON SPORTS = MPEG-DASH + ClearKey -> app IPTV khong giai ma -> MAN DEN
  kenh quoc te / noi dia       = HLS thuan            -> app phat duoc

Relay nay dung ffmpeg giai ma ClearKey roi xuat ra HLS (MPEG-TS), y het
kieu cua kenh quoc te => moi app IPTV deu phat duoc.

Link cho app:
  http://<bb2-lan-ip>:8099/onsports.m3u8
  http://<bb2-lan-ip>:8099/onsportsplus.m3u8
  http://<bb2-lan-ip>:8099/onfootball.m3u8
  http://<bb2-lan-ip>:8099/onsportsnews.m3u8
"""
import http.server
import io
import json
import os
import shutil
import signal
import socketserver
import subprocess
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(HERE, "pin_overrides.json")
BASE_DIR = os.environ.get("RELAY_DIR", "/var/lib/hlsrelay")
PORT = int(os.environ.get("RELAY_PORT", 8099))
SEG_SEC = 4
LIST_SIZE = 20
KEEP_FILES = 40          # so segment giu lai tren dia
IDLE_STOP = 300          # tat ffmpeg sau 5 phut khong co client
UA = "Dalvik/2.1.0"
LOG = "/tmp/relay_hls.log"

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
STATE = {}          # ch -> {"proc":Popen, "last":float, "nreq":int}
LOCK = threading.Lock()


def chan_dir(ch):
    return os.path.join(BASE_DIR, ch)


def ffmpeg_cmd(ch, spec, d, pl, seg):
    cmd = [
        "ffmpeg", "-loglevel", "error",
        "-user_agent", UA,
        "-allowed_extensions", "ALL",
        "-reconnect", "1", "-reconnect_streamed", "1",
        "-reconnect_delay_max", "5", "-rw_timeout", "30000000",
        # BO DAU LUONG: 12s. Ly do: ffmpeg vao giua luong nen frame I dau
        # tien hong -> player bao "top block unavailable" / man den.
        "-ss", "12",
        "-i", spec["url"],
    ]
    key = spec.get("license_key")
    if key:
        # PHAI dat SAU -i (input option cua demuxer cenc)
        cmd += ["-cenc_decryption_key", key]
    cmd += [
        # Video: copy (khong transcode - CPU S912 yeu).
        # Audio: transcode AAC 128k/48k stereo. Bat buoc: demuxer TS khong
        # do duoc thong so am thanh khi segment bat dau giua luong, player
        # bao "sample rate not set" -> khong phat duoc.
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
    # xoa file cu (khong xoa thu muc, tranh pha hoang session dang chay)
    for fn in os.listdir(d):
        if fn.endswith(".ts") or fn == "live.m3u8":
            try:
                os.remove(os.path.join(d, fn))
            except Exception:
                pass
    cmd = ffmpeg_cmd(ch, spec, d, pl, seg)
    log("[%s] khoi dong ffmpeg" % ch)
    p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                         stderr=open(os.path.join(d, "ffmpeg.log"), "w"))
    STATE[ch] = {"proc": p, "last": time.time(), "nreq": 0}
    return True, "ok"


def cleanup_files(ch):
    """Giu lai KEEP_FILES segment moi nhat, xoa phan con lai."""
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
            return True, "ok"
        if st:
            try:
                st["proc"].kill()
            except Exception:
                pass
    if ch not in PINS:
        return False, "khong co pin cho %s" % ch
    ok, msg = start_ffmpeg(ch)
    if not ok:
        return False, msg
    # cho ffmpeg tao segment dau tien (co -ss 12 -> can them thoi gian)
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
    """Doc playlist ffmpeg ghi -> sua duong dan segment thanh /<ch>/<seg>.ts"""
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

        # segment: /<ch>/<seg>.ts
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
            ok, msg = ensure_running(ch)
            if not ok:
                self._send(503, "text/plain", msg.encode())
                return
            body = build_playlist(ch)
            if body is None:
                self._send(503, "text/plain", b"segment chua san sang")
                return
            cleanup_files(ch)
            self._send(200, "application/vnd.apple.mpegurl", body)
            return

        self._send(404, "text/plain", b"not found")


class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def watchdog():
    """Tat ffmpeg khi khong ai xem, va khoi dong lai neu process chet."""
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
                log("[%s] dung ffmpeg (%s)" % (ch, reason))
                if dead:
                    # thu khoi dong lai ngay neu client dang yeu cau
                    try:
                        with LOCK:
                            want = STATE.get(ch)
                    except Exception:
                        want = None
                if idle:
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
    for c in sorted(PINS):
        log("  %-14s %s" % (c, PINS[c]["url"][:90]))
    threading.Thread(target=watchdog, daemon=True).start()
    srv = S(("0.0.0.0", PORT), H)
    log("=== RELAY HLS san sang ===")
    srv.serve_forever()
