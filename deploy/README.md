# Deploy AYO Booking Sync (M5)

Semua perintah `sudo` dijalankan **oleh pemilik** di sesi SSH. Agen tidak
menjalankan perintah berhak istimewa. Ganti `NAMA_USER_SSH` dengan user SSH Anda
(mis. `fitcitypadel`).

## 0. Prasyarat

```bash
dig +short api.fitcitypadel.com          # harus = IP VPS 202.155.16.141
sudo ufw status                          # 22, 80, 443 dibuka; SSH tetap aktif
```

## 1. Kunci API dan konfigurasi

```bash
sudo install -m 600 -o root -g root /dev/null /etc/ayo-sync/env
echo "AYO_API_KEY=$(openssl rand -hex 32)" | sudo tee -a /etc/ayo-sync/env
printf 'AYO_TZ=Asia/Makassar\nAYO_DB_PATH=/var/lib/ayo-sync/bookings.db\nAYO_TOKEN_FILE=/etc/ayo-sync/mobile_token\nLOG_LEVEL=INFO\n' | sudo tee -a /etc/ayo-sync/env
sudo chmod 600 /etc/ayo-sync/env
sudo cat /etc/ayo-sync/env    # JANGAN bagikan isinya
```

## 2. Token AYO (pemilik menulis sendiri, tidak lewat chat)

```bash
sudo install -m 600 -o ayo -g ayo /dev/null /etc/ayo-sync/mobile_token
sudo nano /etc/ayo-sync/mobile_token     # tempel satu baris token, simpan
```

## 3. Layanan systemd

```bash
sudo install -m 644 -o root -g root /opt/ayo-sync/deploy/ayo-sync.service /etc/systemd/system/ayo-sync.service
sudo systemctl daemon-reload
sudo systemctl enable --now ayo-sync
systemctl status ayo-sync --no-pager
journalctl -u ayo-sync -n 50 --no-pager
curl -s http://127.0.0.1:8000/health; echo
```

## 4. Caddy (HTTPS)

```bash
sudo install -m 644 -o root -g root /opt/ayo-sync/deploy/Caddyfile /etc/caddy/Caddyfile
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl enable --now caddy
systemctl status caddy --no-pager
curl -s https://api.fitcitypadel.com/health; echo
```

## 5. Firewall (jaga sesi SSH tetap terbuka)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

## 6. Backup harian

```bash
sudo install -m 644 -o root -g root /opt/ayo-sync/deploy/ayo-sync-backup.cron /etc/cron.d/ayo-sync-backup
sudo sqlite3 /var/lib/ayo-sync/bookings.db ".backup '/var/backups/ayo-sync/uji.db'" && ls -l /var/backups/ayo-sync/
```

## 7. Verifikasi penerimaan (M5)

```bash
# RAM total (target <= ~200 MB untuk aplikasi + Caddy)
systemctl status ayo-sync --no-pager | grep Memory
ps -o rss= -C uvicorn,caddy | awk '{s+=$1} END {print s/1024 " MB"}'

# Token uji tidak ada di log
journalctl -u ayo-sync --no-pager | grep -c TESTTOKEN123 || true

# Hidup otomatis setelah reboot
sudo reboot
```

Setelah reboot, ulangi `systemctl status ayo-sync` dan
`curl -s https://api.fitcitypadel.com/health`.

## Rollback

```bash
cd /opt/ayo-sync && git log --oneline      # pilih commit sebelumnya
git checkout <commit>
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
sudo systemctl restart ayo-sync
```
