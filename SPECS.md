# SPECS: AYO Booking Sync

Versi: 1.0 (23 November 2026)
Dokumen terkait: `AGENTS.md` (aturan kerja agen), `PRD.md` (kebutuhan produk)

Kata "wajib" (MUST), "dilarang" (MUST NOT), dan "sebaiknya" (SHOULD) dipakai dengan arti baku. Nilai bawaan boleh diubah lewat konfigurasi (§2), tidak lewat kode.

---

## 1. Arsitektur dan stack

```
 Server AYO  <──GET per tanggal──  [ VPS: satu proses ]  <──HTTPS + X-API-Key──  Aplikasi pemilik
 (kalender)                          scheduler (latar belakang)                    (server lain)
                                     API (FastAPI)
                                     SQLite (WAL)
                                     Caddy (TLS, reverse proxy)
```

Keputusan awal (lihat PRD OD-1; ubah hanya atas persetujuan pemilik):

| Komponen | Pilihan | Catatan |
|---|---|---|
| Bahasa | Python 3.12 | Bawaan Ubuntu 24.04 |
| Web | FastAPI + uvicorn, **satu worker** | Dokumentasi otomatis (`/docs`, `/openapi.json`) dimatikan di produksi |
| HTTP client | httpx (`AsyncClient`, reuse koneksi) | Logger httpx/httpcore wajib WARNING+ (URL memuat token) |
| Database | SQLite (stdlib `sqlite3`), mode WAL | Satu penulis di dalam proses; akses lewat `asyncio.to_thread` atau fungsi sinkron dalam threadpool |
| Proxy | Caddy | TLS otomatis; aplikasi hanya bind `127.0.0.1` |
| Proses | systemd | `Restart=always` |

Alasan **satu proses**: scheduler latar belakang dan permintaan `/schedule/availability` berbagi state (kunci per tanggal, `last_ok_at`, pembagian pengambilan). Banyak worker akan merusak itu dan tidak menambah kecepatan di 1 vCPU.

Seluruh logika memakai `clock.now()` (waktu UTC, dapat diganti saat test) dan `clock.venue_now()` (waktu lokal venue memakai `AYO_TZ`).

Sumber data kedua (opsional, milestone M6): daftar pelanggan dari `gateway.ayo.co.id` (§3.6, §5.7). Fitur ini **dimatikan** secara bawaan dan tidak boleh dikerjakan sebelum izin dan cara `signature`-nya dikonfirmasi AYO (PRD OD-9 dan OD-10).

## 2. Konfigurasi

Dibaca dari environment (`EnvironmentFile=/etc/ayo-sync/env`). Aplikasi wajib gagal start dengan pesan jelas bila nilai wajib kosong atau tidak valid.

| Variabel | Bawaan | Keterangan |
|---|---|---|
| `AYO_BASE_URL` | `https://api-new.ayo.co.id/api/v2/venue_booking/path` | Diganti ke mock saat test |
| `AYO_VENUE_ID` | `3749` | |
| `AYO_FIELD_IDS` | `10479,10480,10481,10482,10483,10484` | Pisahkan koma |
| `AYO_TZ` | `Asia/Makassar` | Zona waktu venue (OD-2) |
| `AYO_TOKEN_FILE` | `/etc/ayo-sync/mobile_token` | Isi: satu baris token. Dibaca saat dipakai (cache berdasarkan mtime) |
| `AYO_DB_PATH` | `/var/lib/ayo-sync/bookings.db` | |
| `AYO_API_KEY` | (wajib) | Minimal 32 karakter. Jika kosong atau pendek, aplikasi tidak boleh start |
| `AYO_WINDOW_DAYS` | `30` | Hari ini sampai hari ke-29 |
| `AYO_POLL_YESTERDAY` | `3600` | Detik |
| `AYO_POLL_NEAR` | `120` | Hari ini dan besok |
| `AYO_POLL_MID` | `600` | Hari ke-3 sampai ke-7 |
| `AYO_POLL_FAR` | `3600` | Hari ke-8 sampai ke-30 |
| `AYO_HTTP_TIMEOUT` | `15` | Detik, pengambilan latar belakang |
| `AVAIL_FETCH_TIMEOUT` | `5` | Detik, pengambilan saat `/schedule/availability` |
| `AVAIL_MAX_AGE` | `30` | Detik; data lebih tua memicu pengambilan ulang |
| `AVAIL_MIN_RETRY` | `10` | Detik; jeda minimum antar percobaan pada tanggal yang sama |
| `AVAIL_STALE_LIMIT` | `600` | Detik; lebih tua dari ini dan pengambilan ulang gagal → status `unknown` |
| `AYO_FREE_STATUSES` | `CANCELLED,CANCELED,EXPIRED` | Status yang **tidak** menempati slot (OD-4). Dibandingkan tanpa peka huruf besar/kecil |
| `AYO_EXPOSE_CUSTOMER` | `true` | Jika `false`, field `customer` dan `customer_id` dihilangkan dari API, dan `GET /customer` ditolak (403 `feature_disabled`) |
| `AYO_EXPOSE_CUSTOMER_CONTACT` | `false` | Jika `true`, `phone` dan `email` pelanggan boleh tampil di `GET /customer`. Bawaan `false` (minimisasi data pribadi) |
| `AYO_CUSTOMER_ENABLED` | `false` | Mengaktifkan sinkronisasi daftar pelanggan (§5.7). Jangan aktifkan sebelum OD-9 dan OD-10 terjawab |
| `AYO_CUSTOMER_BASE_URL` | `https://gateway.ayo.co.id/api/v1/venue-owner` | |
| `AYO_CUSTOMER_OWNER_USER_ID` | (wajib bila fitur aktif) | ID akun pemilik, parameter `user_id`. Dibaca dari konfigurasi, tidak ditulis di kode |
| `AYO_CUSTOMER_VENUE_ID` | `0` | Dikirim di path dan query seperti aplikasi. Arti `0` belum dipastikan (OD-11) |
| `AYO_CUSTOMER_TOKEN_FILE` | `/etc/ayo-sync/customer_token` | Terpisah dari token kalender; hubungan keduanya belum diketahui (OD-10). Kredensial penanda tangan, bila diperlukan, memakai file terpisah di direktori yang sama (nama ditetapkan setelah OD-10) |
| `AYO_CUSTOMER_PAGE_SIZE` | `50` | Batas maksimum yang diterima AYO belum diketahui (OD-11); mulai kecil |
| `AYO_CUSTOMER_FULL_POLL` | `21600` | Detik; sinkronisasi penuh |
| `AYO_CUSTOMER_LIGHT_POLL` | `900` | Detik; pembaruan ringan (halaman pertama) |
| `EVENT_RETENTION_DAYS` | `60` | |
| `BIND_HOST` / `BIND_PORT` | `127.0.0.1` / `8000` | |
| `LOG_LEVEL` | `INFO` | |

## 3. Integrasi AYO

### 3.1 Request

```
GET {AYO_BASE_URL}
    ?url_path=venue/{AYO_VENUE_ID}/reservations-calendar
    &date={YYYY-MM-DD}
    &mobile_token={TOKEN}
    &field_ids[]=10479&field_ids[]=10480&...      # satu pasangan per lapangan
Header: Accept: application/json
```

- Satu request mencakup **satu tanggal** dan **semua lapangan**.
- Token dibaca dari `AYO_TOKEN_FILE`. Dilarang mencetak atau me-log URL, query, atau token. Exception dari httpx hanya dicatat nama kelasnya, bukan pesannya (pesan bisa memuat URL).
- Hanya metode GET. Tidak ada endpoint lain yang dipanggil.

### 3.2 Bentuk respons yang diketahui

```
{ "error": false, "message": "Berhasil",
  "data": [ { "field_name", "session_duration", "sport_name", "sport_id", "field_id",
              "data": [ <booking>, ... ], "start_time": "07:00:00", "end_time": "22:00:00" }, ... ],
  "status_code": 200, "signature": null, "execution_time": 0.002 }
```

Booking (field yang dipakai): `id`, `user_name`, `start_time`, `end_time`, `created_at`, `status` (null untuk reservation), `final_status`, `total_price`, `payment_method`, `payment_rule`, `source_table` (`order` atau `reservation`), `add_on_group_name` (null atau array string). Field lain disimpan apa adanya di kolom `raw`. Respons **tidak memuat tanggal** booking.

### 3.3 Validasi respons (gerbang sebelum menyimpan)

Respons dianggap valid bila: HTTP 200, badan berupa objek JSON, `error` bukan `true`, `status_code` (jika ada) bernilai 200, dan `data` berupa array. Lapangan yang diminta tetapi tidak ada di `data` dianggap **tidak ada informasi** untuk lapangan itu (tidak boleh memicu penandaan `removed`, lihat §5.6).

### 3.4 Klasifikasi error

| Kondisi | Jenis | Tindakan |
|---|---|---|
| HTTP 401/403; atau `status_code` badan 401/403; atau `error: true` dengan pesan berisi `unauth` atau `token` (tanpa peka huruf besar/kecil) | `AuthError` | §5.4 |
| Timeout, koneksi gagal, HTTP 5xx, HTTP 4xx lain, bukan JSON, bentuk tidak valid | `FetchError` | Backoff §5.4 |

Aturan pesan berisi `unauth`/`token` adalah heuristik; periksa dengan respons nyata saat M2 dan laporkan ke pemilik.

### 3.5 Pemetaan ke database

| Sumber | Kolom `bookings` | Aturan |
|---|---|---|
| parameter `date` | `booking_date` | Apa adanya |
| `source_table` | `source` | Jika kosong: `unknown` |
| `id` | `booking_id` | Integer |
| `field_id` (level lapangan) | `field_id` | |
| `start_time`, `end_time` | `start_time`, `end_time` | Potong ke `HH:MM` |
| `user_name` | `customer` | Kosong → `''` |
| `user_id` | `customer_id` | Integer; kosong atau null → `0` (0 = reservasi manual atau tamu tanpa akun) |
| `final_status`, jika kosong `status` | `status` | Simpan huruf besar, kosong → `''` |
| `total_price` | `price` | Bulatkan ke integer, kosong → 0 |
| `payment_method`, `payment_rule` | sama | Kosong/null → `''` |
| `add_on_group_name` | `addons` | Gabung dengan `, `; null → `''` |
| `created_at` | `created_at` | Apa adanya (UTC) |
| seluruh objek booking | `raw` | JSON dengan kunci diurutkan |

### 3.6 Daftar pelanggan (opsional, milestone M6)

Sumber data kedua dengan host, versi API, dan autentikasi yang **berbeda** dari kalender. Fitur ini **dimatikan** secara bawaan (`AYO_CUSTOMER_ENABLED=false`) dan **tidak boleh dikerjakan** sebelum OD-9 (izin) dan OD-10 (cara `signature` dan hubungan token) terjawab. Tanpa fitur ini, `GET /customer` tetap berfungsi dalam mode turunan dari data booking (§7.7).

Bentuk request yang teramati dari aplikasi pemilik:

```
GET https://gateway.ayo.co.id/api/v1/venue-owner/venue/{AYO_CUSTOMER_VENUE_ID}/customer-lists
    ?token={CUSTOMER_TOKEN}
    &user_id={AYO_CUSTOMER_OWNER_USER_ID}
    &venue_id={AYO_CUSTOMER_VENUE_ID}
    &start_from={offset}
    &limit={page_size}
    &with_trashed=true
    &sort_by=latest_desc
    &signature={SIGNATURE}
```

- Paginasi berbasis offset (`start_from`) dan `limit`. Respons memuat `total` (jumlah seluruh pelanggan; nilai teramati: 529).
- `with_trashed=true` wajib agar akun yang dihapus tetap ada (`is_deleted_account`, `deleted_date`).
- `sort_by=latest_desc` mengurutkan dari pelanggan dengan booking terbaru, sehingga pelanggan baru atau aktif muncul di halaman pertama.
- `user_id` adalah ID akun pemilik (nilai teramati sama dengan `created_by` pada reservasi manual di kalender).
- `venue_id`: aplikasi mengirim `0` di path dan query. Artinya belum dipastikan (kemungkinan "semua venue milik akun", OD-11). Kirim persis seperti aplikasi, kecuali AYO menyatakan lain. Jika akun memiliki lebih dari satu venue, daftar bisa memuat pelanggan venue lain; laporkan ke pemilik.
- `signature`: **algoritma dan masukannya belum diketahui (OD-10).** Agen dilarang menebak algoritma, membongkar (reverse engineering) aplikasi AYO, atau memakai ulang `signature` yang disalin dari aplikasi. Implementasi menunggu jawaban resmi AYO. Siapkan sebagai fungsi terpisah (`sign(params) -> str`) agar mudah dilengkapi.
- Token fitur ini dibaca dari `AYO_CUSTOMER_TOKEN_FILE`. Aturan §3.1 berlaku sama: URL, token, dan `signature` tidak boleh di-log, dan hanya metode GET.

Bentuk respons yang diketahui:

```
{ "data": [ <pelanggan>, ... ], "error": false, "message": "Data pelanggan venue",
  "signature": null, "status_code": 200, "total": 529 }
```

Objek pelanggan: `id`, `combined_name`, `first_name`, `username`, `email`, `phone`, `latest_booking_date`, `total_booking_success`, `total_booking_cancelled`, `is_deleted_account`, `deleted_date`, `credit_balance`, `avatar`.

Validasi respons: HTTP 200, objek JSON, `error` bukan `true`, `status_code` 200, `data` berupa array, `total` bilangan bulat. Klasifikasi error sama dengan §3.4 (`AuthError` dan `FetchError`).

Pemetaan ke tabel `customers`:

| Sumber | Kolom | Aturan |
|---|---|---|
| `id` | `customer_id` | Integer |
| `combined_name` | `name` | Kosong → `''` |
| `first_name`, `username` | `first_name`, `username` | Kosong → `''` |
| `email` | `email` | Hapus spasi di tepi; kosong → `''` |
| `phone` | `phone` | Apa adanya (format teramati `62…` tanpa tanda `+`) |
| `latest_booking_date` | `latest_booking_date` | `YYYY-MM-DD` atau `NULL` |
| `total_booking_success`, `total_booking_cancelled` | sama | Integer, kosong → 0 |
| `is_deleted_account`, `deleted_date` | sama | |
| seluruh objek | `raw` | JSON kunci terurut. `credit_balance` dan `avatar` hanya ada di sini dan **tidak pernah diekspos** |

Pengamatan: `id` pelanggan sama dengan `user_id` pada booking kalender (empat pelanggan contoh cocok), sehingga `bookings.customer_id` dapat dihubungkan langsung ke `customers.customer_id`. `customer_id = 0` pada booking berarti reservasi manual atau tamu tanpa akun, dan tidak memiliki baris di `customers`.

## 4. Model data (SQLite)

Pragma saat membuka koneksi: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=30000`, `foreign_keys=ON`.

```sql
CREATE TABLE IF NOT EXISTS fields (
  field_id         INTEGER PRIMARY KEY,
  name             TEXT    NOT NULL,
  sport            TEXT,
  session_duration INTEGER NOT NULL DEFAULT 60,   -- menit
  open_time        TEXT    NOT NULL,              -- 'HH:MM'
  close_time       TEXT    NOT NULL,              -- 'HH:MM'
  updated_at       TEXT    NOT NULL               -- UTC ISO 8601 'Z'
);

CREATE TABLE IF NOT EXISTS bookings (
  source         TEXT    NOT NULL,                -- 'order' | 'reservation' | 'unknown'
  booking_id     INTEGER NOT NULL,
  booking_date   TEXT    NOT NULL,                -- 'YYYY-MM-DD' (waktu lokal venue)
  field_id       INTEGER NOT NULL,
  start_time     TEXT    NOT NULL,                -- 'HH:MM'
  end_time       TEXT    NOT NULL,
  customer       TEXT    NOT NULL DEFAULT '',
  customer_id    INTEGER NOT NULL DEFAULT 0,      -- user_id AYO; 0 = reservasi manual/tamu
  status         TEXT    NOT NULL DEFAULT '',
  price          INTEGER NOT NULL DEFAULT 0,
  payment_method TEXT    NOT NULL DEFAULT '',
  payment_rule   TEXT    NOT NULL DEFAULT '',
  addons         TEXT    NOT NULL DEFAULT '',
  created_at     TEXT,                            -- dari AYO (UTC)
  raw            TEXT    NOT NULL,                -- JSON mentah
  first_seen_at  TEXT    NOT NULL,
  updated_at     TEXT    NOT NULL,
  removed_at     TEXT,                            -- NULL = aktif
  PRIMARY KEY (source, booking_id)
);
CREATE INDEX IF NOT EXISTS idx_bookings_date_field ON bookings (booking_date, field_id);
CREATE INDEX IF NOT EXISTS idx_bookings_customer ON bookings (customer_id);

CREATE TABLE IF NOT EXISTS booking_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT, -- AUTOINCREMENT: nomor tidak dipakai ulang
  ts           TEXT    NOT NULL,
  event        TEXT    NOT NULL CHECK (event IN ('created','updated','removed','restored')),
  source       TEXT    NOT NULL,
  booking_id   INTEGER NOT NULL,
  booking_date TEXT    NOT NULL,
  field_id     INTEGER NOT NULL,
  start_time   TEXT    NOT NULL,
  end_time     TEXT    NOT NULL,
  customer     TEXT    NOT NULL DEFAULT '',
  status       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON booking_events (ts);

CREATE TABLE IF NOT EXISTS date_sync (
  booking_date    TEXT PRIMARY KEY,
  last_ok_at      TEXT,                -- UTC; NULL = belum pernah berhasil
  last_attempt_at TEXT,
  fail_count      INTEGER NOT NULL DEFAULT 0,
  last_error      TEXT NOT NULL DEFAULT ''   -- hanya nama kelas error/kode, tanpa URL atau token
);

CREATE TABLE IF NOT EXISTS sync_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
-- key yang dipakai: auth_ok ('1'/'0'), last_success_at, last_error, baseline_done ('1'),
--                   seen_statuses (JSON array string), token_mtime,
--                   customers_auth_ok, customers_last_error, customers_last_full_at,
--                   customers_last_light_at, customers_total (lihat §5.7)

-- Hanya terisi bila AYO_CUSTOMER_ENABLED=true. Berisi DATA PRIBADI.
CREATE TABLE IF NOT EXISTS customers (
  customer_id             INTEGER PRIMARY KEY,     -- id AYO = bookings.customer_id
  name                    TEXT    NOT NULL DEFAULT '',
  first_name              TEXT    NOT NULL DEFAULT '',
  username                TEXT    NOT NULL DEFAULT '',
  email                   TEXT    NOT NULL DEFAULT '',
  phone                   TEXT    NOT NULL DEFAULT '',
  latest_booking_date     TEXT,                    -- 'YYYY-MM-DD' dari AYO
  total_booking_success   INTEGER NOT NULL DEFAULT 0,
  total_booking_cancelled INTEGER NOT NULL DEFAULT 0,
  is_deleted_account      INTEGER NOT NULL DEFAULT 0,
  deleted_date            TEXT,
  raw                     TEXT    NOT NULL,
  first_seen_at           TEXT    NOT NULL,
  updated_at              TEXT    NOT NULL,        -- hanya berubah bila kolom terlacak berubah
  missed_syncs            INTEGER NOT NULL DEFAULT 0,
  removed_at              TEXT                     -- NULL = masih ada di daftar AYO
);
CREATE INDEX IF NOT EXISTS idx_customers_name ON customers (name COLLATE NOCASE);
```

Aturan data:

- Kunci booking: `(source, booking_id)`.
- Waktu sistem (`*_at`) UTC format `YYYY-MM-DDTHH:MM:SSZ`.
- Dilarang menghapus baris `bookings`. Hanya `removed_at` yang diisi atau dikosongkan.
- `booking_events` dihapus setelah `EVENT_RETENTION_DAYS` (pembersihan satu kali per hari).

## 5. Sinkronisasi

### 5.1 Jendela rolling

Setiap tick, hitung `today = venue_now().date()`. Tanggal target = `today - 1` sampai `today + WINDOW_DAYS - 1`. Karena dihitung ulang tiap tick, pergeseran tengah malam terjadi otomatis. Tanggal di luar jendela tidak diambil lagi, tetapi datanya tetap tersimpan.

### 5.2 Interval per tanggal

| Offset terhadap hari ini | Interval (bawaan) |
|---|---|
| -1 (kemarin) | 3600 s (`AYO_POLL_YESTERDAY`) |
| 0 dan +1 | 120 s (`AYO_POLL_NEAR`) |
| +2 sampai +6 | 600 s (`AYO_POLL_MID`) |
| +7 sampai +29 | 3600 s (`AYO_POLL_FAR`) |

Tambahkan jitter acak 0 sampai 10% pada interval saat menghitung jatuh tempo. Perkiraan beban latar belakang: ± 2 request per menit.

### 5.3 Scheduler

Tugas latar belakang yang dimulai di `lifespan` aplikasi dan berhenti bersih saat SIGTERM:

1. Setiap 5 detik: tentukan tanggal jatuh tempo, yaitu `last_ok_at` kosong, atau `now - last_ok_at ≥ interval`, dan bukan sedang dalam masa backoff (§5.4).
2. Proses tanggal jatuh tempo berurutan dari offset terkecil, dengan jeda 0,5 detik antar request.
3. Untuk tiap tanggal panggil `fetch_and_apply(date)`, fungsi bersama yang juga dipakai `/schedule/availability` (§6.5).
4. Sekali per jam: periksa apakah seluruh tanggal jendela pernah berhasil; jika ya dan `baseline_done` belum `1`, set `baseline_done=1`. Sekali per hari: hapus event lama.
5. Mode CLI `python -m app.sync --once`: ambil seluruh jendela sekali berurutan, set baseline bila semua berhasil, cetak ringkasan (jumlah booking, error), kode keluar 0 bila semua sukses.

### 5.4 Kegagalan

**FetchError**: naikkan `fail_count` tanggal itu; tunggu `min(300, 5 × 2^fail_count)` detik sebelum mencoba tanggal itu lagi. Jika 3 kegagalan berturut-turut terjadi pada tanggal berbeda, jeda seluruh polling 60 detik. Reset `fail_count` ke 0 saat sukses. Data lama tetap dipakai.

**AuthError**:

- Set `sync_state.auth_ok='0'` dan catat `last_error` (tanpa token atau URL).
- Hentikan polling latar belakang. Periksa mtime `AYO_TOKEN_FILE` tiap 15 detik; coba lagi segera jika berubah, atau otomatis tiap 600 detik.
- Pengambilan ulang di `/schedule/availability` juga ditunda (tidak memanggil AYO) selama `auth_ok='0'`, kecuali mtime token berubah.
- Saat satu pengambilan berhasil: `auth_ok='1'`.
- Data tidak boleh dihapus atau ditandai `removed`.

### 5.5 Batas laju ke AYO (ketat)

- Maksimal **3** request bersamaan (semaphore global untuk latar belakang dan `/schedule/availability`).
- Polling latar belakang berurutan, jeda 0,5 detik.
- Pengambilan ulang saat dicek: maksimal satu per tanggal per `AVAIL_MAX_AGE` (30 detik), dengan jeda minimum `AVAIL_MIN_RETRY` setelah kegagalan.
- Pengambilan yang berhasil (dari sumber mana pun) memperbarui `last_ok_at`, sehingga polling latar belakang melewati tanggal itu sampai jatuh tempo berikutnya.
- Dilarang menaikkan batas ini tanpa persetujuan pemilik.

### 5.6 Algoritma `apply_snapshot(date, data, emit_events)`

Satu transaksi per pemanggilan. Hanya dipanggil setelah respons lolos §3.3.

1. Untuk tiap lapangan `f` di `data`: upsert `fields` (nama, olahraga, durasi sesi, `open_time`/`close_time` dari `f.start_time`/`f.end_time`). Catat `fields_seen`.
2. Untuk tiap booking `b` pada `f.data`: normalisasi (§3.5), tambahkan kunci ke `present`, lalu:
   - Tidak ada di DB → `INSERT`; event `created`.
   - Ada, dan `removed_at` terisi → `UPDATE`, kosongkan `removed_at`; event `restored`.
   - Ada, dan salah satu kolom terlacak berbeda → `UPDATE` (`updated_at` = sekarang); event `updated`.
   - Ada dan identik → tidak ada perubahan (jangan menulis).
   - Kolom terlacak: `booking_date, field_id, start_time, end_time, customer, customer_id, status, price, payment_method, payment_rule, addons`. Kolom `raw` selalu diperbarui bila baris ditulis.
   - Catat `status` ke `seen_statuses` jika baru.
3. Penandaan hilang: untuk tiap `fid` di `fields_seen`, ambil booking aktif (`removed_at IS NULL`) dengan `booking_date = date` dan `field_id = fid`. Yang kuncinya tidak ada di `present` → isi `removed_at`; event `removed`. **Lapangan yang tidak ada di respons tidak diproses** pada langkah ini.
4. Event hanya ditulis bila `emit_events` (yaitu `baseline_done = '1'`).
5. Perbarui `date_sync` (`last_ok_at`, `last_attempt_at`, `fail_count=0`) dan `sync_state` (`last_success_at`, `auth_ok='1'`) dalam transaksi yang sama.

Booking yang pindah tanggal (kunci sama, `booking_date` berbeda): ditangani sebagai `updated` pada tanggal baru. Jika tanggal lama diproses lebih dulu, ia sempat `removed`, lalu `restored` saat tanggal baru diproses. Perilaku ini dapat diterima.

### 5.7 Sinkronisasi pelanggan (hanya bila `AYO_CUSTOMER_ENABLED=true`)

Berjalan sebagai tugas latar belakang **terpisah** dari scheduler kalender, dengan status auth dan kegagalan sendiri (`sync_state.customers_*`). Token pelanggan yang ditolak atau gangguan endpoint pelanggan **tidak boleh** mengganggu sinkronisasi kalender, dan sebaliknya.

| Mode | Frekuensi | Isi | Efek |
|---|---|---|---|
| Ringan | `AYO_CUSTOMER_LIGHT_POLL` (900 s) | Halaman pertama saja (`start_from=0`) | Upsert; **tidak** menandai hilang |
| Penuh | `AYO_CUSTOMER_FULL_POLL` (21600 s) | Seluruh halaman | Upsert dan penandaan hilang |

Sinkronisasi penuh dijalankan segera saat layanan pertama kali aktif (`customers_last_full_at` kosong), lalu berkala.

Langkah sinkronisasi penuh:

1. Ambil halaman berurutan. `start_from` berikutnya = `start_from` sebelumnya **ditambah jumlah baris yang benar-benar diterima** (bukan `limit`), agar tidak ada baris terlewat bila AYO memotong `limit`. Berhenti saat halaman kosong atau jumlah ID unik ≥ `total`. Batas pengaman: 500 halaman.
2. Jeda 1 detik antar halaman; satu halaman setiap kali (tidak paralel).
3. Kumpulkan semua baris di memori dan deduplikasi per `id` (paginasi offset dapat bergeser bila data berubah di tengah proses).
4. Jika **satu** halaman gagal (FetchError, AuthError, atau respons tidak valid), buang seluruh hasil. Tidak ada perubahan database.
5. Jika sukses, dalam satu transaksi: upsert semua baris (tulis hanya bila kolom terlacak berubah, dan `updated_at` diisi saat itu); yang ada → `missed_syncs = 0` dan `removed_at = NULL`; yang tidak ada dalam hasil → `missed_syncs += 1`, dan bila `missed_syncs ≥ 2` → isi `removed_at`.
6. Simpan `customers_last_full_at` dan `customers_total`.

Aturan "hilang setelah dua sinkronisasi penuh berturut-turut" mencegah penandaan keliru akibat pergeseran halaman.

Kolom terlacak: `name, first_name, username, email, phone, latest_booking_date, total_booking_success, total_booking_cancelled, is_deleted_account, deleted_date`.

Kegagalan:

- `FetchError`: backoff `min(900, 30 × 2^n)` detik.
- `AuthError`: `customers_auth_ok = '0'`; berhenti; coba lagi tiap 600 detik atau saat mtime file token pelanggan berubah.

Batas laju: satu request pelanggan berjalan pada satu waktu. Perkiraan beban: sekitar 4 request per jam (ringan) ditambah `ceil(total / N) + 1` request per 6 jam (penuh), contoh 12 request per 6 jam untuk 529 pelanggan dengan N = 50. Ukuran halaman awal 50; naikkan hanya setelah terbukti diterima AYO dan atas persetujuan pemilik (OD-11). Jika AYO menolak atau memotong `limit` secara diam-diam, laporkan ke pemilik. Dilarang menaikkan frekuensi tanpa persetujuan.

## 6. Ketersediaan

### 6.1 Masukan

- `date` (`YYYY-MM-DD`), `time` (`HH:MM`), `duration` (menit, bilangan bulat 15 sampai 480; bawaan = `session_duration` lapangan), `field_id` (opsional, boleh berulang).
- Interval yang diminta: `[time, time + duration)` pada `date`, setengah terbuka.
- `date` lebih dari `today + WINDOW_DAYS - 1` → error `date_out_of_window` (422). `date` sebelum hari ini → semua lapangan berstatus `past` (200).

### 6.2 Status per lapangan

Dievaluasi berurutan; yang pertama cocok menang:

| Urutan | Status | Syarat |
|---|---|---|
| 1 | `past` | `date < today`, atau `date == today` dan `time < venue_now().time` |
| 2 | `closed` | Interval tidak seluruhnya berada dalam `[open_time, close_time]` lapangan |
| 3 | `unknown` | Lapangan tidak ada di `fields`; atau tanggal belum pernah berhasil diambil; atau data lebih tua dari `AVAIL_STALE_LIMIT` |
| 4 | `booked` | Ada booking yang menempati slot dan tumpang tindih (§6.3) |
| 5 | `available` | Selain itu |

Tumpang tindih (setengah terbuka): `req_start < b.end_time` **dan** `b.start_time < req_end`. Booking yang bersebelahan (selesai tepat saat permintaan mulai) **tidak** bertumpang tindih.

### 6.3 Booking yang menempati slot

Booking menempati slot bila `removed_at IS NULL` **dan** `UPPER(status)` tidak termasuk `AYO_FREE_STATUSES`. Status yang tidak dikenal dianggap menempati slot (aman). Nilai status yang pernah terlihat dicatat di `sync_state.seen_statuses` agar pemilik dapat memvalidasi OD-4. Saat ini hanya `MANUAL` dan `FINISHED` yang terbukti ada. Catatan: daftar pelanggan AYO memuat `total_booking_cancelled` > 0, jadi pembatalan memang terjadi, tetapi contoh kalender belum menunjukkan baris berstatus batal. Kemungkinannya booking batal menghilang dari kalender (ditangani sebagai `removed`) atau tampil dengan status yang belum terlihat. Validasi dengan data nyata (OD-4, OD-7).

### 6.4 `next_available` untuk lapangan `booked`

Waktu mulai paling awal setelah `time`, pada kelipatan `session_duration` dari `open_time`, saat lapangan bebas selama `duration` dan interval itu berakhir sebelum atau tepat pada `close_time`. `null` bila tidak ada pada hari itu.

### 6.5 Kesegaran data dan pembagian pengambilan (coalescing)

Prosedur `ensure_fresh(date)` dipanggil di awal setiap `/schedule/availability` dan `/schedule/availability/day`:

1. `age = now - date_sync.last_ok_at` (tak terhingga bila kosong).
2. Jika `age ≤ AVAIL_MAX_AGE` → `data_source = "cache"`, selesai.
3. Jika `auth_ok = '0'` (dan token tidak berubah), atau `now - last_attempt_at < AVAIL_MIN_RETRY` → jangan memanggil AYO; lanjut ke langkah 6.
4. Jika sudah ada pengambilan sedang berjalan untuk tanggal itu → tunggu hasil pengambilan yang sama (wajib **satu** request ke AYO per tanggal, berapa pun jumlah pemanggil).
5. Jika belum ada → buat satu task `fetch_and_apply(date)` dengan timeout `AVAIL_FETCH_TIMEOUT`, simpan di peta `in_flight[date]`, hapus dari peta saat selesai. Pemanggil menunggu task dengan `asyncio.shield` dan batas waktu yang sama.
6. Hasil: sukses → `data_source = "live"`. Gagal atau tidak dipanggil → pakai data tersimpan: `age ≤ AVAIL_STALE_LIMIT` → `"stale"`, selebihnya status `unknown` (§6.2).

`fetch_and_apply` yang sama dipakai scheduler (§5.3), dengan kunci per tanggal (`asyncio.Lock`) agar dua pengambilan untuk tanggal yang sama tidak berjalan bersamaan.

## 7. API

Base URL produksi: `https://api.fitcitypadel.com`
Dasar: JSON, UTF-8. Seluruh endpoint kecuali `/health` memerlukan header `X-API-Key`. Perbandingan kunci wajib waktu-konstan (`hmac.compare_digest`).

| Method | Path | Fungsi | Bagian |
|---|---|---|---|
| GET | `/health` | Status sinkronisasi: `ok`, `stale`, `auth_failed` (tanpa kunci) | 7.1 |
| GET | `/fields` | Daftar lapangan | 7.2 |
| GET | `/schedule` | Jadwal per rentang tanggal | 7.3 |
| GET | `/changes?since=` | Sinkronisasi inkremental memakai nomor urut event | 7.4 |
| GET | `/schedule/availability` | Status tiap lapangan untuk tanggal, jam, dan durasi; mengambil ulang tanggal terkait dari AYO bila data lebih tua dari 30 detik (satu pengambilan dibagi bersama, batas tunggu pendek) | 7.5, 6.5 |
| GET | `/schedule/availability/day` | Seluruh slot satu hari per lapangan | 7.6 |
| GET | `/customer` | Daftar pelanggan (akun AYO dan tamu) | 7.7 |

Path dengan dan tanpa garis miring akhir (`/schedule` dan `/schedule/`) wajib dilayani langsung **tanpa redirect**, karena redirect dapat menghilangkan header `X-API-Key` pada sebagian klien HTTP. Rute `/schedule/availability` dan `/schedule/availability/day` tidak boleh bentrok dengan `/schedule`.

Format error:

```json
{ "error": { "code": "invalid_parameter", "message": "Parameter 'time' harus berformat HH:MM." } }
```

| HTTP | `code` | Kapan |
|---|---|---|
| 401 | `unauthorized` | Kunci tidak ada atau salah |
| 403 | `feature_disabled` | `GET /customer` dipanggil saat `AYO_EXPOSE_CUSTOMER=false` |
| 422 | `invalid_parameter` | Parameter tidak valid |
| 422 | `date_out_of_window` | Tanggal melebihi jendela (hanya untuk `/schedule/availability*`) |
| 503 | `service_unavailable` | Database tidak dapat diakses |
| 500 | `internal_error` | Kesalahan tak terduga (pesan generik, tanpa detail internal) |

### 7.1 `GET /health` (tanpa kunci)

```json
{ "status": "ok", "auth_ok": true, "last_success_at": "2026-11-23T02:10:05Z", "baseline_done": true, "version": "1.0.0",
  "customers": { "enabled": true, "auth_ok": true, "last_full_sync_at": "2026-11-23T00:00:00Z" } }
```

`status`: `auth_failed` bila `auth_ok = 0`; `stale` bila tidak ada keberhasilan dalam 15 menit; selain itu `ok`. HTTP 200 untuk ketiganya; 503 hanya bila database tidak dapat diakses. `status` hanya menilai sinkronisasi **kalender**. Blok `customers` melaporkan sinkronisasi pelanggan secara terpisah (`enabled: false` bila dimatikan; `auth_ok` dan `last_full_sync_at` bernilai `null` bila belum pernah berjalan). `/health` tidak pernah memuat data pelanggan (termasuk jumlah).

### 7.2 `GET /fields`

```json
{ "fields": [ { "field_id": 10479, "name": "Lapangan A", "sport": "Padel",
                "session_duration": 60, "open_time": "07:00", "close_time": "22:00" } ] }
```

### 7.3 `GET /schedule`

Parameter: `from` (bawaan hari ini), `to` (bawaan `from + 29`), `field_id` (berulang, opsional), `include_removed` (bawaan `false`). Rentang maksimal 62 hari. Urutan: tanggal, jam mulai, lapangan.

```json
{ "from": "2026-11-23", "to": "2026-12-22", "count": 1,
  "bookings": [ { "source": "order", "booking_id": 10054526, "date": "2026-11-23",
                  "field_id": 10479, "field_name": "Lapangan A",
                  "start_time": "08:00", "end_time": "09:00",
                  "customer_id": 607776, "customer": "Nama Samaran", "status": "FINISHED", "price": 150000,
                  "payment_method": "", "payment_rule": "BAYAR LUNAS", "addons": "",
                  "created_at": "2026-11-21T14:16:02.000000Z",
                  "updated_at": "2026-11-21T14:20:00Z", "removed": false } ] }
```

`customer` dan `customer_id` dihilangkan bila `AYO_EXPOSE_CUSTOMER=false`. Dilayani di `/schedule` maupun `/schedule/` (tanpa redirect).

### 7.4 `GET /changes`

Parameter: `since` (bilangan bulat, bawaan 0), `limit` (1 sampai 1000, bawaan 500). Mengembalikan event dengan `id > since`, urut naik.

```json
{ "events": [ { "id": 42, "ts": "2026-11-23T03:00:00Z", "event": "updated",
                "source": "reservation", "booking_id": 19708835, "date": "2026-11-24",
                "field_id": 10479, "start_time": "07:00", "end_time": "08:00", "status": "MANUAL" } ],
  "next_since": 42, "latest_event_id": 57, "has_more": true }
```

Pola konsumsi: ambil `/schedule` penuh sekali, simpan `latest_event_id`, lalu panggil `/changes?since=...` berkala (30 sampai 60 detik) dan perbarui baris terkait. Bila ada keraguan, ambil ulang `/schedule`.

### 7.5 `GET /schedule/availability`

Contoh: `/schedule/availability?date=2026-11-23&time=08:00&duration=60`

```json
{ "date": "2026-11-23", "time": "08:00", "duration_minutes": 60,
  "checked_at": "2026-11-23T02:15:30Z", "data_age_seconds": 3, "data_source": "live",
  "fields": [
    { "field_id": 10479, "name": "Lapangan A", "status": "available" },
    { "field_id": 10480, "name": "Lapangan B", "status": "booked", "next_available": "10:00" },
    { "field_id": 10481, "name": "Lapangan C", "status": "closed" } ] }
```

Status: `available`, `booked`, `closed`, `past`, `unknown`. `data_source`: `live`, `cache`, `stale`. **Tidak memuat nama pelanggan.**

### 7.6 `GET /schedule/availability/day`

Parameter: `date`, `duration` (opsional), `field_id` (opsional). Slot dibangun dari `open_time` sampai `close_time` dengan langkah `session_duration` per lapangan. Memakai aturan status §6.2.

```json
{ "date": "2026-11-23", "data_age_seconds": 3, "data_source": "cache",
  "fields": [ { "field_id": 10479, "name": "Lapangan A",
                "slots": [ { "start": "07:00", "end": "08:00", "status": "past" },
                           { "start": "08:00", "end": "09:00", "status": "booked" },
                           { "start": "09:00", "end": "10:00", "status": "available" } ] } ] }
```

Aplikasi dapat menurunkan daftar "jam yang masih punya lapangan kosong" dari respons ini.

### 7.7 `GET /customer`

Daftar pelanggan. Mengikuti `AYO_EXPOSE_CUSTOMER` (bila `false` → 403 `feature_disabled`).

**Himpunan pelanggan** (gabungan tiga sumber):

- (a) **Akun AYO**: semua baris `customers` yang `removed_at`-nya kosong (hanya bila sinkronisasi §5.7 aktif).
- (b) **Akun AYO dari booking**: `customer_id > 0` pada `bookings` yang belum ada di `customers` (misalnya sinkronisasi pelanggan belum aktif, atau pelanggan baru). Hanya nama dari booking yang tersedia.
- (c) **Tamu**: `customer_id = 0` (reservasi manual), dikelompokkan per nama yang dinormalisasi (hapus spasi di tepi, gabungkan spasi ganda, huruf kecil). Dua orang berbeda dengan nama sama akan tergabung dan ejaan berbeda akan terpisah; hal ini wajib didokumentasikan kepada pemanggil. Tamu tidak pernah digabung dengan akun.

Parameter (semua opsional):

| Parameter | Bawaan | Keterangan |
|---|---|---|
| `q` | - | Cari pada `name` dan `username` (tanpa peka huruf besar/kecil, minimal 2 karakter). Mencocokkan `phone` dan `email` **hanya** bila `AYO_EXPOSE_CUSTOMER_CONTACT=true` |
| `type` | `all` | `account`, `guest`, atau `all` |
| `include_deleted` | `false` | Sertakan akun yang `is_deleted_account = 1` |
| `from`, `to` | - | Jika diberikan, hanya pelanggan yang punya booking tersimpan pada rentang itu, dan hitungan slot hanya untuk rentang itu |
| `updated_since` | - | Waktu UTC ISO 8601. Pelanggan yang profilnya (`customers.updated_at`) atau booking-nya (`bookings.updated_at`) berubah sejak waktu itu |
| `sort` | `latest` | `latest` (aktivitas terakhir menurun: yang terbaru dari `ayo_latest_booking_date` dan `last_booking_date`) atau `name` |
| `limit`, `offset` | 100, 0 | `limit` 1 sampai 500 |

Field respons:

| Field | Ada bila |
|---|---|
| `customer_id` (0 untuk tamu), `type` (`account` atau `guest`), `name` | selalu |
| `slot_count`, `active_slot_count`, `first_booking_date`, `last_booking_date`, `sources` | selalu; dihitung dari booking yang **tersimpan** di layanan ini (per slot 60 menit; `0` dan `null` bila tidak ada) |
| `first_name`, `username`, `total_booking_success`, `total_booking_cancelled`, `ayo_latest_booking_date`, `deleted`, `profile_updated_at` | akun yang punya baris di `customers` |
| `phone`, `email` | akun yang punya baris di `customers` **dan** `AYO_EXPOSE_CUSTOMER_CONTACT=true` |

`total_booking_success` dan `total_booking_cancelled` berasal dari AYO (seluruh riwayat) dan **tidak sama** dengan `slot_count` (hanya booking yang tersimpan di sini, per slot). `credit_balance` dan `avatar` tidak pernah diekspos.

```json
{ "total": 3, "count": 3, "limit": 100, "offset": 0,
  "customers": [
    { "customer_id": 555, "type": "account", "name": "Pelanggan Dua",
      "slot_count": 1, "active_slot_count": 1,
      "first_booking_date": "2026-11-23", "last_booking_date": "2026-11-23", "sources": ["order"],
      "first_name": "Pelanggan", "username": "pelanggan.dua",
      "total_booking_success": 4, "total_booking_cancelled": 1,
      "ayo_latest_booking_date": "2026-11-23", "deleted": false,
      "phone": "6281200000002", "email": "pelanggan.dua@example.com",
      "profile_updated_at": "2026-11-23T02:00:00Z" },
    { "customer_id": 9001, "type": "account", "name": "Pelanggan Baru",
      "slot_count": 1, "active_slot_count": 1,
      "first_booking_date": "2026-11-24", "last_booking_date": "2026-11-24", "sources": ["order"] },
    { "customer_id": 0, "type": "guest", "name": "Pelanggan Tiga",
      "slot_count": 2, "active_slot_count": 2,
      "first_booking_date": "2026-11-22", "last_booking_date": "2026-11-22", "sources": ["reservation"] } ] }
```

Contoh di atas memperlihatkan tiga keadaan: akun dengan profil dari AYO (`phone` dan `email` hanya tampil bila flag kontak aktif), akun yang hanya dikenal dari booking, dan tamu. Saat `AYO_CUSTOMER_ENABLED=false`, hanya keadaan kedua dan ketiga yang ada.

## 8. Keamanan

- Aplikasi bind ke `127.0.0.1`; hanya Caddy (443) yang terbuka ke internet. Firewall: 22, 80, 443.
- Kunci API: acak minimal 32 karakter (contoh: `openssl rand -hex 32`), disimpan di `/etc/ayo-sync/env` (izin 600). Jika `AYO_API_KEY` kosong atau pendek, aplikasi menolak start (gagal tertutup).
- Token AYO di `/etc/ayo-sync/mobile_token`, izin 600, dimiliki user `ayo`. Tidak pernah diteruskan ke klien API dan tidak pernah muncul dalam respons atau log.
- Dokumentasi otomatis FastAPI dimatikan (`docs_url=None`, `redoc_url=None`, `openapi_url=None`). Tidak ada CORS (komunikasi server ke server).
- Pesan error ke klien tidak memuat jejak tumpukan (stack trace), path internal, atau detail AYO.
- Rate limit sederhana pada API (sebaiknya, mis. 60 request per menit per kunci) agar bug di aplikasi pemilik tidak membanjiri AYO lewat pengambilan ulang; pembagian pengambilan di §6.5 sudah membatasi dampaknya ke AYO.
- Logger `httpx`, `httpcore`, `urllib3` diset ke WARNING. Ada test yang memastikan token uji tidak muncul di log.
- Nama dan ID pelanggan hanya di `/schedule`, `/changes`, dan `/customer` (bila `AYO_EXPOSE_CUSTOMER=true`). `/schedule/availability` dan `/schedule/availability/day` tidak pernah memuatnya.
- Telepon dan email pelanggan hanya tampil bila `AYO_EXPOSE_CUSTOMER_CONTACT=true` (bawaan `false`). Keduanya tidak pernah masuk log, `/health`, atau `booking_events`.
- Token pelanggan di `/etc/ayo-sync/customer_token` (izin 600, milik `ayo`). Kredensial penanda tangan, bila ada, memakai file terpisah dengan aturan yang sama.
- Database dan berkas backup berisi data pribadi bila fitur pelanggan aktif: direktori backup berizin 700 milik `ayo`, dan backup tidak boleh disalin ke layanan pihak ketiga tanpa persetujuan pemilik.

## 9. Deploy

### 9.1 Lokasi dan user

| Item | Lokasi |
|---|---|
| Kode dan virtualenv | `/opt/ayo-sync` (`.venv` di dalamnya) |
| Database | `/var/lib/ayo-sync/bookings.db` |
| Konfigurasi | `/etc/ayo-sync/env` (600) |
| Token AYO | `/etc/ayo-sync/mobile_token` (600) |
| Backup | `/var/backups/ayo-sync/` |
| User sistem | `ayo` (tanpa login shell) |

### 9.2 systemd (`/etc/systemd/system/ayo-sync.service`)

```ini
[Unit]
Description=AYO Booking Sync
After=network-online.target
Wants=network-online.target

[Service]
User=ayo
Group=ayo
WorkingDirectory=/opt/ayo-sync
EnvironmentFile=/etc/ayo-sync/env
ExecStart=/opt/ayo-sync/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/lib/ayo-sync
MemoryMax=300M

[Install]
WantedBy=multi-user.target
```

### 9.3 Caddy (`/etc/caddy/Caddyfile`)

```
api.fitcitypadel.com {
    encode gzip
    reverse_proxy 127.0.0.1:8000
}
```

Domain produksi: `api.fitcitypadel.com`. Syarat sebelum Caddy dijalankan: A record (dan AAAA bila VPS punya IPv6) mengarah ke IP VPS, dan port 80 dan 443 terbuka dari internet agar Caddy dapat menerbitkan sertifikat. Periksa dengan `dig +short api.fitcitypadel.com` (hasilnya harus IP VPS). Caddy mengurus sertifikat otomatis.

### 9.4 Firewall (butuh konfirmasi pemilik)

```
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Pastikan sesi SSH aktif tetap terbuka saat mengaktifkan.

### 9.5 Backup

Cron `/etc/cron.d/ayo-sync-backup` (butuh paket `sqlite3`; instalasi paket butuh konfirmasi):

```
15 2 * * * ayo sqlite3 /var/lib/ayo-sync/bookings.db ".backup '/var/backups/ayo-sync/bookings-$(date +\%F).db'" && find /var/backups/ayo-sync -name 'bookings-*.db' -mtime +7 -delete
```

### 9.6 Prosedur rutin

- **Ganti token kedaluwarsa**: pemilik menulis token baru ke `/etc/ayo-sync/mobile_token` (agen tidak boleh menerima token lewat chat). Tidak perlu restart. Periksa `/health` dalam 1 menit sampai `auth_failed` hilang.
- **Update versi**: `git pull` → `pip install -r requirements.txt` (di venv) → `pytest` → `systemctl restart ayo-sync` → periksa `/health` dan `journalctl`.
- **Rollback**: kembali ke commit sebelumnya, lalu restart. Skema database hanya boleh berubah dengan migrasi yang kompatibel mundur (tambah kolom, bukan hapus).

## 10. Observabilitas

- Log ke stdout (ditangkap journald). Satu baris per kejadian, tanpa rahasia dan tanpa nama pelanggan. Contoh:
  `INFO sync date=2026-11-23 created=0 updated=1 removed=0 restored=0 ms=240`
  `WARNING sync date=2026-11-23 error=FetchError code=timeout fail_count=2`
  `ERROR auth state=auth_failed reason=token_rejected`
- Perintah pemeriksaan: `systemctl status ayo-sync`, `journalctl -u ayo-sync -n 100`, `curl -s https://api.fitcitypadel.com/health`.
- Pemantauan eksternal (sebaiknya): layanan uptime gratis yang memanggil `/health` dan memberi tahu pemilik bila `status` bukan `ok` atau tidak ada respons.
- Pemakaian memori diperiksa saat M5 (`systemctl status` atau `ps`), target ≤ ~200 MB total.

## 11. Pengujian dan kriteria penerimaan

Test memakai `tests/mock_ayo.py` (server lokal yang meniru endpoint kalender, responsnya dapat diubah per test, dapat mengembalikan 401, 5xx, atau respons rusak, dan menghitung jumlah request yang diterima).

| ID | Kasus uji | Milestone |
|---|---|---|
| T1 | Parser mengubah fixture (Lampiran A) menjadi baris `bookings` yang benar; tanggal berasal dari parameter | M1 |
| T2 | Pengambilan baseline tidak menulis event | M1 |
| T3 | Setelah baseline: booking baru → `created`; ubah pemesan/jam/status/harga → `updated`; hilang dari respons → `removed`; muncul lagi → `restored`; tanpa perubahan → tidak ada event dan tidak ada tulisan | M1 |
| T4 | Booking pindah tanggal menghasilkan `updated` (atau `removed` lalu `restored`) dan tidak meninggalkan duplikat | M1 |
| T5 | Lapangan yang tidak ada di respons tidak memicu `removed` pada lapangan itu | M1 |
| T6 | Respons tidak valid (bukan JSON, `error: true`, `data` bukan array, HTTP 500) tidak mengubah data sama sekali | M1 |
| T7 | HTTP 401 → `auth_ok = 0`, data tidak berubah, polling berhenti; setelah file token diubah polling lanjut dan `auth_ok = 1` | M2 |
| T8 | Token uji (mis. `TESTTOKEN123`) tidak muncul di log selama skenario sukses, gagal koneksi, dan 401 (`grep` pada log yang ditangkap) | M2 |
| T9 | Rolling: dengan jam simulasi melewati tengah malam, jendela bergeser dan tanggal baru terambil; tanggal yang keluar dari jendela berhenti diambil tetapi datanya tetap ada | M2 |
| T10 | Interval berjenjang: jumlah request per tanggal sesuai §5.2 pada jam simulasi | M2 |
| T11 | Backoff `min(300, 5×2^n)` pada FetchError; reset saat sukses | M2 |
| T12 | API: tanpa kunci atau kunci salah → 401; tanpa `AYO_API_KEY` aplikasi menolak start; `/health` terbuka tanpa kunci | M3 |
| T13 | `/schedule` (filter, `include_removed`), `/fields`, `/changes` (`since`, `has_more`, `next_since`) | M3 |
| T14 | Ketersediaan: contoh di Lampiran B | M4 |
| T15 | 20 panggilan `/schedule/availability` bersamaan untuk tanggal yang sama dengan data basi → **1** request ke mock | M4 |
| T16 | Data masih segar (≤ 30 detik) → 0 request ke AYO | M4 |
| T17 | Pengambilan ulang gagal: `data_source = "stale"` bila umur ≤ `AVAIL_STALE_LIMIT`, selebihnya status `unknown` | M4 |
| T18 | Pengambilan on-demand memperbarui `last_ok_at` sehingga scheduler melewati tanggal itu | M4 |
| T19 | `date` melebihi jendela → 422 `date_out_of_window`; `date` masa lalu → semua `past` | M4 |
| T20 | `AYO_FREE_STATUSES`: booking berstatus batal tidak menempati slot; status tak dikenal menempati slot | M4 |
| T21 | `/customer` mode turunan (`AYO_CUSTOMER_ENABLED=false`): `customer_id > 0` dikelompokkan per ID dan memakai nama terbaru; `customer_id = 0` dikelompokkan per nama ternormalisasi; kedua jenis tidak digabung; hitungan dalam slot; `q`, `from`/`to`, `limit`/`offset`; 403 `feature_disabled` saat `AYO_EXPOSE_CUSTOMER=false` | M3 |
| T22 | `/schedule` dan `/schedule/` sama-sama dilayani tanpa redirect (status 200, bukan 307/308) | M3 |
| T23 | Parser `customer-lists` (Lampiran C): field terpetakan ke `customers`; `avatar` dan `credit_balance` hanya di `raw` | M6 |
| T24 | Paginasi: `start_from` maju sesuai jumlah baris yang diterima; berhenti saat halaman kosong atau jumlah unik ≥ `total`; deduplikasi; bila server memotong `limit`, tidak ada baris terlewat; batas 500 halaman | M6 |
| T25 | Satu halaman gagal di tengah sinkronisasi penuh → tidak ada perubahan database | M6 |
| T26 | Penandaan hilang: tidak ada pada satu sinkronisasi penuh → `missed_syncs = 1` tanpa `removed_at`; dua kali berturut-turut → `removed_at`; muncul lagi → pulih | M6 |
| T27 | Sinkronisasi ringan tidak pernah menandai hilang | M6 |
| T28 | Isolasi: 401 pada endpoint pelanggan → `customers_auth_ok = '0'` dan kalender tetap berjalan; sebaliknya; `/health` menampilkan blok `customers` terpisah | M6 |
| T29 | Token pelanggan, `signature`, telepon, dan email uji tidak muncul di log; `/health` tidak memuat data pelanggan | M6 |
| T30 | `/customer` gabungan: himpunan (a), (b), (c); `type`; `q` (tidak mencocokkan telepon/email saat flag kontak `false`); `include_deleted`; `updated_since`; `sort`; paginasi; `phone` dan `email` hanya saat `AYO_EXPOSE_CUSTOMER_CONTACT=true` | M6 |
| T31 | `AYO_CUSTOMER_ENABLED=false`: tidak ada satu pun request ke host pelanggan (dihitung di mock) | M6 |

Kriteria penerimaan per milestone:

- **M0**: user `ayo` dan direktori ada dengan izin benar; virtualenv jalan; Caddy terpasang; firewall aktif tanpa memutus SSH.
- **M1 sampai M4**: seluruh test pada tabel untuk milestone itu lulus; tidak ada test lama yang rusak.
- **M2 tambahan**: satu putaran `python -m app.sync --once` terhadap AYO asli (atas persetujuan pemilik) berhasil; hasilnya dilaporkan, termasuk status booking yang terlihat.
- **M5**: layanan hidup otomatis setelah `reboot`; `https://api.fitcitypadel.com/health` merespons; backup pertama terbentuk; uji 20 pemanggil bersamaan `/schedule/availability` memenuhi NFR-3; RAM total ≤ ~200 MB; token uji tidak ada di `journalctl`.
- **M6** (hanya setelah OD-9 dan OD-10 terjawab): T23 sampai T31 lulus; satu sinkronisasi penuh terhadap AYO asli atas persetujuan pemilik, dengan jumlah pelanggan tersimpan mendekati `total` dari AYO; batas `limit` yang diterima AYO dicatat (OD-11); `/health.customers` sehat; tidak ada data pribadi di `journalctl`.

---

## Lampiran A: fixture minimal

```json
{
  "error": false,
  "message": "Berhasil",
  "data": [
    {
      "field_name": "Lapangan A", "session_duration": 60, "sport_name": "Padel",
      "sport_id": 12, "field_id": 10479,
      "data": [
        { "id": 1001, "field_id": 10479, "user_id": 0, "user_name": "Pelanggan Satu",
          "reservation_type": "Normal", "start_time": "07:00:00", "end_time": "08:00:00",
          "created_at": "2026-11-21T06:59:35.000000Z", "status": null, "total_price": 1,
          "payment_method": "Transfer", "payment_type": "MANUAL", "final_status": "MANUAL",
          "payment_rule": "MANUAL", "source_table": "reservation", "add_on_group_name": null },
        { "id": 2001, "field_id": 10479, "user_id": 555, "user_name": "Pelanggan Dua",
          "reservation_type": "", "start_time": "08:00:00", "end_time": "09:00:00",
          "created_at": "2026-11-21T14:16:02.000000Z", "status": "FINISHED", "total_price": 150000,
          "payment_method": "", "payment_type": "", "final_status": "FINISHED",
          "payment_rule": "BAYAR LUNAS", "source_table": "order", "add_on_group_name": null },
        { "id": 1002, "field_id": 10479, "user_id": 0, "user_name": "Pelanggan Tiga",
          "reservation_type": "Normal", "start_time": "14:00:00", "end_time": "15:00:00",
          "created_at": "2026-11-22T02:04:42.000000Z", "status": null, "total_price": 0,
          "payment_method": null, "payment_type": "", "final_status": "MANUAL",
          "payment_rule": "MANUAL", "source_table": "reservation", "add_on_group_name": ["Sewa Raket"] }
      ],
      "start_time": "07:00:00", "end_time": "22:00:00"
    },
    {
      "field_name": "Lapangan B", "session_duration": 60, "sport_name": "Padel",
      "sport_id": 12, "field_id": 10480, "data": [],
      "start_time": "07:00:00", "end_time": "22:00:00"
    }
  ],
  "status_code": 200, "signature": null, "execution_time": 0.003
}
```

Pemilik dapat memberikan contoh respons nyata (nama pelanggan diganti nama samaran) untuk melengkapi fixture, terutama contoh booking dengan status batal atau tertunda (OD-7).

## Lampiran B: kasus uji ketersediaan (T14)

Data: Lapangan A (buka 07:00 sampai 22:00, sesi 60 menit) memiliki satu booking aktif `08:00–09:00`. Hari uji: hari ini, jam simulasi 06:00 kecuali disebut lain.

| `time` | `duration` | Hasil Lapangan A | Alasan |
|---|---|---|---|
| 08:00 | 60 | `booked` | Sama persis |
| 07:30 | 60 | `booked` | Tumpang tindih 08:00 sampai 08:30 |
| 08:30 | 30 | `booked` | Berada di dalam booking |
| 07:00 | 60 | `available` | Selesai tepat saat booking mulai |
| 09:00 | 60 | `available` | Mulai tepat saat booking selesai |
| 21:00 | 60 | `available` | Selesai tepat pada jam tutup |
| 21:30 | 60 | `closed` | Berakhir 22:30, melewati jam tutup |
| 06:00 | 60 | `closed` | Sebelum jam buka |
| 07:00 (jam simulasi 10:00) | 60 | `past` | Sudah lewat |
| 08:00, booking berstatus batal | 60 | `available` | Status batal tidak menempati slot (bila termasuk `AYO_FREE_STATUSES`) |
| 08:00, booking `removed_at` terisi | 60 | `available` | Booking sudah hilang |

## Lampiran C: fixture daftar pelanggan (data samaran)

Semua nilai di bawah rekaan. `id` 555 sengaja sama dengan `user_id` pelanggan di Lampiran A agar uji penggabungan dapat dilakukan. **Jangan menyalin data pelanggan asli ke repositori.** Anonimkan dahulu (nama, username, email, telepon).

```json
{
  "data": [
    { "avatar": "upload/default-avatar-contoh1.png", "combined_name": "Pelanggan Dua", "credit_balance": 0,
      "deleted_date": null, "email": "pelanggan.dua@example.com", "first_name": "Pelanggan",
      "id": 555, "is_deleted_account": 0, "latest_booking_date": "2026-11-23",
      "phone": "6281200000002", "total_booking_cancelled": 1, "total_booking_success": 4,
      "username": "pelanggan.dua" },
    { "avatar": "upload/default-avatar-contoh2.png", "combined_name": "Pelanggan Lima", "credit_balance": 0,
      "deleted_date": null, "email": "pelanggan.lima@example.com", "first_name": "Pelanggan",
      "id": 556, "is_deleted_account": 0, "latest_booking_date": "2026-11-20",
      "phone": "6281200000005", "total_booking_cancelled": 0, "total_booking_success": 1,
      "username": "pelanggan.lima" },
    { "avatar": "upload/default-avatar-contoh3.png", "combined_name": "Pelanggan Enam", "credit_balance": 0,
      "deleted_date": "2026-10-01", "email": "pelanggan.enam@example.com", "first_name": "Pelanggan",
      "id": 557, "is_deleted_account": 1, "latest_booking_date": "2026-09-15",
      "phone": "6281200000006", "total_booking_cancelled": 0, "total_booking_success": 2,
      "username": "pelanggan.enam" }
  ],
  "error": false,
  "message": "Data pelanggan venue",
  "signature": null,
  "status_code": 200,
  "total": 3
}
```

Skenario paginasi (T24): `limit` 2 → halaman 1 (`start_from=0`) berisi id 555 dan 556; halaman 2 (`start_from=2`) berisi id 557; halaman 3 (`start_from=3`) kosong.
