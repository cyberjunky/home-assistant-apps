"""The pages and their API, served through Home Assistant's ingress.

Live things (the Flipper's screen, the console, a build's output) go over one WebSocket; the rest is JSON.
"""

import asyncio
import contextlib
import json
import logging
import os
import re
from collections import deque
from pathlib import Path

from aiohttp import WSMsgType, web

from . import templates
from .config import APP_VERSION, DATA_DIR
from .donate import DonatePrompt
from .link import Flipper, FlipperError
from .workspace import CHANNELS, Workspace, WorkspaceError

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
INGRESS_ADDRESS = "172.30.32.2"
MAX_UPLOAD = 32 * 1024 * 1024
MAX_DOWNLOAD = 16 * 1024 * 1024
DEVICE_PATH = re.compile(r"/(ext|int)(/[^/\x00-\x1f]{1,100})*")


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _device_path(value: object) -> str:
    """A path on the Flipper: on its SD card (/ext) or its internal storage (/int)."""
    path = str(value or "")
    if len(path) > 255 or not DEVICE_PATH.fullmatch(path) or ".." in path.split("/"):
        raise WorkspaceError("not a path on the Flipper")
    return path


class _Client:
    """One open page: what waits to be sent to it. Of the screen only the newest picture is kept."""

    def __init__(self, ws: web.WebSocketResponse):
        self.ws = ws
        self.queue: deque[dict] = deque(maxlen=4000)
        self.frame: tuple[bytes, int] | None = None
        self.wake = asyncio.Event()

    def send(self, message: dict) -> None:
        self.queue.append(message)
        self.wake.set()

    def show(self, frame: tuple[bytes, int]) -> None:
        self.frame = frame
        self.wake.set()

    async def pump(self) -> None:
        while True:
            await self.wake.wait()
            self.wake.clear()
            while self.queue:
                await self.ws.send_str(json.dumps(self.queue.popleft()))
            if self.frame is not None:
                (data, orientation), self.frame = self.frame, None
                await self.ws.send_bytes(bytes([orientation & 3]) + data)


class WebUI:
    def __init__(self, port: int, flipper: Flipper, workspace: Workspace):
        self.port = port
        self.flipper = flipper
        self.workspace = workspace
        self.clients: set[_Client] = set()
        self._tasks: set[asyncio.Task] = set()
        flipper.listener = self._from_flipper
        workspace.listener = self._from_workspace

        self.app = web.Application(middlewares=[self._guard], client_max_size=MAX_UPLOAD)
        route = self.app.router
        route.add_get("/", self._index)
        route.add_get("/api/state", self._state)
        route.add_get("/api/ws", self._socket)
        route.add_post("/api/device/{action}", self._device)
        route.add_get("/api/files", self._files)
        route.add_get("/api/files/download", self._download)
        route.add_post("/api/files/upload", self._upload)
        route.add_post("/api/files/{action}", self._file_action)
        route.add_get("/api/projects", self._projects)
        route.add_post("/api/projects", self._create)
        route.add_post("/api/projects/import", self._import)
        route.add_get("/api/projects/{name}", self._project)
        route.add_delete("/api/projects/{name}", self._remove)
        route.add_get("/api/projects/{name}/export", self._export)
        route.add_get("/api/projects/{name}/fap", self._fap)
        route.add_get("/api/projects/{name}/file", self._read)
        route.add_put("/api/projects/{name}/file", self._write)
        route.add_delete("/api/projects/{name}/file", self._delete)
        route.add_post("/api/projects/{name}/{action}", self._project_action)
        route.add_get("/api/build/log", self._log)
        route.add_post("/api/build/cancel", self._cancel)
        route.add_post("/api/sdk", self._sdk_install)
        route.add_delete("/api/sdk", self._sdk_remove)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        route.add_static("/static", WEB_DIR)

    @web.middleware
    async def _guard(self, request: web.Request, handler):
        # Under Home Assistant only its signed-in ingress proxy may reach the pages and the API.
        # Other apps on Home Assistant's network get nothing.
        if os.environ.get("SUPERVISOR_TOKEN") and request.remote != INGRESS_ADDRESS:
            return _error("use the Home Assistant sidebar", 403)
        try:
            return await handler(request)
        except WorkspaceError as e:
            return _error(str(e))
        except FlipperError as e:
            return _error(f"Flipper: {e}", 502)

    async def start(self) -> None:
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info("Dashboard on port %s", self.port)

    # ---- live ----

    def _snapshot(self) -> dict:
        return {
            "t": "state",
            "version": APP_VERSION,
            "flipper": self.flipper.status(),
            "job": self.workspace.job,
            "sdk": self.workspace.sdk(),
        }

    def _broadcast(self, message: dict) -> None:
        for client in self.clients:
            client.send(message)

    def _from_flipper(self, kind: str, payload: object) -> None:
        if kind == "frame":
            for client in self.clients:
                client.show(payload)
        elif kind == "console":
            self._broadcast({"t": "console", "text": payload})
        else:
            self._broadcast(self._snapshot())

    def _from_workspace(self, kind: str, payload: object) -> None:
        if kind == "build":
            self._broadcast({"t": "build", "line": payload})
        else:
            self._broadcast(self._snapshot())

    def _spawn(self, job) -> None:
        """Run something long (a build, an upload) beside the request; how it ends goes to the pages."""

        async def runner() -> None:
            try:
                await job
            except (WorkspaceError, FlipperError) as e:
                self._broadcast({"t": "notice", "error": str(e)})
            except Exception as e:
                logger.exception("A job failed")
                self._broadcast({"t": "notice", "error": f"unexpected error: {e}"})
            self._broadcast(self._snapshot())

        task = asyncio.create_task(runner())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _socket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        client = _Client(ws)
        client.send(self._snapshot())
        if self.flipper.frame is not None:
            client.show(self.flipper.frame)
        self.clients.add(client)
        pump = asyncio.create_task(client.pump())
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(message.data)
                    if data.get("t") == "key":
                        await self.flipper.press(str(data.get("key")), str(data.get("kind")))
                    elif data.get("t") == "console":
                        await self.flipper.console_send(str(data.get("text", ""))[:600])
                except FlipperError as e:
                    client.send({"t": "notice", "error": f"Flipper: {e}"})
                except (ValueError, AttributeError):
                    continue
        finally:
            self.clients.discard(client)
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError, ConnectionError):
                await pump
        return ws

    # ---- pages ----

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    async def _state(self, request: web.Request) -> web.Response:
        return web.json_response({**self._snapshot(), "templates": templates.TEMPLATES, "channels": CHANNELS})

    @staticmethod
    async def _body(request: web.Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            raise WorkspaceError("invalid JSON") from None
        return body if isinstance(body, dict) else {}

    # ---- the device ----

    async def _device(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        flipper = self.flipper
        if action == "clock":
            await flipper.set_clock()
        elif action == "alert":
            await flipper.alert()
        elif action == "reboot":
            await flipper.reboot()
        elif action == "refresh":
            await flipper.refresh()
        elif action == "close-app":
            return web.json_response({"closed": await flipper.close_app()})
        elif action == "release":
            await flipper.release(True)
        elif action == "connect":
            await flipper.release(False)
        elif action == "console-open":
            await flipper.open_console()
        elif action == "console-close":
            await flipper.close_console()
        else:
            return _error("unknown action", 404)
        return web.json_response({"ok": True})

    async def _files(self, request: web.Request) -> web.Response:
        path = _device_path(request.query.get("path"))
        entries = await self.flipper.list(path)
        entries.sort(key=lambda e: (not e["dir"], e["name"].lower()))
        return web.json_response({"path": path, "entries": entries})

    async def _download(self, request: web.Request) -> web.Response:
        path = _device_path(request.query.get("path"))
        data = await self.flipper.read(path, MAX_DOWNLOAD)
        name = re.sub(r"[^A-Za-z0-9_. ()-]", "_", path.rsplit("/", 1)[1]) or "file"
        return web.Response(
            body=data,
            content_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    async def _upload(self, request: web.Request) -> web.Response:
        path = _device_path(request.query.get("path"))
        data = await request.read()
        self.flipper.set_busy(f"uploading {path.rsplit('/', 1)[1]}")
        try:
            await self.flipper.write(path, data)
        finally:
            self.flipper.set_busy("")
        return web.json_response({"ok": True, "size": len(data)})

    async def _file_action(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        body = await self._body(request)
        path = _device_path(body.get("path"))
        if path in ("/ext", "/int") and action != "mkdir":
            return _error("not on the storage itself")
        if action == "mkdir":
            await self.flipper.mkdir(path)
        elif action == "delete":
            await self.flipper.delete(path)
        elif action == "rename":
            await self.flipper.rename(path, _device_path(body.get("new_path")))
        elif action == "run":
            if path.endswith(".js"):
                await self.flipper.start_app("JS Runner", path)
            elif path.endswith(".fap"):
                await self.flipper.start_app(path)
            else:
                return _error("only a .fap or a .js can be started")
        else:
            return _error("unknown action", 404)
        return web.json_response({"ok": True})

    # ---- projects ----

    async def _projects(self, request: web.Request) -> web.Response:
        return web.json_response({"projects": self.workspace.projects()})

    async def _create(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        project = self.workspace.create(
            str(body.get("name") or ""), str(body.get("template") or ""), str(body.get("title") or "")
        )
        return web.json_response(project)

    async def _import(self, request: web.Request) -> web.Response:
        return web.json_response(self.workspace.import_zip(request.query.get("name", ""), await request.read()))

    async def _project(self, request: web.Request) -> web.Response:
        return web.json_response(self.workspace.describe(request.match_info["name"]))

    async def _remove(self, request: web.Request) -> web.Response:
        self.workspace.remove(request.match_info["name"])
        return web.json_response({"ok": True})

    async def _export(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        return web.Response(
            body=self.workspace.export(name),
            content_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}.zip"'},
        )

    async def _fap(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        info = self.workspace.describe(name)
        if "built" not in info:
            return _error("not built yet", 404)
        return web.FileResponse(
            self.workspace._existing(name) / "dist" / f"{info['appid']}.fap",
            headers={"Content-Disposition": f'attachment; filename="{info["appid"]}.fap"'},
        )

    async def _read(self, request: web.Request) -> web.Response:
        text = self.workspace.read(request.match_info["name"], request.query.get("path", ""))
        return web.json_response({"text": text})

    async def _write(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        self.workspace.write(request.match_info["name"], request.query.get("path", ""), str(body.get("text", "")))
        return web.json_response({"ok": True})

    async def _delete(self, request: web.Request) -> web.Response:
        self.workspace.delete_file(request.match_info["name"], request.query.get("path", ""))
        return web.json_response({"ok": True})

    async def _project_action(self, request: web.Request) -> web.Response:
        name, action = request.match_info["name"], request.match_info["action"]
        body = await self._body(request) if request.can_read_body else {}
        workspace = self.workspace
        workspace.describe(name)  # refuse a project that is not there before anything starts
        if action == "rename":
            workspace.rename_file(name, str(body.get("path") or ""), str(body.get("new_path") or ""))
        elif action == "build":
            self._spawn(workspace.build(name))
        elif action == "launch":
            self._spawn(workspace.launch(name, build=body.get("build", True) is not False))
        elif action == "run":
            self._spawn(workspace.run_script(name, str(body.get("path") or "")))
        else:
            return _error("unknown action", 404)
        return web.json_response({"ok": True})

    async def _log(self, request: web.Request) -> web.Response:
        return web.json_response({"lines": list(self.workspace.log), "job": self.workspace.job})

    async def _cancel(self, request: web.Request) -> web.Response:
        self.workspace.cancel()
        return web.json_response({"ok": True})

    async def _sdk_install(self, request: web.Request) -> web.Response:
        channel = str((await self._body(request)).get("channel") or "release")
        if channel not in CHANNELS:
            return _error("unknown channel")
        self._spawn(self.workspace.install_sdk(channel))
        return web.json_response({"ok": True})

    async def _sdk_remove(self, request: web.Request) -> web.Response:
        self.workspace.remove_sdk()
        return web.json_response({"ok": True})
