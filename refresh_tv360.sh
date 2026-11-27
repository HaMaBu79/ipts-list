#!/bin/bash
# Tu gia han / phuc hoi phien dang nhap TV360 de server chay 24/7.
# Chay moi 20 phut.
#
#   refresh-token  -> tao access-token moi (nhanh, khong can SMS)
#   auto-login     -> dang nhap lai qua kenh Viettel 4G/5G (khi refresh chet)
#
# Neu ca 2 deu fail: can dang nhap lai bang OTP (1 lan) -> xem login_tv360.sh
set -u
PY=/opt/qweb/venv/bin/python
LOG=/var/log/qweb_tv360_refresh.log

OUT=$($PY - <<'EOF' 2>&1
import sys, json, time
sys.path.insert(0, "/opt/qweb")
from tv360_client import client
c = client()
if not c.access_token and not c.refresh_token and not c.msisdn:
    print("NO_SESSION")
else:
    # Session con han -> KHONG can auto-login (auto-login yeu cau 4G/5G
    # Viettel -> luon FAIL "H-101" va lam canh bao gia). Chi can refresh
    # access-token bang refresh-token (signed cookie) - khong can mang Viettel.
    exp = c.expires_at or 0
    left = exp - int(time.time()) if exp else 0
    if left > 3600:
        print("OK session con han (%.0f phut)" % (left / 60.0))
    elif c.refresh():
        e2 = c.expires_at or 0
        print("OK qua refresh-token (con %.0f phut)"
              % ((e2 - int(time.time())) / 60.0))
    else:
        r = c.recover()
        if r.get("ok"):
            print("OK via %s" % r.get("via"))
        else:
            print("FAIL: %s" % str(r.get("message"))[:120])
EOF
)

echo "$(date '+%Y-%m-%d %H:%M:%S') $OUT" >> "$LOG"
tail -n 500 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"

case "$OUT" in
  NO_SESSION*)
    echo "$(date '+%Y-%m-%d %H:%M:%S') Chua co phien dang nhap. Chay: /opt/qweb/login_tv360.sh <sdt>" >> "$LOG"
    ;;
  FAIL*)
    echo "$(date '+%Y-%m-%d %H:%M:%S') CANH BAO: khong gia han duoc. Can dang nhap lai bang OTP." >> "$LOG"
    ;;
esac
exit 0
