#!/bin/bash
# Dong bo playlist tu TV360 truc tiep (link khong qua nguon trung gian).
# Chay moi 30 phut. Link TV360 co han -> can lam moi thuong xuyen.
set -u
PY=/opt/qweb/venv/bin/python
LOG=/var/log/qweb_sync_tv360.log
REPO=/opt/ipts-list
cd /opt/qweb || exit 1

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S') ====="
  # 1. Tu dong dang nhap lai TV360 khi token het han (khong can OTP)
  $PY /opt/qweb/auto_login_tv360.py 2>&1
  # 2. Dong bo playlist
  $PY /opt/qweb/sync_tv360.py 2>&1
} >> "$LOG"

# chi giu 200 dong log
tail -n 200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"

# copy playlist sang repo git
[ -d "$REPO/.git" ] && cp -f /opt/qweb/list.m3u8 /opt/qweb/Listphu.m3u8 "$REPO/" 2>/dev/null

# day file len GitHub
TOKEN_FILE=/opt/qweb/github_token
if [ -d "$REPO/.git" ]; then
  cd "$REPO" || exit 0
  git add list.m3u8 Listphu.m3u8 2>/dev/null
  if ! git diff --cached --quiet; then
    git -c user.name="github-actions[bot]" \
        -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
        commit -m "chore: dong bo URL tu TV360 ($(date -u +%Y-%m-%dT%H:%MZ))" 2>&1
  fi
  if [ -f "$TOKEN_FILE" ]; then
    PAT=$(cat "$TOKEN_FILE")
    git remote set-url origin "https://x-access-token:${PAT}@github.com/HaMaBu79/ipts-list.git"
    # Don rebase treo (do conflict truoc) -> tranh lam push fail
    if [ -d "$REPO/.git/rebase-merge" ] || [ -d "$REPO/.git/rebase-apply" ]; then
      git rebase --abort 2>/dev/null || rm -rf "$REPO/.git/rebase-merge" "$REPO/.git/rebase-apply"
    fi
    # pull --rebase truoc de khong conflict voi workflow GitHub
    git pull --rebase origin main 2>&1 | tail -3
    # Conflict playlist: giu ban tren box (nguon chinh, IP Viet Nam)
    if [ -d "$REPO/.git/rebase-merge" ]; then
      cp -f /opt/qweb/list.m3u8 /opt/qweb/Listphu.m3u8 "$REPO/" 2>/dev/null
      git add list.m3u8 Listphu.m3u8 2>/dev/null
      GIT_EDITOR=true git rebase --continue 2>&1 | tail -3
      [ -d "$REPO/.git/rebase-merge" ] && git rebase --abort 2>/dev/null
    fi
    if ! git diff --quiet origin/main..main 2>/dev/null; then
      git push origin main 2>&1 | tail -4
    fi
    git remote set-url origin "https://github.com/HaMaBu79/ipts-list.git"
  else
    git push origin main 2>&1 | tail -3
  fi
fi
exit 0
