"""Web dashboard: serves the single-page UI and its JSON API (behind Home Assistant ingress)."""

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import asyncpg
from aiohttp import web

from .config import ADDON_VERSION, DATA_DIR, Config
from .database import QUERY_TIMEOUT, RANGES, Database, NotConnectedError, QueryRefusedError
from .donate import DonatePrompt

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"

# Home Assistant's ingress proxy: the only client allowed when running as an app
INGRESS_ADDRESS = "172.30.32.2"
ENTITY_ID = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+$")
OVERVIEW_HOURS = (24, 168)
MAX_QUERY_LENGTH = 100_000


def _json(data, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, default=str), content_type="application/json", status=status)


def _error(message: str, status: int = 400) -> web.Response:
    return _json({"error": message}, status)


class WebUI:
    """The app's web server."""

    def __init__(self, port: int, config: Config, database: Database):
        self.port = port
        self.config = config
        self.database = database
        self.ingress_only = bool(os.environ.get("SUPERVISOR_TOKEN"))
        self.app = web.Application(middlewares=[self._ingress_only, self._database_errors])
        self._runner: web.AppRunner | None = None
        self._schema: dict | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        get = self.app.router.add_get
        get("/", self._handle_index)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        self.app.router.add_static("/web/", WEB_DIR)
        get("/api/status", self._handle_status)
        get("/api/overview", self._handle_overview)
        get("/api/entities", self._handle_entities)
        get("/api/entity", self._handle_entity)
        get("/api/storage", self._handle_storage)
        self.app.router.add_post("/api/query", self._handle_query)

    async def start(self) -> None:
        # handler_cancellation: a query the page cancels (or a closed page) stops in the database too
        self._runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=1.0, handler_cancellation=True)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        logger.info(f"Web UI listening on port {self.port}")
        await asyncio.Event().wait()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @web.middleware
    async def _ingress_only(self, request: web.Request, handler):
        # The history is private: under Home Assistant only the (authenticated) ingress proxy may ask
        if self.ingress_only and request.remote != INGRESS_ADDRESS:
            return web.Response(status=403, text="Open Chronicle from the Home Assistant sidebar")
        return await handler(request)

    @web.middleware
    async def _database_errors(self, request: web.Request, handler):
        try:
            return await handler(request)
        except NotConnectedError as e:
            return _error(f"Cannot connect to the database: {e}", 503)
        except asyncpg.QueryCanceledError:
            return _error("The database took too long to answer; try a shorter range", 504)
        except asyncpg.UndefinedTableError:
            self._schema = None
            return _error("The Chronicle tables are not in this database", 409)
        except asyncpg.PostgresError as e:
            logger.warning(f"Query failed: {e}")
            return _error(f"The database refused the query: {e}", 500)

    async def _status(self) -> dict:
        """Server and schema information, cached until the tables appear or disappear."""
        if self._schema is None or not self._schema.get("schema"):
            self._schema = await self.database.status()
        return self._schema

    async def _require_schema(self) -> dict | None:
        status = await self._status()
        return status if status["schema"] else None

    # ------------------------------------------------------------------ pages

    async def _handle_index(self, request: web.Request) -> web.Response:
        # Behind Home Assistant ingress the dashboard lives under X-Ingress-Path; the
        # <link>/<script> tags in the head can't compute that like the JS does
        base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace("__ADDON_VERSION__", ADDON_VERSION).replace("__BASE__", base)
        return web.Response(
            text=html,
            content_type="text/html",
            # Never let the browser serve a stale cached page after an app update
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    async def _handle_status(self, _request: web.Request) -> web.Response:
        result = {"version": ADDON_VERSION, "connection": self.config.public(), "connected": False}
        try:
            result.update(await self._status())
            result["connected"] = True
        except NotConnectedError as e:
            result["error"] = str(e)
        return _json(result)

    async def _handle_overview(self, request: web.Request) -> web.Response:
        if not await self._require_schema():
            return _error("The Chronicle tables are not in this database", 409)
        try:
            hours = int(request.query.get("hours", 24))
        except ValueError:
            hours = 24
        return _json(await self.database.overview(hours if hours in OVERVIEW_HOURS else 24))

    async def _handle_entities(self, _request: web.Request) -> web.Response:
        if not await self._require_schema():
            return _error("The Chronicle tables are not in this database", 409)
        return _json({"entities": await self.database.entities()})

    async def _handle_entity(self, request: web.Request) -> web.Response:
        """One entity: a range (?range=24h), or a zoom window in epoch seconds (?from=&to=)."""
        status = await self._require_schema()
        if not status:
            return _error("The Chronicle tables are not in this database", 409)
        entity_id = request.query.get("entity_id", "")
        if not ENTITY_ID.match(entity_id):
            return _error("Invalid entity_id")
        range_key = request.query.get("range", "24h")
        if range_key not in RANGES:
            return _error("Invalid range")
        window = None
        if "from" in request.query or "to" in request.query:
            try:
                start = datetime.fromtimestamp(float(request.query["from"]), UTC)
                end = datetime.fromtimestamp(float(request.query["to"]), UTC)
            except (KeyError, ValueError, OverflowError, OSError):
                return _error("from and to must both be times in seconds")
            if end <= start:
                return _error("to must be after from")
            window = (start, end)
        data = await self.database.entity(entity_id, range_key, aggregates=status["aggregates"], window=window)
        if data is None:
            return _error("Unknown entity", 404)
        return _json(data)

    async def _handle_query(self, request: web.Request) -> web.Response:
        """The Query page: one read-only statement; PostgreSQL's own message when it fails."""
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            body = None
        sql = body.get("sql") if isinstance(body, dict) else None
        if not isinstance(sql, str) or not sql.strip():
            return _error("Enter a query")
        if len(sql) > MAX_QUERY_LENGTH:
            return _error("The query is too long")
        sql = sql.strip().rstrip(";").strip()
        try:
            return _json(await self.database.query(sql))
        except QueryRefusedError as e:
            return _error(str(e))
        except asyncpg.QueryCanceledError:
            return _error(f"The query was stopped after {QUERY_TIMEOUT}: it took too long", 400)
        except asyncpg.PostgresError as e:
            # Shown in the page as PostgreSQL wrote it, with the hint and position when given
            detail = {"error": str(e), "sqlstate": e.sqlstate}
            for key in ("detail", "hint", "position"):
                if getattr(e, key, None):
                    detail[key] = getattr(e, key)
            return _json(detail, 400)

    async def _handle_storage(self, _request: web.Request) -> web.Response:
        if not await self._require_schema():
            return _error("The Chronicle tables are not in this database", 409)
        return _json(await self.database.storage())
