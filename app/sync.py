"""Parser, algoritma diff, dan penerapan snapshot (SPECS §3.5, §5.6).

Modul ini sengaja sinkron (sqlite3). Pemanggil async (scheduler/API) akan
membungkusnya dengan ``asyncio.to_thread`` pada milestone berikutnya.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

from . import db

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
