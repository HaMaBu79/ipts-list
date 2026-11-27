# ============================================================
#  CHU KY 10 PHUT: BB2 <-> GitHub  (DONG BO TOAN BO LIST)
#
#  MOC: 0, 10, 20, 30, 40, 50 phut
#
#  1. autosync.sh          : LAM MOI TOAN BO LIST (TV360 + SCTV + ClearKey)
#  2. cron_tv360_event.sh  : lam moi nhom "Su kien TV360" (17 kenh)
#  3. push_5min.sh         : commit + push list.m3u8 / Listphu.m3u8 len GitHub
#  4. watch_github.sh      : keo thay doi lam tay cua user tren GitHub ve BB2
#  0. watchdog.py          : giam sat + tu phuc hoi khi GitHub im lang (chay dau)
#
#  NGUON SACH THOI GIAN (tong phai < 600s):
#    buoc 0: 20s   (watchdog - phat hien GitHub im som)
#    buoc 1: 280s  (autosync - CHAY NEN, song song voi buoc 2+3)
#    buoc 2: 240s  (refresh TV360 - HLS truoc, DASH song song)
#    buoc 3: 470s  (push - verify_event 420s + commit + push)
#    buoc 4: 25s
#    => tong ~ max(280, 240+320) + 45 = 585s -> du an toan cho 600s.
#    Thuc te do duoc: buoc1 ~160s, buoc2 ~100s, buoc3 ~130s => ~390s.
#    Buoc 2 va 3 deu co buoc VERIFY THAT 17 kenh (giai ma ClearKey).
#    Buoc 3 doc CACHE verify cua buoc 2 -> khong chay lai.
#
#  KHOA:
#   - qweb_every5.lock    : chong chay trung 2 lan
#   - autosync.sh / push_5min.sh / watch_github.sh tu giu khoa rieng
# ============================================================
set -u
QWEB=/opt/qweb
LOG=/var/log/qweb_every5.log
LOCK=/var/lock/qweb_every5.lock

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# --- khoa: neu lan truoi van chay -> bo qua lan nay ---
exec 9>"$LOCK" || true
if ! flock -n 9; then
    log "SKIP: lan truoi van chay"
    exit 0
fi

START=$(date +%s)

log "===== CHU KY 10 PHUT BAT DAU ====="

# --- 0. WATCHDOG (chay dau): phat hien GitHub im lang SOM ---
# Neu GitHub khong co commit moi qua 3 chu ky -> watchdog tu chay lai
# pipeline + gui Telegram canh bao. Binh thuong chi ton ~3 giay.
if [ -f "$QWEB/watchdog.py" ] && [ -x "$QWEB/venv/bin/python" ]; then
    log "buoc 0: watchdog.py (giam sat GitHub)"
    timeout 20 "$QWEB/venv/bin/python" "$QWEB/watchdog.py" >>"$LOG" 2>&1
    log "buoc 0 xong (rc=$?)"
fi

# --- 1. LAM MOI TOAN BO LIST (chay NEN - buoc 2+3 chay song song) ---
# Ly do (27/11): buoc 1 (autosync) + buoc 2 (TV360) + buoc 3 (push) chay
# TUAN TU -> tong > 600s -> chu ky bi truot. Cho buoc 1 chay nen (bg) ->
# buoc 2 & 3 chay ngay -> tong ~ max(280, 240+320) + 45 = 585s.
if [ -x "$QWEB/autosync.sh" ]; then
    log "buoc 1: autosync.sh (lam moi TOAN BO list) - chay nen"
    ( TV360_RESOLVE_BUDGET=200 timeout 280 bash "$QWEB/autosync.sh" >>"$LOG" 2>&1; \
      echo "buoc 1 xong (rc=$?)" >> "$LOG" ) &
    AUTOSYNC_PID=$!
    log "buoc 1 chay nen (pid=$AUTOSYNC_PID)"
fi

# --- 2. lam moi nhom "Su kien TV360" ---
if [ -x "$QWEB/cron_tv360_event.sh" ]; then
    log "buoc 2: cron_tv360_event.sh (refresh link TV360)"
    TV360_RESOLVE_BUDGET=200 timeout 240 bash "$QWEB/cron_tv360_event.sh" >>"$LOG" 2>&1
    log "buoc 2 xong (rc=$?)"
fi

# --- 3. commit + push len GitHub ---
if [ -x "$QWEB/push_5min.sh" ]; then
    log "buoc 3: push_5min.sh"
    # timeout 90 -> 320: script ben trong chay verify_event.py (timeout 240)
    # + commit + push. Timeout 90 cat ngan giua chung -> KHONG push duoc.
    timeout 470 bash "$QWEB/push_5min.sh" >>"$LOG" 2>&1
    log "buoc 3 xong (rc=$?)"
fi

# --- 4. keo thay doi cua user tu GitHub (chay cuoi) ---
if [ -x "$QWEB/watch_github.sh" ]; then
    log "buoc 4: watch_github.sh"
    timeout 25 bash "$QWEB/watch_github.sh" >>"$LOG" 2>&1
    log "buoc 4 xong (rc=$?)"
fi


# --- cho buoc 1 (autosync nen) chay xong ---
if [ -n "${AUTOSYNC_PID:-}" ]; then
    log "cho buoc 1 (autosync nen) chay xong..."
    wait "$AUTOSYNC_PID" 2>/dev/null || true
    log "buoc 1 da ket thuc"
fi

END=$(date +%s)
log "===== CHU KY 10 PHUT XONG ($((END-START)) giay) ====="

# TG5_NOTIFY: bao Telegram moi chu ky 10 phut
if [ -x "$QWEB/tg_notify.sh" ]; then
    COMMIT=$(git -C /opt/ipts-list log --format='%h %cd' --date=format:%H:%M -n 1 2>/dev/null)
    LASTPUSH=$(grep -E "PUSH (OK|FAIL)|khong co thay doi" /var/log/qweb_push_5min.log 2>/dev/null | tail -1 | sed 's/^\[[^]]*\] //')
    bash "$QWEB/tg_notify.sh" "BB2 cap nhat 10p xong $(date '+%H:%M %d/%m') ($((END-START))s)
Commit: $COMMIT
Push: $LASTPUSH" >/dev/null 2>&1 || true
fi

exit 0
