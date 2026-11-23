#!/usr/bin/env bash
# Verifikasi baca-saja AYO Booking Sync. TIDAK perlu sudo:
#     bash /opt/ayo-sync/deploy/verify.sh
set -uo pipefail

echo "== status layanan =="
printf 'ayo-sync enabled=%s active=%s\n' "$(systemctl is-enabled ayo-sync 2>&1)" "$(systemctl is-active ayo-sync 2>&1)"
printf 'caddy    enabled=%s active=%s\n' "$(systemctl is-enabled caddy 2>&1)" "$(systemctl is-active caddy 2>&1)"

echo "== health lokal (127.0.0.1:8000) =="
curl -s --max-time 5 http://127.0.0.1:8000/health || echo "(tidak ada respons)"
echo
echo "== health publik (https://api.fitcitypadel.com) =="
curl -s --max-time 10 https://api.fitcitypadel.com/health || echo "(tidak ada respons)"
echo
echo "== autentikasi API (harus 401 tanpa kunci) =="
curl -s -o /dev/null -w 'GET /fields tanpa kunci -> %{http_code}\n' --max-time 10 https://api.fitcitypadel.com/fields || true

echo "== RAM uvicorn + caddy =="
ps -o rss= -C uvicorn,caddy | awk '{s+=$1} END {printf "%.0f MB\n", s/1024}'

echo "== port =="
ss -tlnp 2>/dev/null | grep -E ':(80|443|8000)\b' || echo "(tidak terlihat; mungkin butuh sudo)"
