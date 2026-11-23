"""AYO tiruan untuk pengujian (SPECS §11).

Server HTTP lokal berbasis stdlib yang responsnya dapat diubah per test dan
menghitung jumlah request yang diterima. Tidak ada koneksi ke AYO asli.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 (nama wajib dari BaseHTTPRequestHandler)
        mock: "MockAyo" = self.server.mock  # type: ignore[attr-defined]
        mock.requests.append(self.path)
        body = mock.body
        self.send_response(mock.status)
        self.send_header("Content-Type", mock.content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        """Matikan log bawaan agar tidak berisik dan tidak bocor URL."""


class MockAyo:
    """Server AYO tiruan dengan respons yang dapat diganti kapan saja."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.status: int = 200
        self.content_type: str = "application/json"
        self.body: bytes = b"{}"
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def set_json(self, payload: Any, status: int = 200) -> None:
        self.status = status
        self.content_type = "application/json"
        self.body = json.dumps(payload).encode("utf-8")

    def set_raw(
        self, payload: str | bytes, status: int = 200, content_type: str = "text/plain"
    ) -> None:
        self.status = status
        self.content_type = content_type
        self.body = payload.encode("utf-8") if isinstance(payload, str) else payload

    @property
    def port(self) -> int:
        assert self._httpd is not None, "mock belum dijalankan"
        return self._httpd.server_address[1]

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/api/v2/venue_booking/path"

    def start(self) -> "MockAyo":
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.mock = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "MockAyo":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop()
