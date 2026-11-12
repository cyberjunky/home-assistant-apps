"""Web dashboard: the page, a WebSocket for the live radio, and a JSON API for the data pages."""

import asyncio
import contextlib
import json
import logging
import os
import struct
import time
from datetime import date, timedelta
from pathlib import Path

from aiohttp import WSMsgType, web

from .backups import Backups
from .bands import MODES, STEPS
from .config import ADDON_VERSION, DATA_DIR, Config
from .database import SETTINGS, Database, ValidationError
from .dsp import DB_CEIL, DB_FLOOR
from .ha import HomeAssistant
from .radio import Radio

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"

# Home Assistant's ingress proxy: the only client allowed when running as an app
INGRESS_ADDRESS = "172.30.32.2"
MAX_UPLOAD = 64 * 1024 * 1024
# Commands the page may send over the WebSocket, with the radio method they become
COMMANDS = {"tune", "channel", "step_tune", "set", "cycle", "band", "scan", "priority", "lockout", "memory",
            "autotune", "power"}  # fmt: skip
SPECTRUM_FRAME = 1
AUDIO_FRAME = 2


def _json(data, status: int = 200) -> web.Response:
    return web.Response(text=json.dumps(data, default=str), content_type="application/json", status=status)


def _error(message: str, status: int = 400) -> web.Response:
    return _json({"error": message}, status)


class Client:
    """One open page: what it wants to receive, and a queue so a slow page can't hold up the radio."""

    def __init__(self, ws: web.WebSocketResponse):
        self.ws = ws
        self.listen = False
        self.spectrum = False
        self.sweep = False
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=60)

    def send(self, item) -> None:
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            # Behind: drop the oldest frame, keep the newest
            with contextlib.suppress(asyncio.QueueEmpty):
                self.queue.get_nowait()
            with contextlib.suppress(asyncio.QueueFull):
                self.queue.put_nowait(item)


class WebUI:
    def __init__(self, port: int, config: Config, database: Database, device_factory, data_dir: Path = DATA_DIR):
        self.port = port
        self.config = config
        self.db = database
        self.backups = Backups(database, Path(data_dir) / "backups")
        self.ha = HomeAssistant()
        self.clients: set[Client] = set()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.radio = Radio(config, database, device_factory, self._emit_threadsafe)
        self.state: dict = self.radio.snapshot()
        self.ingress_only = bool(os.environ.get("SUPERVISOR_TOKEN"))
        self.app = web.Application(middlewares=[self._ingress_only, self._errors], client_max_size=MAX_UPLOAD)
        self.app.on_startup.append(self._on_startup)
        self.app.on_shutdown.append(self._on_shutdown)
        self._runner: web.AppRunner | None = None
        self._tasks: list[asyncio.Task] = []
        self._pending: set[asyncio.Future] = set()
        self._setup_routes()

    def _setup_routes(self) -> None:
        from .donate import DonatePrompt

        r = self.app.router
        r.add_get("/", self._handle_index)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        r.add_static("/web/", WEB_DIR)
        r.add_get("/api/ws", self._handle_ws)
        r.add_get("/api/status", self._handle_status)
        r.add_get("/api/channels", self._handle_channels)
        r.add_post("/api/channels", self._handle_channel_create)
        r.add_put("/api/channels/{id:\\d+}", self._handle_channel_update)
        r.add_delete("/api/channels/{id:\\d+}", self._handle_channel_delete)
        r.add_get("/api/channels/export", self._handle_export)
        r.add_post("/api/channels/import", self._handle_import)
        r.add_get("/api/finds", self._handle_finds)
        r.add_post("/api/finds/ignore", self._handle_ignore)
        r.add_post("/api/finds/unignore", self._handle_unignore)
        r.add_get("/api/stats", self._handle_stats)
        r.add_get("/api/hits", self._handle_hits)
        r.add_get("/api/settings", self._handle_settings)
        r.add_put("/api/settings", self._handle_settings_update)
        r.add_post("/api/bands", self._handle_band_create)
        r.add_put("/api/bands/{id:\\d+}", self._handle_band_update)
        r.add_delete("/api/bands/{id:\\d+}", self._handle_band_delete)
        r.add_post("/api/bands/reset", self._handle_bands_reset)
        r.add_get("/api/backups", self._handle_backups)
        r.add_post("/api/backups", self._handle_backup_create)
        r.add_post("/api/backups/upload", self._handle_backup_upload)
        r.add_get("/api/backups/{name}", self._handle_backup_download)
        r.add_post("/api/backups/{name}/restore", self._handle_backup_restore)
        r.add_delete("/api/backups/{name}", self._handle_backup_delete)

    # ================================================================ lifecycle

    async def _on_startup(self, _app) -> None:
        self.loop = asyncio.get_running_loop()
        self.radio.start()
        from .backups import nightly

        self._tasks.append(asyncio.create_task(nightly(self.backups, self.db), name="nightly"))
        self._tasks.append(asyncio.create_task(self._count_listening(), name="listening"))

    async def _on_shutdown(self, _app) -> None:
        for task in self._tasks:
            task.cancel()
        for client in list(self.clients):
            await client.ws.close()
        await asyncio.to_thread(self.radio.stop)
        await self.ha.close()

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=1.0)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        logger.info(f"Web UI listening on port {self.port}")
        await asyncio.Event().wait()

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    @web.middleware
    async def _ingress_only(self, request: web.Request, handler):
        if self.ingress_only and request.remote != INGRESS_ADDRESS:
            return web.Response(status=403, text="Open SDRadio from the Home Assistant sidebar")
        return await handler(request)

    @web.middleware
    async def _errors(self, request: web.Request, handler):
        try:
            return await handler(request)
        except ValidationError as e:
            return _error(str(e))
        except KeyError:
            return _error("Not found", 404)

    # ================================================================ radio → pages

    def _emit_threadsafe(self, kind: str, payload) -> None:
        loop = self.loop
        if loop is None or loop.is_closed():
            if kind == "state":
                self.state = payload
            return
        loop.call_soon_threadsafe(self._dispatch, kind, payload)

    def _dispatch(self, kind: str, payload) -> None:
        if kind == "state":
            self.state = payload
            message = json.dumps({"type": "state", "state": payload}, default=str)
            for client in self.clients:
                client.send(message)
            if self.db_settings_cache().get("ha_sensors") and self.ha.available:
                self._background(self.ha.publish_state(payload))
        elif kind == "spectrum":
            frame = struct.pack("<BddffI", SPECTRUM_FRAME, payload["center"], payload["rate"], DB_FLOOR, DB_CEIL, 0)
            frame += payload["bins"]
            for client in self.clients:
                if client.spectrum:
                    client.send(frame)
        elif kind == "audio":
            frame = struct.pack("<Bf", AUDIO_FRAME, float(payload["rate"])) + payload["pcm"]
            for client in self.clients:
                if client.listen:
                    client.send(frame)
        else:
            if (
                kind == "hit"
                and payload.get("channel")
                and self.db_settings_cache().get("ha_events")
                and self.ha.available
            ):
                self._background(self.ha.fire_hit(payload))
            message = json.dumps({"type": kind, **payload}, default=str)
            for client in self.clients:
                client.send(message)

    def _background(self, coro) -> None:
        """Runs a Home Assistant update without waiting for it, keeping a reference until it is done."""
        task = asyncio.ensure_future(coro)
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    def db_settings_cache(self) -> dict:
        return self.radio.settings

    def _update_listeners(self) -> None:
        self.radio.listeners = sum(1 for c in self.clients if c.listen)
        self.radio.spectrum_listeners = sum(1 for c in self.clients if c.spectrum)
        # The band view sweeps the receiver: only while a page shows it
        self.radio.sweep_wanted = any(c.sweep and c.spectrum for c in self.clients)

    async def _count_listening(self) -> None:
        """Keeps the listening time per day for the Stats page."""
        while True:
            await asyncio.sleep(30)
            if self.radio.listeners:
                await asyncio.to_thread(self.db.add_listening, 30, date.today().isoformat())

    # ================================================================ pages

    async def _handle_index(self, request: web.Request) -> web.Response:
        base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace("__ADDON_VERSION__", ADDON_VERSION).replace("__BASE__", base)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30, max_msg_size=64 * 1024)
        await ws.prepare(request)
        client = Client(ws)
        self.clients.add(client)
        client.send(json.dumps({"type": "state", "state": self.state}, default=str))
        writer = asyncio.create_task(self._writer(client))
        try:
            async for message in ws:
                if message.type != WSMsgType.TEXT:
                    continue
                try:
                    data = json.loads(message.data)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                self._handle_message(client, data)
        finally:
            self.clients.discard(client)
            self._update_listeners()
            writer.cancel()
        return ws

    def _handle_message(self, client: Client, data: dict) -> None:
        cmd = data.pop("cmd", None)
        if cmd == "listen":
            client.listen = bool(data.get("on"))
            self._update_listeners()
        elif cmd == "spectrum":
            client.spectrum = bool(data.get("on"))
            self._update_listeners()
        elif cmd == "sweep":
            client.sweep = bool(data.get("on"))
            self._update_listeners()
        elif cmd in COMMANDS:
            self.radio.command(cmd, **{k: v for k, v in data.items() if isinstance(k, str)})

    async def _writer(self, client: Client) -> None:
        try:
            while True:
                item = await client.queue.get()
                if isinstance(item, bytes):
                    await client.ws.send_bytes(item)
                else:
                    await client.ws.send_str(item)
        except (ConnectionResetError, RuntimeError, asyncio.CancelledError):
            pass

    async def _handle_status(self, _request: web.Request) -> web.Response:
        last = self.backups.last()
        return _json(
            {
                "version": ADDON_VERSION,
                "options": self.config.public(),
                "receiver": self.config.receiver.name,
                "state": self.state,
                "modes": {k: {"label": v["label"], "bandwidths": v["bandwidths"]} for k, v in MODES.items()},
                "steps": STEPS,
                "bands": self.db.bands(),
                "counts": self.db.counts(),
                "last_backup": last["ts"] if last else None,
                "ha": self.ha.available,
            }
        )

    # ================================================================ channels

    async def _body(self, request: web.Request) -> dict:
        try:
            data = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise ValidationError("The request is not valid JSON") from None
        if not isinstance(data, dict):
            raise ValidationError("Expected a JSON object")
        return data

    def _changed(self, what: str) -> None:
        self.radio.reload()
        self._dispatch("changed", {"what": what})

    async def _handle_channels(self, _request: web.Request) -> web.Response:
        return _json({"channels": self.db.channels(), "next_number": self.db.next_number()})

    async def _handle_channel_create(self, request: web.Request) -> web.Response:
        channel = self.db.save_channel(await self._body(request))
        self._changed("channels")
        return _json(channel, 201)

    async def _handle_channel_update(self, request: web.Request) -> web.Response:
        channel = self.db.save_channel(await self._body(request), int(request.match_info["id"]))
        self._changed("channels")
        return _json(channel)

    async def _handle_channel_delete(self, request: web.Request) -> web.Response:
        self.db.delete_channel(int(request.match_info["id"]))
        self._changed("channels")
        return _json({"ok": True})

    async def _handle_export(self, _request: web.Request) -> web.Response:
        return web.Response(
            text=self.db.export_csv(),
            content_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="sdradio-channels-{date.today().isoformat()}.csv"'},
        )

    async def _handle_import(self, request: web.Request) -> web.Response:
        replace = request.query.get("replace") == "1"
        raw = await request.read()
        if len(raw) > 5 * 1024 * 1024:
            raise ValidationError("The file is too big for a channel list")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        result = await asyncio.to_thread(self.db.import_csv, text, replace)
        self._changed("channels")
        return _json(result)

    async def _handle_finds(self, _request: web.Request) -> web.Response:
        min_hits = int(self.radio.settings["find_min_hits"])
        return _json({"finds": self.db.finds(min_hits), "ignored": self.db.ignored(), "min_hits": min_hits})

    async def _handle_ignore(self, request: web.Request) -> web.Response:
        frequency = int((await self._body(request)).get("frequency") or 0)
        if frequency <= 0:
            raise ValidationError("No frequency")
        self.db.ignore(frequency)
        self._changed("finds")
        return _json({"ok": True})

    async def _handle_unignore(self, request: web.Request) -> web.Response:
        self.db.unignore(int((await self._body(request)).get("frequency") or 0))
        self._changed("finds")
        return _json({"ok": True})

    # ================================================================ stats

    async def _handle_stats(self, _request: web.Request) -> web.Response:
        stats = await asyncio.to_thread(self.db.stats)
        today = date.today()
        days = [(today - timedelta(days=i)).isoformat() for i in range(7)]
        listening = self.db.listening(days)
        stats["listening_today"] = listening.get(days[0], 0)
        stats["listening_week"] = sum(listening.values())
        stats["finds"] = len(self.db.finds(int(self.radio.settings["find_min_hits"])))
        stats["recent"] = self.db.recent_hits(15)
        last = self.backups.last()
        stats["last_backup"] = last["ts"] if last else None
        stats["backups"] = len(self.backups.list())
        return _json(stats)

    async def _handle_hits(self, request: web.Request) -> web.Response:
        try:
            limit = max(1, min(500, int(request.query.get("limit", 100))))
        except ValueError:
            limit = 100
        return _json({"hits": self.db.recent_hits(limit)})

    # ================================================================ settings and bands

    async def _handle_settings(self, _request: web.Request) -> web.Response:
        return _json(
            {
                "settings": self.db.settings(),
                "spec": {k: {kk: vv for kk, vv in v.items() if kk != "default"} for k, v in SETTINGS.items()},
                "options": self.config.public(),
                "bands": self.db.bands(),
                "ha": self.ha.available,
            }
        )

    async def _handle_settings_update(self, request: web.Request) -> web.Response:
        settings = self.db.update_settings(await self._body(request))
        self._changed("settings")
        return _json({"settings": settings})

    async def _handle_band_create(self, request: web.Request) -> web.Response:
        band = self.db.save_band(await self._body(request))
        self._changed("bands")
        return _json(band, 201)

    async def _handle_band_update(self, request: web.Request) -> web.Response:
        band = self.db.save_band(await self._body(request), int(request.match_info["id"]))
        self._changed("bands")
        return _json(band)

    async def _handle_band_delete(self, request: web.Request) -> web.Response:
        self.db.delete_band(int(request.match_info["id"]))
        self._changed("bands")
        return _json({"ok": True})

    async def _handle_bands_reset(self, _request: web.Request) -> web.Response:
        bands = self.db.reset_bands()
        self._changed("bands")
        return _json({"bands": bands})

    # ================================================================ backups

    async def _handle_backups(self, _request: web.Request) -> web.Response:
        return _json({"backups": self.backups.list(), "folder": str(self.backups.folder)})

    async def _handle_backup_create(self, _request: web.Request) -> web.Response:
        item = await asyncio.to_thread(self.backups.create)
        await asyncio.to_thread(self.backups.prune, int(self.radio.settings["backups_keep"]))
        return _json(item, 201)

    async def _handle_backup_upload(self, request: web.Request) -> web.Response:
        data = await request.read()
        if not data:
            raise ValidationError("No file")
        return _json(await asyncio.to_thread(self.backups.add_upload, data), 201)

    async def _handle_backup_download(self, request: web.Request) -> web.StreamResponse:
        path = self.backups.path(request.match_info["name"])
        return web.FileResponse(path, headers={"Content-Disposition": f'attachment; filename="{path.name}"'})

    async def _handle_backup_restore(self, request: web.Request) -> web.Response:
        await asyncio.to_thread(self.backups.restore, request.match_info["name"])
        self._changed("all")
        return _json({"ok": True, "restored_at": time.time()})

    async def _handle_backup_delete(self, request: web.Request) -> web.Response:
        self.backups.path(request.match_info["name"]).unlink()
        return _json({"ok": True})
