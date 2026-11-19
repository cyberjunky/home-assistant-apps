"""The dashboard and its JSON API, served through Home Assistant's ingress."""

import logging
import os
import time
from pathlib import Path

from aiohttp import web

from . import payload
from .config import APP_VERSION, DATA_DIR
from .donate import DonatePrompt
from .engine import Engine
from .sources import REPOSITORY, SourceError

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
INGRESS_ADDRESS = "172.30.32.2"


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


class WebUI:
    def __init__(self, port: int, engine: Engine):
        self.port = port
        self.engine = engine
        self.app = web.Application(middlewares=[self._guard], client_max_size=65536)
        self.app.router.add_get("/", self._index)
        self.app.router.add_get("/api/report", self._report)
        self.app.router.add_post("/api/scan", self._scan)
        self.app.router.add_post("/api/check", self._check)
        self.app.router.add_delete("/api/check/{owner}/{name}", self._forget)
        self.app.router.add_get("/api/export", self._export)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        self.app.router.add_static("/static", WEB_DIR)

    @web.middleware
    async def _guard(self, request: web.Request, handler):
        # Under Home Assistant only its signed-in ingress proxy may reach the dashboard and its API.
        # Other apps on Home Assistant's network get nothing.
        if os.environ.get("SUPERVISOR_TOKEN") and request.remote != INGRESS_ADDRESS:
            return _error("use the Home Assistant sidebar", 403)
        return await handler(request)

    async def start(self) -> None:
        runner = web.AppRunner(self.app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info("Dashboard on port %s", self.port)

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    async def _report(self, request: web.Request) -> web.Response:
        return web.json_response(self.engine.payload())

    async def _scan(self, request: web.Request) -> web.Response:
        self.engine.wake()
        return web.json_response({"queued": True})

    async def _check(self, request: web.Request) -> web.Response:
        try:
            body = await request.json()
        except ValueError:
            return _error("invalid JSON")
        repository = str((body if isinstance(body, dict) else {}).get("repository") or "").strip()
        repository = repository.removeprefix("https://github.com/").removesuffix(".git").strip("/")
        if not REPOSITORY.match(repository):
            return _error("write the repository as owner/name, as on GitHub")
        try:
            report = await self.engine.check(repository)
        except SourceError as e:
            return _error(str(e), 502)
        return web.json_response({"key": report["key"]})

    async def _forget(self, request: web.Request) -> web.Response:
        repository = f"{request.match_info['owner']}/{request.match_info['name']}"
        if not await self.engine.forget(repository):
            return _error("not found", 404)
        return web.json_response({"ok": True})

    async def _export(self, request: web.Request) -> web.Response:
        page = payload.render_static(self.engine.payload())
        stamp = time.strftime("%Y%m%d-%H%M")
        return web.Response(
            text=page,
            content_type="text/html",
            headers={"Content-Disposition": f'attachment; filename="marshal-{stamp}.html"'},
        )
