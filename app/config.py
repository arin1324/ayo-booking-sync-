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
    poll_yesterday: int
    poll_near: int
    poll_mid: int
    poll_far: int
    event_retention_days: int
    avail_fetch_timeout: float
    avail_max_age: int
    avail_min_retry: int
    avail_stale_limit: int
    expose_customer: bool
    expose_customer_contact: bool
    customer_enabled: bool
    api_rate_limit: int
    api_rate_window: int
    bind_host: str
    bind_port: int
    log_level: str


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


def _as_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = env.get(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} harus bernilai true/false.")


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


def load_config(
    env: Mapping[str, str] | None = None, *, require_api_key: bool = True
) -> Config:
    """Baca konfigurasi dari environment. Gagal tertutup bila tidak valid.

    ``require_api_key=False`` dipakai oleh CLI ``--once`` yang tidak melayani
    API HTTP, sehingga tidak memerlukan ``AYO_API_KEY``.
    """
    e = os.environ if env is None else env

    api_key = (e.get("AYO_API_KEY") or "").strip()
    if require_api_key and len(api_key) < 32:
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
        poll_yesterday=_as_int(e, "AYO_POLL_YESTERDAY", 3600),
        poll_near=_as_int(e, "AYO_POLL_NEAR", 120),
        poll_mid=_as_int(e, "AYO_POLL_MID", 600),
        poll_far=_as_int(e, "AYO_POLL_FAR", 3600),
        event_retention_days=_as_int(e, "EVENT_RETENTION_DAYS", 60),
        avail_fetch_timeout=_as_float(e, "AVAIL_FETCH_TIMEOUT", 5.0),
        avail_max_age=_as_int(e, "AVAIL_MAX_AGE", 30),
        avail_min_retry=_as_int(e, "AVAIL_MIN_RETRY", 10),
        avail_stale_limit=_as_int(e, "AVAIL_STALE_LIMIT", 600),
        expose_customer=_as_bool(e, "AYO_EXPOSE_CUSTOMER", True),
        expose_customer_contact=_as_bool(e, "AYO_EXPOSE_CUSTOMER_CONTACT", False),
        customer_enabled=_as_bool(e, "AYO_CUSTOMER_ENABLED", False),
        api_rate_limit=_as_int(e, "API_RATE_LIMIT", 60),
        api_rate_window=_as_int(e, "API_RATE_WINDOW", 60),
        bind_host=e.get("BIND_HOST", "127.0.0.1"),
        bind_port=_as_int(e, "BIND_PORT", 8000),
        log_level=(e.get("LOG_LEVEL") or "INFO").upper(),
    )
