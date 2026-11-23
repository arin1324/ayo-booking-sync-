#!/usr/bin/env bash
# Pemasangan AYO Booking Sync (M5). Dijalankan oleh PEMILIK:
#     sudo bash /opt/ayo-sync/deploy/install.sh
#
# Skrip ini hanya: pasang unit systemd, jalankan layanan, pasang Caddyfile,
# jalankan Caddy, pasang cron backup, lalu tampilkan verifikasi.
# Skrip TIDAK mengubah firewall dan TIDAK reboot (lakukan manual, lihat akhir).
set -euo pipefail

REPO=/opt/ayo-sync
ENV_FILE=/etc/ayo-sync/env
TOKEN_FILE=/etc/ayo-sync/mobile_token

if [ "${EUID}" -ne 0 ]; then
  echo "ERROR: jalankan dengan sudo." >&2
  exit 1
fi

if [ ! -s "$ENV_FILE" ] || ! grep -q '^AYO_API_KEY=.\{32,\}' "$ENV_FILE"; then
  echo "ERROR: $ENV_FILE belum ada, atau AYO_API_KEY belum diisi (minimal 32 karakter)." >&2
  echo "Selesaikan langkah konfigurasi (kunci API) lebih dulu." >&2
  exit 1
fi

if [ ! -s "$TOKEN_FILE" ]; then
  echo "PERINGATAN: $TOKEN_FILE kosong/belum ada."
  echo "Layanan tetap dipasang, tetapi belum memanggil AYO sampai token diisi."
fi

echo "== 1/3 systemd: ayo-sync =="
install -m 644 -o root -g root "$REPO/deploy/ayo-sync.service" /etc/systemd/system/ayo-sync.service
systemctl daemon-reload
systemctl enable --now ayo-sync
systemctl status ayo-sync --no-pager | sed -n '1,12p' || true
sleep 2
echo -n "health lokal: "
curl -s http://127.0.0.1:8000/health || echo "(belum ada respons)"
echo

echo "== 2/3 Caddy: HTTPS =="
install -m 644 -o root -g root "$REPO/deploy/Caddyfile" /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy 2>/dev/null || systemctl restart caddy
sleep 2
systemctl status caddy --no-pager | sed -n '1,12p' || true

echo "== 3/3 backup harian =="
install -m 644 -o root -g root "$REPO/deploy/ayo-sync-backup.cron" /etc/cron.d/ayo-sync-backup

cat <<'EOF'

== Terpasang. Sisa langkah manual (jalankan di sesi SSH Anda) ==
1) Firewall (jika belum dibuka):
     sudo ufw allow OpenSSH
     sudo ufw allow 80/tcp
     sudo ufw allow 443/tcp
     sudo ufw enable
2) Uji HTTPS dari internet:
     curl -s https://api.fitcitypadel.com/health; echo
3) Melihat log:
     sudo journalctl -u ayo-sync -n 50 --no-pager
EOF
