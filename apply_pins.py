#!/usr/bin/env python3
"""
Ap dung PIN OVERRIDE cho 4 kenh the thao trong list.m3u8 / Listphu.m3u8.

Ly do: nguon TV360 (broadpeak) tra segment 404 => man den tren moi player.
Nguon CDN cu cua ON SPORTS (cdnet-cdn-01.onsports.vn) + ClearKey cu van con
song va giai ma duoc => ghi de 4 kenh nay bang nguon cu.

Chay: python3 apply_pins.py
"""
import io, json, os, re, shutil, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PIN_FILE = os.path.join(HERE, "pin_overrides.json")
FILES = ["list.m3u8", "Listphu.m3u8"]


def log(m):
    print("[pin] %s" % m, flush=True)


def load_pins():
    try:
        with io.open(PIN_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log("LOAI doc pin_overrides.json: %s" % e)
        return {}


def relay_conf(pins):
    """Lay host/port cua relay HLS (uu tien env, roi den file config)."""
    host = os.environ.get("RELAY_HOST") or pins.get("_relay_host") or "127.0.0.1"
    port = int(os.environ.get("RELAY_PORT") or pins.get("_relay_port") or 8099)
    return host, port


def relay_url(host, port, tvg):
    """Duong dan relay cho app.

    Cloudflare Quick Tunnel chi ho tro port 80/443 -> dung duong dan /relay/<tvg>.m3u8
    (nginx proxy_pass sang relay 8099). Neu host la IP noi bo thi dung port truc tiep.
    """
    if host.startswith("http://") or host.startswith("https://"):
        host = host.split("://", 1)[1].rstrip("/")
    hostname = host.split(":")[0]
    is_domain = not hostname.replace(".", "").isdigit()
    if is_domain or port in (80, 443):
        # host la domain -> di qua nginx/cloudflare bang duong dan /relay/
        return "http://%s/relay/%s.m3u8" % (host, tvg)
    return "http://%s:%d/%s.m3u8" % (host, port, tvg)


def find_block(lines, tvg):
    """Tra (start, end) cua block kenh co tvg-id = tvg (tu #EXTINF den URL)."""
    for i, ln in enumerate(lines):
        if ln.startswith("#EXTINF") and ('tvg-id="%s"' % tvg) in ln:
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("http"):
                if lines[j].startswith("#EXTINF"):
                    break
                j += 1
            return i, j
    return None, None


def apply_one(lines, tvg, spec, relay_host="127.0.0.1", relay_port=8099):
    i, j = find_block(lines, tvg)
    if i is None:
        log("  %-14s KHONG tim thay trong playlist" % tvg)
        return False, lines

    # GIU NGUYEN EXTINF GOC (tên, group-title, logo, thu tu attribute)
    # Chi thay dong URL + bo KODIPROP ClearKey neu chuyen sang relay.
    is_relay = (spec.get("mode") == "relay")
    ua = spec.get("ua") or "Dalvik/2.1.0"

    mid = []
    for k in range(i + 1, j):
        s = lines[k]
        if is_relay and s.startswith("#KODIPROP"):
            continue                      # bo ClearKey (khong can khi da giai ma)
        mid.append(s)                     # GIU NGUYEN UA + attribute goc
    if not any(s.startswith("#EXTVLCOPT") for s in mid):
        mid.insert(0, "#EXTVLCOPT:http-user-agent=%s" % ua)

    if is_relay:
        # RELAY: HLS thuan (MPEG-TS) da giai ma tren BB2 -> moi app deu phat duoc
        url_line = relay_url(relay_host, relay_port, tvg)
    else:
        key = spec.get("license_key") or ""
        if key:
            mid += [
                "#KODIPROP:inputstream=inputstream.adaptive",
                "#KODIPROP:inputstream.adaptive.manifest_type=dash",
                "#KODIPROP:inputstream.adaptive.license_type=clearkey",
                "#KODIPROP:inputstream.adaptive.license_key=%s" % key,
                "#KODIPROP:inputstream.adaptive.license_key_data_type=base64",
            ]
        url_line = spec["url"]

    out = lines[:i] + [lines[i]] + mid + [url_line] + lines[j + 1:]
    return True, out
    return True, out


def main():
    pins = load_pins()
    if not pins:
        log("khong co pin nao")
        return 1
    for fn in FILES:
        path = os.path.join(HERE, fn)
        if not os.path.exists(path):
            continue
        with io.open(path, encoding="utf-8", newline="") as f:
            raw = f.read()
        crlf = "\r\n" in raw
        lines = raw.replace("\r\n", "\n").split("\n")
        changed = 0
        rh, rp = relay_conf(pins)
        for tvg, spec in pins.items():
            if tvg.startswith("_"):
                continue
            ok, lines = apply_one(lines, tvg, spec, rh, rp)
            if ok:
                changed += 1
                if spec.get("mode") == "relay":
                    log("  %-14s -> RELAY %s" % (tvg, relay_url(rh, rp, tvg)))
                else:
                    log("  %-14s -> %s" % (tvg, spec["url"].split("/onplus/")[-1]))
        if not changed:
            continue
        shutil.copyfile(path, path + ".bak")
        out = "\n".join(lines)
        if crlf:
            out = out.replace("\n", "\r\n")
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(out)
        log("  %s: da ghi de %d kenh" % (fn, changed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
