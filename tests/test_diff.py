"""T3, T4, T5: algoritma diff, booking pindah tanggal, dan lapangan absen."""

from __future__ import annotations

import copy

from app import sync

DATE = "2026-11-23"
T0 = "2026-11-23T00:00:00Z"
T1 = "2026-11-23T01:00:00Z"
T2 = "2026-11-23T02:00:00Z"
T3 = "2026-11-23T03:00:00Z"


def _field(field_id: int, data: list[dict], name: str = "Lapangan A") -> dict:
    return {
        "field_name": name,
        "session_duration": 60,
        "sport_name": "Padel",
        "sport_id": 12,
        "field_id": field_id,
        "data": data,
        "start_time": "07:00:00",
        "end_time": "22:00:00",
    }


def _booking(
    booking_id: int,
    *,
    source: str = "order",
    field_id: int = 10479,
    start: str = "09:00:00",
    end: str = "10:00:00",
    customer: str = "Pelanggan Uji",
    user_id: int = 0,
    status: str = "FINISHED",
    price: int = 100000,
) -> dict:
    return {
        "id": booking_id,
        "field_id": field_id,
        "user_id": user_id,
        "user_name": customer,
        "start_time": start,
        "end_time": end,
        "created_at": "2026-11-22T03:00:00.000000Z",
        "status": status,
        "total_price": price,
        "payment_method": "",
        "payment_type": "",
        "final_status": status,
        "payment_rule": "BAYAR LUNAS",
        "source_table": source,
        "add_on_group_name": None,
    }


def _events(conn) -> set[tuple[str, int]]:
    rows = conn.execute("SELECT event, booking_id FROM booking_events").fetchall()
    return {(r["event"], r["booking_id"]) for r in rows}


def test_t3_diff_created_updated_removed_restored(conn, lampiran_a):
    base = copy.deepcopy(lampiran_a["data"])
    sync.apply_snapshot(conn, DATE, base, emit_events=False, ts=T0)
    assert conn.execute("SELECT COUNT(*) AS n FROM booking_events").fetchone()["n"] == 0

    # Modifikasi: 2001 berubah status, 1002 hilang, 2002 muncul di lapangan B.
    modified = copy.deepcopy(base)
    field_a = next(f for f in modified if f["field_id"] == 10479)
    field_b = next(f for f in modified if f["field_id"] == 10480)
    field_a["data"] = [b for b in field_a["data"] if b["id"] != 1002]
    for b in field_a["data"]:
        if b["id"] == 2001:
            b["status"] = "CANCELLED"
            b["final_status"] = "CANCELLED"
    field_b["data"].append(
        _booking(2002, field_id=10480, start="10:00:00", end="11:00:00", user_id=777)
    )

    counts = sync.apply_snapshot(conn, DATE, modified, emit_events=True, ts=T1)
    kinds = _events(conn)
    assert ("created", 2002) in kinds
    assert ("updated", 2001) in kinds
    assert ("removed", 1002) in kinds
    assert not any(bid == 1001 for _, bid in kinds)  # 1001 tidak berubah
    assert counts["created"] == 1 and counts["updated"] == 1 and counts["removed"] == 1

    removed = conn.execute(
        "SELECT removed_at FROM bookings WHERE source='reservation' AND booking_id=1002"
    ).fetchone()
    assert removed["removed_at"] is not None

    # Kembali ke kondisi awal: 1002 muncul lagi (restored), 2002 hilang, 2001 berubah.
    sync.apply_snapshot(conn, DATE, base, emit_events=True, ts=T2)
    restored = _events(conn)
    assert ("restored", 1002) in restored
    assert ("removed", 2002) in restored
    assert ("updated", 2001) in restored
    assert conn.execute(
        "SELECT removed_at FROM bookings WHERE source='reservation' AND booking_id=1002"
    ).fetchone()["removed_at"] is None
    assert conn.execute(
        "SELECT removed_at FROM bookings WHERE source='order' AND booking_id=2002"
    ).fetchone()["removed_at"] is not None

    # Snapshot identik: tidak ada event baru dan tidak ada tulisan.
    before_updated = conn.execute(
        "SELECT updated_at FROM bookings WHERE booking_id=1001"
    ).fetchone()["updated_at"]
    before_events = conn.execute("SELECT COUNT(*) AS n FROM booking_events").fetchone()["n"]
    counts = sync.apply_snapshot(conn, DATE, base, emit_events=True, ts=T3)
    assert counts["created"] == counts["updated"] == counts["removed"] == counts["restored"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM booking_events").fetchone()["n"] == before_events
    assert conn.execute(
        "SELECT updated_at FROM bookings WHERE booking_id=1001"
    ).fetchone()["updated_at"] == before_updated


def test_t4_moved_date_no_duplicate(conn):
    d1, d2 = "2026-11-24", "2026-11-25"
    sync.apply_snapshot(
        conn, d1, [_field(10479, [_booking(3001)])], emit_events=False, ts=T0
    )
    sync.apply_snapshot(
        conn, d2, [_field(10479, [_booking(3001)])], emit_events=True, ts=T1
    )

    rows = conn.execute(
        "SELECT booking_date FROM bookings WHERE source='order' AND booking_id=3001"
    ).fetchall()
    assert len(rows) == 1  # tidak ada duplikat
    assert rows[0]["booking_date"] == d2
    assert ("updated", 3001) in _events(conn) or ("restored", 3001) in _events(conn)


def test_t5_absent_field_is_not_removed(conn):
    date = "2026-11-24"
    snap = [
        _field(10479, [_booking(5001)]),
        _field(10480, [_booking(4001, field_id=10480)], name="Lapangan B"),
    ]
    sync.apply_snapshot(conn, date, snap, emit_events=False, ts=T0)

    # Lapangan B tidak ada di respons berikutnya: booking-nya TIDAK boleh di-removed.
    snap2 = [_field(10479, [])]
    sync.apply_snapshot(conn, date, snap2, emit_events=True, ts=T1)

    assert conn.execute(
        "SELECT removed_at FROM bookings WHERE source='order' AND booking_id=4001"
    ).fetchone()["removed_at"] is None
    assert ("removed", 4001) not in _events(conn)
    # Lapangan A yang hadir dan kosong: booking-nya ditandai hilang.
    assert conn.execute(
        "SELECT removed_at FROM bookings WHERE source='order' AND booking_id=5001"
    ).fetchone()["removed_at"] is not None
    assert ("removed", 5001) in _events(conn)
