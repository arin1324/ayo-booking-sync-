"""Titik masuk ASGI: ``uvicorn app.main:app``.

Konfigurasi dimuat dari environment (systemd ``EnvironmentFile``). Aplikasi
gagal start bila konfigurasi tidak valid (mis. ``AYO_API_KEY`` kosong).
"""

from __future__ import annotations

import logging

from .api import create_app
from .config import load_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

app = create_app(load_config())
