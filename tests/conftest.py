"""Fixture bersama untuk seluruh test."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from app import clock, db
from app.config import Config

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DATE = "2026-11-23"


@pytest.fixture(autouse=True)
def _clock_state():
    """Setiap test memakai zona venue Asia/Makassar dan jam sistem semula."""
    clock.configure("Asia/Makassar")
    clock.reset_now_fn()
    yield
    clock.reset_now_fn()
    clock.configure("Asia/Makassar")


@pytest.fixture
def conn(tmp_path: Any):
    connection = db.connect(str(tmp_path / "test.db"))
    db.init_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def lampiran_a() -> dict[str, Any]:
    """Fixture minimal SPECS Lampiran A (nama pelanggan sudah samaran)."""
    path = FIXTURES / "calendar_lampiran_a.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def make_config():
    """Bangun Config lengkap tanpa membaca environment."""

    def _make(mock: Any = None, **overrides: Any) -> Config:
        values: dict[str, Any] = dict(
            base_url=mock.base_url if mock is not None else "http://127.0.0.1:9/api",
            venue_id=3749,
            field_ids=(10479, 10480),
            tz="Asia/Makassar",
            token_file="/tmp/ayo-sync-token-tidak-ada",
            db_path="/tmp/ayo-sync-test.db",
            api_key="0" * 32,
            window_days=30,
            http_timeout=5.0,
            free_statuses=("CANCELLED", "CANCELED", "EXPIRED"),
            poll_yesterday=3600,
            poll_near=120,
            poll_mid=600,
            poll_far=3600,
            event_retention_days=60,
            avail_fetch_timeout=5.0,
            avail_max_age=30,
            avail_min_retry=10,
            avail_stale_limit=600,
            expose_customer=True,
            expose_customer_contact=False,
            customer_enabled=False,
            bind_host="127.0.0.1",
            bind_port=8000,
            log_level="INFO",
        )
        values.update(overrides)
        return Config(**values)

    return _make
