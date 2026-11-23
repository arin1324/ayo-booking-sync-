"""API baca (SPECS §7). FastAPI, tanpa dokumentasi otomatis, tanpa CORS.

Seluruh endpoint selain ``/health`` memerlukan header ``X-API-Key``.
Perbandingan kunci memakai ``hmac.compare_digest`` (waktu konstan).
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import __version__, clock, db
from .availability import AvailabilityService, parse_hhmm
from .ayo_client import AyoClient, TokenFile
from .config import Config
from .sync import FetchCoordinator, scheduler_loop

logger = logging.getLogger("ayo.api")

STALE_AFTER_SECONDS = 900  # 15 menit (SPECS §7.1)


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _parse_date(value: str, field: str) -> date_cls:
    try:
        return date_cls.fromisoformat(value)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_parameter",
                "message": f"Parameter '{field}' harus berformat YYYY-MM-DD.",
            },
        ) from None


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def create_app(
    cfg: Config,
    *,
    start_scheduler: bool = True,
    database: db.Database | None = None,
    client: AyoClient | None = None,
) -> FastAPI:
    """Bangun aplikasi FastAPI. ``start_scheduler=False`` dipakai pada test.

    ``client`` boleh diberikan (mis. klien mock) agar endpoint ketersediaan dapat
    mengambil ulang data tanpa jaringan.
    """
    provider = database or db.Database(cfg.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        clock.configure(cfg.tz)
        conn = provider.conn()
        db.init_schema(conn)
        stop_event = asyncio.Event()
        task: asyncio.Task[None] | None = None
        token_file = TokenFile(cfg.token_file)
        ayo = client
        if ayo is None and start_scheduler:
            ayo = AyoClient(
                base_url=cfg.base_url,
                venue_id=cfg.venue_id,
                field_ids=cfg.field_ids,
                timeout=cfg.http_timeout,
                token_provider=token_file.read,
            )
        coordinator = (
            FetchCoordinator(conn, ayo, cfg, token_file=token_file)
            if ayo is not None
            else None
        )
        app.state.avail = AvailabilityService(
            conn, cfg, coordinator=coordinator, token_file=token_file
        )
        if start_scheduler and ayo is not None:
            task = asyncio.create_task(
                scheduler_loop(
                    conn,
                    ayo,
                    cfg,
                    token_file=token_file,
                    stop_event=stop_event,
                    coordinator=coordinator,
                )
            )
        try:
            yield
        finally:
            stop_event.set()
            if task is not None:
                try:
                    await asyncio.wait_for(task, timeout=5)
                except asyncio.TimeoutError:
                    task.cancel()
            if ayo is not None:
                await ayo.aclose()

    app = FastAPI(
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.state.cfg = cfg
    app.state.db = provider

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            return _error(exc.status_code, str(detail["code"]), str(detail.get("message", "")))
        code = "internal_error" if exc.status_code >= 500 else "invalid_parameter"
        return _error(exc.status_code, code, "Permintaan tidak dapat diproses.")

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, "invalid_parameter", "Parameter tidak valid.")

    @app.exception_handler(sqlite3.Error)
    async def _db_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
        logger.error("db error=%s", type(exc).__name__)
        return _error(503, "service_unavailable", "Database tidak dapat diakses.")

    def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
        expected: str = request.app.state.cfg.api_key
        if not x_api_key or not hmac.compare_digest(x_api_key, expected):
            raise HTTPException(
                status_code=401,
                detail={"code": "unauthorized", "message": "Kunci API tidak valid atau tidak ada."},
            )

    # --- /health (tanpa kunci) ------------------------------------------
    @app.get("/health")
    def health(request: Request) -> Any:
        app_cfg: Config = request.app.state.cfg
        conn = request.app.state.db.conn()
        try:
            auth_ok = db.get_state(conn, "auth_ok", "1") != "0"
            last_success_raw = db.get_state(conn, "last_success_at")
            baseline_done = db.get_state(conn, "baseline_done") == "1"
        except sqlite3.Error:
            return _error(503, "service_unavailable", "Database tidak dapat diakses.")

        last_success = _parse_ts(last_success_raw)
        now = clock.now()
        if not auth_ok:
            status = "auth_failed"
        elif last_success is None or (
            now - last_success
        ).total_seconds() > STALE_AFTER_SECONDS:
            status = "stale"
        else:
            status = "ok"

        return {
            "status": status,
            "auth_ok": auth_ok,
            "last_success_at": last_success_raw,
            "baseline_done": baseline_done,
            "version": __version__,
            "customers": {
                "enabled": app_cfg.customer_enabled,
                "auth_ok": None,
                "last_full_sync_at": None,
            },
        }

    # --- /fields ---------------------------------------------------------
    @app.get("/fields", dependencies=[Depends(require_api_key)])
    def fields(request: Request) -> Any:
        conn = request.app.state.db.conn()
        rows = conn.execute(
            "SELECT field_id, name, sport, session_duration, open_time, close_time "
            "FROM fields ORDER BY field_id"
        ).fetchall()
        return {
            "fields": [
                {
                    "field_id": row["field_id"],
                    "name": row["name"],
                    "sport": row["sport"],
                    "session_duration": row["session_duration"],
                    "open_time": row["open_time"],
                    "close_time": row["close_time"],
                }
                for row in rows
            ]
        }

    # --- /schedule -------------------------------------------------------
    @app.get("/schedule", dependencies=[Depends(require_api_key)])
    @app.get("/schedule/", dependencies=[Depends(require_api_key)], include_in_schema=False)
    def schedule(
        request: Request,
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        field_id: list[int] | None = Query(default=None),
        include_removed: bool = Query(default=False),
    ) -> Any:
        cfg_now: Config = request.app.state.cfg
        conn = request.app.state.db.conn()
        today = clock.venue_today()
        d_from = _parse_date(from_, "from") if from_ else today
        d_to = _parse_date(to, "to") if to else d_from + timedelta(days=cfg_now.window_days - 1)
        if d_to < d_from:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_parameter", "message": "Parameter 'to' mendahului 'from'."},
            )
        if (d_to - d_from).days + 1 > 62:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_parameter", "message": "Rentang maksimal 62 hari."},
            )

        sql = (
            "SELECT source, booking_id, booking_date, field_id, start_time, end_time, "
            "customer, customer_id, status, price, payment_method, payment_rule, addons, "
            "created_at, updated_at, removed_at FROM bookings "
            "WHERE booking_date >= ? AND booking_date <= ?"
        )
        params: list[Any] = [d_from.isoformat(), d_to.isoformat()]
        if field_id:
            placeholders = ",".join("?" * len(field_id))
            sql += f" AND field_id IN ({placeholders})"
            params.extend(field_id)
        if not include_removed:
            sql += " AND removed_at IS NULL"
        sql += " ORDER BY booking_date, start_time, field_id"

        field_names = {
            row["field_id"]: row["name"]
            for row in conn.execute("SELECT field_id, name FROM fields")
        }
        bookings = []
        for row in conn.execute(sql, params):
            item: dict[str, Any] = {
                "source": row["source"],
                "booking_id": row["booking_id"],
                "date": row["booking_date"],
                "field_id": row["field_id"],
                "field_name": field_names.get(row["field_id"], ""),
                "start_time": row["start_time"],
                "end_time": row["end_time"],
            }
            if cfg_now.expose_customer:
                item["customer_id"] = row["customer_id"]
                item["customer"] = row["customer"]
            item.update(
                {
                    "status": row["status"],
                    "price": row["price"],
                    "payment_method": row["payment_method"],
                    "payment_rule": row["payment_rule"],
                    "addons": row["addons"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                    "removed": row["removed_at"] is not None,
                }
            )
            bookings.append(item)

        return {
            "from": d_from.isoformat(),
            "to": d_to.isoformat(),
            "count": len(bookings),
            "bookings": bookings,
        }

    # --- /changes --------------------------------------------------------
    @app.get("/changes", dependencies=[Depends(require_api_key)])
    def changes(
        request: Request,
        since: int = Query(default=0, ge=0),
        limit: int = Query(default=500, ge=1, le=1000),
    ) -> Any:
        conn = request.app.state.db.conn()
        rows = conn.execute(
            "SELECT id, ts, event, source, booking_id, booking_date, field_id, "
            "start_time, end_time, status FROM booking_events "
            "WHERE id > ? ORDER BY id LIMIT ?",
            (since, limit),
        ).fetchall()
        events = [
            {
                "id": row["id"],
                "ts": row["ts"],
                "event": row["event"],
                "source": row["source"],
                "booking_id": row["booking_id"],
                "date": row["booking_date"],
                "field_id": row["field_id"],
                "start_time": row["start_time"],
                "end_time": row["end_time"],
                "status": row["status"],
            }
            for row in rows
        ]
        next_since = events[-1]["id"] if events else since
        latest_row = conn.execute("SELECT MAX(id) AS m FROM booking_events").fetchone()
        latest_event_id = latest_row["m"] or 0
        has_more = (
            conn.execute("SELECT 1 FROM booking_events WHERE id > ? LIMIT 1", (next_since,)).fetchone()
            is not None
        )
        return {
            "events": events,
            "next_since": next_since,
            "latest_event_id": latest_event_id,
            "has_more": has_more,
        }

    # --- /schedule/availability (M4) -------------------------------------
    def _validate_time(value: str) -> int:
        minutes = parse_hhmm(value)
        if minutes is None:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_parameter",
                    "message": "Parameter 'time' harus berformat HH:MM.",
                },
            )
        return minutes

    def _validate_window(day: date_cls, request: Request) -> None:
        app_cfg: Config = request.app.state.cfg
        limit = clock.venue_today() + timedelta(days=app_cfg.window_days - 1)
        if day > limit:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "date_out_of_window",
                    "message": "Tanggal melebihi jendela data yang tersedia.",
                },
            )

    def _data_age_seconds(avail: AvailabilityService, date: str, now: datetime) -> int | None:
        age = avail.age_seconds(date, now)
        return None if age is None else int(age)

    @app.get("/schedule/availability", dependencies=[Depends(require_api_key)])
    async def availability(
        request: Request,
        date: str = Query(...),
        time: str = Query(...),
        duration: int | None = Query(default=None, ge=15, le=480),
        field_id: list[int] | None = Query(default=None),
    ) -> Any:
        avail: AvailabilityService = request.app.state.avail
        day = _parse_date(date, "date")
        _validate_time(time)
        _validate_window(day, request)

        source = await avail.ensure_fresh(day.isoformat())
        now = clock.now()
        age = _data_age_seconds(avail, day.isoformat(), now)
        fields = avail.compute(day.isoformat(), time, duration, field_id, age)
        return {
            "date": day.isoformat(),
            "time": time,
            "duration_minutes": duration or avail.default_duration(field_id),
            "checked_at": clock.utc_iso(now),
            "data_age_seconds": age,
            "data_source": source,
            "fields": fields,
        }

    @app.get("/schedule/availability/day", dependencies=[Depends(require_api_key)])
    async def availability_day(
        request: Request,
        date: str = Query(...),
        duration: int | None = Query(default=None, ge=15, le=480),
        field_id: list[int] | None = Query(default=None),
    ) -> Any:
        avail: AvailabilityService = request.app.state.avail
        day = _parse_date(date, "date")
        _validate_window(day, request)

        source = await avail.ensure_fresh(day.isoformat())
        now = clock.now()
        age = _data_age_seconds(avail, day.isoformat(), now)
        fields = avail.compute_day(day.isoformat(), duration, field_id, age)
        return {
            "date": day.isoformat(),
            "data_age_seconds": age,
            "data_source": source,
            "fields": fields,
        }

    # --- /customer (mode turunan; fitur AYO_CUSTOMER dimatikan) ----------
    @app.get("/customer", dependencies=[Depends(require_api_key)])
    def customer(
        request: Request,
        q: str | None = Query(default=None),
        type: str = Query(default="all"),
        include_deleted: bool = Query(default=False),
        from_: str | None = Query(default=None, alias="from"),
        to: str | None = Query(default=None),
        updated_since: str | None = Query(default=None),
        sort: str = Query(default="latest"),
        limit: int = Query(default=100, ge=1, le=500),
        offset: int = Query(default=0, ge=0),
    ) -> Any:
        cfg_now: Config = request.app.state.cfg
        if not cfg_now.expose_customer:
            raise HTTPException(
                status_code=403,
                detail={"code": "feature_disabled", "message": "Fitur daftar pelanggan dimatikan."},
            )
        if type not in ("all", "account", "guest"):
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_parameter", "message": "Parameter 'type' tidak dikenal."},
            )
        if sort not in ("latest", "name"):
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_parameter", "message": "Parameter 'sort' tidak dikenal."},
            )
        if q is not None and len(q.strip()) < 2:
            raise HTTPException(
                status_code=422,
                detail={"code": "invalid_parameter", "message": "Parameter 'q' minimal 2 karakter."},
            )

        d_from = _parse_date(from_, "from") if from_ else None
        d_to = _parse_date(to, "to") if to else None
        conn = request.app.state.db.conn()
        items = _collect_customers(
            conn,
            cfg_now,
            d_from=d_from.isoformat() if d_from else None,
            d_to=d_to.isoformat() if d_to else None,
            include_deleted=include_deleted,
        )

        if type != "all":
            items = [item for item in items if item["type"] == type]
        if q:
            needle = q.strip().lower()
            items = [item for item in items if _customer_matches(item, needle, cfg_now)]
        if updated_since:
            items = [item for item in items if item["_updated"] > updated_since]

        if sort == "name":
            items.sort(key=lambda item: (item["name"].lower(), item["customer_id"]))
        else:
            items.sort(key=lambda item: (item["_latest"], item["name"].lower()), reverse=True)

        total = len(items)
        page = items[offset : offset + limit]
        customers = [{k: v for k, v in item.items() if not k.startswith("_")} for item in page]
        return {
            "total": total,
            "count": len(customers),
            "limit": limit,
            "offset": offset,
            "customers": customers,
        }

    return app


def _normalize_name(name: str) -> str:
    return " ".join(name.split()).lower()


def _customer_matches(item: dict[str, Any], needle: str, cfg: Config) -> bool:
    if needle in item["name"].lower():
        return True
    username = item.get("username") or ""
    if needle in username.lower():
        return True
    if cfg.expose_customer_contact:
        for field in ("phone", "email"):
            if needle in (item.get(field) or "").lower():
                return True
    return False


def _collect_customers(
    conn: sqlite3.Connection,
    cfg: Config,
    *,
    d_from: str | None,
    d_to: str | None,
    include_deleted: bool,
) -> list[dict[str, Any]]:
    """Gabungan (a) akun dari tabel customers, (b) akun dari booking, (c) tamu."""
    sql = (
        "SELECT customer_id, customer, booking_date, source, removed_at, "
        "updated_at, created_at FROM bookings"
    )
    params: list[Any] = []
    if d_from and d_to:
        sql += " WHERE booking_date >= ? AND booking_date <= ?"
        params = [d_from, d_to]

    accounts: dict[int, dict[str, Any]] = {}
    guests: dict[str, dict[str, Any]] = {}

    for row in conn.execute(sql, params):
        customer_id = row["customer_id"]
        if customer_id > 0:
            key = customer_id
            bucket: dict[Any, dict[str, Any]] = accounts
        else:
            key = _normalize_name(row["customer"])
            bucket = guests
        entry = bucket.get(key)
        if entry is None:
            entry = {
                "customer_id": customer_id,
                "name": row["customer"],
                "slot_count": 0,
                "active_slot_count": 0,
                "first": None,
                "last": None,
                "sources": set(),
                "latest_ts": "",
                "updated": "",
            }
            bucket[key] = entry
        entry["slot_count"] += 1
        if row["removed_at"] is None:
            entry["active_slot_count"] += 1
        day = row["booking_date"]
        if entry["first"] is None or day < entry["first"]:
            entry["first"] = day
        if entry["last"] is None or day > entry["last"]:
            entry["last"] = day
        entry["sources"].add(row["source"])
        ts = row["updated_at"] or row["created_at"] or ""
        if ts >= entry["latest_ts"]:
            entry["latest_ts"] = ts
            if row["customer"]:
                entry["name"] = row["customer"]
        if (row["updated_at"] or "") > entry["updated"]:
            entry["updated"] = row["updated_at"] or ""

    profiles: dict[int, sqlite3.Row] = {}
    for row in conn.execute("SELECT * FROM customers"):
        if row["removed_at"] is None:
            profiles[row["customer_id"]] = row

    items: list[dict[str, Any]] = []
    for customer_id, entry in accounts.items():
        profile = profiles.get(customer_id)
        if profile is not None and profile["is_deleted_account"] and not include_deleted:
            continue
        item: dict[str, Any] = {
            "customer_id": customer_id,
            "type": "account",
            "name": (profile["name"] if profile is not None and profile["name"] else entry["name"]),
            "slot_count": entry["slot_count"],
            "active_slot_count": entry["active_slot_count"],
            "first_booking_date": entry["first"],
            "last_booking_date": entry["last"],
            "sources": sorted(entry["sources"]),
        }
        if profile is not None:
            item.update(
                {
                    "first_name": profile["first_name"],
                    "username": profile["username"],
                    "total_booking_success": profile["total_booking_success"],
                    "total_booking_cancelled": profile["total_booking_cancelled"],
                    "ayo_latest_booking_date": profile["latest_booking_date"],
                    "deleted": bool(profile["is_deleted_account"]),
                    "profile_updated_at": profile["updated_at"],
                }
            )
            if cfg.expose_customer_contact:
                item["phone"] = profile["phone"]
                item["email"] = profile["email"]
            entry_updated = profile["updated_at"] or ""
        else:
            entry_updated = ""
        item["_latest"] = max(item.get("ayo_latest_booking_date") or "", entry["last"] or "")
        item["_updated"] = max(entry["updated"], entry_updated)
        items.append(item)

    for entry in guests.values():
        item = {
            "customer_id": 0,
            "type": "guest",
            "name": entry["name"],
            "slot_count": entry["slot_count"],
            "active_slot_count": entry["active_slot_count"],
            "first_booking_date": entry["first"],
            "last_booking_date": entry["last"],
            "sources": sorted(entry["sources"]),
            "_latest": entry["last"] or "",
            "_updated": entry["updated"],
        }
        items.append(item)

    return items
