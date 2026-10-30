#!/bin/bash
# Dang nhap TV360 truc tiep tren server.
#
#   ./login_tv360.sh <so_dien_thoai>              -> gui OTP qua SMS
#   ./login_tv360.sh <so_dien_thoai> <otp>         -> dang nhap bang OTP
#   ./login_tv360.sh <so_dien_thoai> -p <mat_khau> -> dang nhap bang mat khau
#
# Chi can 1 lan. Token duoc luu vao /opt/qweb/tv360_session.json va
# cron */20 * * * * tu gia han 24/7.
set -u
cd /opt/qweb
PY=/opt/qweb/venv/bin/python

MSISDN="${1:-}"
MODE="${2:-otp}"
ARG="${3:-}"

if [ -z "$MSISDN" ]; then
    echo "Dung: $0 <so_dien_thoai> [otp | -p <mat_khau>]"
    exit 1
fi

# --- Buoc 1: xin OTP ---
if [ "$MODE" = "otp" ] && [ -z "$ARG" ]; then
    echo "[1/2] Dang gui OTP toi $MSISDN ..."
    OUT=$($PY - "$MSISDN" <<'EOF'
import sys, json
sys.path.insert(0, "/opt/qweb")
from tv360_client import client
c = client()
c.ping()
r = c.request_otp(sys.argv[1])
print(json.dumps(r, ensure_ascii=False))
EOF
)
    echo "    $OUT"
    if echo "$OUT" | grep -q '"ok": true'; then
        echo ""
        echo "==> Nhap ma OTP ban vua nhan, roi chay lai:"
        echo "    $0 $MSISDN <ma_otp>"
    else
        echo "!! Khong gui duoc OTP (het luot trong ngay?)."
        echo "   Hay soan tin nhan 'MK' gui 1331 de lay mat khau, roi:"
        echo "    $0 $MSISDN -p <mat_khau>"
    fi
    exit 0
fi

# --- Buoc 2: dang nhap ---
if [ "$MODE" = "-p" ]; then
    echo "[2/2] Dang nhap bang mat khau ..."
    OUT=$($PY - "$MSISDN" "$ARG" <<'EOF'
import sys, json
sys.path.insert(0, "/opt/qweb")
from tv360_client import client
c = client()
c.ping()
r = c.login_password(sys.argv[1], sys.argv[2])
print(json.dumps(r, ensure_ascii=False))
if r.get("ok"):
    print(json.dumps(c.status(), ensure_ascii=False))
EOF
)
else
    echo "[2/2] Dang nhap voi OTP $ARG ..."
    OUT=$($PY - "$MSISDN" "$ARG" <<'EOF'
import sys, json
sys.path.insert(0, "/opt/qweb")
from tv360_client import client
c = client()
c.ping()
r = c.login_otp(sys.argv[1], sys.argv[2])
print(json.dumps(r, ensure_ascii=False))
if r.get("ok"):
    print(json.dumps(c.status(), ensure_ascii=False))
EOF
)
fi
echo "$OUT"

if echo "$OUT" | grep -q '"ok": true'; then
    echo ""
    echo "==> DANG NHAP THANH CONG. Token da luu vao /opt/qweb/tv360_session.json"
    echo "    Cron */20 * * * * se tu gia han token 24/7."
    echo "    Kiem tra: /opt/qweb/tv360.sh status"
else
    echo "!! Dang nhap that bai."
fi
