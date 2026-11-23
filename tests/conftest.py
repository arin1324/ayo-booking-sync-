"""Fixture bersama untuk seluruh test M1."""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from app import db

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
DATE = "2026-11-23"


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
