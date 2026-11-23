"""Parser, algoritma diff, dan penerapan snapshot (SPECS §3.5, §5.6).

Modul ini sengaja sinkron (sqlite3). Pemanggil async (scheduler/API) akan
membungkusnya dengan ``asyncio.to_thread`` pada milestone berikutnya.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from . import clock, db
from .ayo_client import AuthError, AyoClient, AyoError, TokenFile
from .config import Config, ConfigError

logger = logging.getLogger("ayo.sync")

# Kolom yang dibandingkan saat menentukan ``updated`` (SPECS §5.6 langkah 2).
TRACKED_COLUMNS = (
    "booking_date",
    "field_id",
    "start_time",
    "end_time",
    "customer",
    "customer_id",
    "status",
    "price",
    "payment_method",
    "payment_rule",
    "addons",
)


def _as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _hhmm(value: Any) -> str:
    if not value:
        return ""
    return str(value)[:5]


def _status(booking: dict[str, Any]) -> str:
    """Pakai ``final_status``; bila kosong, ``status``. Huruf besar, '' bila kosong."""
    raw = booking.get("final_status") or booking.get("status") or ""
    return str(raw).upper()


def _addons(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def normalize_booking(
    date: str, field_id: int, booking: dict[str, Any]
) -> dict[str, Any]:
    """Petakan satu booking AYO ke bentuk baris ``bookings`` (SPECS §3.5)."""
    return {
        "source": str(booking.get("source_table") or "unknown"),
        "booking_id": _as_int(booking.get("id")),
        "booking_date": date,
        "field_id": int(field_id),
        "start_time": _hhmm(booking.get("start_time")),
        "end_time": _hhmm(booking.get("end_time")),
        "customer": str(booking.get("user_name") or ""),
        "customer_id": _as_int(booking.get("user_id")),
        "status": _status(booking),
        "price": _as_int(booking.get("total_price")),
        "payment_method": str(booking.get("payment_method") or ""),
        "payment_rule": str(booking.get("payment_rule") or ""),
        "addons": _addons(booking.get("add_on_group_name")),
        "created_at": booking.get("created_at"),
        "raw": json.dumps(booking, sort_keys=True, ensure_ascii=False),
    }


def _upsert_field(
    conn: sqlite3.Connection, field: dict[str, Any], field_id: int, ts: str
) -> None:
    conn.execute(
        """
        INSERT INTO fields
            (field_id, name, sport, session_duration, open_time, close_time, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(field_id) DO UPDATE SET
            name = excluded.name,
            sport = excluded.sport,
            session_duration = excluded.session_duration,
            open_time = excluded.open_time,
            close_time = excluded.close_time,
            updated_at = excluded.updated_at
        """,
        (
            field_id,
            str(field.get("field_name") or ""),
            field.get("sport_name"),
            _as_int(field.get("session_duration")) or 60,
            _hhmm(field.get("start_time")),
            _hhmm(field.get("end_time")),
            ts,
        ),
    )


def _emit(
    conn: sqlite3.Connection, ts: str, event: str, row: dict[str, Any]
) -> None:
    conn.execute(
        """
        INSERT INTO booking_events
            (ts, event, source, booking_id, booking_date, field_id,
             start_time, end_time, customer, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            event,
            row["source"],
            row["booking_id"],
            row["booking_date"],
            row["field_id"],
            row["start_time"],
            row["end_time"],
            row["customer"],
            row["status"],
        ),
    )


def _insert_booking(
    conn: sqlite3.Connection, row: dict[str, Any], ts: str
) -> None:
    conn.execute(
        """
        INSERT INTO bookings
            (source, booking_id, booking_date, field_id, start_time, end_time,
             customer, customer_id, status, price, payment_method, payment_rule,
             addons, created_at, raw, first_seen_at, updated_at, removed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        """,
        (
            row["source"],
            row["booking_id"],
            row["booking_date"],
            row["field_id"],
            row["start_time"],
            row["end_time"],
            row["customer"],
            row["customer_id"],
            row["status"],
            row["price"],
            row["payment_method"],
            row["payment_rule"],
            row["addons"],
            row["created_at"],
            row["raw"],
            ts,
            ts,
        ),
    )


def _update_booking(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    ts: str,
    *,
    clear_removed: bool,
) -> None:
    assignments = ", ".join(f"{col} = ?" for col in TRACKED_COLUMNS)
    params = [row[col] for col in TRACKED_COLUMNS]
    if clear_removed:
        assignments += ", removed_at = NULL"
    conn.execute(
        f"UPDATE bookings SET {assignments}, raw = ?, updated_at = ? "
        "WHERE source = ? AND booking_id = ?",
        (*params, row["raw"], ts, row["source"], row["booking_id"]),
    )


def _apply_booking(
    conn: sqlite3.Connection,
    row: dict[str, Any],
    ts: str,
    emit_events: bool,
) -> str | None:
    existing = conn.execute(
        "SELECT * FROM bookings WHERE source = ? AND booking_id = ?",
        (row["source"], row["booking_id"]),
    ).fetchone()

    if existing is None:
        _insert_booking(conn, row, ts)
        if emit_events:
            _emit(conn, ts, "created", row)
        return "created"

    if existing["removed_at"] is not None:
        _update_booking(conn, row, ts, clear_removed=True)
        if emit_events:
            _emit(conn, ts, "restored", row)
        return "restored"

    if any(existing[col] != row[col] for col in TRACKED_COLUMNS):
        _update_booking(conn, row, ts, clear_removed=False)
        if emit_events:
            _emit(conn, ts, "updated", row)
        return "updated"

    return None


def _mark_missing(
    conn: sqlite3.Connection,
    date: str,
    field_id: int,
    present: set[tuple[str, int]],
    ts: str,
    emit_events: bool,
) -> int:
    rows = conn.execute(
        "SELECT source, booking_id, booking_date, field_id, start_time, end_time, "
        "customer, status FROM bookings "
        "WHERE booking_date = ? AND field_id = ? AND removed_at IS NULL",
        (date, field_id),
    ).fetchall()
    marked = 0
    for existing in rows:
        key = (existing["source"], existing["booking_id"])
        if key in present:
            continue
        conn.execute(
            "UPDATE bookings SET removed_at = ? WHERE source = ? AND booking_id = ?",
            (ts, existing["source"], existing["booking_id"]),
        )
        if emit_events:
            _emit(conn, ts, "removed", dict(existing))
        marked += 1
    return marked


def _record_statuses(conn: sqlite3.Connection, statuses: Iterable[str]) -> None:
    fresh = {s for s in statuses if s}
    if not fresh:
        return
    current_raw = db.get_state(conn, "seen_statuses", "[]") or "[]"
    try:
        current = set(json.loads(current_raw))
    except (TypeError, ValueError):
        current = set()
    merged = sorted(current | fresh)
    if merged != sorted(current):
        db.set_state(conn, "seen_statuses", json.dumps(merged))


def apply_snapshot(
    conn: sqlite3.Connection,
    date: str,
    data: list[dict[str, Any]],
    *,
    emit_events: bool,
    ts: str,
) -> dict[str, int]:
    """Terapkan satu snapshot tanggal (SPECS §5.6). Satu transaksi.

    Hanya dipanggil setelah respons lolos validasi §3.3.
    """
    counts = {
        "fields": 0,
        "created": 0,
        "updated": 0,
        "removed": 0,
        "restored": 0,
        "unchanged": 0,
    }
    present: set[tuple[str, int]] = set()
    fields_seen: list[int] = []
    statuses: set[str] = set()

    with conn:
        for field in data:
            field_id = field.get("field_id")
            if field_id is None:
                continue
            field_id = int(field_id)
            _upsert_field(conn, field, field_id, ts)
            fields_seen.append(field_id)
            counts["fields"] += 1
            for booking in field.get("data") or []:
                row = normalize_booking(date, field_id, booking)
                present.add((row["source"], row["booking_id"]))
                if row["status"]:
                    statuses.add(row["status"])
                outcome = _apply_booking(conn, row, ts, emit_events)
                if outcome is None:
                    counts["unchanged"] += 1
                else:
                    counts[outcome] += 1

        for field_id in fields_seen:
            counts["removed"] += _mark_missing(
                conn, date, field_id, present, ts, emit_events
            )

        _record_statuses(conn, statuses)
        conn.execute(
            """
            INSERT INTO date_sync
                (booking_date, last_ok_at, last_attempt_at, fail_count, last_error)
            VALUES (?, ?, ?, 0, '')
            ON CONFLICT(booking_date) DO UPDATE SET
                last_ok_at = excluded.last_ok_at,
                last_attempt_at = excluded.last_attempt_at,
                fail_count = 0,
                last_error = ''
            """,
            (date, ts, ts),
        )
        db.set_state(conn, "last_success_at", ts)
        db.set_state(conn, "auth_ok", "1")

    return counts


async def fetch_and_apply(
    conn: sqlite3.Connection,
    client: Any,
    date: str,
    *,
    emit_events: bool,
    ts: str,
) -> dict[str, int]:
    """Ambil satu tanggal dari AYO lalu terapkan. Dipakai scheduler dan API."""
    data = await client.fetch_calendar(date)
    return apply_snapshot(conn, date, data, emit_events=emit_events, ts=ts)


# Batas laju AYO: maksimal 3 request bersamaan, untuk scheduler DAN API
# (SPECS §5.5). Jangan dinaikkan tanpa persetujuan pemilik.
MAX_CONCURRENT_FETCHES = 3


class FetchCoordinator:
    """Pembagi pengambilan AYO antar scheduler dan API (SPECS §5.5, §6.5).

    - Semaphore global membatasi maksimal ``MAX_CONCURRENT_FETCHES`` request.
    - Kunci per tanggal mencegah dua pengambilan tanggal yang sama berjalan
      bersamaan (scheduler + API).
    - ``start()`` membagi satu task pengambilan: berapa pun pemanggil bersamaan
      untuk tanggal yang sama, hanya **satu** request ke AYO.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        client: Any,
        cfg: Config,
        *,
        token_file: TokenFile | None = None,
    ) -> None:
        self.conn = conn
        self.client = client
        self.cfg = cfg
        self.token_file = token_file
        self._sem = asyncio.Semaphore(MAX_CONCURRENT_FETCHES)
        self._locks: dict[str, asyncio.Lock] = {}
        self._in_flight: dict[str, asyncio.Task[Any]] = {}

    def _lock(self, date: str) -> asyncio.Lock:
        lock = self._locks.get(date)
        if lock is None:
            lock = self._locks[date] = asyncio.Lock()
        return lock

    async def fetch(self, date: str, *, emit_events: bool) -> dict[str, int]:
        """Ambil satu tanggal dengan kunci per tanggal dan semaphore global."""
        async with self._lock(date):
            async with self._sem:
                return await fetch_and_apply(
                    self.conn, self.client, date, emit_events=emit_events, ts=clock.utc_iso()
                )

    def start(self, date: str, *, emit_events: bool) -> asyncio.Task[Any]:
        """Mulai (atau pakai ulang) satu task pengambilan untuk ``date``."""
        task = self._in_flight.get(date)
        if task is None or task.done():
            task = asyncio.ensure_future(self.fetch(date, emit_events=emit_events))
            self._in_flight[date] = task

            def _forget(done: asyncio.Task[Any], key: str = date) -> None:
                if self._in_flight.get(key) is done:
                    self._in_flight.pop(key, None)

            task.add_done_callback(_forget)
        return task

    def auth_allows(self, now: datetime) -> bool:
        return _auth_allows_poll(self.conn, self.token_file, now)

    def mark_fetch_failure(self, date: str, now: datetime, code: str) -> None:
        _mark_fetch_failure(self.conn, date, now, code)

    def mark_auth_failed(self, now: datetime, code: str) -> None:
        _mark_auth_failed(self.conn, self.token_file, now, code)



# --------------------------------------------------------------------------
# Scheduler (SPECS §5.1–§5.4)
# --------------------------------------------------------------------------


@dataclass
class SchedulerState:
    """Keadaan dalam memori satu proses (bukan data yang perlu disimpan)."""

    consecutive_failures: int = 0
    pause_until: datetime | None = None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return None


def window_dates(today: date, window_days: int) -> list[str]:
    """Tanggal target: hari ini - 1 sampai hari ini + (WINDOW_DAYS - 1)."""
    start = today - timedelta(days=1)
    return [(start + timedelta(days=offset)).isoformat() for offset in range(window_days + 1)]


def offset_of(date_str: str, today: date) -> int:
    return (date.fromisoformat(date_str) - today).days


def interval_for_offset(offset: int, cfg: Config) -> int:
    if offset <= -1:
        return cfg.poll_yesterday
    if offset in (0, 1):
        return cfg.poll_near
    if 2 <= offset <= 6:
        return cfg.poll_mid
    return cfg.poll_far


def backoff_seconds(fail_count: int) -> int:
    return min(300, 5 * (2 ** fail_count))


def _is_due(
    row: sqlite3.Row | None, interval: int, now: datetime, rng: random.Random | None
) -> bool:
    if row is None:
        return True
    fail_count = row["fail_count"] or 0
    if fail_count > 0:
        last_attempt = _parse_ts(row["last_attempt_at"])
        if last_attempt is not None and (
            now - last_attempt
        ).total_seconds() < backoff_seconds(fail_count):
            return False
    last_ok = _parse_ts(row["last_ok_at"])
    if last_ok is None:
        return True
    threshold = interval
    if rng is not None:
        threshold = interval * (1.0 + rng.uniform(0.0, 0.10))
    return (now - last_ok).total_seconds() >= threshold


def due_dates(
    conn: sqlite3.Connection,
    today: date,
    now: datetime,
    cfg: Config,
    rng: random.Random | None = None,
) -> list[str]:
    """Tanggal jatuh tempo, urut dari offset terkecil (SPECS §5.3)."""
    due: list[str] = []
    for date_str in window_dates(today, cfg.window_days):
        row = conn.execute(
            "SELECT last_ok_at, last_attempt_at, fail_count FROM date_sync "
            "WHERE booking_date = ?",
            (date_str,),
        ).fetchone()
        interval = interval_for_offset(offset_of(date_str, today), cfg)
        if _is_due(row, interval, now, rng):
            due.append(date_str)
    return due


def _auth_allows_poll(
    conn: sqlite3.Connection, token_file: TokenFile | None, now: datetime
) -> bool:
    if db.get_state(conn, "auth_ok", "1") != "0":
        return True
    if token_file is not None:
        stored = db.get_state(conn, "auth_failed_token_mtime")
        current = token_file.mtime_ns()
        if current is not None and str(current) != (stored or ""):
            return True
    last_attempt = _parse_ts(db.get_state(conn, "auth_last_attempt_at"))
    if last_attempt is None:
        return True
    return (now - last_attempt).total_seconds() >= 600


def _mark_auth_failed(
    conn: sqlite3.Connection,
    token_file: TokenFile | None,
    now: datetime,
    code: str,
) -> None:
    with conn:
        db.set_state(conn, "auth_ok", "0")
        db.set_state(conn, "last_error", code)
        db.set_state(conn, "auth_last_attempt_at", clock.utc_iso(now))
        if token_file is not None:
            mtime = token_file.mtime_ns()
            if mtime is not None:
                db.set_state(conn, "auth_failed_token_mtime", str(mtime))


def _mark_fetch_failure(
    conn: sqlite3.Connection, date_str: str, now: datetime, code: str
) -> None:
    with conn:
        conn.execute(
            "INSERT INTO date_sync (booking_date, last_attempt_at, fail_count, last_error) "
            "VALUES (?, ?, 1, ?) "
            "ON CONFLICT(booking_date) DO UPDATE SET "
            "  last_attempt_at = excluded.last_attempt_at, "
            "  fail_count = date_sync.fail_count + 1, "
            "  last_error = excluded.last_error",
            (date_str, clock.utc_iso(now), code),
        )
        db.set_state(conn, "last_error", code)


def check_baseline(
    conn: sqlite3.Connection, cfg: Config, today: date, now: datetime
) -> bool:
    """Set ``baseline_done`` bila seluruh jendela pernah berhasil (FR-6)."""
    if db.get_state(conn, "baseline_done") == "1":
        return True
    dates = window_dates(today, cfg.window_days)
    placeholders = ",".join("?" * len(dates))
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM date_sync "
        f"WHERE last_ok_at IS NOT NULL AND booking_date IN ({placeholders})",
        dates,
    ).fetchone()
    if row["n"] == len(dates):
        with conn:
            db.set_state(conn, "baseline_done", "1")
        return True
    return False


def _maybe_cleanup_events(
    conn: sqlite3.Connection, now: datetime, retention_days: int
) -> None:
    last = _parse_ts(db.get_state(conn, "last_event_cleanup_at"))
    if last is not None and (now - last).total_seconds() < 86400:
        return
    cutoff = clock.utc_iso(now - timedelta(days=retention_days))
    with conn:
        conn.execute("DELETE FROM booking_events WHERE ts < ?", (cutoff,))
        db.set_state(conn, "last_event_cleanup_at", clock.utc_iso(now))


async def run_tick(
    conn: sqlite3.Connection,
    client: Any,
    cfg: Config,
    *,
    token_file: TokenFile | None = None,
    state: SchedulerState | None = None,
    delay: float = 0.0,
    rng: random.Random | None = None,
    coordinator: FetchCoordinator | None = None,
) -> list[dict[str, Any]]:
    """Satu putaran scheduler: ambil semua tanggal jatuh tempo secara berurutan."""
    now = clock.now()
    today = clock.venue_now().date()

    if state is not None and state.pause_until is not None and now < state.pause_until:
        return []
    allowed = (
        coordinator.auth_allows(now)
        if coordinator is not None
        else _auth_allows_poll(conn, token_file, now)
    )
    if not allowed:
        return []

    emit_events = db.get_state(conn, "baseline_done") == "1"
    results: list[dict[str, Any]] = []
    dates = due_dates(conn, today, now, cfg, rng)

    for index, date_str in enumerate(dates):
        if index > 0 and delay:
            await asyncio.sleep(delay)
        try:
            if coordinator is not None:
                counts = await coordinator.fetch(date_str, emit_events=emit_events)
            else:
                counts = await fetch_and_apply(
                    conn, client, date_str, emit_events=emit_events, ts=clock.utc_iso()
                )
        except AuthError as exc:
            code = type(exc).__name__
            if coordinator is not None:
                coordinator.mark_auth_failed(now, code)
            else:
                _mark_auth_failed(conn, token_file, now, code)
            logger.error("auth state=auth_failed reason=%s", code)
            results.append({"date": date_str, "error": code})
            break
        except AyoError as exc:
            code = type(exc).__name__
            if coordinator is not None:
                coordinator.mark_fetch_failure(date_str, now, code)
            else:
                _mark_fetch_failure(conn, date_str, now, code)
            if state is not None:
                state.consecutive_failures += 1
                if state.consecutive_failures >= 3:
                    state.pause_until = now + timedelta(seconds=60)
                    state.consecutive_failures = 0
            logger.warning("sync date=%s error=%s", date_str, code)
            results.append({"date": date_str, "error": code})
            continue

        if state is not None:
            state.consecutive_failures = 0
            state.pause_until = None
        db.set_state(conn, "last_error", "")
        logger.info(
            "sync date=%s created=%d updated=%d removed=%d restored=%d",
            date_str,
            counts["created"],
            counts["updated"],
            counts["removed"],
            counts["restored"],
        )
        results.append({"date": date_str, **counts})

    check_baseline(conn, cfg, today, now)
    _maybe_cleanup_events(conn, now, cfg.event_retention_days)
    return results


async def scheduler_loop(
    conn: sqlite3.Connection,
    client: Any,
    cfg: Config,
    *,
    token_file: TokenFile | None = None,
    stop_event: asyncio.Event | None = None,
    tick_seconds: float = 5.0,
    coordinator: FetchCoordinator | None = None,
) -> None:
    """Loop latar belakang. Dijalankan dari lifespan aplikasi (M3)."""
    state = SchedulerState()
    rng = random.Random()
    if coordinator is None:
        coordinator = FetchCoordinator(conn, client, cfg, token_file=token_file)
    while stop_event is None or not stop_event.is_set():
        try:
            await run_tick(
                conn,
                client,
                cfg,
                token_file=token_file,
                state=state,
                delay=0.5,
                rng=rng,
                coordinator=coordinator,
            )
        except Exception as exc:  # noqa: BLE001 - loop tidak boleh mati
            logger.error("scheduler tick gagal error=%s", type(exc).__name__)
        if stop_event is None:
            await asyncio.sleep(tick_seconds)
        else:
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=tick_seconds)
                return
            except asyncio.TimeoutError:
                continue


# --------------------------------------------------------------------------
# Mode CLI: python -m app.sync --once
# --------------------------------------------------------------------------


async def run_once(cfg: Config) -> int:
    """Ambil seluruh jendela sekali. Kode keluar 0 bila semua sukses."""
    conn = db.connect(cfg.db_path)
    try:
        db.init_schema(conn)
        token_file = TokenFile(cfg.token_file)
        client = AyoClient(
            base_url=cfg.base_url,
            venue_id=cfg.venue_id,
            field_ids=cfg.field_ids,
            timeout=cfg.http_timeout,
            token_provider=token_file.read,
        )
        try:
            today = clock.venue_today()
            emit_events = db.get_state(conn, "baseline_done") == "1"
            dates = window_dates(today, cfg.window_days)
            ok = failed = written = 0
            for date_str in dates:
                try:
                    counts = await fetch_and_apply(
                        conn,
                        client,
                        date_str,
                        emit_events=emit_events,
                        ts=clock.utc_iso(),
                    )
                except AuthError:
                    print(f"ERROR once date={date_str} error=AuthError")
                    return 2
                except AyoError as exc:
                    failed += 1
                    print(f"WARNING once date={date_str} error={type(exc).__name__}")
                    continue
                ok += 1
                written += counts["created"] + counts["updated"] + counts["restored"]
                print(
                    f"INFO once date={date_str} fields={counts['fields']} "
                    f"created={counts['created']} updated={counts['updated']} "
                    f"removed={counts['removed']} restored={counts['restored']}"
                )
            baseline = check_baseline(conn, cfg, today, clock.now())
            print(
                f"INFO once summary dates={len(dates)} ok={ok} failed={failed} "
                f"written={written} baseline_done={int(baseline)}"
            )
            return 0 if failed == 0 else 1
        finally:
            await client.aclose()
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.sync", description="AYO Booking Sync - mode CLI"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="ambil seluruh jendela sekali secara berurutan lalu keluar",
    )
    args = parser.parse_args(argv)
    if not args.once:
        parser.error("hanya mode --once yang tersedia saat ini")
    try:
        return asyncio.run(run_once(load_config_for_cli()))
    except ConfigError as exc:
        print(f"ERROR config {exc}")
        return 1
    except sqlite3.Error as exc:
        print(f"ERROR database {type(exc).__name__}")
        return 1


def load_config_for_cli() -> Config:
    """Konfigurasi untuk CLI --once (tanpa mewajibkan AYO_API_KEY)."""
    from .config import load_config

    return load_config(require_api_key=False)


if __name__ == "__main__":
    raise SystemExit(main())
