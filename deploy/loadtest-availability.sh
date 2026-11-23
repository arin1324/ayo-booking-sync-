#!/usr/bin/env bash
# Uji beban baca-saja: 20 pemanggil bersamaan ke /schedule/availability (NFR-3, M5).
#
# Jalankan sebagai root agar dapat membaca /etc/ayo-sync/env tanpa menampilkan
# rahasia apa pun:
#     sudo bash /opt/ayo-sync/deploy/loadtest-availability.sh [tanggal] [jam] [jumlah]
# Contoh:
#     sudo bash /opt/ayo-sync/deploy/loadtest-availability.sh
#     sudo bash /opt/ayo-sync/deploy/loadtest-availability.sh 2026-11-25 20:00 20
#
# Keluaran: kode status, ringkasan jumlah, latensi rata-rata/p95/maks. Tanpa kunci.
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/ayo-sync/env}"
BASE="${BASE:-http://127.0.0.1:8000}"

DATE="${1:-$(date -d '+2 days' +%F)}"
TIME="${2:-20:00}"
N="${3:-20}"

KEY="$(sed -n 's/^AYO_API_KEY=//p' "$ENV_FILE" | head -n1)"
if [ -z "$KEY" ]; then
  echo "AYO_API_KEY tidak ditemukan di $ENV_FILE" >&2
  exit 1
fi

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "== uji beban: $N permintaan bersamaan =="
echo "target : ${BASE}/schedule/availability?date=${DATE}&time=${TIME}"
echo "mulai  : $(date -u '+%Y-%m-%dT%H:%M:%SZ')"

url="${BASE}/schedule/availability?date=${DATE}&time=${TIME}&duration=60"
start_ns="$(date +%s%N)"
for i in $(seq 1 "$N"); do
  (
    curl -s -o /dev/null -w '%{http_code} %{time_total}\n' \
      -H "X-API-Key: ${KEY}" "$url" >"$tmp/$i"
  ) &
done
wait
end_ns="$(date +%s%N)"

cat "$tmp"/* | awk -v total_s="$(( (end_ns - start_ns) / 1000000 ))" '
{
  code[$1]++
  n += 1
  t[n] = $2 + 0
  sum += $2
}
END {
  # urutkan latensi
  for (i = 1; i <= n; i++) {
    for (j = i + 1; j <= n; j++) {
      if (t[j] < t[i]) { tmpv = t[i]; t[i] = t[j]; t[j] = tmpv }
    }
  }
  p95 = (n > 0) ? t[int((n * 95 + 99) / 100)] : 0
  printf "\n== kode status ==\n"
  for (c in code) printf "  %s -> %d\n", c, code[c]
  printf "\n== latensi (detik) ==\n"
  printf "  rata-rata : %.3f\n", (n > 0 ? sum / n : 0)
  printf "  p95       : %.3f\n", p95
  printf "  maks      : %.3f\n", (n > 0 ? t[n] : 0)
  printf "  total     : %d permintaan dalam %.3f detik\n", n, total_s / 1000
}'

echo
echo "Catatan: 200 = dilayani; 429 = kena batas laju (wajar bila burst >> batas per menit);"
echo "         5xx = masalah. Tidak ada rahasia yang ditampilkan."
