#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cache key DRM (KID:KEY) cho BB2.

Ly do: endpoint cleankey.php cua vietanhtv chay CHAY CHON (404 xen ke 200).
Neu luc dong bo playlist bi 404 ma khong co key trong playlist -> kenh MAN DEN.
Giai phap: luu key da biet vao /opt/qweb/drm_key_cache.json, dung lai key do
khi fetch that bai (key SCTV da on dinh nhieu ngay).

Dung nhu module:
    import drm_cache
    kk = drm_cache.get(lic_url)        # key da cache hoac None
    drm_cache.put(lic_url, "KID:KEY")  # luu key moi
"""
import io
import json
import os

CACHE = "/opt/qweb/drm_key_cache.json"


def _load():
    try:
        with io.open(CACHE, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(d):
    try:
        tmp = CACHE + ".tmp"
        with io.open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(d, indent=1, sort_keys=True))
        os.rename(tmp, CACHE)
    except Exception:
        pass


def get(lic_url):
    """Tra key da cache cho license URL, hoac None."""
    if not lic_url:
        return None
    return _load().get(lic_url.strip())


def put(lic_url, kidkey):
    """Luu key moi (giu toi da 200 muc)."""
    if not lic_url or not kidkey:
        return
    d = _load()
    d[lic_url.strip()] = kidkey
    if len(d) > 200:
        # cat cac muc cu nhat
        items = list(d.items())[-200:]
        d = dict(items)
    _save(d)


def count():
    return len(_load())
