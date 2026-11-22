"""The dashboard, served through Home Assistant's ingress.

The pages only read. Mining is paused and resumed with each miner's Mining switch in Home Assistant;
the one change made here is deleting the app's own history, and that only through the signed-in
ingress proxy.
"""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from aiohttp import web

from .config import APP_VERSION, DATA_DIR, Config
from .database import Database
from .donate import DonatePrompt
from .miner import Miner, local_day
from .stats import miner_odds, totals

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy
INGRESS_ADDRESS = "172.30.32.2"
MAX_HOURS = 24 * 31


class WebUI:
    def __init__(self, port: int, config: Config, database: Database, miners: list[Miner], network, mqtt=None):
        self.port = port
        self.config = config
        self.database = database
        self.miners = {miner.slug: miner for miner in miners}
        self.network = network
        self.mqtt = mqtt
        for stale in database.path.parent.glob("backup-*.db"):
            stale.unlink(missing_ok=True)
        self.app = web.Application()
        self.app.router.add_get("/", self._index)
        self.app.router.add_get("/api/status", self._status)
        self.app.router.add_get("/api/history", self._history)
        self.app.router.add_get("/api/bests", self._bests)
        self.app.router.add_get("/api/days", self._days)
        self.app.router.add_get("/api/events", self._events)
        self.app.router.add_get("/api/entities", self._entities)
        self.app.router.add_get("/api/backup", self._backup)
        self.app.router.add_post("/api/data/delete", self._delete_data)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        self.app.router.add_static("/static", WEB_DIR)

    async def start(self) -> None:
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
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    def _miner_arg(self, request: web.Request) -> str | None:
        slug = request.query.get("miner") or None
        if slug and slug not in self.miners:
            raise web.HTTPNotFound(text="no such miner")
        return slug

    @staticmethod
    def _int(request: web.Request, name: str, default: int, lo: int, hi: int) -> int:
        try:
            return min(hi, max(lo, int(request.query.get(name, default))))
        except ValueError as e:
            raise web.HTTPBadRequest(text=f"{name} must be a number") from e

    async def _status(self, request: web.Request) -> web.Response:
        network = self.network.status()
        difficulty = network["difficulty"]
        miners = []
        for miner in self.miners.values():
            summary = miner.summary()
            summary["odds"] = miner_odds(miner, difficulty)
            miners.append(summary)
        today = await asyncio.to_thread(self.database.days, local_day(time.time()))
        return web.json_response(
            {
                "version": APP_VERSION,
                "now": time.time(),
                "network": network,
                "totals": totals(list(self.miners.values()), difficulty),
                "today": today,
                "miners": miners,
                "config": {
                    "poll_seconds": self.config.poll_seconds,
                    "network_data": self.config.network_data,
                    "firmware_check": self.config.firmware_check,
                    "mqtt_enabled": self.config.mqtt_enabled,
                    "mqtt_base_topic": self.config.mqtt_base_topic,
                    "mqtt_discovery_prefix": self.config.mqtt_discovery_prefix,
                    "log_level": self.config.log_level,
                },
                "mqtt": {
                    "connected": bool(self.mqtt and self.mqtt.connected),
                    "broker": self.mqtt.broker if self.mqtt else "",
                },
                "database": await asyncio.to_thread(self.database.counts),
            }
        )

    async def _history(self, request: web.Request) -> web.Response:
        hours = self._int(request, "hours", 24, 1, MAX_HOURS)
        end = int(time.time())
        result = await asyncio.to_thread(self.database.history, end - hours * 3600, end, self._miner_arg(request))
        return web.json_response(result)

    async def _bests(self, request: web.Request) -> web.Response:
        limit = self._int(request, "limit", 20, 1, 500)
        return web.json_response(await asyncio.to_thread(self.database.bests, limit, self._miner_arg(request)))

    async def _days(self, request: web.Request) -> web.Response:
        days = self._int(request, "days", 30, 1, 3660)
        since = local_day(time.time() - (days - 1) * 86400)
        return web.json_response(await asyncio.to_thread(self.database.days, since, self._miner_arg(request)))

    async def _events(self, request: web.Request) -> web.Response:
        limit = self._int(request, "limit", 50, 1, 1000)
        try:
            before = float(request.query["before"]) if request.query.get("before") else None
        except ValueError as e:
            raise web.HTTPBadRequest(text="before must be a number") from e
        return web.json_response(await asyncio.to_thread(self.database.events, limit, self._miner_arg(request), before))

    async def _entities(self, request: web.Request) -> web.Response:
        """The entities each miner has in Home Assistant, for the Info page."""
        from .mqtt import discovery_messages, totals_discovery

        prefix, base = self.config.mqtt_discovery_prefix, self.config.mqtt_base_topic
        firmware = self.mqtt.firmware if self.mqtt else None
        out = {"Lottery": [_entity_row(topic, payload) for topic, payload in totals_discovery(prefix, base).items()]}
        for miner in self.miners.values():
            if miner.info or miner.snapshot:
                out[miner.name] = [
                    _entity_row(topic, payload)
                    for topic, payload in discovery_messages(miner, prefix, base, firmware).items()
                ]
            else:
                out[miner.name] = []
        return web.json_response(out)

    async def _delete_data(self, request: web.Request) -> web.Response:
        """Delete the app's own history. The page asks for DELETE to be typed; so does this."""
        if os.environ.get("SUPERVISOR_TOKEN") and request.remote != INGRESS_ADDRESS:
            raise web.HTTPForbidden(text="Use the sidebar")
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Send JSON")
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict) or body.get("confirm") != "DELETE":
            return web.json_response({"error": "Type DELETE to confirm"}, status=400)
        removed = await asyncio.to_thread(self.database.wipe)
        for miner in self.miners.values():
            miner.reset_counters()
        logger.warning(f"The app's history was deleted: {removed}")
        return web.json_response({"ok": True, "removed": removed})

    async def _backup(self, request: web.Request) -> web.StreamResponse:
        handle, name = tempfile.mkstemp(prefix="backup-", suffix=".db", dir=self.database.path.parent)
        os.close(handle)
        path = Path(name)
        try:
            await asyncio.to_thread(self.database.backup_to, path)
            stamp = time.strftime("%Y%m%d-%H%M")
            response = web.StreamResponse(
                headers={
                    "Content-Type": "application/vnd.sqlite3",
                    "Content-Length": str(path.stat().st_size),
                    "Content-Disposition": f'attachment; filename="lottery-app-{stamp}.db"',
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


def _entity_row(topic: str, payload: dict) -> dict:
    return {
        "platform": topic.split("/")[-4],
        "name": payload.get("name"),
        "unit": payload.get("unit_of_measurement"),
        "category": payload.get("entity_category"),
        "command": payload.get("command_topic"),
    }
