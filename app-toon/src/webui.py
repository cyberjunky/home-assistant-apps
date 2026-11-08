"""The dashboard, served through Home Assistant's ingress."""

import asyncio
import contextlib
import logging
import os
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

from aiohttp import web

from .config import APP_VERSION, DATA_DIR
from .database import Database, backup_copy, database_report
from .donate import DonatePrompt
from .history import ARCHIVES
from .metrics import METRIC_BY_KEY, METRICS
from .monitor import ToonMonitor
from .mqtt import MqttPublisher
from .stats import BUCKETS, Stats, buckets_csv

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
MAX_RANGE_HOURS = 24 * 3660


def public_config(config) -> dict:
    """The options to show. Passwords stay out; for the MQTT user only whether there is one."""
    return {
        "ssh_host": config.ssh_host,
        "ssh_port": config.ssh_port,
        "ssh_username": config.ssh_username,
        "trust_new_host_key": config.trust_new_host_key,
        "import_history": config.import_history,
        "raw_retention_days": config.raw_retention_days,
        "mqtt_enabled": config.mqtt_enabled,
        "mqtt_base_topic": config.mqtt_base_topic,
        "mqtt_discovery_prefix": config.mqtt_discovery_prefix,
        "log_level": config.log_level,
        "gas_kwh_per_m3": config.gas_kwh_per_m3,
    }


def next_change(monitor: ToonMonitor) -> dict:
    """When the week program changes next, in real time: the Toon counts with its own clock."""
    schedule = monitor.schedule
    result = {"time": None, "state": None}
    try:
        toon_time = int(schedule.get("nextTime") or 0)
    except ValueError:
        toon_time = 0
    if toon_time > 0:
        result["time"] = toon_time - float(monitor.values.get("clock_offset") or 0)
    with contextlib.suppress(KeyError, ValueError):
        result["state"] = int(schedule["nextState"])
    return result


class WebUI:
    """The dashboard's page and its small JSON API."""

    def __init__(self, port: int, database: Database, monitor: ToonMonitor, mqtt: MqttPublisher, external=None):
        self.port = port
        self.database = database
        self.monitor = monitor
        self.mqtt = mqtt
        self.external = external
        # A backup cut short by a crash must not stay behind in the app's storage
        for stale in database.path.parent.glob("backup-*.db"):
            stale.unlink(missing_ok=True)
        extra = tuple(sorted(external.keys)) if external else ()
        self.stats = Stats(database.path, monitor.config.gas_kwh_per_m3, extra)
        self.app = web.Application()
        self.app.router.add_get("/", self._index)
        self.app.router.add_get("/api/status", self._status)
        self.app.router.add_get("/api/history", self._history)
        self.app.router.add_get("/api/outages", self._outages)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        self.app.router.add_get("/api/today", self._today)
        self.app.router.add_get("/api/stats", self._stats)
        self.app.router.add_get("/api/backup", self._backup)
        self.app.router.add_get("/api/database", self._database)
        self.app.router.add_get("/api/export/periods.csv", self._export_periods)
        self.app.router.add_get("/api/export/values.csv", self._export_values)
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

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(
            text=page,
            content_type="text/html",
            # Never let the browser serve a stale cached page after an update
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

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
                "next": next_change(monitor),
                "metrics": {
                    **{m.key: {"name": m.name, "unit": m.unit, "binary": m.binary} for m in METRICS},
                    **{
                        s["key"]: {"name": s["name"], "unit": s["unit"], "binary": False}
                        for s in (self.external.status() if self.external else [])
                    },
                },
                "config": public_config(monitor.config),
                "external": self.external.status() if self.external else [],
                "mqtt": {"connected": self.mqtt.connected, "broker": self.mqtt.broker},
                "database": self.database.counts(),
                "import": {
                    "running": monitor.importer.running,
                    "total": len(ARCHIVES),
                    "done": sum(self.database.import_position(a.logger, a.rra)[1] for a in ARCHIVES),
                },
            }
        )

    async def _history(self, request: web.Request) -> web.Response:
        known = self.external.keys if self.external else set()
        metrics = [m for m in request.query.get("metrics", "").split(",") if m in METRIC_BY_KEY or m in known]
        if not metrics:
            raise web.HTTPBadRequest(text="no known metrics")
        try:
            hours = min(max(1, int(request.query.get("hours", "24"))), MAX_RANGE_HOURS)
            end = int(request.query.get("end", time.time()))
            start = int(request.query.get("start", end - hours * 3600))
        except ValueError as e:
            raise web.HTTPBadRequest(text="hours, start and end must be numbers") from e
        return web.json_response(self.database.history(metrics, start, end))

    async def _backup(self, request: web.Request) -> web.StreamResponse:
        """Download a copy of the database. The Toon's SSH key and the options aren't in it."""
        self.database.flush()
        handle, name = tempfile.mkstemp(prefix="backup-", suffix=".db", dir=self.database.path.parent)
        os.close(handle)
        path = Path(name)
        try:
            await asyncio.to_thread(backup_copy, self.database.path, path)
            stamp = time.strftime("%Y%m%d-%H%M")
            response = web.StreamResponse(
                headers={
                    "Content-Type": "application/vnd.sqlite3",
                    "Content-Length": str(path.stat().st_size),
                    "Content-Disposition": f'attachment; filename="toon-{stamp}.db"',
                }
            )
            await response.prepare(request)
            with path.open("rb") as backup:
                while chunk := await asyncio.to_thread(backup.read, 1024 * 1024):
                    await response.write(chunk)
            await response.write_eof()
            return response
        finally:
            path.unlink(missing_ok=True)

    async def _database(self, request: web.Request) -> web.Response:
        self.database.flush()
        report = await asyncio.to_thread(database_report, self.database.path)
        names = {metric.key: metric.name for metric in METRICS}
        if self.external:
            names.update(self.external.names())
        for metric in report["metrics"]:
            metric["name"] = names.get(metric["metric"], metric["metric"])
        return web.json_response(report)

    async def _outages(self, request: web.Request) -> web.Response:
        return web.json_response(self.database.outages())

    async def _today(self, request: web.Request) -> web.Response:
        self.database.flush()
        return web.json_response(await asyncio.to_thread(self.stats.today))

    @staticmethod
    def _period(request: web.Request) -> tuple[str, str]:
        today = date.today()
        try:
            first = date.fromisoformat(request.query.get("from", (today - timedelta(days=29)).isoformat()))
            last = date.fromisoformat(request.query.get("to", today.isoformat()))
        except ValueError as e:
            raise web.HTTPBadRequest(text="from and to must be dates") from e
        return first.isoformat(), last.isoformat()

    @staticmethod
    def _bucket(request: web.Request) -> str:
        bucket = request.query.get("bucket", "day")
        if bucket not in BUCKETS:
            raise web.HTTPBadRequest(text="unknown bucket")
        return bucket

    @staticmethod
    def _csv(text: str, name: str) -> web.Response:
        return web.Response(
            text=text,
            content_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    async def _stats(self, request: web.Request) -> web.Response:
        bucket = self._bucket(request)
        first, last = self._period(request)
        self.database.flush()
        return web.json_response(await asyncio.to_thread(self.stats.report, first, last, bucket))

    async def _export_periods(self, request: web.Request) -> web.Response:
        bucket = self._bucket(request)
        first, last = self._period(request)
        self.database.flush()
        report = await asyncio.to_thread(self.stats.report, first, last, bucket)
        return self._csv(buckets_csv(report), f"toon-{bucket}-{first}-{last}.csv")

    async def _export_values(self, request: web.Request) -> web.Response:
        known = self.external.keys if self.external else set()
        metrics = [m for m in request.query.get("metrics", "").split(",") if m in METRIC_BY_KEY or m in known]
        metrics = metrics or [*[m.key for m in METRICS if not m.text], *sorted(known)]
        first, last = self._period(request)
        hourly = request.query.get("source") == "hourly"
        self.database.flush()
        text = await asyncio.to_thread(self.stats.raw_csv, metrics, first, last, hourly)
        return self._csv(text, f"toon-{'hourly' if hourly else 'samples'}-{first}-{last}.csv")
