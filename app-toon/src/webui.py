"""The dashboard, served through Home Assistant's ingress."""

import asyncio
import logging
import time
from pathlib import Path

from aiohttp import web

from .config import APP_VERSION, DATA_DIR
from .database import Database
from .donate import DonatePrompt
from .metrics import METRIC_BY_KEY, METRICS
from .monitor import ToonMonitor
from .mqtt import MqttPublisher

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_RANGE_HOURS = 24 * 3660


class WebUI:
    """The dashboard's page and its small JSON API."""

    def __init__(self, port: int, database: Database, monitor: ToonMonitor, mqtt: MqttPublisher):
        self.port = port
        self.database = database
        self.monitor = monitor
        self.mqtt = mqtt
        self.app = web.Application()
        self.app.router.add_get("/", self._index)
        self.app.router.add_get("/api/status", self._status)
        self.app.router.add_get("/api/history", self._history)
        self.app.router.add_get("/api/outages", self._outages)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        self.app.router.add_static("/static", WEB_DIR)

    async def start(self) -> None:
        """Serve until cancelled."""
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info(f"Dashboard on port {self.port}")
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    async def _index(self, request: web.Request) -> web.FileResponse:
        return web.FileResponse(WEB_DIR / "index.html")

    async def _status(self, request: web.Request) -> web.Response:
        monitor = self.monitor
        return web.json_response(
            {
                "version": APP_VERSION,
                "connected": monitor.connected,
                "connected_since": monitor.connected_since,
                "error": monitor.last_error,
                "now": time.time(),
                "info": {k: monitor.info.get(k) for k in ("vers", "serialNumber", "partNumber") if k in monitor.info},
                "adapters": monitor.adapters,
                "values": monitor.values,
                "metrics": {m.key: {"name": m.name, "unit": m.unit, "binary": m.binary} for m in METRICS},
                "mqtt": {"connected": self.mqtt.connected, "broker": self.mqtt.broker},
                "database": self.database.counts(),
                "import": {"running": monitor.importer.running, **self.database.import_summary()},
            }
        )

    async def _history(self, request: web.Request) -> web.Response:
        metrics = [m for m in request.query.get("metrics", "").split(",") if m in METRIC_BY_KEY]
        if not metrics:
            raise web.HTTPBadRequest(text="no known metrics")
        try:
            hours = min(max(1, int(request.query.get("hours", "24"))), MAX_RANGE_HOURS)
        except ValueError as e:
            raise web.HTTPBadRequest(text="hours must be a number") from e
        end = int(time.time())
        return web.json_response(self.database.history(metrics, end - hours * 3600, end))

    async def _outages(self, request: web.Request) -> web.Response:
        return web.json_response(self.database.outages())
