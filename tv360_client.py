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

# Duong dan luu session. Mac dinh: cung thu muc voi file nay (de chay
# duoc tren GitHub Actions / noi bat ky), chi dung /opt/qweb khi thu muc do
# ton tai.
_HERE = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_STATE = ("/opt/qweb/tv360_session.json"
                  if os.path.isdir("/opt/qweb")
                  else os.path.join(_HERE, "tv360_session.json"))
STATE_FILE = os.environ.get("TV360_STATE", _DEFAULT_STATE)

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
        self._signed_device = ""
        self._signed_refresh = ""
        self.access_token = ""
        self.refresh_token = ""
        self.user_id = ""
        self.msisdn = ""
        self.account_id = ""
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
        self.account_id = st.get("account_id") or ""
        self.expires_at = st.get("expires_at") or 0
        # Token co han nhung file state khong luu expires_at (vd secrets
        # GitHub Actions) -> tu parse 'exp' tu JWT.
        if self.access_token and not self.expires_at:
            self.expires_at = self._jwt_exp(self.access_token) or 0
        self._device_id = st.get("device_id") or ""
        self._session_id = st.get("session_id") or ""
        self._signed_device = st.get("signed_device") or ""
        self._signed_refresh = st.get("signed_refresh") or ""
        self._restore_cookies()

    def _restore_cookies(self):
        """Khoi phuc cookie jar tu state file.

        API `refresh-token` yeu cau cookie phien dang nhap (device-id,
        refresh-token, session-id) gui kem request. Khong co cookie jar
        moi, client khong the gia han token duoc.
        """
        for name, val in (("device-id", self._signed_device or self._device_id),
                          ("refresh-token", self._signed_refresh),
                          ("session-id", self._session_id),
                          ("access-token", self.access_token),
                          ("user-id", self.user_id),
                          ("msisdn", self.msisdn),
                          ("account-id", self.account_id)):
            if not val:
                continue
            ck = http.cookiejar.Cookie(
                version=0, name=name, value=val, port=None, port_specified=False,
                domain="tv360.vn", domain_specified=True, domain_initial_dot=False,
                path="/", path_specified=True, secure=False, expires=None,
                discard=True, comment=None, comment_url=None, rest={})
            try:
                self.jar.set_cookie(ck)
            except Exception:
                pass

    def _save_state(self):
        data = {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "user_id": self.user_id,
            "msisdn": self.msisdn,
            "account_id": self.account_id,
            "expires_at": self.expires_at,
            "device_id": self._device_id,
            "session_id": self._session_id,
            "signed_device": self._signed_device,
            "signed_refresh": self._signed_refresh,
            "saved_at": int(time.time()),
        }
        tmp = self.state_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_file)

    def _capture_tokens(self):
        # Cookie TV360 dang `s%3A<signed>` (express-session signed) -> KHONG
        # phai la access-token/refresh-token that. Token that nam trong body.
        # Signed cookie `device-id` + `refresh-token` la thu API refresh-token
        # doi voi `refresh-token` thu.
        def tok(name):
            v = self._cookie(name)
            if v.startswith("s%3A") or v.startswith("s:"):
                return ""
            return v

        self.access_token = tok(COOKIE_ACCESS) or self.access_token
        self.refresh_token = tok(COOKIE_REFRESH) or self.refresh_token
        # `user-id` / `msisdn` cookie cung bi signed -> gia tri that chi lay
        # tu body (`data.userId`, `data.msisdn`).
        self._device_id = self._cookie(COOKIE_DEVICE) or self._device_id
        self._session_id = self._cookie("session-id") or self._session_id
        sd = self._cookie(COOKIE_DEVICE)
        if sd:
            self._signed_device = sd
        sr = self._cookie(COOKIE_REFRESH)
        if sr:
            self._signed_refresh = sr

    def _capture_body(self, j: dict) -> dict:
        """Lay token tu body JSON (`data.accessToken` / `data.refreshToken`).

        Cookie cua TV360 co dang ky `s%3A...` nen khong dung lam Bearer token.
        Body moi chua token that.
        """
        d = j.get("data") or {}
        if not isinstance(d, dict):
            return {}
        tok = d.get("accessToken")
        if tok:
            self.access_token = tok
        rt = d.get("refreshToken")
        if rt:
            self.refresh_token = rt
        if d.get("userId") is not None:
            self.user_id = str(d.get("userId"))
        if d.get("msisdn"):
            self.msisdn = str(d.get("msisdn"))
        if d.get("accountId"):
            self.account_id = str(d.get("accountId"))
        return d

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

    def login_otp(self, msisdn: str, otp: str,
                  prefer_username: str = "") -> dict:
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
        self._capture_body(j)
        if j.get("errorCode") == 200 and self.access_token:
            self.expires_at = int(time.time()) + 3600
            self._save_state()
            return {"ok": True, "message": j.get("message"),
                    "user_id": self.user_id,
                    "msisdn": self.msisdn,
                    "access_token": _mask(self.access_token)}
        # 207: so dien thoai co nhieu tai khoan -> chon tai khoan
        if j.get("errorCode") == 207:
            sec = (j.get("data") or {}).get("secretToken")
            users = (j.get("data") or {}).get("listUser") or []
            if sec and users:
                r = self.login_multi_account(sec, users, prefer_username)
                if r.get("ok"):
                    return r
                return {"ok": False, "message": r.get("message"),
                        "accounts": r.get("detail", [])}
        return {"ok": False, "message": j.get("message") or "login that bai",
                "error_code": j.get("errorCode")}

    def select_login_user(self, secret_token: str, user_id: int,
                          username: str) -> dict:
        """Chon 1 tai khoan sau khi OTP/PASS tra ve errorCode 207.

        Token that nam trong `data.accessToken` (khong phai cookie).
        Neu tai khoan vuot gioi han thiet bi (422/H-150) server tra them
        `data.authToken` -> dung token nay de go thiet bi cu.
        """
        st, raw = self._call(WEB + "/public/v1/auth/validate-login-user",
                             {"userId": user_id, "secretToken": secret_token,
                              "username": username}, auth=False)
        try:
            j = json.loads(raw)
        except Exception:
            return {"ok": False, "error_code": st, "message": "HTTP %s" % st}
        d = self._capture_body(j)
        if d.get("accessToken"):
            self._capture_tokens()
            exp = self._jwt_exp(self.access_token)
            self.expires_at = exp or int(time.time()) + 3600
            # body la nguon chinh xac cho user_id/msisdn (cookie bi signed)
            self.user_id = str(d.get("userId") or self.user_id)
            self.msisdn = d.get("msisdn") or self.msisdn
            self.account_id = d.get("accountId") or self.account_id
            self._save_state()
            return {"ok": True, "message": j.get("message") or "Thanh cong",
                    "user_id": self.user_id, "msisdn": self.msisdn,
                    "access_token": _mask(self.access_token)}
        return {"ok": False, "error_code": j.get("errorCode"),
                "message": j.get("message") or "login that bai",
                "auth_token": d.get("authToken")}

    def list_devices(self, secret_token: str) -> list:
        """Danh sach thiet bi da dang nhap (dung 'secretToken' cua buoc OTP)."""
        st, raw = self._call(WEB + "/public/v1/auth/get-list-devices",
                             {"authToken": secret_token}, auth=False)
        try:
            j = json.loads(raw)
        except Exception:
            return []
        data = j.get("data")
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in ("devices", "listDevice", "list", "items"):
                if isinstance(data.get(k), list):
                    return data[k]
        return []

    def remove_devices(self, secret_token: str, device_ids: list) -> dict:
        """Dang xuat thiet bi. `deviceIds` phai la CHUOI id nối dau phay
        (web dung `.map(x => x.id).join(",")`)."""
        ids = ",".join(str(i) for i in device_ids)
        st, raw = self._call(WEB + "/public/v1/auth/remove-device",
                             {"deviceIds": ids, "authToken": secret_token},
                             auth=False)
        try:
            return json.loads(raw)
        except Exception:
            return {"errorCode": st,
                    "message": raw[:200].decode("utf-8", "replace")}

    def login_multi_account(self, secret_token: str, users: list,
                            prefer_username: str = "") -> dict:
        """Thu chon tung tai khoan, xu ly loi gioi han thiet bi (422/H-150).

        `prefer_username` (vd "vt093208646859") -> uu tien tai khoan do truoc,
        tranh chon nham tai khoan khac cung so dien thoai.
        """
        detail = []
        # Uu tien tai khoan yeu thich
        if prefer_username:
            pref = [u for u in users
                    if prefer_username in str(u.get("username") or "")]
            rest = [u for u in users if u not in pref]
            users = pref + rest
        for u in users:
            uid = u.get("id")
            uname = u.get("username")
            r = self.select_login_user(secret_token, uid, uname)
            if r.get("ok"):
                return {"ok": True, "message": "Dang nhap thanh cong (%s)" % uname,
                        "user_id": self.user_id, "msisdn": self.msisdn,
                        "access_token": _mask(self.access_token)}
            code = r.get("error_code")
            detail.append({"username": uname, "code": code,
                           "message": r.get("message") or ""})
            if code == 422:
                # server gui kem `authToken` -> dung no de go thiet bi cu
                auth_token = r.get("auth_token") or secret_token
                devs = self.list_devices(auth_token)
                if devs:
                    # Xoa TAT CA device cu (khong phai device dang dung).
                    # Chi xoa 1 device se van fail neu con > 1 slot bi chiem.
                    ids = [d.get("id") for d in devs
                           if d.get("id") is not None
                           and d.get("deviceId") != self._device_id]
                    if not ids:
                        ids = [d.get("id") for d in devs
                               if d.get("id") is not None]
                    rm = self.remove_devices(auth_token, ids)
                    if rm.get("errorCode") == 200:
                        # Server hay tra 502 ngay sau khi xoa device ->
                        # thu lai vai lan (co delay nho).
                        for attempt in range(4):
                            if attempt:
                                time.sleep(2 + attempt * 2)
                            r = self.select_login_user(secret_token, uid, uname)
                            if r.get("ok"):
                                return {"ok": True,
                                        "message": "Dang nhap thanh cong (%s)" % uname,
                                        "user_id": self.user_id, "msisdn": self.msisdn,
                                        "access_token": _mask(self.access_token)}
                            # van 422 -> xoa them device con lai roi thu lai
                            if r.get("error_code") == 422 and attempt < 2:
                                devs2 = self.list_devices(
                                    r.get("auth_token") or auth_token)
                                ids2 = [x.get("id") for x in devs2
                                        if x.get("id") is not None
                                        and x.get("deviceId") != self._device_id]
                                if ids2:
                                    self.remove_devices(auth_token, ids2)
                        detail[-1]["code"] = r.get("error_code")
                        detail[-1]["retry"] = r.get("message")
        return {"ok": False, "message": "Khong tai khoan nao dang nhap duoc",
                "detail": detail}

    def login_password(self, msisdn: str, password: str,
                       prefer_username: str = "") -> dict:
        """Dang nhap bang mat khau (neu tai khoan co mat khau).

        `prefer_username` -> uu tien tai khoan con so (vd "vt093208646859")
        khi so dien thoai co nhieu tai khoan.
        """
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
        self._capture_body(j)
        if j.get("errorCode") == 200 and self.access_token:
            self.expires_at = int(time.time()) + 3600
            self._save_state()
            return {"ok": True, "message": j.get("message"),
                    "user_id": self.user_id, "msisdn": self.msisdn}
        # 207: so dien thoai co nhieu tai khoan -> chon tai khoan
        if j.get("errorCode") == 207:
            sec = (j.get("data") or {}).get("secretToken")
            users = (j.get("data") or {}).get("listUser") or []
            if sec and users:
                r = self.login_multi_account(sec, users, prefer_username)
                if r.get("ok"):
                    return r
                return {"ok": False, "message": r.get("message"),
                        "accounts": r.get("detail", [])}
        return {"ok": False, "message": j.get("message") or "login that bai",
                "error_code": j.get("errorCode")}

    @staticmethod
    def _jwt_exp(token: str) -> int:
        """Doc `exp` tu JWT (khong verify chu ky, chi de lay han)."""
        try:
            p = token.split(".")[1]
            p += "=" * (-len(p) % 4)
            return int(json.loads(base64.urlsafe_b64decode(p)).get("exp") or 0)
        except Exception:
            return 0

    def refresh(self) -> bool:
        """Gia han access-token. Tra True neu thanh cong.

        TV360 yeu cau `deviceId` va `refreshToken` la BAN DA KY (signed cookie
        dang `s%3A<value>.<sig>`) - khong phai gia tri thu.
        """
        with self._lock:
            if not self.refresh_token:
                return False
            dev = self._signed_device or self._device_id
            rt = self._signed_refresh or self.refresh_token
            st, raw = self._call(WEB + "/public/v1/auth/refresh-token",
                                 {"deviceId": dev,
                                  "refreshToken": rt},
                                 auth=False)
            try:
                j = json.loads(raw)
            except Exception:
                return False
            old = self.access_token
            self._capture_tokens()
            self._capture_body(j)
            if j.get("errorCode") == 200 and self.access_token:
                exp = self._jwt_exp(self.access_token)
                self.expires_at = exp or int(time.time()) + 3600
                self._save_state()
                return True
            # refresh fail -> reset
            if j.get("errorCode") in (401, 412, 417):
                self.access_token = ""
                self.refresh_token = ""
                self._signed_refresh = ""
                self._save_state()
            return False

    def auto_login(self) -> dict:
        """Dang nhap lai tu dong (khong can SMS) qua kenh Viettel 4G/5G.

        Chi hoat dong khi dang ky 'Auto Login' tren app TV360.
        Server dung khi refresh-token het han.
        """
        if not self.msisdn:
            return {"ok": False, "message": "chua co so dien thoai"}
        if not self._device_id:
            self.ping()
        st, raw = self._call(WEB + "/public/v1/auth/auto-login-http",
                             {"msisdn": self.msisdn,
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
        self._capture_body(j)
        if j.get("errorCode") == 200 and self.access_token:
            self.expires_at = int(time.time()) + 3600
            self._save_state()
            return {"ok": True, "message": j.get("message")}
        return {"ok": False,
                "message": j.get("message") or "auto-login that bai"}

    def recover(self) -> dict:
        """Tu phuc hoi phien dang nhap: refresh -> auto-login."""
        if self.refresh():
            return {"ok": True, "via": "refresh"}
        r = self.auto_login()
        if r.get("ok"):
            r["via"] = "auto-login"
        return r

    def ensure_token(self) -> bool:
        """Dam bao co access-token con hieu luc.

        Thu tu: refresh-token -> auto-login (khong can SMS) -> that bai.

        LUU Y: `recover()` co the goi auto-login (cho OTP) -> treo lau.
        Khi chay dong bo (cron/CI) phai tranh treo: bo qua auto-login,
        chi thu refresh-token. Pass-through len `allow_login=False`.
        """
        return self.ensure_token_safe(allow_login=True)

    def ensure_token_safe(self, allow_login=True) -> bool:
        """Giong ensure_token nhung co the bo qua buoc auto-login (cho OTP)."""
        if self.access_token and self.expires_at - int(time.time()) > 300:
            return True
        if self.refresh():
            return True
        if not allow_login:
            return False
        r = self.auto_login()
        return bool(r.get("ok"))

    def get_stream(self, channel_id, timeout=25):
        """Tra (url_m3u8, ten_kenh) hoac (None, ly_do).

        Thu KHONG DANG NHAP truoc: 123/179 kenh free lay duoc, va khong bi
        server ap goi thue bao. Neu server tra 'D-164' (can dang nhap) thi
        moi thu voi access-token (kenh thuoc goi cua tai khoan).
        """
        ts = int(time.time())
        q = urllib.parse.urlencode({"id": int(channel_id), "type": "live",
                                    "secured": "true", "t": ts})
        url_api = WEB + "/public/v1/composite/get-link?" + q

        last_err = ""
        for use_auth in (False, True):
            if use_auth and not self.access_token:
                break
            saved = None
            if not use_auth:
                # Bo cookie access-token khoi jar de server khong nhan dien
                # da dang nhap (nen khong ap goi thue bao len kenh free).
                saved = [ck for ck in self.jar if ck.name == COOKIE_ACCESS]
                for ck in saved:
                    self.jar.clear(ck.domain, ck.path, ck.name)
            try:
                st, raw = self._call(url_api, auth=use_auth, timeout=timeout)
            finally:
                if saved:
                    for ck in saved:
                        self.jar.set_cookie(ck)
            if st != 200:
                last_err = "HTTP %s" % st
                continue
            try:
                j = json.loads(raw)
            except Exception:
                last_err = "JSON loi"
                continue
            if j.get("errorCode") != 200:
                last_err = j.get("message") or ("errorCode %s" % j.get("errorCode"))
                continue
            blob = j.get("data")
            if not blob:
                last_err = "data rong"
                continue
            try:
                info = json.loads(decrypt_aes(blob))
            except Exception as e:
                last_err = "giai ma loi: %s" % e
                continue
            url = info.get("urlStreaming")
            if not url:
                last_err = "khong co urlStreaming"
                continue
            ov = info.get("overlay") or {}
            return url, ov.get("name") or ""
        return None, last_err or "khong lay duoc link"

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
        q = urllib.parse.urlencode({"offset": 0, "limit": 500})
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
            "account_id": self.account_id,
            "access_token": _mask(self.access_token),
            "refresh_token": _mask(self.refresh_token),
            "expires_at": self.expires_at,
            "expires_in": max(0, self.expires_at - int(time.time())),
            "device_id": self._device_id,
        }


# --------------------------------------------------------------------- global
_client = None
_cl_lock = threading.Lock()
_anon = None
_anon_lock = threading.Lock()


def client() -> Tv360Client:
    global _client
    with _cl_lock:
        if _client is None:
            _client = Tv360Client()
        return _client


def anon_client() -> Tv360Client:
    """Client 'khach' - device rieng, KHONG gui access-token.

    TV360 ap goi thue bao cua tai khoan dang nhap len moi request. Neu tai
    khoan khong co goi, cac kenh FREE cung bi chan. Client nay tach ra
    de lay link kenh free (123/179 kenh) ma khong bi anh huong.
    """
    global _anon
    with _anon_lock:
        if _anon is None:
            path = STATE_FILE + ".anon"
            if not os.path.exists(path):
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump({}, f)
                except Exception:
                    pass
            _anon = Tv360Client(path)
            _anon.ping()
        return _anon


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
