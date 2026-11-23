# PRD: AYO Booking Sync

Versi: 1.0 (23 November 2026)
Pemilik produk: pemilik venue (pengguna proyek ini)
Dokumen terkait: `AGENTS.md` (aturan kerja agen), `SPECS.md` (spesifikasi teknis)

---

## 1. Latar belakang

Pemilik mengelola venue padel (6 lapangan) yang terdaftar di AYO. Pemesanan masuk dari dua jalur: pelanggan lewat aplikasi AYO (`order`) dan input manual oleh pengelola venue (`reservation`). Pemilik juga punya aplikasi sendiri dengan formulir sesi (tanggal, jam, lapangan, coach). Pada formulir itu, daftar lapangan harus menampilkan mana yang **tersedia** untuk tanggal dan jam yang dipilih, dan informasi itu harus sama dengan kalender di AYO.

Kalender booking AYO tersedia lewat endpoint internal aplikasi AYO (dipakai dengan `mobile_token` milik pemilik; AYO sudah mengonfirmasi bahwa ini boleh dipakai hanya untuk venue milik sendiri). Endpoint itu tidak menyediakan notifikasi perubahan dan tidak cocok dipanggil langsung dari aplikasi pelanggan setiap kali ada pengecekan. Karena itu dibutuhkan satu layanan perantara di VPS pemilik.

## 2. Tujuan

1. Menyediakan salinan kalender booking 30 hari ke depan (rolling) yang selalu diperbarui.
2. Mendeteksi dan mencatat setiap perubahan booking (baru, berubah, hilang, muncul lagi).
3. Menyimpan data dalam tabel database yang rapi dan bisa dikueri.
4. Menyediakan REST API agar aplikasi pemilik di server lain dapat membaca jadwal dan mengecek ketersediaan lapangan per tanggal dan jam.

## 3. Non-tujuan (di luar cakupan saat ini)

- Membuat, mengubah, atau membatalkan booking di AYO. Fitur itu butuh API resmi dan token resmi (lihat fase opsional M7).
- Antarmuka pengguna (web atau mobile). Layanan ini hanya API.
- Pembayaran, penagihan, dan laporan keuangan.
- Banyak venue sekaligus. Rancangan boleh memudahkan perluasan nanti, tetapi tidak dikerjakan sekarang.
- Menjamin 100% mencegah bentrok jadwal. Layanan memberi status "tersedia" menurut kondisi AYO pada saat dicek, bukan penguncian slot.

## 4. Pengguna dan kasus penggunaan

| Pengguna | Kebutuhan |
|---|---|
| Aplikasi pemilik (server ke server) | Mengecek ketersediaan lapangan saat pengguna memilih tanggal dan jam; menampilkan jadwal; mengikuti perubahan. |
| Pemilik / operator | Melihat apakah sinkronisasi sehat; mengganti token yang kedaluwarsa tanpa bongkar kode; memperoleh riwayat perubahan booking. |
| Agen AI | Membangun dan merawat layanan sesuai dokumen ini (AGENTS.md). |

User stories utama:

- US-1: Sebagai aplikasi pemilik, saat pengguna memilih "Sen, 23 Nov 2026" dan "08:00", saya memanggil API dan mendapat status tiap lapangan (tersedia, terisi, tutup, lewat, tidak diketahui) supaya dropdown "Lapangan" menampilkan "Lapangan A · Tersedia" dengan benar.
- US-2: Sebagai aplikasi pemilik, saya dapat mengambil seluruh jadwal 30 hari ke depan sekali, lalu hanya mengambil perubahan sesudahnya.
- US-3: Sebagai pemilik, saya tahu dalam hitungan menit jika sinkronisasi berhenti karena token kedaluwarsa, dan saya bisa memperbaikinya dengan mengganti satu file.
- US-4: Sebagai pemilik, saya dapat menelusuri kapan sebuah booking dibuat, diubah, atau hilang.
- US-5: Sebagai aplikasi pemilik, saya dapat mengambil daftar pelanggan yang pernah melakukan booking di venue (nama dan ringkasan jumlah slot; bila sinkronisasi pelanggan aktif, juga profil dari AYO) lewat `GET /customer`.

## 5. Kebutuhan fungsional

Prioritas: **P0** wajib, **P1** penting, **P2** opsional.

| ID | Kebutuhan | Prioritas |
|---|---|---|
| FR-1 | Mengambil kalender booking untuk jendela rolling: dari kemarin sampai 29 hari ke depan (30 hari mulai hari ini), memakai zona waktu venue. | P0 |
| FR-2 | Frekuensi ambil ulang berjenjang: tanggal dekat lebih sering, tanggal jauh lebih jarang (SPECS §5.2). | P0 |
| FR-3 | Menyimpan setiap booking di tabel `bookings`; tanggal berasal dari parameter request. | P0 |
| FR-4 | Mendeteksi perubahan dan mencatatnya di `booking_events` dengan jenis `created`, `updated`, `removed`, `restored`. | P0 |
| FR-5 | Booking yang tidak muncul lagi ditandai `removed_at`, tidak dihapus. Respons AYO yang tidak valid tidak boleh menyebabkan penandaan ini. | P0 |
| FR-6 | Pengambilan awal (baseline) tidak menghasilkan event. Event baru muncul setelah seluruh jendela berhasil diambil sekali. | P1 |
| FR-7 | Endpoint `GET /schedule/availability` mengembalikan status tiap lapangan untuk tanggal, jam, dan durasi tertentu. | P0 |
| FR-8 | `GET /schedule/availability` mengambil ulang tanggal terkait dari AYO bila data lebih tua dari 30 detik, dengan satu pengambilan dibagi bersama untuk semua pemanggil dan batas waktu tunggu pendek. | P0 |
| FR-9 | Endpoint `GET /schedule/availability/day` mengembalikan seluruh slot satu hari per lapangan. | P1 |
| FR-10 | Endpoint `GET /schedule` (jadwal per rentang tanggal) dan `GET /fields`. | P0 |
| FR-11 | Endpoint `GET /changes?since=` untuk sinkronisasi inkremental memakai nomor urut event. | P1 |
| FR-12 | Endpoint `GET /health` menampilkan status sinkronisasi: `ok`, `stale`, `auth_failed`. | P0 |
| FR-13 | Seluruh endpoint selain `/health` memerlukan kunci API. | P0 |
| FR-14 | Token AYO dibaca dari file setiap kali dipakai, sehingga bisa diganti tanpa restart. Saat token ditolak, polling berhenti agresif dan status menjadi `auth_failed`. | P0 |
| FR-15 | Backup database harian dengan retensi 7 hari. | P1 |
| FR-16 | Ekspor jadwal ke Excel untuk pemilik. | P2 |
| FR-17 | Endpoint `GET /customer` mengembalikan daftar pelanggan: akun AYO dan tamu (reservasi manual). Tanpa sinkronisasi pelanggan, daftar diturunkan dari data booking (SPECS §7.7). | P1 |
| FR-18 | Sinkronisasi daftar pelanggan dari AYO (`customer-lists`): penuh tiap 6 jam dan ringan tiap 15 menit, terpisah dari sinkronisasi kalender, **dimatikan secara bawaan** dan hanya diaktifkan setelah OD-9 dan OD-10 terjawab (SPECS §3.6, §5.7). | P1 |
| FR-19 | Telepon dan email pelanggan tidak tampil di API kecuali `AYO_EXPOSE_CUSTOMER_CONTACT=true`; tidak pernah masuk log, `/health`, atau log perubahan. | P0 |

## 6. Kebutuhan non-fungsional

| ID | Kebutuhan |
|---|---|
| NFR-1 Sumber daya | Berjalan di VPS 1 GB RAM / 1 vCPU. Total RAM aplikasi + proxy sekitar 200 MB atau kurang. Satu proses aplikasi. |
| NFR-2 Kesegaran | Perubahan pada tanggal hari ini dan besok terlihat di API dalam 2 menit (saat token valid). Tanggal jauh paling lama 1 jam. |
| NFR-3 Kinerja | `GET /schedule/availability` p95 ≤ 300 ms jika data masih segar; ≤ 6 detik jika memicu pengambilan ulang. |
| NFR-4 Ketahanan | Gangguan AYO atau jaringan tidak menghapus data dan tidak menjatuhkan API. Layanan hidup kembali otomatis setelah restart server. |
| NFR-5 Beban ke AYO | Latar belakang ≈ 2 request per menit. Pengambilan ulang saat dicek maksimal satu per tanggal per 30 detik. Maksimal 3 request bersamaan. |
| NFR-6 Keamanan | HTTPS, kunci API acak panjang, rahasia di luar kode dan log, hanya port 22/80/443 terbuka, API bind ke localhost di belakang proxy. |
| NFR-7 Privasi | Nama dan ID pelanggan hanya tampil di `/schedule`, `/changes`, dan `/customer`, dan dapat dimatikan lewat konfigurasi (`AYO_EXPOSE_CUSTOMER`). Telepon dan email dimatikan secara bawaan (`AYO_EXPOSE_CUSTOMER_CONTACT`). `/schedule/availability` tidak memuat data pelanggan. Data pribadi minimal yang disimpan; `credit_balance` dan `avatar` tidak pernah diekspos. |
| NFR-8 Perawatan | Pemilik cukup mengganti file token dan melihat `/health`. Log terbaca di `journalctl`. |

## 7. Fase dan milestone

| Milestone | Isi | Hasil yang dapat diperiksa |
|---|---|---|
| M0 | Persiapan server: user layanan, direktori, virtualenv, Caddy, firewall | Server siap; SSH tetap aman |
| M1 | Klien AYO, parser, skema database, algoritma diff | Test parser dan diff lulus dengan mock |
| M2 | Scheduler rolling, backoff, penanganan token kedaluwarsa | Test scheduler lulus; satu putaran `--once` berhasil |
| M3 | API baca (`/schedule`, `/fields`, `/changes`, `/health`) + kunci API | Test API lulus |
| M4 | `/schedule/availability` dan `/schedule/availability/day` + pembagian pengambilan + cache | Test ketersediaan dan konkurensi lulus |
| M5 | Deploy: systemd, Caddy HTTPS, backup, pengamatan | Layanan hidup sendiri setelah reboot; HTTPS jalan |
| M6 (bergantung OD-9 dan OD-10) | Sinkronisasi daftar pelanggan dari AYO (`customer-lists`) dan pengayaan `GET /customer` (telepon dan email hanya bila diizinkan) | Test T23 sampai T31 lulus; sinkronisasi penuh berhasil terhadap AYO asli |
| M7 (opsional) | API resmi AYO: penerima notifikasi (Notify Booking) dan Create Reservation; ekspor Excel; migrasi ke PostgreSQL bila perlu | Ditentukan setelah M5 |

Setiap milestone berakhir dengan laporan ke pemilik dan persetujuan sebelum lanjut.

## 8. Asumsi, ketergantungan, risiko

Asumsi:

- Enam lapangan tetap (ID 10479 sampai 10484). Penambahan lapangan dilakukan lewat konfigurasi.
- Zona waktu venue: **Asia/Makassar (WITA)** untuk menentukan "hari ini". Perlu dikonfirmasi (OD-2).
- Endpoint kalender AYO menerima satu tanggal per request dan beberapa `field_ids[]` sekaligus.

Ketergantungan:

- Endpoint internal AYO yang tidak terdokumentasi resmi.
- `mobile_token` pemilik.
- (Opsional, M6) Endpoint daftar pelanggan di `gateway.ayo.co.id`, beserta token dan `signature`-nya.
- Domain `api.fitcitypadel.com` untuk HTTPS. A record harus mengarah ke IP VPS sebelum Caddy dijalankan.

Risiko:

| Risiko | Dampak | Mitigasi |
|---|---|---|
| Token kedaluwarsa | Data berhenti diperbarui | Status `auth_failed`, status `unknown` pada ketersediaan bila data terlalu tua, token dibaca dari file; tanyakan AYO soal token tahan lama |
| Endpoint internal berubah tanpa pemberitahuan | Parser gagal | Validasi ketat respons, data tidak pernah dihapus karena respons tidak valid, alarm lewat `/health`; rencanakan peralihan ke API resmi |
| Pemesanan di AYO terjadi di antara pengecekan dan konfirmasi | Bentrok jadwal | Pengecekan ulang saat konfirmasi; jangka panjang: kirim pesanan ke AYO lewat API resmi (M7) |
| Status booking batal belum diketahui | Slot batal terhitung terisi | Konfigurasi `AYO_FREE_STATUSES`, catat status yang terlihat, validasi dengan data nyata |
| VPS kecil kehabisan memori | Layanan mati | Satu proses, tanpa dependency berat, systemd `Restart=always`, pantau RAM |
| Endpoint `customer-lists` memerlukan `signature` yang algoritmanya belum diketahui | Fitur pelanggan tidak dapat diaktifkan | Tanyakan ke AYO (OD-10); jangan menebak atau membongkar aplikasi; fallback: `/customer` diturunkan dari booking |
| Menyimpan telepon dan email pelanggan | Tanggung jawab atas data pribadi (di Indonesia diatur UU PDP; ini bukan nasihat hukum) | Simpan seperlunya, jangan di log atau fixture, ekspos hanya dengan flag, batasi dengan kunci API, izin file dan backup ketat |

## 9. Pertanyaan terbuka (perlu keputusan pemilik)

| ID | Pertanyaan | Default sementara | Menghalangi |
|---|---|---|---|
| OD-1 | Stack: Python/FastAPI + SQLite, atau Node/Express? | Python + SQLite | M1 |
| OD-2 | Zona waktu venue | Asia/Makassar | M2 |
| OD-3 | Domain untuk HTTPS | **Ditetapkan: `api.fitcitypadel.com`.** Tinggal memastikan DNS (A record) mengarah ke IP VPS | M5 |
| OD-4 | Nilai status booking batal di AYO yang sebenarnya | `CANCELLED,CANCELED,EXPIRED` (ditebak) | Akurasi M4 |
| OD-5 | Masa berlaku `mobile_token` dan apakah ada cara memperbarui otomatis atau token khusus server | Tidak diketahui; tanyakan ke AYO | Keandalan jangka panjang |
| OD-6 | Apakah pesanan dari aplikasi pemilik nanti dikirim ke AYO lewat API resmi (butuh email ke support@ayo.co.id untuk token resmi) | Belum | M7 |
| OD-7 | Contoh respons nyata dengan variasi status (batal, tertunda, dsb.) | Hanya `MANUAL` dan `FINISHED` yang terlihat | Akurasi M4 |
| OD-8 | Data kontak pelanggan (telepon, email) yang boleh dilihat aplikasi pemilik lewat `/customer` | Tidak diekspos (`AYO_EXPOSE_CUSTOMER_CONTACT=false`) sampai pemilik memutuskan | M6 |
| OD-9 | Izin AYO memakai endpoint `customer-lists` (memuat data pribadi pelanggan) dari server venue sendiri | Belum dikonfirmasi; fitur dimatikan | M6 (penghalang) |
| OD-10 | Cara menghitung `signature` pada request `customer-lists`, hubungan `token`-nya dengan `mobile_token`, dan masa berlakunya | Tidak diketahui; tidak boleh ditebak | M6 (penghalang) |
| OD-11 | Arti `venue_id=0` (semua venue atau bawaan aplikasi) dan batas maksimum `limit` | Kirim seperti aplikasi; `limit` 50 | M6 |

Catatan observasi (2026-11-23, sinkronisasi nyata pertama):

- Jam operasional venue dari respons AYO: **08:00–23:00**, sesi 60 menit (catatan lama 07:00–22:00 tidak dipakai lagi).
- Status booking yang terlihat pada `/schedule`: `MANUAL`. Status batal belum terlihat; OD-4 dan OD-7 tetap terbuka.

## 10. Kriteria keberhasilan

- Pada pengecekan sampel, tidak ada kasus "tersedia" untuk slot yang sudah terlihat terisi di AYO lebih dari 1 menit sebelumnya (hari ini dan besok).
- Perubahan booking hari ini dan besok muncul di `/changes` dalam 2 menit (p95).
- `/schedule/availability` memenuhi NFR-3 pada uji 20 pemanggil bersamaan, dan hanya menghasilkan 1 request ke AYO per tanggal per jendela 30 detik.
- Layanan pulih otomatis setelah reboot dan setelah penggantian token, tanpa intervensi kode.
- RAM total layanan ≤ sekitar 200 MB pada pemakaian normal.

## 11. Glosarium

- **Order**: booking yang dibuat pelanggan lewat aplikasi AYO (`source_table = order`).
- **Reservation**: booking yang dibuat manual oleh pengelola venue (`source_table = reservation`).
- **Slot**: satu sesi 60 menit pada satu lapangan.
- **Baseline**: pengambilan penuh pertama; tidak menghasilkan event.
- **Rolling**: jendela tanggal yang bergeser mengikuti hari ini.
- **Stale**: data lebih tua dari batas kesegaran.
- **Coalescing**: banyak pemanggil berbagi satu pengambilan ke AYO yang sama.
