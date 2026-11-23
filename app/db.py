"""Koneksi SQLite, skema, dan transaksi (SPECS §4).

Aturan: koneksi memakai mode WAL, dan skema hanya ditambah (kompatibel mundur).
Data booking tidak pernah dihapus; hanya ``removed_at`` yang diisi.
"""

from __future__ import annotations

import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS fields (
  field_id         INTEGER PRIMARY KEY,
  name             TEXT    NOT NULL,
  sport            TEXT,
  session_duration INTEGER NOT NULL DEFAULT 60,
  open_time        TEXT    NOT NULL,
  close_time       TEXT    NOT NULL,
  updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS bookings (
  source         TEXT    NOT NULL,
  booking_id     INTEGER NOT NULL,
  booking_date   TEXT    NOT NULL,
  field_id       INTEGER NOT NULL,
  start_time     TEXT    NOT NULL,
  end_time       TEXT    NOT NULL,
  customer       TEXT    NOT NULL DEFAULT '',
  customer_id    INTEGER NOT NULL DEFAULT 0,
  status         TEXT    NOT NULL DEFAULT '',
  price          INTEGER NOT NULL DEFAULT 0,
  payment_method TEXT    NOT NULL DEFAULT '',
  payment_rule   TEXT    NOT NULL DEFAULT '',
  addons         TEXT    NOT NULL DEFAULT '',
  created_at     TEXT,
  raw            TEXT    NOT NULL,
  first_seen_at  TEXT    NOT NULL,
  updated_at     TEXT    NOT NULL,
  removed_at     TEXT,
  PRIMARY KEY (source, booking_id)
);
CREATE INDEX IF NOT EXISTS idx_bookings_date_field ON bookings (booking_date, field_id);
CREATE INDEX IF NOT EXISTS idx_bookings_customer ON bookings (customer_id);

CREATE TABLE IF NOT EXISTS booking_events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  ts           TEXT    NOT NULL,
  event        TEXT    NOT NULL CHECK (event IN ('created','updated','removed','restored')),
  source       TEXT    NOT NULL,
  booking_id   INTEGER NOT NULL,
  booking_date TEXT    NOT NULL,
  field_id     INTEGER NOT NULL,
  start_time   TEXT    NOT NULL,
  end_time     TEXT    NOT NULL,
  customer     TEXT    NOT NULL DEFAULT '',
  status       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON booking_events (ts);

CREATE TABLE IF NOT EXISTS date_sync (
  booking_date    TEXT PRIMARY KEY,
  last_ok_at      TEXT,
  last_attempt_at TEXT,
  fail_count      INTEGER NOT NULL DEFAULT 0,
  last_error      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS sync_state (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS customers (
  customer_id             INTEGER PRIMARY KEY,
  name                    TEXT    NOT NULL DEFAULT '',
  first_name              TEXT    NOT NULL DEFAULT '',
  username                TEXT    NOT NULL DEFAULT '',
  email                   TEXT    NOT NULL DEFAULT '',
  phone                   TEXT    NOT NULL DEFAULT '',
  latest_booking_date     TEXT,
  total_booking_success   INTEGER NOT NULL DEFAULT 0,
  total_booking_cancelled INTEGER NOT NULL DEFAULT 0,
  is_deleted_account      INTEGER NOT NULL DEFAULT 0,
  deleted_date            TEXT,
  raw                     TEXT    NOT NULL,
  first_seen_at           TEXT    NOT NULL,
  updated_at              TEXT    NOT NULL,
  missed_syncs            INTEGER NOT NULL DEFAULT 0,
  removed_at              TEXT
);
CREATE INDEX IF NOT EXISTS idx_customers_name ON customers (name COLLATE NOCASE);
"""


def connect(path: str) -> sqlite3.Connection:
    """Buka koneksi SQLite dengan pragma yang ditetapkan SPECS §4."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Buat seluruh tabel dan indeks bila belum ada (idempoten)."""
    with conn:
        conn.executescript(SCHEMA)


def get_state(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM sync_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row is not None else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
