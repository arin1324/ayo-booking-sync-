"""T6: respons tidak valid tidak pernah mengubah data."""

from __future__ import annotations

import asyncio

from app import ayo_client, sync
from tests.mock_ayo import MockAyo

DATE = "2026-11-23"
TOKEN = "TESTTOKEN123"  # token uji, bukan rahasia


def _client(mock: MockAyo, *, field_ids=(10479,), token: str = TOKEN) -> ayo_client.AyoClient:
    return ayo_client.AyoClient(
        base_url=mock.base_url,
        venue_id=3749,
        field_ids=list(field_ids),
        timeout=5.0,
        token_provider=lambda: token,
    )


def _fetch_error(mock: MockAyo):
    client = _client(mock)
    async def run():
        try:
            await client.fetch_calendar(DATE)
        except ayo_client.AyoError as exc:
            return type(exc)
        finally:
            await client.aclose()
        return None
    return asyncio.run(run())


def _snapshot(conn) -> list[tuple]:
    rows = conn.execute(
        "SELECT source, booking_id, booking_date, start_time, end_time, status, "
        "price, removed_at, updated_at FROM bookings ORDER BY source, booking_id"
    ).fetchall()
    return [tuple(r) for r in rows]


def test_valid_fetch_returns_data(conn, lampiran_a):
    with MockAyo() as mock:
        mock.set_json(lampiran_a)
        client = _client(mock, field_ids=(10479, 10480))

        async def run():
            try:
                return await client.fetch_calendar(DATE)
            finally:
                await client.aclose()

        data = asyncio.run(run())
    assert isinstance(data, list) and len(data) == 2
    assert len(mock.requests) == 1
    assert f"date={DATE}" in mock.requests[0]
    assert "field_ids" in mock.requests[0]


def test_empty_token_never_sends_request():
    """Token kosong (file belum diisi) tidak boleh memicu request ke AYO."""
    with MockAyo() as mock:
        client = _client(mock, token="")

        async def run():
            try:
                await client.fetch_calendar(DATE)
            except ayo_client.FetchError:
                return "FetchError"
            finally:
                await client.aclose()
            return None

        assert asyncio.run(run()) == "FetchError"
        assert mock.requests == []


def test_invalid_responses_raise_expected_errors():
    with MockAyo() as mock:
        mock.set_raw("bukan json", 200, "application/json")
        assert _fetch_error(mock) is ayo_client.FetchError

        mock.set_json({"error": True, "message": "Gagal", "data": []})
        assert _fetch_error(mock) is ayo_client.FetchError

        mock.set_json({"error": False, "data": {"x": 1}})
        assert _fetch_error(mock) is ayo_client.FetchError

        mock.set_raw("boom", 500, "text/plain")
        assert _fetch_error(mock) is ayo_client.FetchError

        mock.set_json({"error": False, "message": "ok", "status_code": 500, "data": []})
        assert _fetch_error(mock) is ayo_client.FetchError

        mock.set_json({"error": True, "message": "Unauthenticated token", "data": []})
        assert _fetch_error(mock) is ayo_client.AuthError

        mock.set_raw("{}", 401, "application/json")
        assert _fetch_error(mock) is ayo_client.AuthError

        mock.set_json({"error": False, "status_code": 403, "data": []})
        assert _fetch_error(mock) is ayo_client.AuthError


def test_invalid_response_leaves_data_untouched(conn, lampiran_a):
    sync.apply_snapshot(conn, DATE, lampiran_a["data"], emit_events=False, ts="2026-11-23T00:00:00Z")
    before = _snapshot(conn)

    with MockAyo() as mock:
        mock.set_raw("bukan json", 200, "application/json")
        client = _client(mock)

        async def run():
            try:
                await sync.fetch_and_apply(
                    conn, client, DATE, emit_events=True, ts="2026-11-23T01:00:00Z"
                )
                return "no-error"
            except ayo_client.AyoError as exc:
                return type(exc).__name__
            finally:
                await client.aclose()

        result = asyncio.run(run())

    assert result == "FetchError"
    assert _snapshot(conn) == before
    assert conn.execute("SELECT COUNT(*) AS n FROM booking_events").fetchone()["n"] == 0


def test_fetch_and_apply_valid_writes_bookings(conn, lampiran_a):
    with MockAyo() as mock:
        mock.set_json(lampiran_a)
        client = _client(mock, field_ids=(10479, 10480))

        async def run():
            try:
                return await sync.fetch_and_apply(
                    conn, client, DATE, emit_events=False, ts="2026-11-23T00:00:00Z"
                )
            finally:
                await client.aclose()

        counts = asyncio.run(run())

    assert counts["created"] == 3
    assert conn.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"] == 3
