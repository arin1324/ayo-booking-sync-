# Panduan integrasi API untuk agen Next.js

Dokumen ini ditujukan untuk agen/engineer yang bekerja di **server aplikasi Next.js**
pemilik venue. Tujuannya: memakai layanan **AYO Booking Sync** (read-only) untuk
menampilkan jadwal dan mengecek ketersediaan lapangan.

Layanan lain: `https://api.fitcitypadel.com` (VPS terpisah). Repositori layanan
tidak boleh diubah dari sini; integrasi hanya lewat HTTP.

---

## 0. Aturan wajib

1. **Panggil hanya dari server** (Route Handler, Server Action, Server Component,
   `getServerSideProps`). **Jangan pernah** dari browser/komponen client.
2. Kunci API disimpan sebagai environment server, mis. `AYO_SYNC_API_KEY` di
   `.env.local` server. **Bukan** `NEXT_PUBLIC_*`. Jangan menaruhnya di kode,
   repo, log, atau pesan chat.
3. Layanan ini **read-only**: tidak ada endpoint untuk membuat/mengubah booking.
   Jangan mencoba menulis.
4. Layanan **tidak punya CORS**; permintaan dari browser akan gagal (memang
   disengaja).
5. Data pelanggan (nama) hanya ada di `/schedule`/`/customer`; jangan
   menampilkannya di halaman publik tanpa kebutuhan.

Environment yang dibutuhkan (diisi pemilik, bukan agen):

```
AYO_SYNC_BASE_URL=https://api.fitcitypadel.com
AYO_SYNC_API_KEY=<kunci 32+ karakter, samakan dengan AYO_API_KEY di VPS layanan>
```

---

## 1. Ringkasan endpoint

| Method | Path | Kunci | Kegunaan |
|---|---|---|---|
| GET | `/health` | tidak | Status sinkronisasi (`ok`/`stale`/`auth_failed`) |
| GET | `/fields` | ya | Daftar lapangan + jam buka/tutup + durasi sesi |
| GET | `/schedule` | ya | Booking pada rentang tanggal |
| GET | `/schedule/availability` | ya | Status tiap lapangan untuk tanggal+jam+durasi |
| GET | `/schedule/availability/day` | ya | Semua slot satu hari per lapangan |
| GET | `/changes` | ya | Perubahan inkremental (nomor urut event) |
| GET | `/customer` | ya | Daftar pelanggan (bisa dimatikan → 403) |

Header wajib untuk semua endpoint ber-kunci: `X-API-Key: <kunci>`.
`/health` tidak memakai kunci.

---

## 2. Format nilai dan zona waktu

- `date`: `YYYY-MM-DD`; `time`: `HH:MM` (24 jam). Ini **waktu lokal venue
  (Asia/Makassar, WITA, UTC+8)**, bukan UTC. Jangan mengonversi ke UTC sebelum
  mengirim.
- `created_at`, `updated_at`, `checked_at`, `ts`: timestamp UTC berakhiran `Z`.
- `duration`: menit, bilangan bulat **15–480**; bila kosong memakai
  `session_duration` lapangan.
- `field_id` boleh diulang: `?field_id=10479&field_id=10480`.
- Jendela data: tanggal paling jauh `hari ini + 29` (jendela 31 hari termasuk
  kemarin). Lebih dari itu → `422 date_out_of_window`. Tanggal lampau tetap
  dilayani dan menghasilkan status `past`.

---

## 3. `GET /schedule/availability` (paling penting)

Permintaan contoh:

```
GET /schedule/availability?date=2026-11-23&time=08:00&duration=60
X-API-Key: ...
```

Respons:

```json
{
  "date": "2026-11-23",
  "time": "08:00",
  "duration_minutes": 60,
  "checked_at": "2026-11-23T02:15:30Z",
  "data_age_seconds": 3,
  "data_source": "live",
  "fields": [
    { "field_id": 10479, "name": "Lapangan A", "status": "available" },
    { "field_id": 10480, "name": "Lapangan B", "status": "booked", "next_available": "10:00" },
    { "field_id": 10481, "name": "Lapangan C", "status": "closed" }
  ]
}
```

### Arti `status`

| Status | Arti | Tampilkan di UI |
|---|---|---|
| `available` | Bebas pada interval itu | "Tersedia" |
| `booked` | Ada booking yang tumpang tindih; `next_available` = jam mulai berikutnya (bisa `null`) | "Terisi" |
| `closed` | Interval di luar jam buka lapangan | "Tutup" |
| `past` | Waktu sudah lewat | "Lewat" |
| `unknown` | Lapangan tidak ada, data terlalu tua, atau belum pernah diambil | "Tidak diketahui" |

### Arti `data_source`

| Nilai | Arti |
|---|---|
| `live` | Layanan baru mengambil ulang tanggal ini dari AYO saat permintaan |
| `cache` | Memakai data tersimpan yang masih segar (≤ 30 detik) |
| `stale` | Pengambilan ulang gagal; memakai data lama |

`data_age_seconds` = umur data (detik). Bila `data_source != "live"` dan
`data_age_seconds > 600`, sebagian lapangan bisa `unknown`. **Jangan tampilkan
"Tersedia" bila statusnya `unknown`.**

> **Penting (keselamatan booking):** `available` adalah potret saat dicek, **bukan
> kuncian**. Sebelum menyimpan pesanan, **panggil ulang** endpoint ini dan
> pastikan lapangan masih `available` untuk tanggal+jam yang sama. Jangan
> mengandalkan hasil panggilan lama.

### Contoh pemakaian di formulir
Saat pengguna memilih tanggal & jam, panggil sekali, lalu isi dropdown lapangan
dengan label status di atas. Nonaktifkan pilihan yang bukan `available`.

---

## 4. `GET /schedule/availability/day`

Untuk menampilkan grid satu hari:

```
GET /schedule/availability/day?date=2026-11-23&duration=60
```

```json
{
  "date": "2026-11-23",
  "data_age_seconds": 3,
  "data_source": "cache",
  "fields": [
    { "field_id": 10479, "name": "Lapangan A",
      "slots": [
        { "start": "07:00", "end": "08:00", "status": "past" },
        { "start": "08:00", "end": "09:00", "status": "booked" },
        { "start": "09:00", "end": "10:00", "status": "available" }
      ] }
  ]
}
```

Slot dibangun dari `open_time` sampai `close_time` dengan langkah `session_duration`.

---

## 5. `GET /fields`, `/schedule`, `/changes`, `/health`

**`/fields`**

```json
{ "fields": [ { "field_id": 10479, "name": "Lapangan A", "sport": "Padel",
                "session_duration": 60, "open_time": "08:00", "close_time": "23:00" } ] }
```

⚠️ Jam buka/tutup **jangan di-hardcode** di aplikasi; selalu baca dari respons
(`open_time`/`close_time`) karena bisa berubah.

**`/schedule`** — parameter `from` (bawaan hari ini), `to` (bawaan `from + 29`),
`field_id` (boleh berulang), `include_removed` (bawaan `false`). Rentang maks 62
hari. Urutan: tanggal, jam mulai, lapangan.

**`/changes?since=<id>`** — untuk sinkronisasi inkremental bila aplikasi menyimpan
cache jadwal sendiri. Alur: ambil `/schedule` penuh sekali, simpan
`latest_event_id`, lalu panggil `/changes?since=...` tiap 30–60 detik. Ada
`has_more` untuk halaman berikutnya.

**`/health`** — tanpa kunci. `status`: `ok`, `stale` (tidak ada sinkronisasi
sukses > 15 menit), atau `auth_failed` (token AYO bermasalah). Berguna untuk
banner di dashboard admin: bila bukan `ok`, tampilkan peringatan "data mungkin
belum terbaru".

---

## 6. Error dan penanganannya

Format error:

```json
{ "error": { "code": "invalid_parameter", "message": "Parameter 'time' harus berformat HH:MM." } }
```

| HTTP | `code` | Tindakan di aplikasi |
|---|---|---|
| 401 | `unauthorized` | Salah/absen kunci → perbaiki env, jangan retry berulang |
| 422 | `invalid_parameter` | Bug parameter; perbaiki input (format tanggal/jam/durasi) |
| 422 | `date_out_of_window` | Tanggal di luar jendela 31 hari; batasi date picker |
| 429 | `rate_limited` | Terlalu banyak permintaan; tunggu ±60 detik, jangan spam |
| 503 | `service_unavailable` | Database layanan bermasalah; tampilkan "coba lagi" |
| 5xx | `internal_error` | Tampilkan pesan umum, catat log |

Batas laju: **60 permintaan / 60 detik per kunci**. Jangan polling lebih cepat
dari itu. Layanan sendiri sudah menyegarkan data (≤ 30 detik) sehingga polling
tiap beberapa detik tidak menambah akurasi.

---

## 7. Klien TypeScript (siap pakai)

Taruh di `lib/ayo-sync.server.ts` (hanya diimpor dari kode server):

```ts
const BASE_URL = process.env.AYO_SYNC_BASE_URL ?? "https://api.fitcitypadel.com";
const API_KEY = process.env.AYO_SYNC_API_KEY;

export type FieldStatus = "available" | "booked" | "closed" | "past" | "unknown";
export type DataSource = "live" | "cache" | "stale";

export interface FieldAvailability {
  field_id: number;
  name: string;
  status: FieldStatus;
  next_available?: string | null;
}

export interface AvailabilityResponse {
  date: string;
  time: string;
  duration_minutes: number;
  checked_at: string;
  data_age_seconds: number | null;
  data_source: DataSource;
  fields: FieldAvailability[];
}

export interface FieldInfo {
  field_id: number;
  name: string;
  sport: string | null;
  session_duration: number;
  open_time: string;
  close_time: string;
}

export class AyoSyncError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "AyoSyncError";
  }
}

type Params = Record<string, string | number | string[] | undefined>;

async function getJson<T>(path: string, params: Params = {}, withKey = true): Promise<T> {
  const url = new URL(path, BASE_URL);
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined) continue;
    if (Array.isArray(value)) value.forEach((v) => url.searchParams.append(key, v));
    else url.searchParams.set(key, String(value));
  }

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 10_000);
  try {
    const res = await fetch(url, {
      headers: withKey ? { "X-API-Key": API_KEY ?? "" } : undefined,
      signal: controller.signal,
      cache: "no-store",
    });
    if (!res.ok) {
      let code = "http_error";
      let message = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        code = body?.error?.code ?? code;
        message = body?.error?.message ?? message;
      } catch {
        /* abaikan body non-JSON */
      }
      throw new AyoSyncError(res.status, code, message);
    }
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof AyoSyncError) throw err;
    if ((err as Error).name === "AbortError") {
      throw new AyoSyncError(504, "timeout", "Layanan jadwal tidak merespons.");
    }
    throw new AyoSyncError(502, "network_error", "Gagal menghubungi layanan jadwal.");
  } finally {
    clearTimeout(timer);
  }
}

export function getHealth() {
  return getJson<{ status: "ok" | "stale" | "auth_failed"; auth_ok: boolean }>("/health", {}, false);
}

export function getFields() {
  return getJson<{ fields: FieldInfo[] }>("/fields");
}

export function getAvailability(
  date: string,
  time: string,
  opts: { duration?: number; fieldIds?: number[] } = {},
) {
  return getJson<AvailabilityResponse>("/schedule/availability", {
    date,
    time,
    duration: opts.duration,
    field_id: opts.fieldIds?.map(String),
  });
}

export function getAvailabilityDay(
  date: string,
  opts: { duration?: number; fieldIds?: number[] } = {},
) {
  return getJson<{
    date: string;
    data_age_seconds: number | null;
    data_source: DataSource;
    fields: { field_id: number; name: string; slots: { start: string; end: string; status: FieldStatus }[] }[];
  }>("/schedule/availability/day", {
    date,
    duration: opts.duration,
    field_id: opts.fieldIds?.map(String),
  });
}
```

### Route Handler (proxy tipis, jangan bocorkan kunci)

```ts
// app/api/ayo/availability/route.ts
import { NextRequest, NextResponse } from "next/server";
import { AyoSyncError, getAvailability } from "@/lib/ayo-sync.server";

export async function GET(req: NextRequest) {
  const date = req.nextUrl.searchParams.get("date");
  const time = req.nextUrl.searchParams.get("time");
  if (!date || !time) {
    return NextResponse.json({ error: "date dan time wajib" }, { status: 400 });
  }
  try {
    const data = await getAvailability(date, time);
    return NextResponse.json(data, { headers: { "Cache-Control": "no-store" } });
  } catch (err) {
    const e = err as AyoSyncError;
    const status = e.status === 429 ? 429 : e.status === 422 ? 422 : 502;
    return NextResponse.json({ error: e.message, code: e.code }, { status });
  }
}
```

Alternatif lebih aman: panggil `getAvailability` langsung dari Server Component /
Server Action dan kirim hasilnya ke client; tidak perlu route proxy sama sekali.

---

## 8. Checklist implementasi

- [ ] `AYO_SYNC_API_KEY` hanya di env server (bukan `NEXT_PUBLIC_`), tidak di repo.
- [ ] Klien hanya diimpor dari kode server.
- [ ] `/fields` dipanggil sekali dan di-cache singkat; jam buka dibaca dari respons.
- [ ] Dropdown/form memakai `/schedule/availability` saat tanggal+jam dipilih.
- [ ] Status dipetakan ke label Indonesia (tabel §3).
- [ ] `unknown` tidak pernah ditampilkan sebagai "Tersedia".
- [ ] Sebelum menyimpan pesanan, `/schedule/availability` dipanggil ulang.
- [ ] Date picker dibatasi maksimal `hari ini + 29`.
- [ ] Error 401/422/429/503 ditangani dengan pesan yang sesuai.
- [ ] Tidak ada polling lebih cepat dari 60 permintaan/menit.

## 9. Uji cepat dari server Next.js

```bash
# tanpa kunci (harus 200 dan status ok/stale/auth_failed)
curl -s "$AYO_SYNC_BASE_URL/health"

# dengan kunci (harus 200)
curl -s -H "X-API-Key: $AYO_SYNC_API_KEY" \
  "$AYO_SYNC_BASE_URL/schedule/availability?date=$(date +%F)&time=20:00"

# tanpa kunci pada endpoint ber-kunci (harus 401)
curl -s -o /dev/null -w "%{http_code}\n" "$AYO_SYNC_BASE_URL/fields"
```

---

## 10. Yang tidak boleh dilakukan

- Menaruh kunci API di kode client, `NEXT_PUBLIC_*`, repo, log, atau screenshot.
- Memanggil endpoint langsung dari browser.
- Menganggap `available` sebagai jaminan (bukan penguncian).
- Mengecek ulang lebih cepat dari batas laju.
- Mengubah layanan di VPS (itu di luar server ini).