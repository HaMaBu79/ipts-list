# -*- coding: utf-8 -*-
"""Module TV360 cho qweb.

Chuc nang:
  1. Dang nhap bang OTP (SMS) va giu token (access-token / refresh-token) tren dia.
  2. Tu dong gia han token (refresh) de phuc vu 24/7.
  3. Lay link phat m3u8 truc tiep tu TV360 cho tung kenh.

Khong dung thu vien ngoai ngoai tru cryptography (da co san tren may).
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import http.cookiejar
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

WEB = "https://tv360.vn"
API = "https://api.tv360.vn"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Khoa ma hoa dung chung cho web (lay tu runtimeConfig cua trang chu TV360).
E2E_KEY = "eNdtOeNDeNcRyPteDsCREt#2022"

STATE_FILE = os.environ.get("TV360_STATE", "/opt/qweb/tv360_session.json")

COOKIE_ACCESS = "access-token"
COOKIE_REFRESH = "refresh-token"
COOKIE_USER = "user-id"
COOKIE_DEVICE = "device-id"
COOKIE_MSISDN = "msisdn"
COOKIE_ACCOUNT = "account-id"
COOKIE_PROFILE = "profile"


# --------------------------------------------------------------------- tien ich
def _unpad(b: bytes) -> bytes:
    pad = b[-1]
    if 1 <= pad <= 16 and b[-pad:] == bytes([pad]) * pad:
        return b[:-pad]
    return b


def decrypt_aes(b64data: str, secret: str = E2E_KEY) -> str:
    """Giai ma chuoi CryptoJS encryptAES: AES-128-ECB, key = SHA1(secret).hex[:32]."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = bytes.fromhex(hashlib.sha1(secret.encode()).hexdigest()[:32])
    raw = base64.b64decode(b64data)
    c = Cipher(algorithms.AES(key), modes.ECB()).decryptor()
    return _unpad(c.update(raw) + c.finalize()).decode("utf-8", "replace")


def _mask(text: str, keep: int = 40) -> str:
    """Che token khi in ra log."""
    if not text:
        return ""
    if len(text) <= keep:
        return "*" * len(text)
    return text[:8] + "..." + text[-6:] + " (len=%d)" % len(text)


# --------------------------------------------------------------------- HTTP
class Tv360Client:
    """Client TV360 giu cookie + token, tu gia han token khi can."""

    def __init__(self, state_file: str = STATE_FILE):
        self.state_file = state_file
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        self._lock = threading.Lock()
        self._device_id = ""
        self._session_id = ""
        self.access_token = ""
        self.refresh_token = ""
        self.user_id = ""
        self.msisdn = ""
        self.expires_at = 0
        self._load_state()

    # ---------- headers ----------
    def _headers(self, extra=None, auth=True):
        now = int(time.time() * 1000)
        h = {
            "User-Agent": UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Encoding": "gzip, deflate",
            "Accept-Language": "vi,en;q=0.9",
            "Origin": WEB,
            "Referer": WEB + "/",
            "Content-Type": "application/json",
            "lang": "vi",
            "osapptype": "web",
            "osappversion": "2.0.0",
            "sessionid": self._session_id or str(uuid.uuid4()),
            "deviceid": self._device_id or str(uuid.uuid4()),
            "devicetype": "Chrome",
            "devicename": "Chrome",
            "resolution": "1920x1080",
            "requestid": "web_%d_%s" % (now, uuid.uuid4().hex[:8]),
            "tv360transid": "%d_%s" % (now, uuid.uuid4().hex[:8]),
        }
        if auth and self.access_token:
            h["Authorization"] = "Bearer " + self.access_token
        if extra:
            h.update(extra)
        return h

    def _call(self, url, body=None, extra=None, auth=True, timeout=25):
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url, data=data, headers=self._headers(extra, auth),
            method="POST" if data else "GET")
        try:
            with self.opener.open(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return r.status, raw
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                if e.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
            except Exception:
                pass
            return e.code, raw
        except Exception as e:
            return 0, str(e).encode()

    def _cookie(self, name):
        for c in self.jar:
            if c.name == name:
                return c.value
        return ""

    # ---------- state ----------
    def _load_state(self):
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            return
        self.access_token = st.get("access_token") or ""
        self.refresh_token = st.get("refresh_token") or ""
        self.user_id = st.get("user_id") or ""
        self.msisdn = st.get("msisdn") or ""
        self.expires_at = st.get("expires_at") or 0
        self._device_id = st.get("device_id") or ""
        self._session_id = st.get("session_id") or ""

    def _save_state(self):
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "user_id": self.user_id,
            "msisdn": self.msisdn,
            "expires_at": self.expires_at,
            "device_id": self._device_id,
            "session_id": self._session_id,
            "saved_at": int(time.time()),
        }
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_file)

    def _capture_tokens(self):
        self.access_token = self._cookie(COOKIE_ACCESS) or self.access_token
        self.refresh_token = self._cookie(COOKIE_REFRESH) or self.refresh_token
        self.user_id = self._cookie(COOKIE_USER) or self.user_id
        self.msisdn = self._cookie(COOKIE_MSISDN) or self.msisdn
        self._device_id = self._cookie(COOKIE_DEVICE) or self._device_id
        self._session_id = self._cookie("session-id") or self._session_id

    # ---------- public API ----------
    def ping(self) -> bool:
        """Tao device-id/session-id cookie (bat buoc truocc khi login)."""
        self._call(WEB + "/api/ping",
                   {"deviceInfo": {"deviceId": str(uuid.uuid4())}},
                   auth=False)
        self._capture_tokens()
        self._save_state()
        return bool(self._device_id)

    def request_otp(self, msisdn: str) -> dict:
        """Yeu cau gui OTP qua SMS. Tra {ok, message}."""
        msisdn = msisdn.strip().replace(" ", "").replace(".", "")
        if not msisdn.startswith("0"):
            msisdn = "0" + msisdn
        st, raw = self._call(WEB + "/public/v1/auth/get-otp-login",
                             {"msisdn": msisdn}, auth=False)
        try:
            j = json.loads(raw)
        except Exception:
            return {"ok": False, "message": "HTTP %s" % st}
        return {"ok": j.get("errorCode") == 200,
                "message": j.get("message"),
                "ttl": (j.get("data") or {}).get("ttl")}

    def login_otp(self, msisdn: str, otp: str) -> dict:
        """Dang nhap bang OTP. Neu thanh cong se luu token vao file."""
        msisdn = msisdn.strip().replace(" ", "").replace(".", "")
        if not msisdn.startswith("0"):
            msisdn = "0" + msisdn
        if not self._device_id:
            self.ping()
        st, raw = self._call(WEB + "/public/v1/auth/login",
                             {"msisdn": msisdn, "otp": otp,
                              "grantType": "OTP",
                              "deviceInfo": {"osType": "WEB",
                                             "osVersion": "131.0",
                                             "deviceId": self._device_id,
                                             "screenSize": ""}},
                             auth=False)
        try:
            j = json.loads(raw)
        except Exception:
            return {"ok": False, "message": "HTTP %s" % st}
        self._capture_tokens()
        if j.get("errorCode") == 200 and self.access_token:
            self.expires_at = int(time.time()) + 3600
            self._save_state()
            return {"ok": True, "message": j.get("message"),
                    "user_id": self.user_id,
                    "msisdn": self.msisdn,
                    "access_token": _mask(self.access_token)}
        return {"ok": False, "message": j.get("message") or "login that bai"}

    def login_password(self, msisdn: str, password: str) -> dict:
        """Dang nhap bang mat khau (neu tai khoan co mat khau)."""
        msisdn = msisdn.strip().replace(" ", "").replace(".", "")
        if not msisdn.startswith("0"):
            msisdn = "0" + msisdn
        if not self._device_id:
            self.ping()
        st, raw = self._call(WEB + "/public/v1/auth/login?remember=1",
                             {"msisdn": msisdn, "password": password,
                              "grantType": "PASS",
                              "deviceInfo": {"osType": "WEB",
                                             "osVersion": "131.0",
                                             "deviceId": self._device_id,
                                             "screenSize": ""}},
                             auth=False)
        try:
            j = json.loads(raw)
        except Exception:
            return {"ok": False, "message": "HTTP %s" % st}
        self._capture_tokens()
        if j.get("errorCode") == 200 and self.access_token:
            self.expires_at = int(time.time()) + 3600
            self._save_state()
            return {"ok": True, "message": j.get("message"),
                    "user_id": self.user_id, "msisdn": self.msisdn}
        return {"ok": False, "message": j.get("message") or "login that bai"}

    def refresh(self) -> bool:
        """Gia han access-token. Tra True neu thanh cong."""
        with self._lock:
            if not self.refresh_token:
                return False
            st, raw = self._call(WEB + "/public/v1/auth/refresh-token",
                                 {"deviceId": self._device_id,
                                  "refreshToken": self.refresh_token},
                                 auth=False)
            try:
                j = json.loads(raw)
            except Exception:
                return False
            old = self.access_token
            self._capture_tokens()
            if j.get("errorCode") == 200 and self.access_token:
                self.expires_at = int(time.time()) + 3600
                self._save_state()
                return True
            # refresh fail -> reset
            if j.get("errorCode") in (401, 412, 417):
                self.access_token = ""
                self.refresh_token = ""
                self._save_state()
            return False

    def ensure_token(self) -> bool:
        """Dam bao co access-token con hieu luc."""
        if self.access_token and self.expires_at - int(time.time()) > 300:
            return True
        if self.refresh_token and self.refresh():
            return True
        return False

    def get_stream(self, channel_id, timeout=25):
        """Tra (url_m3u8, ten_kenh) hoac (None, ly_do).

        Kenh mien phi lay duoc khong can dang nhap.
        Kenh tra phi se tra ve thong bao 'can dang nhap' -> can goi ensure_token() truoc.
        """
        ts = int(time.time())
        q = urllib.parse.urlencode({"id": int(channel_id), "type": "live",
                                    "secured": "true", "t": ts})
        st, raw = self._call(WEB + "/public/v1/composite/get-link?" + q,
                             timeout=timeout)
        if st != 200:
            return None, "HTTP %s" % st
        try:
            j = json.loads(raw)
        except Exception:
            return None, "JSON loi"
        if j.get("errorCode") != 200:
            msg = j.get("message") or ("errorCode %s" % j.get("errorCode"))
            return None, msg
        blob = j.get("data")
        if not blob:
            return None, "data rong"
        try:
            info = json.loads(decrypt_aes(blob))
        except Exception as e:
            return None, "giai ma loi: %s" % e
        url = info.get("urlStreaming")
        if not url:
            return None, "khong co urlStreaming"
        ov = info.get("overlay") or {}
        return url, ov.get("name") or ""

    def list_channels(self, box_id=1, timeout=25):
        """Tra danh sach muc trong box 'Truyen hinh'."""
        q = urllib.parse.urlencode({"id": box_id, "page": 1, "size": 200})
        st, raw = self._call(API + "/public/v1/live/get-list-channel?" + q,
                             timeout=timeout)
        if st != 200:
            return []
        try:
            j = json.loads(raw)
        except Exception:
            return []
        return ((j.get("data") or {}).get("content") or [])

    def catalog(self, timeout=30):
        """Tra danh sach TAT CA kenh TV360, kem nhom.

        Dung /public/v1/composite/get-home-live -> moi 'box' la mot nhom kenh
        (Kenh VTV, Kenh HTV, Kenh VTV Cab, Kenh SCTV, Kenh Vĩnh Long, ...).
        """
        q = urllib.parse.urlencode({"offset": 0, "limit": 100})
        st, raw = self._call(API + "/public/v1/composite/get-home-live?" + q,
                             timeout=timeout)
        if st != 200:
            return []
        try:
            boxes = json.loads(raw).get("data") or []
        except Exception:
            return []
        out = []
        for box in boxes:
            if not isinstance(box, dict):
                continue
            if str(box.get("type")) != "LIVE":
                continue
            gname = box.get("name") or ""
            for ch in (box.get("content") or []):
                if not isinstance(ch, dict):
                    continue
                if str(ch.get("type")) != "LIVE":
                    continue
                out.append({
                    "id": ch.get("id"),
                    "name": ch.get("name") or "",
                    "slug": ch.get("slug") or "",
                    "group": gname,
                    "is_free": ch.get("isFree"),
                    "need_login": ch.get("needLogin"),
                    "is_drm": ch.get("isDrm"),
                    "price": ch.get("price"),
                    "logo": ch.get("coverImage") or "",
                    "tv360_page": ch.get("link") or "",
                })
        return out

    def status(self):
        return {
            "logged_in": bool(self.access_token),
            "msisdn": self.msisdn,
            "user_id": self.user_id,
            "access_token": _mask(self.access_token),
            "refresh_token": _mask(self.refresh_token),
            "expires_at": self.expires_at,
            "expires_in": max(0, self.expires_at - int(time.time())),
            "device_id": self._device_id,
        }


# --------------------------------------------------------------------- global
_client = None
_cl_lock = threading.Lock()


def client() -> Tv360Client:
    global _client
    with _cl_lock:
        if _client is None:
            _client = Tv360Client()
        return _client


if __name__ == "__main__":
    import sys
    c = client()
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        print(json.dumps(c.status(), ensure_ascii=False, indent=1))
    elif len(sys.argv) > 2 and sys.argv[1] == "stream":
        url, name = c.get_stream(sys.argv[2])
        print("name:", name)
        print("url :", url)
    elif len(sys.argv) > 2 and sys.argv[1] == "otp":
        print(json.dumps(c.request_otp(sys.argv[2]), ensure_ascii=False))
    elif len(sys.argv) > 3 and sys.argv[1] == "login":
        print(json.dumps(c.login_otp(sys.argv[2], sys.argv[3]), ensure_ascii=False))
    else:
        print("dung: tv360.py status | otp <msisdn> | login <msisdn> <otp> | stream <id>")
