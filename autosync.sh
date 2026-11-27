#!/bin/bash
# ============================================================
#  Auto-sync playlist BB2  (chay moi 30 phut)
#
#  Nguon su that chinh: GitHub list.m3u8 / Listphu.m3u8
#    -> user sua tay tren GitHub (thu tu, ten, logo, comment)
#
#  Luong:
#    1. Keo list.m3u8 + Listphu.m3u8 tu GitHub ve /opt/qweb/template/
#    2. sync_tv360.py: doc file mau, CHI THAY URL TAI CHO
#    3. merge_sctv_vip.py: doi nguon kenh SCTV sang link vip_full
#    4. Kiem tra an toan (so EXTINF phai giong file mau)
#    5. Commit + push nguoc len GitHub
# ============================================================
set -u
QWEB=/opt/qweb
REPO=/opt/ipts-list
GH_RAW="https://raw.githubusercontent.com/HaMaBu79/ipts-list/main"
LOG=/var/log/qweb_sync_tv360.log
PY=$QWEB/venv/bin/python
LOCK=/var/lock/qweb_autosync.lock

# --- khoa: khong cho 2 lan chay chong nhau ---
# QWEB_LOCK_HELD=1 -> watcher da giu roi, bo qua flock (tranh deadlock)
if [ "${QWEB_LOCK_HELD:-0}" != "1" ]; then
    exec 9>"$LOCK" || true
    if ! flock -n 9; then
        echo "=== da co autosync dang chay, bo qua lan nay ===" >> "$LOG"
        exit 0
    fi
fi

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" >> "$LOG"; }

# --- Telegram notify ---
TG="$QWEB/tg_notify.sh"
tg() { [ -x "$TG" ] && bash "$TG" "$1" >/dev/null 2>&1 || true; }


log "===== BAT DAU auto-sync ====="
tg "⏳ BB2: bat dau dong bo link TV360..."

# --- 1. Keo file mau tu GitHub -----------------------------------
# LAY TU GIT (khong phai raw.githubusercontent.com):
#   raw.githubusercontent.com co CDN CACHE -> tra ve BAN CU -> BB2 ghi de
#   thay doi tay cua user (bi mat muc/chuyen thu tu).
#   git show origin/main:<file> luon dung theo commit moi nhat.
mkdir -p "$QWEB/template"
if [ -d "$REPO/.git" ]; then
    git -C "$REPO" fetch origin main >> "$LOG" 2>&1
    for f in list.m3u8 Listphu.m3u8; do
        if git -C "$REPO" show "origin/main:$f" > "$QWEB/template/$f.new" 2>/dev/null \
           && [ -s "$QWEB/template/$f.new" ]; then
            if ! cmp -s "$QWEB/template/$f.new" "$QWEB/template/$f"; then
                mv -f "$QWEB/template/$f.new" "$QWEB/template/$f"
                log "cap nhat file mau tu GitHub (git): $f"
            else
                rm -f "$QWEB/template/$f.new"
            fi
        else
            rm -f "$QWEB/template/$f.new"
            log "LOI lay file mau $f tu git - dung ban cu"
        fi
    done
else
    for f in list.m3u8 Listphu.m3u8; do
        if curl -fsSL --max-time 90 -H 'Cache-Control: no-cache' \
             "$GH_RAW/$f" -o "$QWEB/template/$f.new"; then
            if ! cmp -s "$QWEB/template/$f.new" "$QWEB/template/$f"; then
                mv -f "$QWEB/template/$f.new" "$QWEB/template/$f"
                log "cap nhat file mau tu GitHub (raw): $f"
            else
                rm -f "$QWEB/template/$f.new"
            fi
        else
            rm -f "$QWEB/template/$f.new"
            log "LOI keo file mau $f - dung ban cu"
        fi
    done
fi

# --- 1b. DONG BO TOKEN DA XAC MINH sang file mau ---------------------
# File mau keo tu GitHub co token CU (het han sau ~6-9h). Neu giu nguyen
# -> link het han bi ghi vao list.m3u8 -> push len GitHub -> kenh chet.
# Fix: lay URL co token CON HAN tu list.m3u8 (da verify) de ghi de vao
# file mau. File mau vay luon dung token moi -> GitHub luon dung token moi.
$PY "$QWEB/sync_template_tokens.py" >> "$LOG" 2>&1

# --- 2. Lam moi token TV360 --------------------------------------
$PY "$QWEB/auto_login_tv360.py" >> "$LOG" 2>&1

# --- 3. Sync URL (chi thay URL tai cho) ---------------------------
cd "$QWEB" || exit 1
if ! $PY "$QWEB/sync_tv360.py" >> "$LOG" 2>&1; then
    log "sync that bai - giu nguyen file cu"
    tg "❌ BB2: dong bo link FAIL - giu nguyen file cu (xem log)"
    exit 1
fi

# --- 4. SCTV vip_full --------------------------------------------
$PY "$QWEB/merge_sctv_vip.py" "$QWEB/list.m3u8" "$QWEB/Listphu.m3u8" >> "$LOG" 2>&1

# --- SANITIZE CAU TRUC (xoa byte NULL, sua '##', '#URL' -> URL) ---
# KHONG doi URL nao - chi sua comment/ky tu rac gay loi player.
if [ -f "$QWEB/sanitize.py" ]; then
    "$QWEB/venv/bin/python" "$QWEB/sanitize.py" >>"$LOG" 2>&1
fi

# --- 5. Kiem tra an toan: cau truc phai giong file mau ------------
fail=0
for f in list.m3u8 Listphu.m3u8; do
    a=$(grep -c '^#EXTINF' "$QWEB/template/$f" 2>/dev/null || echo 0)
    b=$(grep -c '^#EXTINF' "$QWEB/$f" 2>/dev/null || echo 0)
    if [ "$a" != "$b" ]; then
        log "AN TOAN FAIL: $f doi so muc EXTINF ($a -> $b) - BO QUA commit"
        tg "❌ BB2: an toan FAIL $f (EXTINF $a -> $b) - bo qua commit"
        fail=1
    else
        log "OK: $f - $b muc, cau truc giong file mau"
    fi
done
if [ "$fail" = "1" ]; then
    log "=== XONG (khong commit) ==="
    exit 1
fi

# --- 0b. CONG CHAN VERIFY KENH 'SU KIEN TV360' ---
# Kiem tra THAT: tai MPD/segment + giai ma ClearKey + anh that.
# Co kenh hong -> BO QUA push (khong day ban hong len GitHub).
# SOFT-FAILURE: rc=2 = vai kenh chet da gan fallback cho rieng kenh do
# -> VAN PUSH (khong chan 188 kenh khac). rc=1 = loi that -> van chan.
if [ -x "$QWEB/verify_event.py" ]; then
    timeout 240 "$QWEB/venv/bin/python" "$QWEB/verify_event.py" >>"$LOG" 2>&1
    VRC=$?
    if [ "$VRC" = "0" ]; then
        log "VERIFY OK: tat ca kenh 'Su kien TV360' deu xac minh duoc"
    elif [ "$VRC" = "2" ]; then
        log "VERIFY SOFT-FAIL: vai kenh chet da gan fallback - VAN PUSH phan con lai"
    else
        log "VERIFY FAIL (rc=$VRC): loi that - BO QUA push (giu ban cu tren GitHub)"
        exit 1
    fi
fi

# --- 6. Commit + push --------------------------------------------
if [ -d "$REPO/.git" ]; then
    cp -f "$QWEB/list.m3u8" "$QWEB/Listphu.m3u8" "$REPO/" 2>/dev/null
    cp -f "$QWEB/template/list.m3u8" "$QWEB/template/Listphu.m3u8" "$REPO/template/" 2>/dev/null
    cd "$REPO" || exit 0

    git add list.m3u8 Listphu.m3u8 template 2>/dev/null
    if git diff --cached --quiet; then
        log "khong co thay moi - khong commit"
    else
        git -c user.name="HaMaBu79" \
            -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
            commit -q -m "chore: dong bo URL tu TV360 ($(date -u +%Y-%m-%dT%H:%MZ))"
        log "commit xong"
    fi

    # Giu file .py da sua (sync_tv360.py...) - tranh 'unstaged changes'
    # lam chet nhanh pull --rebase.
    git add sync_tv360.py merge_sctv_vip.py tv360_client.py 2>/dev/null
    if ! git diff --cached --quiet -- sync_tv360.py merge_sctv_vip.py tv360_client.py 2>/dev/null; then
        git -c user.name="HaMaBu79" \
            -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
            commit -q -m "chore: cap nhat script dong bo" 2>/dev/null && log "commit script"
    fi

    if [ -f "$QWEB/github_token" ]; then
        PAT=$(cat "$QWEB/github_token" | tr -d "\r\n")
        git remote set-url origin "https://x-access-token:${PAT}@github.com/HaMaBu79/ipts-list.git"
        if [ -d "$REPO/.git/rebase-merge" ] || [ -d "$REPO/.git/rebase-apply" ]; then
            git rebase --abort 2>/dev/null
        fi
        if ! git diff --quiet origin/main..main 2>/dev/null; then
            if git push origin main >> "$LOG" 2>&1; then
                log "PUSH OK"
            else
                log "PUSH FAIL - fetch + rebase"
                # FETCH truoc (khong phai pull) -> luon lay commit moi nhat
                # tu GitHub. 'git pull --rebase' FAIL khi remote da doi.
                git fetch origin main >> "$LOG" 2>&1
                if git rebase origin/main >> "$LOG" 2>&1; then
                    # sau rebase: file mau co the bi ghi de -> copy lai
                    cp -f "$QWEB/list.m3u8" "$QWEB/Listphu.m3u8" "$REPO/" 2>/dev/null
                    cp -f "$QWEB/template/list.m3u8" "$QWEB/template/Listphu.m3u8" "$REPO/template/" 2>/dev/null
                    git add list.m3u8 Listphu.m3u8 template 2>/dev/null
                    if ! git diff --cached --quiet; then
                        git -c user.name="HaMaBu79" \
                            -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
                            commit -q -m "chore: dong bo URL tu TV360 ($(date -u +%Y-%m-%dT%H:%MZ))"
                    fi
                    if git push origin main >> "$LOG" 2>&1; then
                        log "PUSH OK (sau rebase)"
                    else
                        log "PUSH FAIL (sau rebase)"
                    fi
                else
                    log "REBASE FAIL - abort, thu push force"
                    git rebase --abort 2>/dev/null
                    # Commit cua BB2 la MOI HON -> day thang (an toan,
                    # vi BB2 la nguon su that chinh cho list.m3u8)
                    cp -f "$QWEB/list.m3u8" "$QWEB/Listphu.m3u8" "$REPO/" 2>/dev/null
                    cp -f "$QWEB/template/list.m3u8" "$QWEB/template/Listphu.m3u8" "$REPO/template/" 2>/dev/null
                    git add list.m3u8 Listphu.m3u8 template 2>/dev/null
                    if ! git diff --cached --quiet; then
                        git -c user.name="HaMaBu79" \
                            -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
                            commit -q -m "chore: dong bo URL tu TV360 ($(date -u +%Y-%m-%dT%H:%MZ))"
                    fi
                    if git push --force-with-lease origin main >> "$LOG" 2>&1; then
                        log "PUSH OK (force-with-lease)"
                    else
                        log "PUSH FAIL (force)"
                    fi
                fi
            fi
        else
            log "khong co commit moi de push"
        fi
        git remote set-url origin "https://github.com/HaMaBu79/ipts-list.git"
    fi
fi

tail -n 300 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
# --- 7. Thong bao Telegram ---
if [ -d "$REPO/.git" ]; then
    COMMIT=$(git -C "$REPO" log --oneline -1 2>/dev/null | head -c 90)
    AHEAD=$(git -C "$REPO" rev-list --left-right --count origin/main...main 2>/dev/null)
    PUSH="khong ro"
    LASTPUSH=$(grep -E "PUSH (OK|FAIL)" "$LOG" 2>/dev/null | tail -1)
    case "$LASTPUSH" in
        *"PUSH OK"*)   PUSH="OK" ;;
        *"PUSH FAIL"*) PUSH="FAIL" ;;
    esac
    MSG="✅ BB2 dong bo xong
⏰ $(date '+%H:%M:%S %d/%m')
📺 Muc list: $(grep -c '^#EXTINF' "$QWEB/list.m3u8" 2>/dev/null) (mau: $(grep -c '^#EXTINF' "$QWEB/template/list.m3u8" 2>/dev/null))
🗂 Nhom: $(grep -o 'group-title="[^"]*"' "$QWEB/list.m3u8" 2>/dev/null | sort -u | wc -l)
🔗 $(grep -m1 'VTV1 HD Update' "$QWEB/list.m3u8" 2>/dev/null | tail -c 60)
⬆️ Push GitHub: $PUSH | ahead/behind: $AHEAD
📝 $COMMIT"
    tg "$MSG"
fi

log "===== XONG ====="
exit 0
