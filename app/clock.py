"""Sumber waktu tunggal agar dapat disimulasikan pada test (SPECS §1).

- ``now()`` mengembalikan waktu UTC (timezone-aware).
- ``venue_now()`` mengembalikan waktu lokal venue memakai zona ``AYO_TZ``.
- Timestamp sistem disimpan sebagai UTC ISO 8601 berakhiran ``Z``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

_DEFAULT_TZ = "Asia/Makassar"

_now_fn: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
_venue_tz: ZoneInfo = ZoneInfo(_DEFAULT_TZ)


def configure(tz: str) -> None:
    """Tetapkan zona waktu venue (dipanggil sekali saat start)."""
    global _venue_tz
    _venue_tz = ZoneInfo(tz)


def venue_tz() -> ZoneInfo:
    return _venue_tz


def set_now_fn(fn: Callable[[], datetime]) -> None:
    """Ganti sumber waktu. Dipakai pada test."""
    global _now_fn
    _now_fn = fn


def reset_now_fn() -> None:
    """Kembalikan sumber waktu ke jam sistem."""
    global _now_fn
    _now_fn = lambda: datetime.now(timezone.utc)


def now() -> datetime:
    """Waktu sekarang dalam UTC (timezone-aware)."""
    return _now_fn()


def venue_now() -> datetime:
    """Waktu sekarang dalam zona waktu venue."""
    return now().astimezone(_venue_tz)


def utc_iso(dt: datetime | None = None) -> str:
    """Format UTC ISO 8601 berakhiran ``Z`` dengan presisi detik."""
    value = now() if dt is None else dt
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def venue_today() -> date:
    """Tanggal 'hari ini' menurut zona waktu venue."""
    return venue_now().date()
