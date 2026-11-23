"""Perhitungan ketersediaan lapangan dan kesegaran data (SPECS §6).

Modul ini memisahkan logika murni (slot, tumpang tindih, status) dari
koordinasi pengambilan ulang AYO (``ensure_fresh``) agar mudah diuji.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import date as date_cls
from datetime import datetime

from . import clock, db
from .ayo_client import AuthError, AyoError
from .config import Config
from .sync import FetchCoordinator, _parse_ts

logger = logging.getLogger("ayo.availability")

# Nilai ``data_source`` yang mungkin (SPECS §7.5).
LIVE = "live"
CACHE = "cache"
STALE = "stale"


def parse_hhmm(value: str | None) -> int | None:
    """Ubah ``"HH:MM"`` / ``"HH:MM:SS"`` menjadi menit sejak tengah malam."""
    if not value:
        return None
    parts = str(value).split(":")
    if len(parts) < 2:
        return None
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def minutes_to_hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def overlaps(req_start: int, req_end: int, b_start: int, b_end: int) -> bool:
    """Tumpang tindih interval setengah terbuka (SPECS §6.3)."""
    return req_start < b_end and b_start < req_end


def occupies(status: str | None, free_statuses: tuple[str, ...]) -> bool:
    """Booking menempati slot bila statusnya bukan status bebas (SPECS §6.3)."""
    return (status or "").strip().upper() not in free_statuses


def status_for(
    *,
    is_past: bool,
    req_start: int,
    req_end: int,
    open_min: int | None,
    close_min: int | None,
    field_exists: bool,
    data_age: float | None,
    stale_limit: int,
    booked: bool,
) -> str:
    """Terapkan urutan status SPECS §6.2 (yang pertama cocok menang)."""
    if is_past:
        return "past"
    if not field_exists or open_min is None or close_min is None:
        return "unknown"
    if req_start < open_min or req_end > close_min:
        return "closed"
    if data_age is None or data_age > stale_limit:
        return "unknown"
    if booked:
        return "booked"
    return "available"


def next_available(
    *,
    req_start: int,
    open_min: int,
    close_min: int,
    duration: int,
    session: int,
    busy: list[tuple[int, int]],
) -> str | None:
    """Waktu mulai paling awal (kelipatan sesi dari jam buka) saat lapangan bebas."""
    step = session if session and session > 0 else 60
    candidate = open_min
    while candidate + duration <= close_min:
        if candidate > req_start and not any(
            overlaps(candidate, candidate + duration, start, end) for start, end in busy
        ):
            return minutes_to_hhmm(candidate)
        candidate += step
    return None


class AvailabilityService:
    """Baca ketersediaan dari database, dengan pengambilan ulang on-demand."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        cfg: Config,
        *,
        coordinator: FetchCoordinator | None = None,
        token_file: object | None = None,
    ) -> None:
        self.conn = conn
        self.cfg = cfg
        self.coordinator = coordinator
        self.token_file = token_file

    # --- akses data ------------------------------------------------------
    def _sync_row(self, date: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT last_ok_at, last_attempt_at FROM date_sync WHERE booking_date = ?",
            (date,),
        ).fetchone()

    def age_seconds(self, date: str, now: datetime) -> float | None:
        row = self._sync_row(date)
        last_ok = _parse_ts(row["last_ok_at"]) if row is not None else None
        if last_ok is None:
            return None
        return max(0.0, (now - last_ok).total_seconds())

    def _field_entries(
        self, field_ids: list[int] | None
    ) -> list[tuple[int, sqlite3.Row | None]]:
        rows = self.conn.execute(
            "SELECT field_id, name, sport, session_duration, open_time, close_time "
            "FROM fields ORDER BY field_id"
        ).fetchall()
        by_id = {row["field_id"]: row for row in rows}
        if field_ids:
            return [(fid, by_id.get(fid)) for fid in field_ids]
        return [(row["field_id"], row) for row in rows]

    def default_duration(self, field_ids: list[int] | None) -> int:
        """Durasi bawaan: ``session_duration`` lapangan pertama, atau 60."""
        for _field_id, row in self._field_entries(field_ids):
            if row is not None:
                return row["session_duration"] or 60
        return 60

    def _busy(self, date: str, field_id: int) -> list[tuple[int, int]]:
        rows = self.conn.execute(
            "SELECT start_time, end_time, status FROM bookings "
            "WHERE booking_date = ? AND field_id = ? AND removed_at IS NULL",
            (date, field_id),
        ).fetchall()
        busy: list[tuple[int, int]] = []
        for row in rows:
            if not occupies(row["status"], self.cfg.free_statuses):
                continue
            start = parse_hhmm(row["start_time"])
            end = parse_hhmm(row["end_time"])
            if start is None or end is None:
                continue
            busy.append((start, end))
        return busy

    # --- kesegaran dan pengambilan ulang (SPECS §6.5) --------------------
    def _maybe_fetch(self, now: datetime, row: sqlite3.Row | None) -> bool:
        if self.coordinator is None:
            return False
        if not self.coordinator.auth_allows(now):
            return False
        if row is not None:
            last_attempt = _parse_ts(row["last_attempt_at"])
            if last_attempt is not None and (
                now - last_attempt
            ).total_seconds() < self.cfg.avail_min_retry:
                return False
        return True

    async def ensure_fresh(self, date: str) -> str:
        """Pastikan data ``date`` segar. Mengembalikan ``live``/``cache``/``stale``."""
        now = clock.now()
        row = self._sync_row(date)
        last_ok = _parse_ts(row["last_ok_at"]) if row is not None else None
        if last_ok is not None and (now - last_ok).total_seconds() <= self.cfg.avail_max_age:
            return CACHE
        if not self._maybe_fetch(now, row) or self.coordinator is None:
            return STALE

        emit_events = db.get_state(self.conn, "baseline_done") == "1"
        task = self.coordinator.start(date, emit_events=emit_events)
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout=self.cfg.avail_fetch_timeout
            )
            return LIVE
        except AuthError as exc:
            self.coordinator.mark_auth_failed(now, type(exc).__name__)
            return STALE
        except AyoError as exc:
            self.coordinator.mark_fetch_failure(date, now, type(exc).__name__)
            return STALE
        except asyncio.TimeoutError:
            return STALE
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - jangan sampai menular ke klien
            logger.error("avail fetch gagal date=%s error=%s", date, type(exc).__name__)
            return STALE

    # --- perhitungan -----------------------------------------------------
    def compute(
        self,
        date: str,
        time_str: str,
        duration: int | None,
        field_ids: list[int] | None,
        data_age: float | None,
    ) -> list[dict[str, object]]:
        req_start = parse_hhmm(time_str)
        if req_start is None:
            raise ValueError("time")
        venue_now = clock.venue_now()
        day = date_cls.fromisoformat(date)
        now_min = venue_now.hour * 60 + venue_now.minute
        is_past = day < venue_now.date() or (day == venue_now.date() and req_start < now_min)

        result: list[dict[str, object]] = []
        for field_id, row in self._field_entries(field_ids):
            if row is None:
                result.append({"field_id": field_id, "name": "", "status": "unknown"})
                continue
            open_min = parse_hhmm(row["open_time"])
            close_min = parse_hhmm(row["close_time"])
            session = row["session_duration"] or 60
            dur = duration or session
            req_end = req_start + dur
            busy = self._busy(date, field_id)
            booked = any(
                overlaps(req_start, req_end, start, end) for start, end in busy
            )
            status = status_for(
                is_past=is_past,
                req_start=req_start,
                req_end=req_end,
                open_min=open_min,
                close_min=close_min,
                field_exists=True,
                data_age=data_age,
                stale_limit=self.cfg.avail_stale_limit,
                booked=booked,
            )
            item: dict[str, object] = {
                "field_id": field_id,
                "name": row["name"],
                "status": status,
            }
            if status == "booked" and open_min is not None and close_min is not None:
                item["next_available"] = next_available(
                    req_start=req_start,
                    open_min=open_min,
                    close_min=close_min,
                    duration=dur,
                    session=session,
                    busy=busy,
                )
            result.append(item)
        return result

    def compute_day(
        self,
        date: str,
        duration: int | None,
        field_ids: list[int] | None,
        data_age: float | None,
    ) -> list[dict[str, object]]:
        venue_now = clock.venue_now()
        day = date_cls.fromisoformat(date)
        now_min = venue_now.hour * 60 + venue_now.minute
        date_is_past = day < venue_now.date()

        result: list[dict[str, object]] = []
        for field_id, row in self._field_entries(field_ids):
            if row is None:
                result.append({"field_id": field_id, "name": "", "slots": []})
                continue
            open_min = parse_hhmm(row["open_time"])
            close_min = parse_hhmm(row["close_time"])
            session = row["session_duration"] or 60
            dur = duration or session
            busy = self._busy(date, field_id)
            slots: list[dict[str, str]] = []
            if open_min is not None and close_min is not None:
                start = open_min
                while start + session <= close_min:
                    booked = any(overlaps(start, start + dur, s, e) for s, e in busy)
                    is_past = date_is_past or (
                        day == venue_now.date() and start < now_min
                    )
                    status = status_for(
                        is_past=is_past,
                        req_start=start,
                        req_end=start + dur,
                        open_min=open_min,
                        close_min=close_min,
                        field_exists=True,
                        data_age=data_age,
                        stale_limit=self.cfg.avail_stale_limit,
                        booked=booked,
                    )
                    slots.append(
                        {"start": minutes_to_hhmm(start), "end": minutes_to_hhmm(start + session), "status": status}
                    )
                    start += session
            result.append({"field_id": field_id, "name": row["name"], "slots": slots})
        return result
