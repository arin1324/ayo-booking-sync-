#!/usr/bin/env bash
# Uji baca API dengan kunci asli, TANPA menampilkan data pribadi.
# Dijalankan oleh pemilik (butuh sudo untuk membaca /etc/ayo-sync/env):
#     sudo bash /opt/ayo-sync/deploy/smoke-api.sh
set -uo pipefail

ENV_FILE=/etc/ayo-sync/env
BASE=https://api.fitcitypadel.com

KEY="$(grep -oP '^AYO_API_KEY=\K.*' "$ENV_FILE" 2>/dev/null || true)"
if [ -z "${KEY:-}" ]; then
  echo "ERROR: tidak bisa membaca AYO_API_KEY dari $ENV_FILE (jalankan dengan sudo)." >&2
  exit 1
fi

echo "== GET /fields =="
curl -s --max-time 10 -H "X-API-Key: $KEY" "$BASE/fields"; echo

echo "== ringkasan GET /schedule (tanpa nama/ID pelanggan) =="
curl -s --max-time 10 -H "X-API-Key: $KEY" "$BASE/schedule" \
  | python3 -c 'import sys,json
d=json.load(sys.stdin)
b=d["bookings"]
print("from", d["from"], "to", d["to"], "count", d["count"])
print("contoh slot:", [(x["date"], x["field_name"], x["start_time"], x["status"]) for x in b[:5]])'

echo "== GET /schedule/availability hari ini 08:00 =="
TODAY="$(TZ=Asia/Makassar date +%F)"
curl -s --max-time 10 -H "X-API-Key: $KEY" \
  "$BASE/schedule/availability?date=$TODAY&time=08:00"; echo

echo "== cek kunci pada log (harus 0) =="
if [ "$(id -u)" -eq 0 ]; then
  echo "baris memuat 'mobile_token=' : $(journalctl -u ayo-sync --no-pager 2>/dev/null | grep -c 'mobile_token=' || true)"
  echo "baris memuat 'api-new.ayo.co.id' : $(journalctl -u ayo-sync --no-pager 2>/dev/null | grep -c 'api-new.ayo.co.id' || true)"
else
  echo "(lewati cek log: perlu sudo)"
fi
