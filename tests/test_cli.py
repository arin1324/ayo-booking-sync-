"""Uji jalur CLI `python -m app.sync --once` terhadap mock (bukan AYO asli)."""

from __future__ import annotations

import asyncio

from app import clock, db, sync
from tests.mock_ayo import MockAyo

EMPTY_OK = {
    "error": False,
    "message": "Berhasil",
    "data": [],
    "status_code": 200,
    "signature": None,
    "execution_time": 0.0,
}


def test_run_once_success_sets_baseline(tmp_path, make_config, lampiran_a, capsys):
    db_path = tmp_path / "once.db"
    token_path = tmp_path / "mobile_token"
    token_path.write_text("TESTTOKEN123", encoding="utf-8")

    with MockAyo() as mock:
        mock.set_json(lampiran_a)
        cfg = make_config(
            mock,
            window_days=2,  # 3 tanggal
            db_path=str(db_path),
            token_file=str(token_path),
        )
        code = asyncio.run(sync.run_once(cfg))

    assert code == 0
    output = capsys.readouterr().out
    assert "TESTTOKEN123" not in output
    assert "baseline_done=1" in output

    conn = db.connect(str(db_path))
    try:
        assert conn.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"] == 3
        assert db.get_state(conn, "baseline_done") == "1"
        assert db.get_state(conn, "auth_ok") == "1"
        assert conn.execute("SELECT COUNT(*) AS n FROM booking_events").fetchone()["n"] == 0
    finally:
        conn.close()


def test_run_once_auth_error_returns_2(tmp_path, make_config, capsys):
    db_path = tmp_path / "once.db"
    token_path = tmp_path / "mobile_token"
    token_path.write_text("TESTTOKEN123", encoding="utf-8")

    with MockAyo() as mock:
        mock.set_raw("{}", 401)
        cfg = make_config(
            mock,
            window_days=1,
            db_path=str(db_path),
            token_file=str(token_path),
        )
        code = asyncio.run(sync.run_once(cfg))

    assert code == 2
    assert "TESTTOKEN123" not in capsys.readouterr().out
    assert clock.venue_today() is not None
