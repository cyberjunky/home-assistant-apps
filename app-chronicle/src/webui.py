"""Web dashboard: serves the single-page UI and its JSON API (behind Home Assistant ingress)."""

import asyncio
import json
import logging
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import aiohttp
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
DOMAIN_NAME = re.compile(r"^[a-z0-9_]+$")
CORE_API_URL = "http://supervisor/core/api"
OVERVIEW_HOURS = (24, 168)
MAX_QUERY_LENGTH = 100_000


# The Chronicle integration's sensors, by the end of their entity id (Home Assistant names them
# after the device and the sensor: binary_sensor.chronicle_connected, sensor.chronicle_queue)
RECORDER_TTL = 10
RECORDER_SENSORS = {
    "connected": "_connected",
    "queue": "_queue",
    "dropped": "_dropped_states",
    "last_write": "_last_write",
    "write_duration": "_write_duration",
}
RECORDER_TEMPLATE = (
    "{% set ns = namespace(out=[]) %}"
    "{% for e in integration_entities('chronicle') %}"
    "{% set ns.out = ns.out + [[e, states(e)]] %}"
    "{% endfor %}{{ ns.out | tojson }}"
)


def recorder_status(states: dict[str, str]) -> dict:
    """The writer's state from the integration's entities ({entity_id: state})."""
    found: dict[str, str] = {}
    for key, suffix in RECORDER_SENSORS.items():
        for entity_id in sorted(states):
            if entity_id.endswith(suffix) and entity_id.split(".", 1)[0] in ("sensor", "binary_sensor"):
                found[key] = states[entity_id]
                break
    if not found:
        return {"available": False}

    def number(key: str) -> float | None:
        try:
            return float(found[key])
        except (KeyError, ValueError):
            return None

    last_write = None
    if found.get("last_write") not in (None, "unknown", "unavailable"):
        try:
            last_write = datetime.fromisoformat(found["last_write"]).timestamp()
        except ValueError:
            last_write = None
    connected = found.get("connected")
    return {
        "available": True,
        "connected": None if connected in (None, "unknown", "unavailable") else connected == "on",
        "queue": number("queue"),
        "dropped": number("dropped"),
        "last_write": last_write,
        "write_duration": number("write_duration"),
    }


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
        self._recorder: tuple[float, dict] | None = None
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
        get("/api/recorder", self._handle_recorder)
        self.app.router.add_post("/api/query", self._handle_query)
        self.app.router.add_post("/api/exclude", self._handle_exclude)
        self.app.router.add_post("/api/purge", self._handle_purge)

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

    # ------------------------------------------------------------------ the integration's own state

    async def _handle_recorder(self, _request: web.Request) -> web.Response:
        """What the Chronicle integration in Home Assistant says about its writing, from its sensors."""
        now = asyncio.get_running_loop().time()
        if self._recorder is None or now - self._recorder[0] > RECORDER_TTL:
            self._recorder = (now, await self._read_recorder())
        return _json(self._recorder[1])

    async def _read_recorder(self) -> dict:
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            return {"available": False}
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with (
                aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}, timeout=timeout) as session,
                session.post(f"{CORE_API_URL}/template", json={"template": RECORDER_TEMPLATE}) as response,
            ):
                if response.status >= 300:
                    return {"available": False}
                states = dict(json.loads(await response.text()))
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError) as e:
            logger.debug(f"Cannot read the Chronicle integration's sensors: {e}")
            return {"available": False}
        return recorder_status(states)

    # ------------------------------------------------------------------ the integration's actions

    async def _call_action(self, service: str, data: dict) -> dict:
        """Calls a chronicle.* action in Home Assistant; the app itself never writes to the history."""
        token = os.environ.get("SUPERVISOR_TOKEN")
        if not token:
            raise web.HTTPServiceUnavailable(
                text=json.dumps({"error": "Only when Chronicle runs as a Home Assistant app"}),
                content_type="application/json",
            )
        url = f"{CORE_API_URL}/services/chronicle/{service}?return_response"
        timeout = aiohttp.ClientTimeout(total=300)
        try:
            async with (
                aiohttp.ClientSession(headers={"Authorization": f"Bearer {token}"}, timeout=timeout) as session,
                session.post(url, json=data) as response,
            ):
                text = await response.text()
                if response.status >= 300:
                    hint = (
                        " Excluding needs the Chronicle integration 0.1.5 or later."
                        if service.startswith("exclude") and response.status == 400
                        else ""
                    )
                    raise web.HTTPBadGateway(
                        text=json.dumps({"error": f"Home Assistant refused: {text.strip()[:300]}.{hint}"}),
                        content_type="application/json",
                    )
                body = json.loads(text) if text else {}
        except (aiohttp.ClientError, TimeoutError, ValueError) as e:
            raise web.HTTPBadGateway(
                text=json.dumps({"error": f"Cannot reach Home Assistant: {e}"}), content_type="application/json"
            ) from e
        self._schema = None
        return body.get("service_response") or {}

    async def _handle_exclude(self, request: web.Request) -> web.Response:
        """Stop recording an entity or a whole domain, optionally deleting what was recorded."""
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            body = None
        if not isinstance(body, dict):
            return _error("Expected a JSON object")
        purge = bool(body.get("purge"))
        if body.get("entity_id"):
            if not ENTITY_ID.match(str(body["entity_id"])):
                return _error("Invalid entity_id")
            result = await self._call_action("exclude_entities", {"entity_id": [body["entity_id"]], "purge": purge})
        elif body.get("domain"):
            if not DOMAIN_NAME.match(str(body["domain"])):
                return _error("Invalid domain")
            result = await self._call_action("exclude_domains", {"domain": [body["domain"]], "purge": purge})
        else:
            return _error("Give an entity_id or a domain")
        return _json(result)

    async def _handle_purge(self, request: web.Request) -> web.Response:
        """Delete everything recorded for an entity; it is recorded again from now on."""
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            body = None
        entity_id = body.get("entity_id") if isinstance(body, dict) else None
        if not isinstance(entity_id, str) or not ENTITY_ID.match(entity_id):
            return _error("Invalid entity_id")
        return _json(await self._call_action("purge_entities", {"entity_id": [entity_id]}))
