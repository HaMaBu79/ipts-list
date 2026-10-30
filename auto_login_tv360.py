#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tu dong dang nhap lai TV360 khi token het han.

Chay tu dong moi 30 phut (cung cron voi dong bo playlist).
- Kiem tra access-token con hop le -> khong lam gi
- Het han / mat -> dang nhap lai bang mat khau (khong can OTP)
- Con that bai -> thu xoa device cu (gioi han H-150) roi thu lai
"""
import sys
import json
import os
import time
import datetime

sys.path.insert(0, "/opt/qweb")

CONFIG = "/opt/qweb/tv360_account.json"


def log(msg):
    print("[auto-login] %s" % msg)


def load_cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log("KHONG DOC DUOC %s: %s" % (CONFIG, e))
        return {}


def token_expiry():
    """Tra (expired, seconds_left) cua access-token hien tai."""
    try:
        from tv360_client import client, STATE_FILE
        c = client()
        st = c.status()
        if not st.get("logged_in"):
            return True, 0
        tok = c.access_token or ""
        if not tok:
            return True, 0
        exp = c._jwt_exp(tok)
        if not exp:
            # khong doc duoc exp -> coi nhu con hieu luc (co the refresh)
            return False, 999999
        left = exp - int(time.time())
        return (left <= 0), left
    except Exception as e:
        log("LOI kiem tra token: %s" % e)
        return True, 0


def do_login(cfg):
    from tv360_client import client
    msisdn = cfg.get("msisdn") or ""
    password = cfg.get("password") or ""
    prefer = cfg.get("account") or ""
    if not msisdn or not password:
        log("THIEU thong tin tai khoan (msisdn/password) trong %s" % CONFIG)
        return False

    c = client()
    c.ping()
    r = c.login_password(msisdn, password, prefer)
    if r.get("ok"):
        log("DANG NHAP THANH CONG: %s" % (r.get("message") or ""))
        return True

    # Thai xoa may cu (gioi han H-150 / 422)
    log("Dang nhap that bai (%s) -> thu xoa device cu ..." % (r.get("message") or r.get("error_code")))
    try:
        devs = c.list_devices()
        if devs:
            ids = [d.get("id") for d in devs if d.get("id") is not None]
            rm = c.remove_devices(None, ids)
            log("Da go y xoa %d device" % len(ids))
            c2 = client()
            c2.ping()
            r2 = c2.login_password(msisdn, password, prefer)
            if r2.get("ok"):
                log("DANG NHAP THANH CONG (sau khi xoa device): %s" % (r2.get("message") or ""))
                return True
            log("Van that bai: %s" % (r2.get("message") or ""))
    except Exception as e:
        log("LOI xoa device: %s" % e)
    return False


def main():
    cfg = load_cfg()
    if not cfg:
        return 0

    expired, left = token_expiry()
    if not expired:
        log("Token con hieu luc (con %d gio) -> khong can dang nhap." % (left // 3600))
        return 0

    log("Token het han (hoac mat) -> dang nhap lai ...")
    ok = do_login(cfg)
    if ok:
        expired2, left2 = token_expiry()
        log("OK - token moi, con %d ngay %d gio" % (left2 // 86400, (left2 % 86400) // 3600))
    else:
        log("THAT BAI - can kiem tra lai tai khoan.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
