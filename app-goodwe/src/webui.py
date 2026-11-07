"""Web dashboard: serves the single-page UI and its JSON API (behind Home Assistant ingress)."""

import asyncio
import csv
import io
import json
import logging
import os
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from aiohttp import web
from goodwe import InverterError

from .config import ADDON_VERSION, Config
from .controller import BatteryController
from .database import DAY_KEYS, Database
from .events import EVENT_TYPES, EventBus
from .forecast import Forecast
from .inverter import WRITABLE, InverterLink
from .mqtt import MqttPublisher

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"

# Home Assistant's ingress proxy: the only client allowed when running as an add-on
INGRESS_ADDRESS = "172.30.32.2"
STREAM_HEARTBEAT = 15
BUCKETS = ("day", "week", "month", "year")


def _int(request: web.Request, name: str, default: int, minimum: int = 0, maximum: int = 2**40) -> int:
    try:
        value = int(request.query.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _date(value: str | None, default: date) -> date:
    try:
        return date.fromisoformat(value or "")
    except ValueError:
        return default


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _json(data, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, default=str), content_type="application/json", status=status)


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Expected a JSON object"}), content_type="application/json")
    return body


class WebUI:
    """The add-on's web server."""

    def __init__(
        self,
        port: int,
        config: Config,
        database: Database,
        link: InverterLink,
        controller: BatteryController,
        forecast: Forecast,
        mqtt: MqttPublisher,
        events: EventBus,
    ):
        self.port = port
        self.config = config
        self.database = database
        self.link = link
        self.controller = controller
        self.forecast = forecast
        self.mqtt = mqtt
        self.events = events
        self.ingress_only = bool(os.environ.get("SUPERVISOR_TOKEN"))
        self.app = web.Application(middlewares=[self._ingress_only])
        self._runner: web.AppRunner | None = None
        self._stopping = False
        self._setup_routes()

    def _setup_routes(self) -> None:
        get, post = self.app.router.add_get, self.app.router.add_post
        get("/", self._handle_index)
        self.app.router.add_static("/web/", WEB_DIR)

        get("/api/status", self._handle_status)
        get("/api/stream", self._handle_stream)
        get("/api/live", self._handle_live)
        get("/api/day", self._handle_day)
        get("/api/flow", self._handle_flow)
        get("/api/stats", self._handle_stats)
        get("/api/solar", self._handle_solar)
        post("/api/forecast/refresh", self._handle_forecast_refresh)
        get("/api/control", self._handle_control)
        post("/api/control", self._handle_control_update)
        post("/api/control/dod", self._handle_control_dod)
        get("/api/inverter/settings", self._handle_inverter_settings)
        post("/api/inverter/write", self._handle_inverter_write)
        get("/api/events", self._handle_events)
        get("/api/export/daily.csv", self._handle_export)
        get("/api/backup", self._handle_backup)

    async def start(self) -> None:
        # Open dashboards hold a live stream each: don't wait for them when stopping
        self._runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=1.0)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        logger.info(f"Web UI listening on port {self.port}")
        await asyncio.Event().wait()

    def closing(self) -> None:
        """The add-on is stopping: end the live streams of open dashboards."""
        self._stopping = True

    async def stop(self) -> None:
        self._stopping = True
        if self._runner:
            await self._runner.cleanup()

    @web.middleware
    async def _ingress_only(self, request: web.Request, handler):
        # The dashboard can change inverter settings: under Home Assistant it only answers
        # the (authenticated) ingress proxy
        if self.ingress_only and request.remote != INGRESS_ADDRESS:
            return web.Response(status=403, text="Open GoodWe Monitor from the Home Assistant sidebar")
        return await handler(request)

    # ------------------------------------------------------------------
    # Page, status, live stream
    # ------------------------------------------------------------------
    async def _handle_index(self, request: web.Request) -> web.Response:
        # Behind Home Assistant ingress the dashboard lives under X-Ingress-Path; the
        # <link>/<script> tags in the head can't compute that like the JS does
        base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        html = (WEB_DIR / "index.html").read_text()
        html = html.replace("__ADDON_VERSION__", ADDON_VERSION).replace("__BASE__", base)
        return web.Response(
            text=html,
            content_type="text/html",
            # Never let the browser serve a stale cached page after an add-on update
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    def _live_payload(self) -> dict:
        return {
            "inverter": self.link.view(),
            "data": self.link.data,
            "control": self.controller.state(),
            "mqtt": self.mqtt.view(),
            "forecast": self.forecast.summary(),
            "events_seq": self.events.seq,
        }

    async def _handle_status(self, request: web.Request) -> web.Response:
        return _json(
            {
                **self._live_payload(),
                "version": ADDON_VERSION,
                "config": {
                    "host": self.config.inverter_host,
                    "poll_seconds": self.config.poll_seconds,
                    "forecast_enabled": self.config.forecast_enabled,
                    "planes": [p.as_dict() for p in self.config.planes],
                    "total_kwp": self.config.total_kwp,
                    "mqtt_enabled": self.config.mqtt_enabled,
                    "mqtt_base_topic": self.config.mqtt_base_topic,
                    "log_level": self.config.log_level,
                },
                "database": {
                    "bytes": self.database.size_bytes(),
                    **self.database.counts(),
                    **self.database.data_span(),
                },
                "writable": list(WRITABLE),
                "event_types": EVENT_TYPES,
                "ingress": self.ingress_only,
            }
        )

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        """Server-sent events: the live state, pushed whenever it changes."""
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        await response.prepare(request)
        last_sent = None
        last_write = time.monotonic()
        try:
            while not self._stopping:
                data = json.dumps(self._live_payload(), default=str)
                if data != last_sent:
                    await response.write(f"data: {data}\n\n".encode())
                    last_sent = data
                    last_write = time.monotonic()
                elif time.monotonic() - last_write >= STREAM_HEARTBEAT:
                    await response.write(b"event: ping\ndata: {}\n\n")
                    last_write = time.monotonic()
                await asyncio.sleep(1)
        except (ConnectionResetError, TimeoutError, asyncio.CancelledError):
            pass
        return response

    # ------------------------------------------------------------------
    # Series and statistics
    # ------------------------------------------------------------------
    async def _handle_live(self, request: web.Request) -> web.Response:
        minutes = _int(request, "minutes", 120, 10, 24 * 60)
        bucket = 60 if minutes <= 180 else 300
        rows = await asyncio.to_thread(self.database.live_series, minutes, bucket)
        return _json({"minutes": minutes, "bucket_s": bucket, "series": rows})

    async def _handle_day(self, request: web.Request) -> web.Response:
        day = _date(request.query.get("date"), date.today()).isoformat()
        result = await asyncio.to_thread(self.database.day_series, day)
        result["summary"] = self.database.day_row(day)
        result["forecast"] = self.forecast.hourly(date.fromisoformat(day)) if self.forecast.configured else []
        return _json(result)

    async def _handle_flow(self, request: web.Request) -> web.Response:
        day = _date(request.query.get("date"), date.today()).isoformat()
        return _json(await asyncio.to_thread(self.database.energy_flow, day))

    async def _handle_stats(self, request: web.Request) -> web.Response:
        today = date.today()
        start = _date(request.query.get("from"), today - timedelta(days=29))
        end = _date(request.query.get("to"), today + timedelta(days=1))
        bucket = request.query.get("bucket", "day")
        if bucket not in BUCKETS:
            return _error("Unknown bucket")
        periods, summary = await asyncio.gather(
            asyncio.to_thread(self.database.periods, bucket, start.isoformat(), end.isoformat()),
            asyncio.to_thread(self.database.summary, start.isoformat(), end.isoformat()),
        )
        return _json(
            {"from": start.isoformat(), "to": end.isoformat(), "bucket": bucket, "periods": periods, "summary": summary}
        )

    async def _handle_solar(self, request: web.Request) -> web.Response:
        today = date.today()
        if self.forecast.configured:
            await self.forecast.fetch()
        return _json(
            {
                "forecast": self.forecast.summary(),
                "hourly_today": self.forecast.hourly(today),
                "hourly_tomorrow": self.forecast.hourly(today + timedelta(days=1)),
                "daily": self.forecast.daily(),
                "accuracy": self.database.forecast_accuracy(14),
                "months": self.database.month_totals(today.year),
                "years": self.database.year_totals(),
                "peak_pv": self.database.peak_pv(),
                "today": self.database.day_row(today.isoformat()),
            }
        )

    async def _handle_forecast_refresh(self, request: web.Request) -> web.Response:
        if not self.forecast.configured:
            return _error(
                "The forecast is not configured: set a location and at least one plane in the add-on configuration", 409
            )
        await self.forecast.fetch(force=True)
        if self.forecast.error:
            return _error(f"Forecast fetch failed: {self.forecast.error}", 502)
        return _json(self.forecast.summary())

    # ------------------------------------------------------------------
    # Battery control and inverter settings
    # ------------------------------------------------------------------
    async def _handle_control(self, request: web.Request) -> web.Response:
        return _json(self.controller.state())

    async def _handle_control_update(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        try:
            self.controller.update(body)
        except ValueError as e:
            return _error(str(e))
        return _json(self.controller.state())

    async def _handle_control_dod(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        try:
            dod = int(body.get("dod"))
            if not 0 <= dod <= 100:
                raise ValueError
        except (TypeError, ValueError):
            return _error("dod must be a number between 0 and 100")
        try:
            result = await self.controller.set_dod(dod, "dashboard")
        except (InverterError, ValueError, OSError, TimeoutError) as e:
            return _error(f"The inverter did not accept the command: {e}", 502)
        return _json({"result": result, "control": self.controller.state()})

    async def _handle_inverter_settings(self, request: web.Request) -> web.Response:
        if request.query.get("cached") == "1":
            # Never a request to the inverter: pages that merely show the values use this
            return _json({"settings": self.link.settings, "read_at": self.link.settings_at or None, "cached": True})
        try:
            settings = await self.link.read_settings()
        except (InverterError, OSError, TimeoutError, ValueError) as e:
            return _error(f"Could not read the inverter's settings: {e}", 502)
        return _json({"settings": settings, "read_at": self.link.settings_at, "cached": False})

    async def _handle_inverter_write(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        key = str(body.get("key") or "")
        if key not in WRITABLE:
            return _error(f"Only these settings can be written: {', '.join(WRITABLE)}")
        try:
            result = await self.link.apply(key, body.get("value"), "dashboard")
        except ValueError as e:
            return _error(str(e))
        except (InverterError, OSError, TimeoutError) as e:
            return _error(f"The inverter did not accept the command: {e}", 502)
        if key == "dod":
            self.controller.last_dod = int(body.get("value"))
        return _json({"result": result})

    # ------------------------------------------------------------------
    # Events and exports
    # ------------------------------------------------------------------
    async def _handle_events(self, request: web.Request) -> web.Response:
        limit = _int(request, "limit", 100, 1, 1000)
        kinds = tuple(k for k in (request.query.get("types") or "").split(",") if k in EVENT_TYPES)
        return _json({"events": self.database.events(limit, kinds or None)})

    async def _handle_export(self, request: web.Request) -> web.Response:
        """Every recorded day as CSV."""
        rows = self.database.daily_rows("0000-01-01", "9999-12-31")
        columns = ("date", *DAY_KEYS, "ppv_max", "soc_min", "soc_max")
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        for row in rows:
            writer.writerow([row.get(column) for column in columns])
        return web.Response(
            text=buffer.getvalue(),
            content_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="goodwe-daily.csv"'},
        )

    async def _handle_backup(self, request: web.Request) -> web.StreamResponse:
        """Download a copy of the database."""
        handle, name = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        path = Path(name)
        try:
            await asyncio.to_thread(self.database.backup_to, path)
            body = await asyncio.to_thread(path.read_bytes)
        finally:
            path.unlink(missing_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M")
        return web.Response(
            body=body,
            content_type="application/vnd.sqlite3",
            headers={"Content-Disposition": f'attachment; filename="goodwe-{stamp}.db"'},
        )
