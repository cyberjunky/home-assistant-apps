"""The dashboard, served through Home Assistant's ingress.

The browser only talks to this app; the node's addresses, the RPC password and the macaroon stay
here. The app only reads from the node. What can be changed is the app's own history.
"""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from aiohttp import web

from .config import APP_VERSION, DATA_DIR
from .database import Database
from .donate import DonatePrompt
from .node import Node

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy
INGRESS_ADDRESS = "172.30.32.2"
RANGES = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "90d": 90 * 86400}


class WebUI:
    """The dashboard's page and its JSON API."""

    def __init__(self, port: int, node: Node, database: Database, mqtt=None):
        self.port = port
        self.node = node
        self.database = database
        self.mqtt = mqtt
        # A backup cut short by a crash must not stay behind in the app's storage
        for stale in database.path.parent.glob("backup-*.db"):
            stale.unlink(missing_ok=True)
        self.app = web.Application()
        self.app.router.add_get("/", self._index)
        self.app.router.add_get("/api/status", self._status)
        self.app.router.add_get("/api/history", self._history)
        self.app.router.add_get("/api/blocks", self._blocks)
        self.app.router.add_get("/api/peers", self._peers)
        self.app.router.add_get("/api/lightning", self._lightning)
        self.app.router.add_get("/api/tor", self._tor)
        self.app.router.add_get("/api/pool", self._pool)
        self.app.router.add_get("/api/events", self._events)
        self.app.router.add_get("/api/backup", self._backup)
        self.app.router.add_get("/api/channel-backup", self._channel_backup)
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

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(await asyncio.to_thread(self.node.status, self.mqtt))

    async def _history(self, request: web.Request) -> web.Response:
        key = request.query.get("range", "24h")
        if key not in RANGES:
            raise web.HTTPBadRequest(text="range must be one of " + ", ".join(RANGES))
        end = int(time.time())
        result = await asyncio.to_thread(self.database.history, end - RANGES[key], end)
        return web.json_response({"range": key, **result})

    async def _blocks(self, request: web.Request) -> web.Response:
        return web.json_response({"blocks": self.node.bitcoin.blocks_view()})

    async def _peers(self, request: web.Request) -> web.Response:
        return web.json_response({"peers": self.node.bitcoin.peers})

    async def _lightning(self, request: web.Request) -> web.Response:
        return web.json_response(self.node.lightning.details(time.time(), self.node.bitcoin.height))

    async def _tor(self, request: web.Request) -> web.Response:
        return web.json_response({"circuits": self.node.tor.circuits})

    async def _pool(self, request: web.Request) -> web.Response:
        return web.json_response(self.node.pool.details())

    async def _events(self, request: web.Request) -> web.Response:
        try:
            limit = min(max(1, int(request.query.get("limit", "60"))), 500)
            before = float(request.query["before"]) if request.query.get("before") else None
        except ValueError as e:
            raise web.HTTPBadRequest(text="limit and before must be numbers") from e
        return web.json_response({"events": await asyncio.to_thread(self.database.events, limit, before)})

    # ------------------------------------------------------------------
    # Changes: only from Home Assistant's signed-in ingress, as JSON
    # ------------------------------------------------------------------
    @staticmethod
    def _from_ingress(request: web.Request) -> None:
        # Under Home Assistant only its signed-in ingress proxy may do this, not other apps on the network
        if os.environ.get("SUPERVISOR_TOKEN") and request.remote != INGRESS_ADDRESS:
            raise web.HTTPForbidden(text="Use the sidebar")

    async def _change_body(self, request: web.Request) -> dict:
        self._from_ingress(request)
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

    async def _delete_data(self, request: web.Request) -> web.Response:
        """The danger zone: delete the app's own history. The page asks for DELETE to be typed; so does this."""
        body = await self._change_body(request)
        if body.get("confirm") != "DELETE":
            return web.json_response({"error": "Type DELETE to confirm"}, status=400)
        removed = await asyncio.to_thread(self.database.wipe)
        logger.warning(f"The app's history was deleted: {removed}")
        return web.json_response({"ok": True, "removed": removed})

    async def _backup(self, request: web.Request) -> web.StreamResponse:
        """Download a copy of the app's database. The options, passwords and the macaroon aren't in it."""
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
                    "Content-Disposition": f'attachment; filename="bitcoin-node-app-{stamp}.db"',
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

    async def _channel_backup(self, request: web.Request) -> web.Response:
        """The latest stored channel backup of LND. It is encrypted with the wallet's seed, and only of use with it."""
        self._from_ingress(request)
        path = self.node.lightning.latest_backup()
        if path is None:
            raise web.HTTPNotFound(text="No channel backup stored yet")
        data = await asyncio.to_thread(path.read_bytes)
        return web.Response(
            body=data,
            content_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{path.name}"'},
        )
