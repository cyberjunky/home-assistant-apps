"""The pages, served through Home Assistant's ingress.

The browser only talks to this app; the app talks to Home Assistant with the token every app gets.
Everything that changes something (a repair, a cleanup, ignoring a finding) is only accepted from
Home Assistant's signed-in ingress, as JSON, and a repair that removes something only with its
word typed.
"""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from aiohttp import web

from . import checks
from .config import APP_VERSION, DATA_DIR, Config
from .database import Database
from .donate import DonatePrompt
from .engine import Engine

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy
INGRESS_ADDRESS = "172.30.32.2"
CLEANUP_WORD = "REMOVE"


class WebUI:
    """The pages and their JSON API."""

    def __init__(self, port: int, config: Config, engine: Engine, database: Database):
        self.port = port
        self.config = config
        self.engine = engine
        self.database = database
        # A backup cut short by a crash must not stay behind in the app's storage
        for stale in database.path.parent.glob("backup-*.db"):
            stale.unlink(missing_ok=True)
        self.app = web.Application()
        add = self.app.router.add_get
        add("/", self._index)
        add("/api/status", self._status)
        add("/api/findings", self._findings)
        add("/api/log", self._log)
        add("/api/trend", self._trend)
        add("/api/actions", self._actions)
        add("/api/search", self._search)
        add("/api/inspect", self._inspect)
        add("/api/backup", self._backup)
        post = self.app.router.add_post
        post("/api/scan", self._scan)
        post("/api/repair", self._repair)
        post("/api/cleanup", self._cleanup)
        post("/api/ignore", self._ignore)
        post("/api/data/delete", self._delete_data)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        self.app.router.add_static("/static", WEB_DIR)

    async def start(self) -> None:
        """Serve until cancelled."""
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info(f"Pages on port {self.port}")
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
        return web.json_response(
            {
                "version": APP_VERSION,
                "now": time.time(),
                "config": {
                    "scan_minutes": self.config.scan_minutes,
                    "check_config": self.config.check_config,
                    "allow_fixes": self.config.allow_fixes,
                    "deep_scan": self.config.deep_scan,
                    "log_level": self.config.log_level,
                },
                "categories": checks.CATEGORIES,
                **self.engine.summary(),
                "database": await asyncio.to_thread(self.database.counts),
            }
        )

    async def _findings(self, request: web.Request) -> web.Response:
        return web.json_response({"findings": self.engine.listed()})

    async def _log(self, request: web.Request) -> web.Response:
        return web.json_response({"entries": self.engine.log})

    async def _trend(self, request: web.Request) -> web.Response:
        try:
            days = min(max(1, int(request.query.get("days", "30"))), 365)
        except ValueError as e:
            raise web.HTTPBadRequest(text="days must be a number") from e
        return web.json_response(
            {"days": days, **await asyncio.to_thread(self.database.trend, time.time() - days * 86400)}
        )

    async def _actions(self, request: web.Request) -> web.Response:
        return web.json_response({"actions": await asyncio.to_thread(self.database.actions)})

    async def _search(self, request: web.Request) -> web.Response:
        return web.json_response({"results": self.engine.search(request.query.get("q", ""))})

    async def _inspect(self, request: web.Request) -> web.Response:
        try:
            return web.json_response(self.engine.inspect(request.query.get("type", ""), request.query.get("id", "")))
        except LookupError as e:
            return web.json_response({"error": str(e)}, status=404)

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

    @staticmethod
    async def _answer(work) -> web.Response:
        """A repair's result, or why it was not run."""
        try:
            return web.json_response(await work)
        except PermissionError as e:
            return web.json_response({"error": str(e)}, status=403)
        except LookupError as e:
            return web.json_response({"error": str(e)}, status=404)

    async def _scan(self, request: web.Request) -> web.Response:
        await self._change_body(request)
        self.engine.scan_soon()
        return web.json_response({"ok": True})

    async def _repair(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        finding_id, fix = str(body.get("finding") or ""), str(body.get("fix") or "")
        try:
            index = int(body.get("index", -1))
            _, offered = self.engine.offered(finding_id, index, fix)
        except (TypeError, ValueError):
            return web.json_response({"error": "index must be a number"}, status=400)
        except LookupError as e:
            return web.json_response({"error": str(e)}, status=404)
        # What removes something asks for its word to be typed; so does this
        if offered.get("word") and body.get("word") != offered["word"]:
            return web.json_response({"error": f"Type {offered['word']} to confirm"}, status=400)
        return await self._answer(self.engine.repair(finding_id, index, fix))

    async def _cleanup(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        if body.get("word") != CLEANUP_WORD:
            return web.json_response({"error": f"Type {CLEANUP_WORD} to confirm"}, status=400)
        return await self._answer(self.engine.cleanup(str(body.get("type") or ""), str(body.get("id") or "")))

    async def _ignore(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        try:
            await asyncio.to_thread(self.engine.ignore, str(body.get("finding") or ""), bool(body.get("ignored", True)))
        except LookupError as e:
            return web.json_response({"error": str(e)}, status=404)
        return web.json_response({"ok": True})

    async def _delete_data(self, request: web.Request) -> web.Response:
        """The danger zone: delete the app's own history. The page asks for DELETE to be typed; so does this."""
        body = await self._change_body(request)
        if body.get("confirm") != "DELETE":
            return web.json_response({"error": "Type DELETE to confirm"}, status=400)
        removed = await asyncio.to_thread(self.database.wipe)
        logger.warning(f"The app's history was deleted: {removed}")
        return web.json_response({"ok": True, "removed": removed})

    async def _backup(self, request: web.Request) -> web.StreamResponse:
        """Download a copy of the app's database."""
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
                    "Content-Disposition": f'attachment; filename="repair-app-{stamp}.db"',
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
