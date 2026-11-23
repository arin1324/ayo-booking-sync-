"""T14-T20: ketersediaan, kesegaran data, coalescing, dan pembatasan laju.

Seluruh pengambilan memakai ``tests/mock_ayo.py`` (tanpa jaringan ke AYO asli).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app import api, clock, db
from app.availability import AvailabilityService
from app.ayo_client import AyoClient
from app.sync import FetchCoordinator
from tests.mock_ayo import MockAyo

API_KEY = "a" * 40
# 2026-11-23 06:00 WITA (22 Nov 22:00 UTC).
NOW = datetime(2026, 11, 22, 22, 0, tzinfo=timezone.utc)
TODAY = "2026-11-23"
TS = "2026-11-23T00:00:00Z"

EMPTY_OK = {
    "error": False,
    "message": "Berhasil",
    "data": [],
    "status_code": 200,
    "signature": None,
    "execution_time": 0.0,
}


def _seed_day(db_path: str, *, bookings: list[tuple[str, str, str]] = ()) -> None:
    """Lapangan A (07:00-22:00, 60 menit) + booking opsional."""
    conn = db.connect(db_path)
    db.init_schema(conn)
    with conn:
        conn.execute(
            "INSERT INTO fields (field_id, name, sport, session_duration, open_time, "
            "close_time, updated_at) VALUES (10479, 'Lapangan A', 'Padel', 60, '07:00', '22:00', ?)",
            (TS,),
        )
        for i, (start, end, status) in enumerate(bookings, start=1):
            conn.execute(
                "INSERT INTO bookings (source, booking_id, booking_date, field_id, "
                "start_time, end_time, customer, customer_id, status, price, "
                "payment_method, payment_rule, addons, created_at, raw, first_seen_at, "
                "updated_at, removed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "order", i, TODAY, 10479, start, end, "Pelanggan Uji", 111, status, 0,
                    "", "", "", TS, json.dumps({"id": i}), TS, TS, None,
                ),
            )
    conn.close()


def _mark_fresh(db_path: str, *, age_seconds: int = 0) -> None:
    """Tulis ``last_ok_at`` relatif terhadap ``NOW`` (deterministik)."""
    conn = db.connect(db_path)
    with conn:
        ts = clock.utc_iso(NOW - timedelta(seconds=age_seconds))
        conn.execute(
            "INSERT INTO date_sync (booking_date, last_ok_at, last_attempt_at, fail_count, "
            "last_error) VALUES (?, ?, ?, 0, '') "
            "ON CONFLICT(booking_date) DO UPDATE SET last_ok_at = excluded.last_ok_at, "
            "last_attempt_at = excluded.last_attempt_at, fail_count = 0",
            (TODAY, ts, ts),
        )
    conn.close()


def _client(cfg, mock=None, *, token: str = "TESTTOKEN123") -> AyoClient:
    base = mock.base_url if mock is not None else cfg.base_url
    return AyoClient(
        base_url=base,
        venue_id=cfg.venue_id,
        field_ids=cfg.field_ids,
        timeout=cfg.http_timeout,
        token_provider=lambda: token,
    )


# --------------------------------------------------------------------------
# T14 — Lampiran B, murni dari cache
# --------------------------------------------------------------------------


def _status_at(db_path, cfg, *, time: str, duration: int = 60, now: datetime = NOW) -> str:
    clock.set_now_fn(lambda: now)
    conn = db.connect(str(db_path))
    db.init_schema(conn)
    service = AvailabilityService(conn, cfg)
    age = service.age_seconds(TODAY, clock.now())
    result = service.compute(TODAY, time, duration, [10479], age)
    conn.close()
    return str(result[0]["status"])


def test_t14_appendix_b(tmp_path, make_config):
    db_path = tmp_path / "b.db"
    _seed_day(str(db_path), bookings=[("08:00", "09:00", "FINISHED")])
    cfg = make_config(db_path=str(db_path))
    _mark_fresh(str(db_path))

    cases = [
        ("08:00", 60, "booked"),
        ("07:30", 60, "booked"),
        ("08:30", 30, "booked"),
        ("07:00", 60, "available"),
        ("09:00", 60, "available"),
        ("21:00", 60, "available"),
        ("21:30", 60, "closed"),
        ("06:00", 60, "closed"),
    ]
    for time_str, duration, expected in cases:
        assert _status_at(db_path, cfg, time=time_str, duration=duration) == expected, time_str

    # 07:00 pada jam simulasi 10:00 -> past.
    later = datetime(2026, 11, 23, 2, 0, tzinfo=timezone.utc)  # 10:00 WITA
    assert _status_at(db_path, cfg, time="07:00", now=later) == "past"

    # Booking berstatus batal tidak menempati slot.
    cancel_path = tmp_path / "cancel.db"
    _seed_day(str(cancel_path), bookings=[("08:00", "09:00", "CANCELLED")])
    _mark_fresh(str(cancel_path))
    assert _status_at(cancel_path, cfg, time="08:00") == "available"

    # Booking removed_at terisi tidak menempati slot.
    removed_path = tmp_path / "removed.db"
    _seed_day(str(removed_path), bookings=[("08:00", "09:00", "FINISHED")])
    conn = db.connect(str(removed_path))
    with conn:
        conn.execute("UPDATE bookings SET removed_at = ?", (TS,))
    conn.close()
    _mark_fresh(str(removed_path))
    assert _status_at(removed_path, cfg, time="08:00") == "available"

    # Status tak dikenal dianggap menempati slot (aman).
    unknown_path = tmp_path / "unknown.db"
    _seed_day(str(unknown_path), bookings=[("08:00", "09:00", "SESUATU_YANG_BARU")])
    _mark_fresh(str(unknown_path))
    assert _status_at(unknown_path, cfg, time="08:00") == "booked"


def test_t14_next_available(tmp_path, make_config):
    db_path = tmp_path / "next.db"
    _seed_day(str(db_path), bookings=[("08:00", "09:00", "FINISHED")])
    cfg = make_config(db_path=str(db_path))
    _mark_fresh(str(db_path))
    clock.set_now_fn(lambda: NOW)

    conn = db.connect(str(db_path))
    db.init_schema(conn)
    service = AvailabilityService(conn, cfg)
    age = service.age_seconds(TODAY, clock.now())
    result = service.compute(TODAY, "08:00", 60, [10479], age)[0]
    conn.close()
    assert result["status"] == "booked"
    assert result["next_available"] == "09:00"


# --------------------------------------------------------------------------
# T15 — coalescing: 20 pengambilan bersamaan -> 1 request
# --------------------------------------------------------------------------


def test_t15_coalescing_single_request(tmp_path, make_config):
    with MockAyo() as mock:
        mock.set_json(EMPTY_OK)
        db_path = tmp_path / "coalesce.db"
        _seed_day(str(db_path), bookings=[("08:00", "09:00", "FINISHED")])
        _mark_fresh(str(db_path), age_seconds=10_000)  # basi -> wajib ambil ulang
        cfg = make_config(mock, db_path=str(db_path))
        clock.set_now_fn(lambda: NOW)

        conn = db.connect(str(db_path))
        db.init_schema(conn)
        client = _client(cfg, mock)
        coordinator = FetchCoordinator(conn, client, cfg)
        service = AvailabilityService(conn, cfg, coordinator=coordinator)

        async def scenario():
            try:
                return await asyncio.gather(
                    *[service.ensure_fresh(TODAY) for _ in range(20)]
                )
            finally:
                await client.aclose()

        results = asyncio.run(scenario())
        conn.close()

    assert len(mock.requests) == 1
    assert all(result == "live" for result in results)


# --------------------------------------------------------------------------
# T16-T18 — endpoint HTTP dengan mock
# --------------------------------------------------------------------------


def _make_app(tmp_path, make_config, mock, *, name: str, fresh_age: int | None = None):
    db_path = tmp_path / name
    _seed_day(str(db_path), bookings=[("08:00", "09:00", "FINISHED")])
    cfg = make_config(mock, db_path=str(db_path), api_key=API_KEY)
    if fresh_age is not None:
        _mark_fresh(str(db_path), age_seconds=fresh_age)
    client = _client(cfg, mock)
    app = api.create_app(cfg, start_scheduler=False, client=client)
    return app, cfg


def test_t16_fresh_data_no_request(tmp_path, make_config):
    with MockAyo() as mock:
        mock.set_json(EMPTY_OK)
        app, _cfg = _make_app(tmp_path, make_config, mock, name="fresh.db", fresh_age=5)
        clock.set_now_fn(lambda: NOW)
        with TestClient(app) as tc:
            response = tc.get(
                "/schedule/availability",
                params={"date": TODAY, "time": "08:00", "duration": 60},
                headers={"X-API-Key": API_KEY},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["data_source"] == "cache"
        assert body["fields"][0]["status"] == "booked"
        assert len(mock.requests) == 0
        assert "customer" not in json.dumps(body["fields"])


def test_t18_on_demand_fetch_updates_last_ok(tmp_path, make_config):
    with MockAyo() as mock:
        mock.set_json(EMPTY_OK)
        app, cfg = _make_app(tmp_path, make_config, mock, name="live.db", fresh_age=10_000)
        clock.set_now_fn(lambda: NOW)
        with TestClient(app) as tc:
            body = tc.get(
                "/schedule/availability",
                params={"date": TODAY, "time": "08:00"},
                headers={"X-API-Key": API_KEY},
            ).json()
        assert body["data_source"] == "live"

        conn = db.connect(cfg.db_path)
        row = conn.execute(
            "SELECT last_ok_at FROM date_sync WHERE booking_date = ?", (TODAY,)
        ).fetchone()
        conn.close()
        assert row["last_ok_at"] == clock.utc_iso(NOW)
        assert len(mock.requests) == 1


def test_t17_stale_then_unknown(tmp_path, make_config):
    # Basi 100 detik (<= 600): pengambilan gagal (500) -> tetap "stale".
    with MockAyo() as mock:
        mock.set_raw("boom", 500)
        app, _cfg = _make_app(tmp_path, make_config, mock, name="stale.db", fresh_age=100)
        clock.set_now_fn(lambda: NOW)
        with TestClient(app) as tc:
            body = tc.get(
                "/schedule/availability",
                params={"date": TODAY, "time": "08:00"},
                headers={"X-API-Key": API_KEY},
            ).json()
        assert body["data_source"] == "stale"
        assert body["fields"][0]["status"] == "booked"

        # Basi 700 detik (> 600) dan pengambilan gagal -> status unknown.
        app2, _cfg2 = _make_app(tmp_path, make_config, mock, name="unknown.db", fresh_age=700)
        with TestClient(app2) as tc2:
            body2 = tc2.get(
                "/schedule/availability",
                params={"date": TODAY, "time": "08:00"},
                headers={"X-API-Key": API_KEY},
            ).json()
        assert body2["data_source"] == "stale"
        assert body2["fields"][0]["status"] == "unknown"


# --------------------------------------------------------------------------
# T19 — jendela tanggal
# --------------------------------------------------------------------------


def test_t19_out_of_window_and_past(tmp_path, make_config):
    with MockAyo() as mock:
        mock.set_json(EMPTY_OK)
        app, cfg = _make_app(tmp_path, make_config, mock, name="window.db", fresh_age=5)
        clock.set_now_fn(lambda: NOW)
        h = {"X-API-Key": API_KEY}
        with TestClient(app) as tc:
            beyond = (clock.venue_today() + timedelta(days=cfg.window_days)).isoformat()
            response = tc.get(
                "/schedule/availability",
                params={"date": beyond, "time": "08:00"},
                headers=h,
            )
            assert response.status_code == 422
            assert response.json()["error"]["code"] == "date_out_of_window"

            # Tanggal masa lalu -> semua past (data tetap disajikan).
            day_before = (clock.venue_today() - timedelta(days=2)).isoformat()
            past = tc.get(
                "/schedule/availability",
                params={"date": day_before, "time": "08:00"},
                headers=h,
            ).json()
            assert past["fields"][0]["status"] == "past"

            # `time` tidak valid -> 422 invalid_parameter.
            bad = tc.get(
                "/schedule/availability",
                params={"date": TODAY, "time": "8 pagi"},
                headers=h,
            )
            assert bad.status_code == 422
            assert bad.json()["error"]["code"] == "invalid_parameter"


# --------------------------------------------------------------------------
# T20 — AYO_FREE_STATUSES
# --------------------------------------------------------------------------


def test_t20_free_statuses(tmp_path, make_config):
    db_path = tmp_path / "free.db"
    _seed_day(str(db_path), bookings=[("08:00", "09:00", "EXPIRED")])
    _mark_fresh(str(db_path))
    cfg = make_config(db_path=str(db_path))
    assert _status_at(db_path, cfg, time="08:00") == "available"

    # EXPIRED tidak ada di daftar bebas -> dianggap menempati slot.
    cfg_other = make_config(db_path=str(db_path), free_statuses=("CANCELLED", "CANCELED"))
    assert _status_at(db_path, cfg_other, time="08:00") == "booked"


# --------------------------------------------------------------------------
# /schedule/availability/day
# --------------------------------------------------------------------------


def test_day_slots_with_statuses(tmp_path, make_config):
    with MockAyo() as mock:
        mock.set_json(EMPTY_OK)
        app, _cfg = _make_app(tmp_path, make_config, mock, name="day.db", fresh_age=5)
        clock.set_now_fn(lambda: NOW)
        with TestClient(app) as tc:
            body = tc.get(
                "/schedule/availability/day",
                params={"date": TODAY},
                headers={"X-API-Key": API_KEY},
            ).json()
        assert body["data_source"] == "cache"
        slots = body["fields"][0]["slots"]
        assert slots[0] == {"start": "07:00", "end": "08:00", "status": "available"}
        assert slots[1] == {"start": "08:00", "end": "09:00", "status": "booked"}
        assert slots[-1] == {"start": "21:00", "end": "22:00", "status": "available"}
        assert len(slots) == 15  # 07:00 sampai 22:00, sesi 60 menit


# --------------------------------------------------------------------------
# Batas laju: semaphore maksimal 3 request bersamaan
# --------------------------------------------------------------------------


def test_semaphore_limits_concurrency(tmp_path, make_config):
    with MockAyo() as mock:
        mock.set_json(EMPTY_OK)
        cfg = make_config(mock)
        clock.set_now_fn(lambda: NOW)

        class _CountingClient:
            def __init__(self):
                self.active = 0
                self.peak = 0
                self.calls = 0

            async def fetch_calendar(self, date: str):
                self.active += 1
                self.peak = max(self.peak, self.active)
                self.calls += 1
                await asyncio.sleep(0.05)
                self.active -= 1
                return []

        conn = db.connect(tmp_path / "rate.db")
        db.init_schema(conn)
        counting = _CountingClient()
        coordinator = FetchCoordinator(conn, counting, cfg)

        async def scenario():
            dates = [f"2026-12-{d:02d}" for d in range(1, 11)]
            await asyncio.gather(*[coordinator.fetch(d, emit_events=False) for d in dates])

        asyncio.run(scenario())
        conn.close()

    assert counting.calls == 10
    assert counting.peak <= 3
