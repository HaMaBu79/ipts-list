#!/bin/bash
# remote.sh - Tong hop kiem tra BB2 (full|net|sync|tunnel|sys|syncnow|verify|check)
ok(){ echo "[OK] $*"; }
bad(){ echo "[LOI] $*"; }
warn(){ echo "[CANH] $*"; }
sec(){ echo; echo "=== $* ==="; }

check_net(){
  sec "MANG"
  ip -br addr show | grep -v DOWN | grep -v UNKNOWN
  echo "--- route ---"; ip route | head -5
  if ping -c3 -W2 192.168.1.1 >/dev/null 2>&1; then ok "Gateway 192.168.1.1 OK"; else bad "KHONG ping duoc gateway"; fi
  if ping -c3 -W3 8.8.8.8 >/dev/null 2>&1; then ok "Internet OK"; else bad "KHONG co internet"; fi
  if getent hosts github.com >/dev/null 2>&1; then ok "DNS github.com OK"; else bad "DNS FAIL"; fi
  echo "--- tailscale ---"; tailscale ip -4 2>/dev/null | head -1
}

check_services(){
  sec "DICH VU"
  for s in cron ssh qweb tailscaled cloudflared cloudflared-term ttyd; do
    if systemctl is-active --quiet "$s"; then ok "$s : active"; else bad "$s : KHONG active"; fi
  done
  echo "--- timers ---"
  systemctl list-timers --all --no-pager 2>/dev/null | grep -E "net-watchdog" | head -3
}

check_sync(){
  sec "DONG BO TV360 / LIST"
  echo "--- cron.d ---"
  cat /etc/cron.d/qweb_sync_tv360 2>/dev/null
  cat /etc/cron.d/qweb_tv360 2>/dev/null
  echo "--- file list ---"
  for f in /opt/qweb/list.m3u8 /opt/qweb/Listphu.m3u8 /opt/qweb/playlist.m3u; do
    if [ -f "$f" ]; then ok "$f | $(stat -c%s "$f") bytes | sua: $(stat -c%y "$f" | cut -d. -f1)"; else warn "$f khong ton tai"; fi
  done
  echo "--- dong Update gan nhat trong list ---"
  grep -h "Update:" /opt/qweb/list.m3u8 2>/dev/null | head -3
  echo "--- so dong ---"
  wc -l /opt/qweb/list.m3u8 2>/dev/null
  echo "--- DRM: so dong KID:KEY / URL da token hoa ---"
  echo "  KID:KEY   = $(grep -c 'license_key=[0-9a-f]\{32\}:' /opt/qweb/list.m3u8 2>/dev/null)"
  echo "  token hoa = $(grep -c 'bpk-token\|tok_eyJ' /opt/qweb/list.m3u8 2>/dev/null)"
  echo "--- log sync ---"
  ls -t /opt/qweb/*.log /var/log/*tv360* 2>/dev/null | head -3
  tail -15 /opt/qweb/logs/sync.log 2>/dev/null
  tail -15 /var/log/tv360_sync.log 2>/dev/null
}

check_tunnel(){
  sec "TUNNEL"
  U1=$(cat /opt/qweb/tunnel_url.txt 2>/dev/null | tr -d '\r\n ')
  U2=$(cat /opt/qweb/tunnel_term_url.txt 2>/dev/null | tr -d '\r\n ')
  if [ -n "$U1" ]; then ok "qweb : $U1"; else bad "Chua luu URL qweb"; fi
  if [ -n "$U2" ]; then ok "term : $U2"; else bad "Chua luu URL term"; fi
  curl -s -o /dev/null -w "qweb local : %{http_code}\n" --max-time 5 http://127.0.0.1:8000/
  curl -s -o /dev/null -w "ttyd local : %{http_code}\n" --max-time 5 http://127.0.0.1:8001/
  if [ -n "$U2" ]; then curl -s -o /dev/null -w "term cloud : %{http_code}\n" --max-time 20 -u bb2admin:BB2Remote2026 "$U2"; fi
}

check_sys(){
  sec "HE THONG"
  uptime
  df -h / | tail -1
  free -h | head -2
  if [ -f /sys/class/thermal/thermal_zone0/temp ]; then echo "Nhiet do: $(( $(cat /sys/class/thermal/thermal_zone0/temp) / 1000 ))C"; fi
  echo "Tailscale IP: $(tailscale ip -4 2>/dev/null | head -1)"
  echo "Peers tailscale:"
  tailscale status 2>/dev/null | head -8
}

do_syncnow(){
  sec "KICH HOAT SYNC NGAY"
  /opt/qweb/cron_sync_tv360.sh 2>&1 | tail -30
  echo "--- ket qua ---"
  stat -c "%n sua luc %y" /opt/qweb/list.m3u8 2>/dev/null
  grep -h "Update:" /opt/qweb/list.m3u8 2>/dev/null | head -2
}

do_verify(){
  sec "VERIFY KENH (gia ma that bang ffmpeg)"
  python3 /opt/qweb/verify_channels.py "${2:-/opt/qweb/list.m3u8}" 2>&1
}

do_check(){
  sec "CHECK URL (nhanh: MPD + segment)"
  python3 /opt/qweb/check_urls.py "${2:-/opt/qweb/list.m3u8}" 2>&1
}

case "${1:-full}" in
  net)    check_net ;;
  sync)   check_sync ;;
  tunnel) check_tunnel ;;
  sys)    check_sys ;;
  syncnow) do_syncnow ;;
  verify) do_verify "$2" ;;
  check)  do_check "$2" ;;
  full)   check_net; check_services; check_sync; check_tunnel; check_sys ;;
  *)      echo "Dung: remote.sh [full|net|sync|tunnel|sys|syncnow|verify [file]|check [file]]" ;;
esac
echo
echo "--- Het --- $(date '+%d/%m %H:%M:%S')"
