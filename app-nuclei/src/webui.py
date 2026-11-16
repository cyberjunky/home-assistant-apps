"""The dashboard and its JSON API, served through Home Assistant's ingress."""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from aiohttp import web

from .config import APP_VERSION, DATA_DIR
from .custom_templates import CustomTemplates, TemplateError
from .database import Database, backup_copy, database_report
from .donate import DonatePrompt
from .engine import Engine, NotScannable
from .freshness import Freshness
from .scheduler import Scheduler

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
INGRESS_ADDRESS = "172.30.32.2"


async def _json_body(request: web.Request) -> dict:
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text="send JSON")
    try:
        body = await request.json()
    except ValueError as e:
        raise web.HTTPBadRequest(text="invalid JSON") from e
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text="expected an object")
    return body


class WebUI:
    def __init__(self, port: int, database: Database, engine: Engine, scheduler: Scheduler, freshness: Freshness):
        self.port = port
        self.database = database
        self.engine = engine
        self.scheduler = scheduler
        self.freshness = freshness
        self.config = engine.config
        self.templates = CustomTemplates()
        self._tasks: set[asyncio.Task] = set()
        for stale in database.path.parent.glob("backup-*.db"):
            stale.unlink(missing_ok=True)
        self.app = web.Application(middlewares=[self._guard])
        add = self.app.router.add_get
        post = self.app.router.add_post
        patch = self.app.router.add_patch
        add("/", self._index)
        add("/api/overview", self._overview)
        add("/api/issues", self._issues)
        add("/api/issues/{id:[0-9]+}", self._issue)
        patch("/api/issues/{id:[0-9]+}", self._update_issue)
        post("/api/issues/acknowledge", self._acknowledge_all)
        add("/api/devices", self._devices)
        patch("/api/devices/{id:[0-9]+}", self._update_device)
        post("/api/devices/{id:[0-9]+}/scan", self._scan_device)
        add("/api/scans", self._scans)
        add("/api/scanner", self._scanner)
        post("/api/scanner/update", self._update_templates)
        add("/api/templates", self._list_templates)
        add("/api/templates/{name}", self._get_template)
        self.app.router.add_put("/api/templates/{name}", self._save_template)
        self.app.router.add_delete("/api/templates/{name}", self._delete_template)
        add("/api/backup", self._backup)
        add("/api/database", self._database)
        post("/api/wipe", self._wipe)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        self.app.router.add_static("/static", WEB_DIR)

    @web.middleware
    async def _guard(self, request: web.Request, handler):
        # Under Home Assistant only its signed-in ingress proxy may reach the API, not other hosts on
        # the network (this app runs on the host network).
        changing = request.method in ("POST", "PUT", "PATCH", "DELETE")
        if changing and os.environ.get("SUPERVISOR_TOKEN") and request.remote != INGRESS_ADDRESS:
            return web.json_response({"error": "use the Home Assistant sidebar"}, status=403)
        return await handler(request)

    async def start(self) -> None:
        runner = web.AppRunner(self.app)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info("Dashboard on port %s", self.port)

    def _background(self, coro) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    async def _overview(self, request: web.Request) -> web.Response:
        running = self.database.running_scan()
        return web.json_response(
            {
                "version": APP_VERSION,
                "summary": self.database.summary(self.config.rescan_days),
                "history": self.database.history(30),
                "running": running,
                "queue": [{"id": d["id"], "name": d["name"], "reason": d["reason"]} for d in self.scheduler.queue],
                "home_assistant": {"error": self.scheduler.ha_error, "last_sync": self.scheduler.last_sync},
                "updating_templates": self.engine.updating_templates,
                "freshness": await self.freshness.report(),
                "config": self.config.public(),
            }
        )

    # -- issues -------------------------------------------------------------
    async def _issues(self, request: web.Request) -> web.Response:
        state = request.query.get("state") or "open"
        device = request.query.get("device")
        issues = self.database.issues(state, int(device) if device and device.isdigit() else None)
        return web.json_response({"issues": issues})

    async def _issue(self, request: web.Request) -> web.Response:
        issue = self.database.issue(int(request.match_info["id"]))
        return web.json_response(issue) if issue else web.json_response({"error": "not found"}, status=404)

    async def _update_issue(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        if body.get("state") not in ("open", "acknowledged"):
            return web.json_response({"error": "state is open or acknowledged"}, status=400)
        issue = self.database.set_issue_state(int(request.match_info["id"]), body["state"])
        return web.json_response(issue) if issue else web.json_response({"error": "not found"}, status=404)

    async def _acknowledge_all(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        severity = body.get("severity") or None
        return web.json_response({"acknowledged": self.database.acknowledge_all(severity)})

    # -- devices and scans --------------------------------------------------
    async def _devices(self, request: web.Request) -> web.Response:
        queued = {d["id"] for d in self.scheduler.queue}
        running = self.database.running_scan()
        devices = self.database.devices()
        for d in devices:
            d["queued"] = d["id"] in queued
            d["scanning"] = bool(running and running["device_id"] == d["id"])
            d["next_scan_at"] = d["last_scan_at"] + self.config.rescan_days * 86400 if d["last_scan_at"] else None
        return web.json_response({"devices": devices, "rescan_days": self.config.rescan_days})

    async def _update_device(self, request: web.Request) -> web.Response:
        device_id = int(request.match_info["id"])
        if not self.database.device(device_id):
            return web.json_response({"error": "not found"}, status=404)
        body = await _json_body(request)
        self.database.set_device(device_id, {"excluded": body.get("excluded")} if "excluded" in body else {})
        self.scheduler.wake()
        return web.json_response(self.database.device(device_id))

    async def _scan_device(self, request: web.Request) -> web.Response:
        device = self.database.device(int(request.match_info["id"]))
        try:
            self.engine.check(device)
        except NotScannable as e:
            return web.json_response({"error": str(e)}, status=403)
        if not device["online"]:
            return web.json_response({"error": "the device is not home; it is scanned once it is back"}, status=409)
        self.database.set_device(device["id"], {"scan_requested": True})
        self.scheduler.wake()
        return web.json_response({"queued": True})

    async def _scans(self, request: web.Request) -> web.Response:
        return web.json_response({"scans": self.database.scans()})

    # -- scanner ------------------------------------------------------------
    async def _scanner(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"freshness": await self.freshness.report(), "updating": self.engine.updating_templates}
        )

    async def _update_templates(self, request: web.Request) -> web.Response:
        if self.engine.updating_templates:
            return web.json_response({"error": "an update is already running"}, status=409)

        async def update() -> None:
            try:
                await self.engine.update_templates(force=True)
            except Exception as e:
                logger.warning("Template update failed: %s", e)
            self.freshness.invalidate()

        self._background(update())
        return web.json_response({"started": True})

    # -- custom templates ---------------------------------------------------
    async def _list_templates(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "templates": self.templates.list(),
            }
        )

    async def _get_template(self, request: web.Request) -> web.Response:
        try:
            return web.json_response(
                {"name": request.match_info["name"], "body": self.templates.read(request.match_info["name"])}
            )
        except (TemplateError, OSError):
            return web.json_response({"error": "not found"}, status=404)

    async def _save_template(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        try:
            result = await self.templates.save(request.match_info["name"], str(body.get("body", "")))
            return web.json_response(result)
        except TemplateError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _delete_template(self, request: web.Request) -> web.Response:
        try:
            self.templates.delete(request.match_info["name"])
        except TemplateError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response({"ok": True})

    # -- database -----------------------------------------------------------
    async def _backup(self, request: web.Request) -> web.StreamResponse:
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
                    "Content-Disposition": f'attachment; filename="nuclei-{stamp}.db"',
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
        report = await asyncio.to_thread(database_report, self.database.path)
        return web.json_response(report)

    async def _wipe(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        if body.get("confirm") != "delete":
            return web.json_response({"error": "type delete to confirm"}, status=400)
        self.database.wipe()
        return web.json_response({"ok": True})
