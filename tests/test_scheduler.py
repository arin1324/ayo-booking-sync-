"""T7-T11: penanganan token kedaluwarsa, jendela rolling, interval, backoff,
dan memastikan token uji tidak pernah bocor ke log."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlparse

from app import clock, db, sync
from app.ayo_client import AyoClient, TokenFile
from tests.mock_ayo import MockAyo

BASE = datetime(2026, 11, 23, 2, 0, tzinfo=timezone.utc)  # 10:00 WITA, 23 Nov
EMPTY_OK = {
    "error": False,
    "message": "Berhasil",
    "data": [],
    "status_code": 200,
    "signature": None,
    "execution_time": 0.0,
}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _client(mock: MockAyo | None, cfg, token_file: TokenFile | None = None) -> AyoClient:
    provider = token_file.read if token_file is not None else (lambda: "TESTTOKEN123")
    return AyoClient(
        base_url=cfg.base_url,
        venue_id=cfg.venue_id,
        field_ids=cfg.field_ids,
        timeout=cfg.http_timeout,
        token_provider=provider,
    )


def test_t7_auth_failure_stops_polling_and_recovers(conn, make_config, tmp_path):
    token_path = tmp_path / "mobile_token"
    token_path.write_text("TESTTOKEN123", encoding="utf-8")
    token_file = TokenFile(str(token_path))

    with MockAyo() as mock:
        cfg = make_config(mock, window_days=1, token_file=str(token_path))
        mock.set_json(EMPTY_OK)
        t0 = BASE
        clock.set_now_fn(lambda: t0)

        async def scenario():
            client = _client(mock, cfg, token_file)
            try:
                await sync.run_tick(conn, client, cfg, token_file=token_file, delay=0.0)
                assert db.get_state(conn, "auth_ok") == "1"
                baseline = conn.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"]

                # Semua tanggal jatuh tempo; AYO balas 401.
                clock.set_now_fn(lambda: t0 + timedelta(hours=2))
                mock.set_raw("{}", 401)
                await sync.run_tick(conn, client, cfg, token_file=token_file, delay=0.0)
                assert db.get_state(conn, "auth_ok") == "0"
                requests_after_401 = len(mock.requests)
                assert conn.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"] == baseline

                # Tanpa perubahan token: tidak ada request baru (polling berhenti agresif).
                await sync.run_tick(conn, client, cfg, token_file=token_file, delay=0.0)
                assert len(mock.requests) == requests_after_401

                # Token diganti: polling lanjut dan auth_ok kembali 1.
                token_path.write_text("TOKENBARU456", encoding="utf-8")
                mock.set_json(EMPTY_OK)
                await sync.run_tick(conn, client, cfg, token_file=token_file, delay=0.0)
                assert db.get_state(conn, "auth_ok") == "1"
                assert len(mock.requests) > requests_after_401
            finally:
                await client.aclose()

        _run(scenario())


def test_t8_token_never_leaks_into_logs(caplog, conn, make_config, tmp_path):
    token_path = tmp_path / "mobile_token"
    token_path.write_text("TESTTOKEN123", encoding="utf-8")
    token_file = TokenFile(str(token_path))

    async def scenario():
        with caplog.at_level(logging.DEBUG):
            # 1) Sukses lewat scheduler
            with MockAyo() as mock:
                mock.set_json(EMPTY_OK)
                cfg = make_config(mock, window_days=1, token_file=str(token_path))
                client = _client(mock, cfg, token_file)
                try:
                    await sync.run_tick(conn, client, cfg, token_file=token_file, delay=0.0)
                finally:
                    await client.aclose()

            # 2) Koneksi gagal
            cfg = make_config(
                None,
                base_url="http://127.0.0.1:9/api",
                window_days=1,
                token_file=str(token_path),
            )
            client = _client(None, cfg, token_file)
            try:
                try:
                    await client.fetch_calendar("2026-11-23")
                except Exception:  # noqa: BLE001 - yang diuji adalah isi log
                    pass
            finally:
                await client.aclose()

            # 3) 401
            with MockAyo() as mock:
                mock.set_raw("{}", 401)
                cfg = make_config(mock, window_days=1, token_file=str(token_path))
                client = _client(mock, cfg, token_file)
                try:
                    try:
                        await client.fetch_calendar("2026-11-23")
                    except Exception:  # noqa: BLE001
                        pass
                finally:
                    await client.aclose()

    _run(scenario())

    assert "TESTTOKEN123" not in caplog.text
    assert "mobile_token" not in caplog.text


def _payload_for(date_str: str) -> dict[str, Any]:
    """Respons kalender dengan ID booking unik per tanggal."""
    booking_id = int(date_str.replace("-", ""))
    return {
        "error": False,
        "message": "Berhasil",
        "data": [
            {
                "field_name": "Lapangan A",
                "session_duration": 60,
                "sport_name": "Padel",
                "sport_id": 12,
                "field_id": 10479,
                "data": [
                    {
                        "id": booking_id,
                        "field_id": 10479,
                        "user_id": 0,
                        "user_name": "Pelanggan Uji",
                        "start_time": "07:00:00",
                        "end_time": "08:00:00",
                        "created_at": "2026-11-21T06:59:35.000000Z",
                        "status": None,
                        "total_price": 1,
                        "payment_method": "Transfer",
                        "final_status": "MANUAL",
                        "payment_rule": "MANUAL",
                        "source_table": "reservation",
                        "add_on_group_name": None,
                    }
                ],
                "start_time": "07:00:00",
                "end_time": "22:00:00",
            }
        ],
        "status_code": 200,
        "signature": None,
        "execution_time": 0.0,
    }


def test_t9_rolling_window_shifts_at_midnight(conn, make_config):
    def responder(path: str):
        query = parse_qs(urlparse(path).query)
        payload = _payload_for(query["date"][0])
        return 200, json.dumps(payload).encode("utf-8"), "application/json"

    with MockAyo() as mock:
        mock.set_responder(responder)
        cfg = make_config(mock, window_days=30)

        # Hari 1: 23:59 WITA (15:59 UTC).
        day1 = datetime(2026, 11, 23, 15, 59, tzinfo=timezone.utc)
        clock.set_now_fn(lambda: day1)

        async def first():
            client = _client(mock, cfg)
            try:
                await sync.run_tick(conn, client, cfg, delay=0.0)
            finally:
                await client.aclose()

        _run(first())
        assert len(mock.requests) == 31  # yesterday + 30 hari

        # Lewat tengah malam: 00:00:30 WITA tanggal 24 (16:00:30 UTC).
        day2 = datetime(2026, 11, 23, 16, 0, 30, tzinfo=timezone.utc)
        clock.set_now_fn(lambda: day2)

        async def second():
            client = _client(mock, cfg)
            try:
                await sync.run_tick(conn, client, cfg, delay=0.0)
            finally:
                await client.aclose()

        _run(second())
        # Hanya tanggal baru (today+29) yang belum pernah diambil.
        assert len(mock.requests) == 32
        assert conn.execute(
            "SELECT 1 FROM date_sync WHERE booking_date = '2026-12-23'"
        ).fetchone() is not None
        # Tanggal yang keluar jendela tidak diambil lagi, tetapi datanya tetap ada
        # dan tidak ditandai hilang.
        left_window = conn.execute(
            "SELECT removed_at FROM bookings WHERE booking_date = '2026-11-22'"
        ).fetchone()
        assert left_window is not None
        assert left_window["removed_at"] is None
        assert conn.execute(
            "SELECT last_ok_at FROM date_sync WHERE booking_date = '2026-11-22'"
        ).fetchone()["last_ok_at"] is not None


def test_t10_interval_mapping(make_config):
    cfg = make_config()
    assert sync.interval_for_offset(-1, cfg) == 3600
    assert sync.interval_for_offset(0, cfg) == 120
    assert sync.interval_for_offset(1, cfg) == 120
    assert sync.interval_for_offset(2, cfg) == 600
    assert sync.interval_for_offset(6, cfg) == 600
    assert sync.interval_for_offset(7, cfg) == 3600
    assert sync.interval_for_offset(29, cfg) == 3600


def test_t10_no_requests_while_dates_fresh(conn, make_config):
    today = date(2026, 11, 23)
    with MockAyo() as mock:
        mock.set_json(EMPTY_OK)
        cfg = make_config(mock, window_days=30)
        clock.set_now_fn(lambda: BASE)

        with conn:
            for date_str in sync.window_dates(today, 30):
                interval = sync.interval_for_offset(sync.offset_of(date_str, today), cfg)
                conn.execute(
                    "INSERT INTO date_sync "
                    "(booking_date, last_ok_at, last_attempt_at, fail_count, last_error) "
                    "VALUES (?, ?, ?, 0, '')",
                    (
                        date_str,
                        clock.utc_iso(BASE - timedelta(seconds=interval - 1)),
                        clock.utc_iso(BASE),
                    ),
                )

        async def fresh():
            client = _client(mock, cfg)
            try:
                await sync.run_tick(conn, client, cfg, delay=0.0)
            finally:
                await client.aclose()

        _run(fresh())
        assert len(mock.requests) == 0

        # Semua data lebih tua dari intervalnya -> seluruh jendela jatuh tempo.
        with conn:
            for date_str in sync.window_dates(today, 30):
                interval = sync.interval_for_offset(sync.offset_of(date_str, today), cfg)
                conn.execute(
                    "UPDATE date_sync SET last_ok_at = ? WHERE booking_date = ?",
                    (clock.utc_iso(BASE - timedelta(seconds=interval + 1)), date_str),
                )

        async def stale():
            client = _client(mock, cfg)
            try:
                await sync.run_tick(conn, client, cfg, delay=0.0)
            finally:
                await client.aclose()

        _run(stale())
        assert len(mock.requests) == 31


def test_t11_backoff_and_reset(conn, make_config):
    assert sync.backoff_seconds(1) == 10
    assert sync.backoff_seconds(2) == 20
    assert sync.backoff_seconds(3) == 40
    assert sync.backoff_seconds(6) == 300  # dibatasi 300

    t = BASE
    with MockAyo() as mock:
        mock.set_raw("boom", 500)
        cfg = make_config(mock, window_days=1)  # hanya 2 tanggal
        clock.set_now_fn(lambda: t)

        async def scenario():
            client = _client(mock, cfg)
            try:
                await sync.run_tick(conn, client, cfg, delay=0.0)
                assert len(mock.requests) == 2
                assert conn.execute(
                    "SELECT MAX(fail_count) AS m FROM date_sync"
                ).fetchone()["m"] == 1

                # Masih dalam backoff 10 detik -> tidak ada request.
                await sync.run_tick(conn, client, cfg, delay=0.0)
                assert len(mock.requests) == 2

                # Lewati backoff -> coba lagi, fail_count naik.
                clock.set_now_fn(lambda: t + timedelta(seconds=11))
                await sync.run_tick(conn, client, cfg, delay=0.0)
                assert len(mock.requests) == 4
                assert conn.execute(
                    "SELECT MAX(fail_count) AS m FROM date_sync"
                ).fetchone()["m"] == 2

                # Sukses -> fail_count kembali 0.
                mock.set_json(EMPTY_OK)
                clock.set_now_fn(lambda: t + timedelta(seconds=40))
                await sync.run_tick(conn, client, cfg, delay=0.0)
                assert len(mock.requests) == 6
                assert conn.execute(
                    "SELECT SUM(fail_count) AS s FROM date_sync"
                ).fetchone()["s"] == 0
            finally:
                await client.aclose()

        _run(scenario())
