# AGENTS.md

Panduan kerja untuk agen AI yang membangun dan merawat **AYO Booking Sync** di VPS milik pemilik.

Urutan baca: `AGENTS.md` (file ini) → `PRD.md` (apa dan kenapa) → `SPECS.md` (bagaimana).
Jika isi tiga file bertentangan, yang berlaku: AGENTS.md (aturan keselamatan) > SPECS.md > PRD.md.
Jika ragu, tanya pemilik. Jangan menebak, terutama untuk hal yang menyangkut data pelanggan, rahasia, atau server.

Versi: 1.0 (23 November 2026)

---

## 1. Ringkasan proyek

Layanan kecil di VPS yang:

1. mengambil kalender booking venue milik pemilik dari AYO untuk 30 hari ke depan (rolling),
2. menyimpannya di database dan mencatat setiap perubahan booking,
3. menyediakan REST API agar aplikasi pemilik (di server lain) dapat membaca jadwal dan mengecek ketersediaan lapangan berdasarkan tanggal dan jam.

Layanan ini hanya membaca dari AYO. Ia tidak membuat, mengubah, atau membatalkan booking di AYO.

Data venue (bukan rahasia, tetapi jangan dipakai di luar proyek ini):

- Venue ID: `3749`
- API produksi: `https://api.fitcitypadel.com` (Caddy, HTTPS)
- Lapangan: `10479` sampai `10484` (Lapangan A sampai F), olahraga Padel, sesi 60 menit, jam buka 07:00 sampai 22:00 (dibaca dari respons AYO, jangan di-hardcode)
- Sumber data: endpoint internal aplikasi AYO (`reservations-calendar`) yang memakai `mobile_token` milik pemilik. Pemilik sudah mengonfirmasi ke AYO bahwa pemakaian ini diizinkan hanya untuk venue miliknya sendiri. Sumber kedua (opsional, M6): daftar pelanggan di `gateway.ayo.co.id`. **Belum boleh dikerjakan** sebelum izin dan cara `signature`-nya dikonfirmasi AYO (PRD OD-9 dan OD-10).

## 2. Lingkungan

- VPS Ubuntu Server, **1 GB RAM, 1 vCPU**. Total pemakaian RAM layanan (aplikasi + Caddy) harus tetap di bawah sekitar 200 MB.
- Stack (keputusan awal, lihat SPECS §1): Python 3.12, FastAPI, uvicorn (satu proses, satu worker), httpx, SQLite mode WAL, Caddy (HTTPS), systemd.
- Jangan memasang Docker, PostgreSQL, Redis, atau layanan berat lain tanpa persetujuan pemilik.
- Aplikasi pemilik berjalan di server lain dan hanya berinteraksi lewat REST API layanan ini. Jangan membuka akses database langsung.

## 3. Struktur repositori

```
ayo-booking-sync/
  AGENTS.md  PRD.md  SPECS.md
  app/
    config.py        # baca environment, validasi
    clock.py         # now() yang bisa diganti saat test
    ayo_client.py    # panggil AYO, validasi respons, deteksi error token
    db.py            # koneksi, skema, transaksi
    sync.py          # parser, diff, scheduler, coalescing
    availability.py  # perhitungan ketersediaan
    api.py           # endpoint FastAPI
    main.py          # app + lifespan (scheduler berjalan di sini)
  tests/
    fixtures/        # contoh respons AYO (nama pelanggan dianonimkan)
    mock_ayo.py      # server AYO tiruan
    test_*.py
  deploy/
    ayo-sync.service  Caddyfile  env.example
  requirements.txt
  .gitignore         # wajib memuat: .env, *.db, *.db-wal, *.db-shm, token, __pycache__
```

Lokasi di server (lihat SPECS §9): kode `/opt/ayo-sync`, data `/var/lib/ayo-sync`, konfigurasi dan rahasia `/etc/ayo-sync`.

## 4. Aturan keras

### 4.1 Rahasia

- `mobile_token`, API key layanan ini, dan kredensial lain **tidak boleh** muncul di kode, git, log, output terminal yang kamu tampilkan ke pemilik, laporan, atau pesan commit.
- URL permintaan ke AYO memuat token di query string. **Jangan pernah mencetak atau me-log URL lengkap.** Logger `httpx`, `httpcore`, dan `urllib3` harus diset ke WARNING atau lebih tinggi karena pada level INFO mereka mencetak URL.
- Saat menampilkan konfigurasi, samarkan nilai rahasia (contoh: `abcd…wxyz` atau `***`).
- Token dan API key disimpan di `/etc/ayo-sync/` dengan izin `600`, dimiliki user layanan.
- Jika rahasia terlanjur tercetak atau ter-commit: hentikan pekerjaan, beri tahu pemilik agar memutar (rotate) rahasia itu, dan bersihkan jejaknya. Jangan menyembunyikan kejadiannya.

### 4.2 Interaksi dengan AYO

- Hanya request `GET` ke endpoint kalender, hanya untuk venue ID dan field ID dari konfigurasi. Jangan menjelajah ID lain dan jangan memanggil endpoint lain, kecuali daftar pelanggan (`gateway.ayo.co.id`, `customer-lists`) **setelah** OD-9 dan OD-10 terjawab dan pemilik mengaktifkan fitur itu.
- Patuhi batas laju di SPECS §5. Jangan menaikkan frekuensi atau paralelisme tanpa persetujuan pemilik. Maksimal 3 request bersamaan ke AYO.
- Pengujian memakai `tests/mock_ayo.py`, bukan AYO asli. Uji ke AYO asli hanya jika pemilik memintanya, dan hanya beberapa request (satu putaran `--once` sudah cukup).
- Jika AYO membalas 401 atau 403, anggap token kedaluwarsa: berhenti polling agresif, tandai `auth_failed`, dan beri tahu pemilik. Jangan mencoba menebak atau mengambil token dengan cara lain.
- Jangan membongkar (reverse engineering atau decompile) aplikasi AYO, menyadap lalu memakai ulang `signature` dari aplikasi, atau menebak algoritma tanda tangan. Jika sebuah endpoint meminta `signature` yang belum didokumentasikan, minta pemilik menanyakan ke AYO.

### 4.3 Data

- Jangan menghapus data booking. Booking yang tidak muncul lagi hanya ditandai `removed_at`.
- Jangan menjalankan `DROP`, `DELETE`, atau `UPDATE` massal pada database produksi tanpa konfirmasi eksplisit dan tanpa backup terbaru.
- Nama pelanggan adalah data pribadi (termasuk daftar dari `GET /customer`). Jangan mengirimkannya ke layanan pihak ketiga, jangan menaruhnya di fixture tanpa anonimisasi, dan jangan menampilkannya di laporan kecuali perlu.
- Nomor telepon dan email pelanggan: jangan ditampilkan di log, laporan, atau respons API kecuali `AYO_EXPOSE_CUSTOMER_CONTACT=true`. Jangan menyalin contoh respons asli dari pemilik ke repositori; anonimkan dahulu. Backup database yang memuat data ini wajib berizin ketat.

### 4.4 Server

Tindakan berikut **memerlukan konfirmasi pemilik** sebelum dijalankan. Jelaskan perintahnya dan alasannya terlebih dahulu:

- `apt install/remove`, `pip install` di luar virtualenv proyek
- mengubah firewall (`ufw`), konfigurasi SSH, atau user sistem
- `systemctl stop/restart/disable` untuk layanan selain `ayo-sync`
- mengubah izin atau isi `/etc` di luar `/etc/ayo-sync`
- menghapus file di luar direktori proyek
- reboot

Aturan keselamatan tambahan:

- Sebelum `ufw enable`, pastikan SSH sudah diizinkan (`ufw allow OpenSSH`) dan biarkan sesi SSH aktif tetap terbuka sampai aturan terbukti aman.
- Hanya port 22, 80, dan 443 yang terbuka. Aplikasi mendengarkan di `127.0.0.1`, bukan `0.0.0.0`.
- Jangan mematikan firewall atau TLS untuk "mempermudah" pengujian.
- Tambahkan dependency baru hanya jika perlu, dan kunci versinya di `requirements.txt` (pin versi yang benar-benar terpasang).

### 4.5 Kejujuran hasil

- Jangan menyatakan sesuatu "sudah berjalan" atau "sudah teruji" tanpa bukti (output test, log, atau respons nyata).
- Bedakan secara tegas: **teruji dengan mock**, **teruji terhadap AYO asli**, dan **belum teruji**.
- Jika menemukan perilaku AYO yang tidak sesuai dugaan di dokumen ini (misalnya status batal yang berbeda), laporkan, jangan menyesuaikan diam-diam.

## 5. Cara kerja

1. Baca ketiga dokumen. Catat pertanyaan terbuka (PRD §9) yang menghalangi pekerjaanmu.
2. Kerjakan berurutan per milestone (M0 sampai M5, lihat PRD §7; M6 hanya setelah OD-9 dan OD-10 terjawab). Jangan melompat.
3. Untuk tiap milestone: tulis rencana singkat → implementasi dalam langkah kecil → jalankan test → tulis laporan → tunggu persetujuan pemilik sebelum lanjut atau sebelum deploy ke produksi.
4. Commit kecil dengan pesan jelas. Satu commit, satu tujuan.
5. Setelah deploy, verifikasi dengan `systemctl status`, `journalctl`, dan `GET /health`, lalu laporkan hasilnya.

## 6. Standar kode

- Python 3.12, type hints pada fungsi publik, fungsi kecil dan satu tanggung jawab.
- Seluruh waktu "sekarang" diambil dari `clock.now()` agar bisa disimulasikan di test.
- Tanggal dan jam booking disimpan sebagai waktu lokal venue (`YYYY-MM-DD`, `HH:MM`). Timestamp sistem disimpan dalam UTC format ISO 8601 berakhiran `Z`.
- Kode error di API memakai `snake_case` bahasa Inggris. Pesan untuk manusia dalam bahasa Indonesia.
- Log: satu baris per kejadian, tanpa data rahasia, tanpa nama pelanggan.
- Tangani kegagalan secara eksplisit. Respons AYO yang tidak valid tidak boleh pernah menyebabkan data dihapus atau ditandai hilang.

## 7. Pengujian

- `pytest`, dengan `tests/mock_ayo.py` sebagai AYO tiruan (server lokal yang responsnya bisa diubah per test).
- Daftar kasus uji wajib ada di SPECS §11. Fungsi diff, perhitungan ketersediaan, penanganan token kedaluwarsa, dan "token tidak bocor ke log" **wajib** punya test.
- Fixture dibuat dari contoh respons AYO yang diberikan pemilik. Nama pelanggan diganti nama samaran. Contoh minimal ada di SPECS Lampiran A.

## 8. Definition of done (per milestone)

- Semua test milestone lulus, dan tidak ada test lama yang rusak.
- Tidak ada rahasia di diff, log, atau output (periksa dengan `grep` terhadap token uji).
- Perilaku sesuai SPECS, atau penyimpangan dicatat dan disetujui pemilik.
- Dokumentasi yang terpengaruh (SPECS atau README) diperbarui.
- Laporan ke pemilik sudah dikirim (format di §10).

## 9. Hal yang sudah diketahui (jebakan)

- Respons AYO **tidak memuat tanggal** di tiap booking. Tanggal berasal dari parameter `date` pada request.
- Satu booking = satu slot jam. Booking dua jam muncul sebagai dua baris.
- Kunci unik booking adalah gabungan `source_table` (`order` atau `reservation`) dan `id`. ID bisa sama di dua tabel sumber.
- Untuk `reservation`, field `status` bernilai `null`. Pakai `final_status` (nilai yang terlihat: `MANUAL`, `FINISHED`). Nilai status untuk booking batal **belum pernah terlihat**; jangan berasumsi (lihat SPECS §6.3).
- `total_price` bernilai `0` atau `1` pada reservasi manual kemungkinan placeholder, bukan harga sebenarnya.
- `created_at` dalam UTC. `signature` pada respons bernilai `null` (tidak ada tanda tangan di endpoint ini).
- Parameter lapangan dikirim berulang: `field_ids[]=10479&field_ids[]=10480&...`.
- `mobile_token` adalah token sesi aplikasi dan bisa kedaluwarsa kapan saja. Masa berlakunya belum diketahui.
- Daftar pelanggan (M6): `id` pelanggan sama dengan `user_id` pada booking kalender, dan `customer_id = 0` pada booking berarti reservasi manual atau tamu tanpa akun. Telepon berformat `62…` tanpa tanda `+`. `signature` pada respons bernilai `null`, tetapi **request**-nya memerlukan `signature` yang algoritmanya belum diketahui. Paginasi berbasis offset dan bisa bergeser saat data berubah; `total` dipakai untuk menentukan akhir. `credit_balance` dan `avatar` tidak boleh diekspos.

## 10. Format laporan ke pemilik

Bahasa Indonesia, ringkas, dengan urutan:

1. **Dikerjakan**: apa yang selesai.
2. **Bukti**: perintah dan hasil test/log yang relevan (tanpa rahasia).
3. **Status pengujian**: mock / AYO asli / belum.
4. **Risiko atau temuan**: hal tak terduga.
5. **Butuh keputusan**: pertanyaan yang menghalangi langkah berikutnya (maksimal 3).
6. **Langkah berikutnya**.

## 11. Yang harus ditanyakan ke pemilik (jangan diputuskan sendiri)

Lihat PRD §9. Ringkasnya: zona waktu venue, status booking batal yang sebenarnya, masa berlaku token, izin dan cara `signature` untuk daftar pelanggan (OD-9, OD-10), data kontak yang boleh diekspos (OD-8), arti `venue_id=0` dan batas `limit` (OD-11), dan apakah pesanan dari aplikasi pemilik nanti dikirim ke AYO lewat API resmi. Domain sudah ditetapkan (`api.fitcitypadel.com`); pastikan DNS mengarah ke VPS sebelum menjalankan Caddy.
