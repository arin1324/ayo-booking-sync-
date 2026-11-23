"""T1 dan T2: parser memetakan fixture dengan benar; baseline tanpa event."""

from __future__ import annotations

import json

from app import sync

DATE = "2026-11-23"
TS0 = "2026-11-23T00:00:00Z"


def test_t1_parser_maps_fixture_to_bookings(conn, lampiran_a):
    sync.apply_snapshot(conn, DATE, lampiran_a["data"], emit_events=False, ts=TS0)

    rows = conn.execute(
        "SELECT * FROM bookings ORDER BY source, booking_id"
    ).fetchall()
    assert len(rows) == 3
    by_key = {(r["source"], r["booking_id"]): r for r in rows}

    # Reservasi manual: status null di AYO -> final_status, customer_id 0.
    r1 = by_key[("reservation", 1001)]
    assert r1["booking_date"] == DATE  # tanggal berasal dari parameter request
    assert r1["field_id"] == 10479
    assert r1["start_time"] == "07:00" and r1["end_time"] == "08:00"
    assert r1["customer"] == "Pelanggan Satu"
    assert r1["customer_id"] == 0
    assert r1["status"] == "MANUAL"
    assert r1["price"] == 1
    assert r1["payment_method"] == "Transfer"
    assert r1["payment_rule"] == "MANUAL"
    assert r1["addons"] == ""
    assert r1["created_at"] == "2026-11-21T06:59:35.000000Z"
    assert r1["removed_at"] is None

    # Order dari aplikasi AYO.
    r2 = by_key[("order", 2001)]
    assert r2["customer_id"] == 555
    assert r2["status"] == "FINISHED"
    assert r2["price"] == 150000
    assert r2["payment_rule"] == "BAYAR LUNAS"

    # add_on_group_name berupa array digabung dengan ", ".
    r3 = by_key[("reservation", 1002)]
    assert r3["addons"] == "Sewa Raket"
    assert r3["price"] == 0  # payment_method null -> ''
    assert r3["payment_method"] == ""

    # raw harus JSON valid dan memuat objek asli.
    for row in rows:
        json.loads(row["raw"])
    assert json.loads(r2["raw"])["id"] == 2001

    # fields terisi dari level lapangan; lapangan tanpa booking tetap tercatat.
    fields = conn.execute(
        "SELECT field_id, name, session_duration, open_time, close_time "
        "FROM fields ORDER BY field_id"
    ).fetchall()
    assert [f["field_id"] for f in fields] == [10479, 10480]
    assert fields[0]["name"] == "Lapangan A"
    assert fields[0]["session_duration"] == 60
    assert fields[0]["open_time"] == "07:00"
    assert fields[0]["close_time"] == "22:00"


def test_t2_baseline_does_not_write_events(conn, lampiran_a):
    sync.apply_snapshot(conn, DATE, lampiran_a["data"], emit_events=False, ts=TS0)
    events = conn.execute("SELECT COUNT(*) AS n FROM booking_events").fetchone()
    assert events["n"] == 0
