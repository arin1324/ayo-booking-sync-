"""Klien HTTP ke endpoint kalender AYO (SPECS §3.1–§3.4).

Aturan rahasia: URL permintaan memuat token di query string, sehingga URL,
query, dan token **tidak boleh** pernah dicetak atau di-log. Exception yang
dilempar hanya memuat nama kelas error atau kode singkat, bukan pesan httpx
(pesan bisa memuat URL).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Callable, Sequence

import httpx

# URL permintaan memuat token di query string. httpx/httpcore mencetak URL pada
# level INFO, jadi logger mereka wajib WARNING atau lebih tinggi (SPECS §4.1, §8).
for _noisy in ("httpx", "httpcore", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

RESERVATIONS_CALENDAR_PATH = "reservations-calendar"


class AyoError(Exception):
    """Kesalahan umum saat mengambil data dari AYO."""


class AuthError(AyoError):
    """Token ditolak AYO (HTTP 401/403 atau pesan token)."""


class FetchError(AyoError):
    """Gagal mengambil atau respons tidak valid."""


class TokenFile:
    """Membaca token dari file, dengan cache berdasarkan mtime.

    Token dibaca ulang setiap kali file berubah, sehingga pemilik dapat
    mengganti token tanpa restart layanan (FR-14).
    """

    def __init__(self, path: str) -> None:
        self._path = path
        self._mtime: int | None = None
        self._value: str = ""

    def read(self) -> str:
        try:
            mtime = os.stat(self._path).st_mtime_ns
        except OSError:
            raise FetchError("token_file_unreadable") from None
        if self._mtime != mtime:
            try:
                with open(self._path, "r", encoding="utf-8") as handle:
                    self._value = handle.read().strip()
            except OSError:
                raise FetchError("token_file_unreadable") from None
            self._mtime = mtime
        return self._value

    def mtime_ns(self) -> int | None:
        """mtime file token dalam nanodetik, atau ``None`` bila tidak ada."""
        try:
            return os.stat(self._path).st_mtime_ns
        except OSError:
            return None


class AyoClient:
    """Klien tipis untuk endpoint kalender. Satu request = satu tanggal."""

    def __init__(
        self,
        *,
        base_url: str,
        venue_id: int,
        field_ids: Sequence[int],
        timeout: float,
        token_provider: Callable[[], str],
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url
        self._venue_id = venue_id
        self._field_ids = tuple(field_ids)
        self._token_provider = token_provider
        self._client = client or httpx.AsyncClient(
            timeout=timeout, follow_redirects=False
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    def _params(self, date: str, token: str) -> list[tuple[str, str]]:
        params: list[tuple[str, str]] = [
            ("url_path", f"venue/{self._venue_id}/{RESERVATIONS_CALENDAR_PATH}"),
            ("date", date),
            ("mobile_token", token),
        ]
        params.extend(("field_ids[]", str(fid)) for fid in self._field_ids)
        return params

    async def fetch_calendar(self, date: str) -> list[dict[str, Any]]:
        """Ambil data kalender satu tanggal, tervalidasi (SPECS §3.3)."""
        token = self._token_provider()
        try:
            response = await self._client.get(
                self._base_url,
                params=self._params(date, token),
                headers={"Accept": "application/json"},
            )
        except httpx.HTTPError as exc:  # nama kelas saja, tanpa pesan/URL
            raise FetchError(type(exc).__name__) from None
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> list[dict[str, Any]]:
        status = response.status_code
        if status in (401, 403):
            raise AuthError(f"http_{status}")

        try:
            body = response.json()
        except ValueError:
            raise FetchError("invalid_json") from None

        if status != 200:
            raise FetchError(f"http_{status}")
        if not isinstance(body, dict):
            raise FetchError("invalid_body")

        body_status = body.get("status_code")
        if body_status in (401, 403):
            raise AuthError(f"body_{body_status}")

        is_error = bool(body.get("error"))
        message = str(body.get("message") or "").lower()
        if is_error and ("unauth" in message or "token" in message):
            raise AuthError("token_rejected")
        if is_error:
            raise FetchError("error_true")
        if body_status is not None and body_status != 200:
            raise FetchError(f"body_status_{body_status}")

        data = body.get("data")
        if not isinstance(data, list):
            raise FetchError("data_not_array")
        return data
