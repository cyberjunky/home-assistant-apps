"""The dashboard, served through Home Assistant's ingress.

The browser only talks to this app; the bots' addresses and passwords stay here. Changes to a bot
(start, stop, pause, reload, force exit) only come from Home Assistant's signed-in ingress proxy.
"""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from aiohttp import web

from .bot import BotClient, FreqtradeError
from .config import APP_VERSION, DATA_DIR, Config
from .database import Database
from .donate import DonatePrompt

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy
INGRESS_ADDRESS = "172.30.32.2"
MAX_RANGE_HOURS = 24 * 3660
CONTROL_ACTIONS = ("start", "stop", "pause", "reload_config")


def public_config(config: Config) -> dict:
    """The options to show. Passwords and tokens stay out; only whether there is a ws_token."""
    return {
        "poll_seconds": config.poll_seconds,
        "read_only": config.read_only,
        "mqtt_enabled": config.mqtt_enabled,
        "mqtt_base_topic": config.mqtt_base_topic,
        "mqtt_discovery_prefix": config.mqtt_discovery_prefix,
        "log_level": config.log_level,
        "bots": [
            {
                "name": bot.name,
                "slug": bot.slug,
                "url": bot.url,
                "username": bot.username,
                "ws_token": bool(bot.ws_token),
                "verify_ssl": bot.verify_ssl,
            }
            for bot in config.bots
        ],
    }


class WebUI:
    """The dashboard's page and its JSON API."""

    def __init__(self, port: int, config: Config, database: Database, bots: list[BotClient], mqtt=None):
        self.port = port
        self.config = config
        self.database = database
        self.bots = {bot.slug: bot for bot in bots}
        self.mqtt = mqtt
        # A backup cut short by a crash must not stay behind in the app's storage
        for stale in database.path.parent.glob("backup-*.db"):
            stale.unlink(missing_ok=True)
        self.app = web.Application()
        self.app.router.add_get("/", self._index)
        self.app.router.add_get("/api/status", self._status)
        self.app.router.add_get("/api/bot/{slug}", self._bot)
        self.app.router.add_get("/api/bot/{slug}/trades", self._trades)
        self.app.router.add_get("/api/bot/{slug}/logs", self._logs)
        self.app.router.add_get("/api/bot/{slug}/candles", self._candles)
        self.app.router.add_post("/api/bot/{slug}/control", self._control)
        self.app.router.add_post("/api/bot/{slug}/forceexit", self._force_exit)
        self.app.router.add_get("/api/history", self._history)
        self.app.router.add_get("/api/events", self._events)
        self.app.router.add_get("/api/backup", self._backup)
        self.app.router.add_post("/api/data/delete", self._delete_data)
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

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(
            text=page,
            content_type="text/html",
            # Never let the browser serve a stale cached page after an update
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    def _get_bot(self, request: web.Request) -> BotClient:
        bot = self.bots.get(request.match_info["slug"])
        if bot is None:
            raise web.HTTPNotFound(text="no such bot")
        return bot

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "version": APP_VERSION,
                "now": time.time(),
                "config": public_config(self.config),
                "bots": [bot.summary() for bot in self.bots.values()],
                "mqtt": {
                    "connected": bool(self.mqtt and self.mqtt.connected),
                    "broker": self.mqtt.broker if self.mqtt else "",
                },
                "database": self.database.counts(),
            }
        )

    async def _bot(self, request: web.Request) -> web.Response:
        return web.json_response(self._get_bot(request).details())

    async def _trades(self, request: web.Request) -> web.Response:
        """The closed trades the app has read, oldest first, for the charts and the trades table."""
        bot = self._get_bot(request)
        return web.json_response({"total": bot.trades_total, "trades": bot.trades})

    async def _logs(self, request: web.Request) -> web.Response:
        bot = self._get_bot(request)
        try:
            limit = min(max(10, int(request.query.get("limit", "200"))), 1000)
        except ValueError as e:
            raise web.HTTPBadRequest(text="limit must be a number") from e
        try:
            return web.json_response({"logs": await bot.logs(limit)})
        except FreqtradeError as e:
            return web.json_response({"error": str(e)}, status=502)

    async def _candles(self, request: web.Request) -> web.Response:
        """One pair's candles as the strategy analysed them, for the Chart section."""
        bot = self._get_bot(request)
        pair = request.query.get("pair", "")
        timeframe = request.query.get("timeframe") or (bot.data.get("config") or {}).get("timeframe") or ""
        if not pair or not timeframe:
            raise web.HTTPBadRequest(text="pair and timeframe are needed")
        try:
            limit = min(max(20, int(request.query.get("limit", "250"))), 1500)
        except ValueError as e:
            raise web.HTTPBadRequest(text="limit must be a number") from e
        extra = [c for c in request.query.get("columns", "").split(",") if c][:20]
        try:
            return web.json_response(await bot.candles(pair, timeframe, limit, extra))
        except FreqtradeError as e:
            return web.json_response({"error": str(e)}, status=502)

    async def _history(self, request: web.Request) -> web.Response:
        try:
            hours = min(max(1, int(request.query.get("hours", "168"))), MAX_RANGE_HOURS)
        except ValueError as e:
            raise web.HTTPBadRequest(text="hours must be a number") from e
        end = int(time.time())
        result = await asyncio.to_thread(self.database.history, end - hours * 3600, end)
        return web.json_response(result)

    async def _events(self, request: web.Request) -> web.Response:
        bot = request.query.get("bot") or None
        if bot and bot not in self.bots:
            raise web.HTTPNotFound(text="no such bot")
        try:
            limit = min(max(1, int(request.query.get("limit", "100"))), 1000)
            before = float(request.query["before"]) if request.query.get("before") else None
        except ValueError as e:
            raise web.HTTPBadRequest(text="limit and before must be numbers") from e
        return web.json_response(await asyncio.to_thread(self.database.events, bot, limit, before))

    # ------------------------------------------------------------------
    # Changes: only from Home Assistant's signed-in ingress, as JSON
    # ------------------------------------------------------------------
    @staticmethod
    async def _change_body(request: web.Request) -> dict:
        # Under Home Assistant only its signed-in ingress proxy may do this, not other apps on the network
        if os.environ.get("SUPERVISOR_TOKEN") and request.remote != INGRESS_ADDRESS:
            raise web.HTTPForbidden(text="Use the sidebar")
        # Another site's page can't send this content type without the browser asking first
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Send JSON")
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Send a JSON object")
        return body

    async def _control(self, request: web.Request) -> web.Response:
        """Start, stop, pause or reload a bot, from its page's buttons."""
        if self.config.read_only:
            return web.json_response({"error": "This app is set to read-only"}, status=403)
        bot = self._get_bot(request)
        action = (await self._change_body(request)).get("action")
        if action not in CONTROL_ACTIONS:
            return web.json_response({"error": "Unknown action"}, status=400)
        try:
            result = await bot.control(action)
        except FreqtradeError as e:
            logger.warning(f"{bot.name}: {action} failed: {e}")
            return web.json_response({"error": f"The bot refused: {e}"}, status=502)
        logger.info(f"{bot.name}: {action} from the dashboard: {result}")
        return web.json_response({"ok": True, "result": result})

    async def _force_exit(self, request: web.Request) -> web.Response:
        """Close one open trade at the market, from the open trades table."""
        if self.config.read_only:
            return web.json_response({"error": "This app is set to read-only"}, status=403)
        bot = self._get_bot(request)
        body = await self._change_body(request)
        try:
            trade_id = int(body.get("trade_id"))
            result = await bot.force_exit(trade_id)
        except (TypeError, ValueError) as e:
            return web.json_response({"error": str(e) or "trade_id must be a number"}, status=400)
        except FreqtradeError as e:
            logger.warning(f"{bot.name}: force exit of trade {body.get('trade_id')} failed: {e}")
            return web.json_response({"error": f"The bot refused: {e}"}, status=502)
        logger.info(f"{bot.name}: force exit of trade {trade_id} from the dashboard: {result}")
        return web.json_response({"ok": True, "result": result})

    async def _delete_data(self, request: web.Request) -> web.Response:
        """The danger zone: delete the app's own history. The page asks for DELETE to be typed; so does this."""
        body = await self._change_body(request)
        if body.get("confirm") != "DELETE":
            return web.json_response({"error": "Type DELETE to confirm"}, status=400)
        removed = await asyncio.to_thread(self.database.wipe)
        logger.warning(f"The app's history was deleted: {removed}")
        return web.json_response({"ok": True, "removed": removed})

    async def _backup(self, request: web.Request) -> web.StreamResponse:
        """Download a copy of the app's database. The options and passwords aren't in it."""
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
                    "Content-Disposition": f'attachment; filename="freqtrade-app-{stamp}.db"',
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
