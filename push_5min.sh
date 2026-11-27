#!/bin/bash
# ============================================================
#  PUSH 5 PHUT: day list.m3u8 / Listphu.m3u8 len GitHub
#
#  Chi commit + push khi co THAY DOI that su.
#
#  LOCK RIENG (qweb_push5.lock) khong dung chung voi autosync.sh:
#    autosync chay luc :17,:47 va giu qweb_autosync.lock -> dung chung
#    se lam push 5 phut bi SKIP. Dung lock rieng + kiem tra so muc
#    EXTINF truoc khi push de khong push file dang ghi nua doi.
#
#  Khong chay dong bo day du (auto_login/sync_tv360/merge_sctv) - phan do
#  do watch_github.sh + cron_tv360_event.sh lo. Script nay CHI:
#    1. kiem tra an toan (so EXTINF khop file mau)
#    2. copy list.m3u8 / Listphu.m3u8 + template -> repo
#    3. commit (neu khac)
#    4. push (neu co commit)
# ============================================================
set -u
QWEB=/opt/qweb
REPO=/opt/ipts-list
LOG=/var/log/qweb_push_5min.log
LOCK=/var/lock/qweb_push5.lock

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

exec 9>"$LOCK" || true
if ! flock -n 9; then
    log "SKIP: lan truoi van chay"
    exit 0
fi

[ -d "$REPO/.git" ] || { log "khong co repo git"; exit 0; }

cd "$REPO" || exit 0

# --- 0. KIEM TRA AN TOAN: so muc EXTINF phai khop file mau ---
fail=0
for f in list.m3u8 Listphu.m3u8; do
    a=$(grep -c '^#EXTINF' "$QWEB/template/$f" 2>/dev/null || echo 0)
    b=$(grep -c '^#EXTINF' "$QWEB/$f" 2>/dev/null || echo 0)
    if [ "$a" != "$b" ]; then
        log "AN TOAN FAIL: $f so muc EXTINF khac ($a -> $b) - BO QUA push"
        fail=1
    fi
done
if [ "$fail" = "1" ]; then
    exit 1
fi

# --- 0b. CONG CHAN VERIFY KENH 'SU KIEN TV360' ---
# Kiem tra THAT: tai MPD/segment + giai ma ClearKey + anh that.
# Co kenh hong -> BO QUA push (khong day ban hong len GitHub).
# SOFT-FAILURE: rc=2 = co kenh chet nhung da gan fallback cho rieng kenh do
# -> VAN PUSH (khong chan 188 kenh khac). rc=1 = loi that -> van chan.
if [ -x "$QWEB/verify_event.py" ]; then
    # timeout 240 -> 420: verify_event chay ~200s (verify 93 + recover 108)
    # -> bi cat ngan o 240 -> rc=124 -> BO QUA push -> GitHub giu link cu.
    timeout 420 "$QWEB/venv/bin/python" "$QWEB/verify_event.py" >>"$LOG" 2>&1
    VRC=$?
    if [ "$VRC" = "0" ]; then
        log "VERIFY OK: tat ca kenh 'Su kien TV360' deu xac minh duoc"
    elif [ "$VRC" = "2" ]; then
        log "VERIFY SOFT-FAIL: vai kenh chet da gan fallback - VAN PUSH phan con lai"
    elif [ "$VRC" = "124" ]; then
        # VERIFY_TIMEOUT_SOFTFAIL: verify qua gio (timeout).
        # Verify la buoc KIEM TRA an toan, khong phai buoc bat buoc.
        # Timeout != kenh chet -> dung cache gan nhat (neu co) va VAN PUSH.
        log "VERIFY TIMEOUT (rc=124) - SOFT-FAIL: dung ket qua verify gan nhat, VAN PUSH"
        if [ -f "$QWEB/verify_event_cache.json" ]; then
            log "  co verify_event_cache.json -> dung cache (da verify truoc do)"
        else
            log "  khong co cache -> van push (verify timeout khong phai kenh chet)"
        fi
    else
        log "VERIFY FAIL (rc=$VRC): loi that - BO QUA push (giu ban cu tren GitHub)"
        exit 1
    fi
fi

# --- SANITIZE CAU TRUC (xoa byte NULL, sua '##', '#URL' -> URL) ---
# KHONG doi URL nao - chi sua comment/ky tu rac gay loi player.
if [ -f "$QWEB/sanitize.py" ]; then
    "$QWEB/venv/bin/python" "$QWEB/sanitize.py" >>"$LOG" 2>&1
fi

# --- 1. copy file sang repo ---
cp -f "$QWEB/list.m3u8" "$QWEB/Listphu.m3u8" "$REPO/" 2>/dev/null
if [ -d "$REPO/template" ]; then
    cp -f "$QWEB/template/list.m3u8" "$QWEB/template/Listphu.m3u8" "$REPO/template/" 2>/dev/null
fi

# --- 2. commit ---
git add list.m3u8 Listphu.m3u8 template 2>/dev/null
if git diff --cached --quiet; then
    log "khong co thay doi - bo qua commit"
    exit 0
fi

git -c user.name="HaMaBu79" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -q -m "chore: cap nhat link TV360 ($(date -u +%Y-%m-%dT%H:%MZ))" 2>>"$LOG"
log "commit xong"

# --- 3. push ---
if [ -f "$QWEB/github_token" ]; then
    PAT=$(cat "$QWEB/github_token" | tr -d "\r\n")
    git remote set-url origin "https://x-access-token:${PAT}@github.com/HaMaBu79/ipts-list.git"
fi

if [ -d "$REPO/.git/rebase-merge" ] || [ -d "$REPO/.git/rebase-apply" ]; then
    git rebase --abort 2>/dev/null
fi

if git diff --quiet origin/main..main 2>/dev/null; then
    log "khong co commit moi de push"
    git remote set-url origin "https://github.com/HaMaBu79/ipts-list.git" 2>/dev/null
    exit 0
fi

if git push origin main >>"$LOG" 2>&1; then
    log "PUSH OK"
else
    log "PUSH FAIL - thu fetch + rebase"
    git fetch origin main >>"$LOG" 2>&1
    if git rebase origin/main >>"$LOG" 2>&1; then
        cp -f "$QWEB/list.m3u8" "$QWEB/Listphu.m3u8" "$REPO/" 2>/dev/null
        if [ -d "$REPO/template" ]; then
            cp -f "$QWEB/template/list.m3u8" "$QWEB/template/Listphu.m3u8" "$REPO/template/" 2>/dev/null
        fi
        git add list.m3u8 Listphu.m3u8 template 2>/dev/null
        if ! git diff --cached --quiet; then
            git -c user.name="HaMaBu79" \
                -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
                commit -q -m "chore: cap nhat link TV360 ($(date -u +%Y-%m-%dT%H:%MZ))" 2>>"$LOG"
        fi
        if git push origin main >>"$LOG" 2>&1; then
            log "PUSH OK (sau rebase)"
        else
            log "PUSH FAIL (sau rebase)"
        fi
    else
        git rebase --abort 2>/dev/null
        cp -f "$QWEB/list.m3u8" "$QWEB/Listphu.m3u8" "$REPO/" 2>/dev/null
        if [ -d "$REPO/template" ]; then
            cp -f "$QWEB/template/list.m3u8" "$QWEB/template/Listphu.m3u8" "$REPO/template/" 2>/dev/null
        fi
        git add list.m3u8 Listphu.m3u8 template 2>/dev/null
        if ! git diff --cached --quiet; then
            git -c user.name="HaMaBu79" \
                -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
                commit -q -m "chore: cap nhat link TV360 ($(date -u +%Y-%m-%dT%H:%MZ))" 2>>"$LOG"
        fi
        if git push --force-with-lease origin main >>"$LOG" 2>&1; then
            log "PUSH OK (force-with-lease)"
        else
            log "PUSH FAIL (force)"
        fi
    fi
fi

git remote set-url origin "https://github.com/HaMaBu79/ipts-list.git" 2>/dev/null
tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
exit 0
