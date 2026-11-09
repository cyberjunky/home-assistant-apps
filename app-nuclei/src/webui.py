"""The dashboard and its JSON API, served through Home Assistant's ingress."""

import asyncio
import logging
import os
import tempfile
import time
from pathlib import Path

from aiohttp import web

from . import targets
from .config import APP_VERSION, DATA_DIR
from .custom_templates import CustomTemplates, TemplateError
from .database import TICKET_STATUSES, Database, backup_copy, database_report
from .donate import DonatePrompt
from .engine import Engine, NotAuthorised

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
    def __init__(self, port: int, database: Database, engine: Engine):
        self.port = port
        self.database = database
        self.engine = engine
        self.config = engine.config
        self.templates = CustomTemplates()
        self._tasks: set[asyncio.Task] = set()
        for stale in database.path.parent.glob("backup-*.db"):
            stale.unlink(missing_ok=True)
        self.app = web.Application(middlewares=[self._guard])
        add = self.app.router.add_get
        post = self.app.router.add_post
        add("/", self._index)
        add("/api/status", self._status)
        add("/api/objects", self._objects)
        post("/api/objects", self._add_object)
        post("/api/objects/preview", self._preview_object)
        self.app.router.add_patch("/api/objects/{id:[0-9]+}", self._update_object)
        self.app.router.add_delete("/api/objects/{id:[0-9]+}", self._delete_object)
        post("/api/objects/{id:[0-9]+}/scan", self._scan_object)
        add("/api/scans", self._scans)
        add("/api/tickets", self._tickets)
        add("/api/tickets/{id:[0-9]+}", self._ticket)
        self.app.router.add_patch("/api/tickets/{id:[0-9]+}", self._update_ticket)
        add("/api/devices", self._devices)
        self.app.router.add_patch("/api/devices/{id:[0-9]+}", self._update_device)
        self.app.router.add_delete("/api/devices/{id:[0-9]+}", self._delete_device)
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

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    async def _status(self, request: web.Request) -> web.Response:
        from .scanner import Scanner

        return web.json_response(
            {
                "version": APP_VERSION,
                "summary": self.database.summary(),
                "running": self.engine.running,
                "scanners": Scanner.available(),
                "templates_updated_at": self.engine.templates_updated_at,
                "config": self.config.public(),
                "database": {"size": self.database.size_bytes()},
            }
        )

    # -- objects ------------------------------------------------------------
    async def _objects(self, request: web.Request) -> web.Response:
        return web.json_response({"objects": self.database.objects()})

    async def _preview_object(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        target = str(body.get("target", ""))
        try:
            kind = targets.classify(target)
            normalised = targets.normalise(target)
            preview = {"target": normalised, "kind": kind, "private": targets.is_private(target)}
            if kind in ("ip", "cidr"):
                hosts = targets.hosts_of(target)
                preview["host_count"] = len(hosts)
                preview["hosts"] = hosts[:256]
            return web.json_response(preview)
        except targets.TargetError as e:
            return web.json_response({"error": str(e)}, status=400)

    async def _add_object(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        try:
            target = targets.normalise(str(body.get("target", "")))
            kind = targets.classify(target)
        except targets.TargetError as e:
            return web.json_response({"error": str(e)}, status=400)
        if self.database.object_by_target(target):
            return web.json_response({"error": "already registered"}, status=409)
        authorised = bool(body.get("authorised"))
        schedule = body.get("schedule") or None
        if schedule not in (None, "", "daily", "weekly", "monthly"):
            return web.json_response({"error": "unknown schedule"}, status=400)
        object_id = self.database.add_object(target, kind, str(body.get("name") or "").strip(), schedule, authorised)
        return web.json_response(self.database.object(object_id))

    async def _update_object(self, request: web.Request) -> web.Response:
        object_id = int(request.match_info["id"])
        if not self.database.object(object_id):
            return web.json_response({"error": "not found"}, status=404)
        body = await _json_body(request)
        self.database.update_object(object_id, body)
        return web.json_response(self.database.object(object_id))

    async def _delete_object(self, request: web.Request) -> web.Response:
        self.database.delete_object(int(request.match_info["id"]))
        return web.json_response({"ok": True})

    async def _scan_object(self, request: web.Request) -> web.Response:
        object_id = int(request.match_info["id"])
        # The ownership gate is checked now, so a refusal reaches the person instead of dying in a task
        try:
            self.engine.check_can_scan(object_id)
        except NotAuthorised as e:
            return web.json_response({"error": str(e)}, status=403)
        task = asyncio.create_task(self._background_scan(object_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return web.json_response({"started": True})

    async def _background_scan(self, object_id: int) -> None:
        try:
            await self.engine.scan_object(object_id, "manual")
        except NotAuthorised as e:
            logger.warning("Scan refused: %s", e)

    # -- scans & tickets ----------------------------------------------------
    async def _scans(self, request: web.Request) -> web.Response:
        return web.json_response({"scans": self.database.scans()})

    async def _tickets(self, request: web.Request) -> web.Response:
        status = request.query.get("status") or "open"
        object_id = request.query.get("object")
        return web.json_response({"tickets": self.database.findings(status, int(object_id) if object_id else None)})

    async def _ticket(self, request: web.Request) -> web.Response:
        finding = self.database.finding(int(request.match_info["id"]))
        if not finding:
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(finding)

    async def _update_ticket(self, request: web.Request) -> web.Response:
        finding_id = int(request.match_info["id"])
        body = await _json_body(request)
        if "status" in body:
            if body["status"] not in TICKET_STATUSES:
                return web.json_response({"error": "unknown status"}, status=400)
            self.database.set_ticket_status(finding_id, body["status"])
        if "note" in body:
            self.database.set_ticket_note(finding_id, str(body["note"])[:4000])
        finding = self.database.finding(finding_id)
        return web.json_response(finding) if finding else web.json_response({"error": "not found"}, status=404)

    # -- devices ------------------------------------------------------------
    async def _devices(self, request: web.Request) -> web.Response:
        return web.json_response({"devices": self.database.devices()})

    async def _update_device(self, request: web.Request) -> web.Response:
        device_id = int(request.match_info["id"])
        if not self.database.device(device_id):
            return web.json_response({"error": "not found"}, status=404)
        body = await _json_body(request)
        return web.json_response(self.database.update_device(device_id, body))

    async def _delete_device(self, request: web.Request) -> web.Response:
        self.database.delete_device(int(request.match_info["id"]))
        return web.json_response({"ok": True})

    # -- custom templates ---------------------------------------------------
    async def _list_templates(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "templates": self.templates.list(),
                "community_count": self.engine.scanner.template_count(),
                "community_updated_at": self.engine.scanner.templates_updated_at(),
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
