"""T12, T13, T21, T22: API baca (SPECS §7). Memakai ``TestClient`` tanpa jaringan.

Scheduler dimatikan (``start_scheduler=False``) agar tidak ada request ke AYO.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app import api, clock, db
from app.config import ConfigError, load_config

API_KEY = "a" * 40
TS = "2026-11-23T02:00:00Z"


def _insert_booking(
    conn,
    *,
    source: str,
    booking_id: int,
    date: str,
    field_id: int,
    start: str,
    end: str,
    customer: str = "",
    customer_id: int = 0,
    status: str = "FINISHED",
    price: int = 100000,
    removed_at: str | None = None,
    updated_at: str = TS,
) -> None:
    conn.execute(
        "INSERT INTO bookings (source, booking_id, booking_date, field_id, "
        "start_time, end_time, customer, customer_id, status, price, "
        "payment_method, payment_rule, addons, created_at, raw, first_seen_at, "
        "updated_at, removed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            source,
            booking_id,
            date,
            field_id,
            start,
            end,
            customer,
            customer_id,
            status,
            price,
            "",
            "MANUAL",
            "",
            TS,
            json.dumps({"id": booking_id}),
            TS,
            updated_at,
            removed_at,
        ),
    )


@pytest.fixture
def api_client(tmp_path, make_config):
    """Aplikasi dengan database yang sudah diisi beberapa booking dan event."""
    db_path = tmp_path / "api.db"
    conn = db.connect(str(db_path))
    db.init_schema(conn)
    with conn:
        conn.execute(
            "INSERT INTO fields (field_id, name, sport, session_duration, "
            "open_time, close_time, updated_at) VALUES "
            "(10479, 'Lapangan A', 'Padel', 60, '07:00', '22:00', ?), "
            "(10480, 'Lapangan B', 'Padel', 60, '07:00', '22:00', ?)",
            (TS, TS),
        )
        _insert_booking(
            conn,
            source="order",
            booking_id=1001,
            date="2026-11-23",
            field_id=10479,
            start="08:00",
            end="09:00",
            customer="Pelanggan Satu",
            customer_id=555,
        )
        _insert_booking(
            conn,
            source="reservation",
            booking_id=2002,
            date="2026-11-24",
            field_id=10480,
            start="07:00",
            end="08:00",
            customer="Tamu Manual",
            customer_id=0,
        )
        _insert_booking(
            conn,
            source="order",
            booking_id=1003,
            date="2026-11-25",
            field_id=10479,
            start="10:00",
            end="11:00",
            customer="Pelanggan Satu",
            customer_id=555,
            removed_at=TS,
            updated_at="2026-11-25T01:00:00Z",
        )
        _insert_booking(
            conn,
            source="order",
            booking_id=1004,
            date="2026-11-24",
            field_id=10479,
            start="09:00",
            end="10:00",
            customer="Pelanggan Baru",
            customer_id=9001,
            updated_at="2026-11-24T03:00:00Z",
        )
        for event, b_id, date, field_id, start, end in (
            ("created", 1001, "2026-11-23", 10479, "08:00", "09:00"),
            ("updated", 2002, "2026-11-24", 10480, "07:00", "08:00"),
            ("removed", 1003, "2026-11-25", 10479, "10:00", "11:00"),
        ):
            conn.execute(
                "INSERT INTO booking_events (ts, event, source, booking_id, "
                "booking_date, field_id, start_time, end_time, customer, status) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (TS, event, "order", b_id, date, field_id, start, end, "", "MANUAL"),
            )
    conn.close()

    cfg = make_config(db_path=str(db_path), api_key=API_KEY)
    app = api.create_app(cfg, start_scheduler=False)
    with TestClient(app) as client:
        yield client, cfg


# --------------------------------------------------------------------------
# T12 — kunci API dan /health
# --------------------------------------------------------------------------


def test_t12_missing_api_key_is_rejected(api_client):
    client, _ = api_client
    assert client.get("/fields").status_code == 401
    assert client.get("/schedule").status_code == 401
    assert client.get("/changes").status_code == 401
    body = client.get("/fields").json()
    assert body["error"]["code"] == "unauthorized"


def test_t12_wrong_api_key_is_rejected(api_client):
    client, _ = api_client
    assert client.get("/fields", headers={"X-API-Key": "salah"}).status_code == 401


def test_t12_health_open_without_key(api_client):
    client, _ = api_client
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] in ("ok", "stale", "auth_failed")
    assert body["customers"]["enabled"] is False
    assert "customer" not in json.dumps(body["customers"]).replace('"enabled"', "")


def test_t12_app_refuses_to_start_without_api_key():
    env = {"AYO_DB_PATH": "/tmp/x.db"}
    with pytest.raises(ConfigError):
        load_config(env)
    with pytest.raises(ConfigError):
        load_config({**env, "AYO_API_KEY": "pendek"})
    # Kunci cukup panjang diterima.
    cfg = load_config({**env, "AYO_API_KEY": "z" * 32})
    assert cfg.api_key == "z" * 32


# --------------------------------------------------------------------------
# T13 — /schedule, /fields, /changes
# --------------------------------------------------------------------------


def test_t13_fields(api_client):
    client, _ = api_client
    response = client.get("/fields", headers={"X-API-Key": API_KEY})
    assert response.status_code == 200
    fields = response.json()["fields"]
    assert [f["field_id"] for f in fields] == [10479, 10480]
    assert fields[0] == {
        "field_id": 10479,
        "name": "Lapangan A",
        "sport": "Padel",
        "session_duration": 60,
        "open_time": "07:00",
        "close_time": "22:00",
    }


def test_t13_schedule_default_hides_removed(api_client):
    client, _ = api_client
    response = client.get(
        "/schedule",
        params={"from": "2026-11-23", "to": "2026-11-25"},
        headers={"X-API-Key": API_KEY},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["from"] == "2026-11-23"
    assert body["to"] == "2026-11-25"
    # Booking yang removed (1003) tidak tampil.
    assert body["count"] == 3
    keys = {(b["source"], b["booking_id"]) for b in body["bookings"]}
    assert keys == {("order", 1001), ("reservation", 2002), ("order", 1004)}
    first = body["bookings"][0]
    assert first["field_name"] == "Lapangan A"
    assert first["customer_id"] == 555
    assert first["customer"] == "Pelanggan Satu"
    assert first["removed"] is False
    # Urutan: tanggal, jam mulai, lapangan.
    assert [(b["date"], b["start_time"]) for b in body["bookings"]] == [
        ("2026-11-23", "08:00"),
        ("2026-11-24", "07:00"),
        ("2026-11-24", "09:00"),
    ]


def test_t13_schedule_include_removed_and_field_filter(api_client):
    client, _ = api_client
    body = client.get(
        "/schedule",
        params={"from": "2026-11-25", "to": "2026-11-25", "include_removed": "true"},
        headers={"X-API-Key": API_KEY},
    ).json()
    assert body["count"] == 1
    assert body["bookings"][0]["removed"] is True

    filtered = client.get(
        "/schedule",
        params={"from": "2026-11-23", "to": "2026-11-25", "field_id": 10480},
        headers={"X-API-Key": API_KEY},
    ).json()
    assert {b["field_id"] for b in filtered["bookings"]} == {10480}
    assert filtered["count"] == 1


def test_t13_schedule_rejects_bad_range_and_dates(api_client):
    client, _ = api_client
    h = {"X-API-Key": API_KEY}
    assert client.get("/schedule", params={"to": "bukan-tanggal"}, headers=h).status_code == 422
    assert (
        client.get("/schedule", params={"from": "2026-11-24", "to": "2026-11-23"}, headers=h).status_code
        == 422
    )
    assert (
        client.get("/schedule", params={"from": "2026-11-23", "to": "2027-11-23"}, headers=h).status_code
        == 422
    )


def test_t13_changes_pagination(api_client):
    client, _ = api_client
    h = {"X-API-Key": API_KEY}
    body = client.get("/changes", params={"since": 0, "limit": 2}, headers=h).json()
    assert len(body["events"]) == 2
    assert body["events"][0]["id"] == 1
    assert body["latest_event_id"] == 3
    assert body["next_since"] == 2
    assert body["has_more"] is True

    body2 = client.get("/changes", params={"since": 2}, headers=h).json()
    assert [e["id"] for e in body2["events"]] == [3]
    assert body2["next_since"] == 3
    assert body2["has_more"] is False

    empty = client.get("/changes", params={"since": 3}, headers=h).json()
    assert empty["events"] == []
    assert empty["next_since"] == 3
    assert empty["latest_event_id"] == 3


def test_t13_schedule_hides_customer_when_disabled(tmp_path, make_config):
    db_path = tmp_path / "hide.db"
    conn = db.connect(str(db_path))
    db.init_schema(conn)
    with conn:
        conn.execute(
            "INSERT INTO fields (field_id, name, session_duration, open_time, "
            "close_time, updated_at) VALUES (10479, 'Lapangan A', 60, '07:00', '22:00', ?)",
            (TS,),
        )
        _insert_booking(
            conn, source="order", booking_id=1, date="2026-11-23", field_id=10479,
            start="08:00", end="09:00", customer="Rahasia", customer_id=42,
        )
    conn.close()
    cfg = make_config(db_path=str(db_path), api_key=API_KEY, expose_customer=False)
    app = api.create_app(cfg, start_scheduler=False)
    with TestClient(app) as client:
        body = client.get("/schedule", params={"from": "2026-11-23", "to": "2026-11-23"},
                          headers={"X-API-Key": API_KEY}).json()
        assert "customer" not in body["bookings"][0]
        assert "customer_id" not in body["bookings"][0]


# --------------------------------------------------------------------------
# T21 — /customer mode turunan
# --------------------------------------------------------------------------


def test_t21_customer_derived_grouping(api_client):
    client, _ = api_client
    h = {"X-API-Key": API_KEY}
    body = client.get("/customer", headers=h).json()
    # Nama: Pelanggan Satu (555), Tamu Manual (0), Pelanggan Baru (9001).
    assert body["total"] == 3
    by_id = {c["customer_id"]: c for c in body["customers"]}
    assert set(by_id) == {555, 9001, 0}

    satu = by_id[555]
    assert satu["type"] == "account"
    assert satu["slot_count"] == 2  # 1001 (aktif) + 1003 (removed)
    assert satu["active_slot_count"] == 1
    assert satu["first_booking_date"] == "2026-11-23"
    assert satu["last_booking_date"] == "2026-11-25"
    assert satu["sources"] == ["order"]
    # Akun tanpa baris di tabel customers -> tidak punya profil.
    assert "username" not in satu

    tamu = by_id[0]
    assert tamu["type"] == "guest"
    assert tamu["slot_count"] == 1
    assert tamu["sources"] == ["reservation"]


def test_t21_customer_guest_grouped_by_normalized_name(api_client, tmp_path):
    client, cfg = api_client
    # Tambah dua tamu dengan spasi berbeda -> harus tergabung (id 0).
    conn = db.connect(cfg.db_path)
    with conn:
        _insert_booking(
            conn, source="reservation", booking_id=3001, date="2026-11-26",
            field_id=10480, start="11:00", end="12:00", customer="  Tamu   Manual ",
            customer_id=0,
        )
    conn.close()
    h = {"X-API-Key": API_KEY}
    guests = [c for c in client.get("/customer", params={"type": "guest"}, headers=h).json()["customers"]]
    assert len(guests) == 1
    assert guests[0]["slot_count"] == 2  # 2002 + 3001


def test_t21_customer_filters_and_pagination(api_client):
    client, _ = api_client
    h = {"X-API-Key": API_KEY}
    body = client.get("/customer", params={"q": "baru"}, headers=h).json()
    assert body["total"] == 1
    assert body["customers"][0]["customer_id"] == 9001

    short = client.get("/customer", params={"q": "b"}, headers=h)
    assert short.status_code == 422

    ranged = client.get("/customer", params={"from": "2026-11-24", "to": "2026-11-24"},
                        headers=h).json()
    assert {c["customer_id"] for c in ranged["customers"]} == {9001, 0}

    page = client.get("/customer", params={"limit": 1, "offset": 0}, headers=h).json()
    assert page["count"] == 1
    assert page["total"] == 3
    assert page["limit"] == 1
    assert page["offset"] == 0

    names = client.get("/customer", params={"sort": "name"}, headers=h).json()["customers"]
    assert [c["name"] for c in names] == sorted(c["name"] for c in names)


def test_t21_customer_contact_hidden_by_default_and_403_when_disabled(api_client, tmp_path, make_config):
    client, _ = api_client
    h = {"X-API-Key": API_KEY}
    body = client.get("/customer", headers=h).json()
    for item in body["customers"]:
        assert "phone" not in item
        assert "email" not in item

    db_path = tmp_path / "off.db"
    cfg = make_config(db_path=str(db_path), api_key=API_KEY, expose_customer=False)
    app = api.create_app(cfg, start_scheduler=False)
    with TestClient(app) as off_client:
        response = off_client.get("/customer", headers=h)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "feature_disabled"


# --------------------------------------------------------------------------
# T22 — tanpa redirect untuk /schedule dan /schedule/
# --------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/schedule", "/schedule/"])
def test_t22_schedule_slash_variants(api_client, path):
    client, _ = api_client
    response = client.get(
        path,
        params={"from": "2026-11-23", "to": "2026-11-23"},
        headers={"X-API-Key": API_KEY},
        follow_redirects=False,
    )
    assert response.status_code == 200
    assert response.status_code not in (307, 308)


# --------------------------------------------------------------------------
# T32 — batas laju API per kunci
# --------------------------------------------------------------------------


def test_t32_rate_limiter_window_resets():
    limiter = api.RateLimiter(2, 60)
    assert limiter.check("kunci", now=0.0) == (True, 0)
    assert limiter.check("kunci", now=1.0)[0] is True
    allowed, retry_after = limiter.check("kunci", now=2.0)
    assert allowed is False
    assert retry_after >= 1
    # Kunci lain punya jendelanya sendiri.
    assert limiter.check("kunci-lain", now=2.0)[0] is True
    # Jendela baru setelah 60 detik.
    assert limiter.check("kunci", now=61.0)[0] is True


def test_t32_rate_limit_returns_429(tmp_path, make_config):
    db_path = tmp_path / "ratelimit.db"
    conn = db.connect(str(db_path))
    db.init_schema(conn)
    conn.close()
    cfg = make_config(
        db_path=str(db_path), api_key=API_KEY, api_rate_limit=2, api_rate_window=60
    )
    app = api.create_app(cfg, start_scheduler=False)
    h = {"X-API-Key": API_KEY}
    with TestClient(app) as client:
        assert client.get("/fields", headers=h).status_code == 200
        assert client.get("/fields", headers=h).status_code == 200
        third = client.get("/fields", headers=h)
        assert third.status_code == 429
        assert third.json()["error"]["code"] == "rate_limited"
        # /health tidak dibatasi (tanpa kunci).
        assert client.get("/health").status_code == 200


# --------------------------------------------------------------------------
# T33 — pekerjaan SQLite dipindahkan ke thread (event loop tetap bebas)
# --------------------------------------------------------------------------


def test_t33_run_blocking_uses_another_thread():
    caller = threading.get_ident()

    async def run():
        return await db.run_blocking(threading.get_ident)

    assert asyncio.run(run()) != caller


def test_t33_run_blocking_lock_serializes():
    lock = threading.Lock()
    active = 0
    peak = 0

    def work():
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.02)
        active -= 1

    async def run():
        await asyncio.gather(
            *[db.run_blocking(work, lock=lock) for _ in range(5)]
        )

    asyncio.run(run())
    assert peak == 1
