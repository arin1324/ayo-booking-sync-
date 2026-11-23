"""Pembacaan dan validasi konfigurasi dari environment.

Aplikasi wajib gagal start dengan pesan jelas bila nilai wajib kosong atau
tidak valid (SPECS §2). Rahasia tidak pernah ditulis ke log atau pesan error.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

DEFAULT_BASE_URL = "https://api-new.ayo.co.id/api/v2/venue_booking/path"
DEFAULT_FIELD_IDS = (10479, 10480, 10481, 10482, 10483, 10484)


class ConfigError(RuntimeError):
    """Konfigurasi tidak valid; aplikasi dilarang start."""


@dataclass(frozen=True)
class Config:
    """Konfigurasi layanan. Hanya nilai yang dipakai sampai M4."""

    base_url: str
    venue_id: int
    field_ids: tuple[int, ...]
    tz: str
    token_file: str
    db_path: str
    api_key: str
    window_days: int
    http_timeout: float
    free_statuses: tuple[str, ...]


def _as_int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(f"{name} harus bilangan bulat.") from None


def _as_float(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigError(f"{name} harus bilangan.") from None


def _as_int_tuple(
    env: Mapping[str, str], name: str, default: tuple[int, ...]
) -> tuple[int, ...]:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    values: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            values.append(int(part))
        except ValueError:
            raise ConfigError(
                f"{name} harus daftar bilangan bulat dipisahkan koma."
            ) from None
    return tuple(values)


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Baca konfigurasi dari environment. Gagal tertutup bila tidak valid."""
    e = os.environ if env is None else env

    api_key = (e.get("AYO_API_KEY") or "").strip()
    if len(api_key) < 32:
        raise ConfigError("AYO_API_KEY wajib dan minimal 32 karakter.")

    field_ids = _as_int_tuple(e, "AYO_FIELD_IDS", DEFAULT_FIELD_IDS)
    if not field_ids:
        raise ConfigError("AYO_FIELD_IDS tidak boleh kosong.")

    window_days = _as_int(e, "AYO_WINDOW_DAYS", 30)
    if window_days < 1:
        raise ConfigError("AYO_WINDOW_DAYS minimal 1.")

    free_raw = e.get("AYO_FREE_STATUSES", "CANCELLED,CANCELED,EXPIRED")
    free_statuses = tuple(
        s.strip().upper() for s in free_raw.split(",") if s.strip()
    )

    return Config(
        base_url=(e.get("AYO_BASE_URL") or DEFAULT_BASE_URL).strip(),
        venue_id=_as_int(e, "AYO_VENUE_ID", 3749),
        field_ids=field_ids,
        tz=(e.get("AYO_TZ") or "Asia/Makassar").strip(),
        token_file=e.get("AYO_TOKEN_FILE", "/etc/ayo-sync/mobile_token"),
        db_path=e.get("AYO_DB_PATH", "/var/lib/ayo-sync/bookings.db"),
        api_key=api_key,
        window_days=window_days,
        http_timeout=_as_float(e, "AYO_HTTP_TIMEOUT", 15.0),
        free_statuses=free_statuses,
    )
