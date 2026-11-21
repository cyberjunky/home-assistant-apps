"""The pages and their API, served through Home Assistant's ingress.

Telegrams and the state of the connections go to the open pages over one WebSocket; the rest is JSON.
"""

import asyncio
import contextlib
import csv
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web

from .bus import BusError
from .config import APP_VERSION, DATA_DIR
from .documents import MAX_PDF, DocumentError, product_key
from .donate import DonatePrompt
from .hub import Hub, HubError
from .model import is_ga
from .recorder import parse_ga

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
INGRESS_ADDRESS = "172.30.32.2"
PUSH_EVERY = 0.25
RANGE_MAX = 3660 * 86400


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


class WebUI:
    def __init__(self, port: int, hub: Hub):
        self.port = port
        self.hub = hub
        self.clients: set[web.WebSocketResponse] = set()
        self._pending: list[dict[str, Any]] = []
        self._state_due = False
        hub.on_telegram = self._pending.append
        hub.on_change = self._mark

        self.app = web.Application(middlewares=[self._guard], client_max_size=MAX_PDF + 4096)
        route = self.app.router
        route.add_get("/", self._index)
        route.add_get("/api/state", self._state)
        route.add_get("/api/model", self._model)
        route.add_get("/api/telegrams", self._telegrams)
        route.add_get("/api/detail", self._detail)
        route.add_get("/api/ws", self._socket)
        route.add_post("/api/interface/scan", self._scan)
        route.add_post("/api/interface/select", self._select)
        route.add_post("/api/job", self._job)
        route.add_post("/api/job/cancel", self._job_cancel)
        route.add_post("/api/read", self._read)
        route.add_post("/api/forget", self._forget)
        route.add_post("/api/raw", self._raw)
        route.add_post("/api/documents/upload", self._document_upload)
        route.add_post("/api/documents/link", self._document_link)
        route.add_post("/api/documents/rename", self._document_rename)
        route.add_post("/api/documents/delete", self._document_delete)
        route.add_get("/api/documents/file/{id}", self._document_file)
        route.add_get("/api/busmon", self._busmon)
        route.add_post("/api/busmon/start", self._busmon_start)
        route.add_post("/api/busmon/stop", self._busmon_stop)
        route.add_post("/api/serial", self._serial)
        route.add_post("/api/findings/hide", self._hide)
        route.add_get("/api/report.md", self._report)
        route.add_get("/api/export/{what}.csv", self._export)
        route.add_post("/api/remove", self._remove)
        route.add_get("/api/archive", self._archive)
        route.add_get("/api/archive.csv", self._archive_csv)
        route.add_get("/api/recorded", self._recorded)
        route.add_get("/api/series", self._series)
        route.add_get("/api/stats", self._stats)
        route.add_post("/api/recorder/clear", self._recorder_clear)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        route.add_static("/static", WEB_DIR)

    @web.middleware
    async def _guard(self, request: web.Request, handler):
        # Under Home Assistant only its signed-in ingress proxy may reach the pages and the API. The app is on
        # the machine's own network (to find KNX interfaces), so its port is open to the LAN: nobody else
        # gets an answer. KNXRAY_SUPERVISOR_URL is only set for development against tools/mock_ha.py.
        dev = bool(os.environ.get("KNXRAY_SUPERVISOR_URL"))
        if os.environ.get("SUPERVISOR_TOKEN") and not dev and request.remote != INGRESS_ADDRESS:
            return _error("use the Home Assistant sidebar", 403)
        try:
            return await handler(request)
        except (HubError, BusError, DocumentError) as e:
            return _error(str(e))

    async def start(self) -> None:
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info("Dashboard on port %s", self.port)
        task = asyncio.create_task(self._pusher(), name="push")
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    # ---- live ----

    def _mark(self) -> None:
        self._state_due = True

    async def _pusher(self) -> None:
        """Hand what came in to the open pages a few times a second, and the state when it changed (and
        every two seconds, for the counters)."""
        last_state = 0.0
        while True:
            await asyncio.sleep(PUSH_EVERY)
            items, self._pending[:] = list(self._pending), []
            messages = []
            if items:
                messages.append(json.dumps({"t": "telegrams", "items": items}))
            now = time.time()
            if self.clients and (self._state_due or now - last_state >= 2):
                self._state_due, last_state = False, now
                messages.append(json.dumps(self._snapshot()))
            for ws in list(self.clients):
                for message in messages:
                    with contextlib.suppress(ConnectionError, RuntimeError):
                        await ws.send_str(message)

    def _snapshot(self) -> dict[str, Any]:
        return {**self.hub.summary(), "version": APP_VERSION, "recorder": self.hub.recorder.summary()}

    async def _socket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=25)
        await ws.prepare(request)
        await ws.send_str(json.dumps(self._snapshot()))
        self.clients.add(ws)
        try:
            async for message in ws:
                if message.type == WSMsgType.ERROR:
                    break
        finally:
            self.clients.discard(ws)
        return ws

    # ---- pages ----

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    async def _state(self, request: web.Request) -> web.Response:
        return web.json_response(self._snapshot())

    async def _model(self, request: web.Request) -> web.Response:
        return web.json_response(self.hub.model())

    async def _telegrams(self, request: web.Request) -> web.Response:
        after = _int(request.query.get("after"), 0)
        return web.json_response({"items": self.hub.monitor.recent(after, 3000)})

    async def _detail(self, request: web.Request) -> web.Response:
        address = request.query.get("address", "")
        if not address or len(address) > 16:
            return _error("no address")
        return web.json_response(self.hub.detail(address))

    @staticmethod
    async def _body(request: web.Request) -> dict:
        try:
            body = await request.json()
        except ValueError:
            raise HubError("invalid JSON") from None
        return body if isinstance(body, dict) else {}

    # ---- the interface ----

    async def _scan(self, request: web.Request) -> web.Response:
        return web.json_response({"interfaces": await self.hub.bus.scan()})

    async def _select(self, request: web.Request) -> web.Response:
        await self.hub.select_interface(await self._body(request))
        return web.json_response({"ok": True})

    # ---- asking the bus ----

    async def _job(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        self.hub.start_job(str(body.get("kind") or ""), str(body.get("target") or "").strip())
        return web.json_response({"ok": True})

    async def _job_cancel(self, request: web.Request) -> web.Response:
        self.hub.cancel_job()
        return web.json_response({"ok": True})

    async def _read(self, request: web.Request) -> web.Response:
        address = str((await self._body(request)).get("address") or "")
        if not is_ga(address) or "/" not in address:
            return _error("not a group address")
        return web.json_response({"responses": await self.hub.read_group(address)})

    def _for_product(self, address: str, wanted: Any) -> str:
        """The product a document is for when it is meant for every device like this one."""
        if str(wanted) not in ("1", "true", "True", "product"):
            return ""
        product = product_key(self.hub.project.devices.get(address))
        if not product:
            raise DocumentError("the project does not say which product this device is")
        return product

    async def _document_upload(self, request: web.Request) -> web.Response:
        address = request.query.get("address", "")
        product = self._for_product(address, request.query.get("product"))
        item = self.hub.documents.add_file(address, request.query.get("title", ""), await request.read(), product)
        self.hub._changed()
        return web.json_response(item)

    async def _document_link(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        address = str(body.get("address") or "")
        product = self._for_product(address, body.get("product"))
        item = self.hub.documents.add_link(address, str(body.get("title") or ""), str(body.get("url") or ""), product)
        self.hub._changed()
        return web.json_response(item)

    async def _document_rename(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        self.hub.documents.rename(str(body.get("id") or ""), str(body.get("title") or ""))
        return web.json_response({"ok": True})

    async def _document_delete(self, request: web.Request) -> web.Response:
        self.hub.documents.delete(str((await self._body(request)).get("id") or ""))
        self.hub._changed()
        return web.json_response({"ok": True})

    async def _document_file(self, request: web.Request) -> web.FileResponse:
        path, name, content_type = self.hub.documents.file(request.match_info["id"])
        return web.FileResponse(
            path,
            headers={
                "Content-Type": content_type,
                "Content-Disposition": f'inline; filename="{name}"',
                # Somebody else's file: it gets no say over this app's pages
                "Content-Security-Policy": "sandbox",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def _busmon(self, request: web.Request) -> web.Response:
        after = _int(request.query.get("after"), 0)
        monitor = self.hub.busmon
        return web.json_response({"status": monitor.status(), "frames": monitor.recent(after, 1000)})

    async def _busmon_start(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        try:
            await self.hub.busmon.start(
                str(body.get("host") or "").strip(), _int(body.get("port"), 3671), _int(body.get("minutes"), 10)
            )
        except ValueError as e:
            raise HubError(str(e)) from None
        return web.json_response({"ok": True})

    async def _busmon_stop(self, request: web.Request) -> web.Response:
        await self.hub.busmon.stop()
        return web.json_response({"ok": True})

    async def _raw(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        address = str(body.get("address") or "")
        try:
            numbers = [int(str(body.get(key, default)), 0) for key, default in (("a", 0), ("b", 1), ("c", 1), ("d", 1))]
        except ValueError:
            return _error("numbers, written as 23 or 0x17")
        if address.count(".") != 2:
            return _error("not an individual address")
        return web.json_response(await self.hub.bus.read_raw(address, str(body.get("what") or ""), *numbers))

    async def _serial(self, request: web.Request) -> web.Response:
        found = await self.hub.bus.find_serial(str((await self._body(request)).get("serial") or ""))
        return web.json_response({"address": found})

    async def _forget(self, request: web.Request) -> web.Response:
        self.hub.forget_device(str((await self._body(request)).get("address") or ""))
        return web.json_response({"ok": True})

    async def _hide(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        finding = str(body.get("id") or "")
        if not finding or len(finding) > 300:
            return _error("no finding")
        self.hub.hide(finding, bool(body.get("hidden", True)))
        return web.json_response({"ok": True})

    async def _report(self, request: web.Request) -> web.Response:
        return web.Response(
            text=self.hub.report(),
            content_type="text/markdown",
            charset="utf-8",
            headers={"Content-Disposition": f'attachment; filename="knx-ray-report-{time.strftime("%Y%m%d-%H%M")}.md"'},
        )

    async def _export(self, request: web.Request) -> web.Response:
        what = request.match_info["what"]
        rows = self.hub.export(what)
        if rows is None:
            return _error("unknown table", 404)
        out = io.StringIO()
        csv.writer(out, lineterminator="\n").writerows(rows)
        return web.Response(
            text=out.getvalue(),
            content_type="text/csv",
            charset="utf-8",
            headers={"Content-Disposition": f'attachment; filename="knx-{what}-{time.strftime("%Y%m%d")}.csv"'},
        )

    async def _remove(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        await self.hub.remove(str(body.get("kind") or ""), str(body.get("id") or ""))
        return web.json_response({"ok": True})

    # ---- the recording ----

    @staticmethod
    def _range(request: web.Request) -> tuple[float, float]:
        now = time.time()
        until = _float(request.query.get("until"), now)
        since = _float(request.query.get("since"), until - 86400)
        if not 0 <= until - since <= RANGE_MAX:
            raise HubError("not a range of time")
        return since, until

    def _filters(self, request: web.Request) -> dict[str, Any]:
        query = request.query
        since, until = self._range(request)
        filters: dict[str, Any] = {"since": since, "until": until}
        if query.get("ga"):
            ga = parse_ga(query["ga"])
            if ga is None:
                raise HubError("not a group address")
            filters["ga"] = ga
        if query.get("prefix"):
            filters["ga_prefix"] = query["prefix"][:12]
        if query.get("src"):
            filters["src"] = query["src"][:16]
        if query.get("type") in ("write", "read", "response"):
            filters["type"] = query["type"]
        if query.get("dpt"):
            filters["dpt_main"] = _int(query["dpt"], 0)
        text = query.get("q", "").strip()[:80]
        if text:
            filters["q"] = text
            # What the text names: group addresses and devices whose name has it
            lowered = text.lower()
            project = self.hub.project
            filters["q_gas"] = [
                raw
                for a, ga in project.group_addresses.items()
                if lowered in ga["name"].lower() and (raw := parse_ga(a)) is not None
            ]
            filters["q_srcs"] = [a for a in project.devices if lowered in project.name_of(a).lower()]
        return filters

    async def _archive(self, request: web.Request) -> web.Response:
        filters = self._filters(request)
        cursor = _int(request.query.get("cursor"), 0) or None
        limit = _int(request.query.get("limit"), 200)
        return web.json_response(await asyncio.to_thread(self.hub.recorder.archive, filters, cursor, limit))

    async def _archive_csv(self, request: web.Request) -> web.StreamResponse:
        filters = self._filters(request)
        response = web.StreamResponse(
            headers={
                "Content-Type": "text/csv; charset=utf-8",
                "Content-Disposition": f'attachment; filename="knx-telegrams-{time.strftime("%Y%m%d-%H%M%S")}.csv"',
            }
        )
        await response.prepare(request)
        rows = self.hub.recorder.csv_rows(filters, self.hub.names())
        while True:
            block = await asyncio.to_thread(next, rows, None)
            if block is None:
                break
            await response.write(block.encode())
        await response.write_eof()
        return response

    async def _recorded(self, request: web.Request) -> web.Response:
        since, until = self._range(request)
        return web.json_response({"addresses": await asyncio.to_thread(self.hub.recorder.addresses, since, until)})

    async def _series(self, request: web.Request) -> web.Response:
        since, until = self._range(request)
        ga = parse_ga(request.query.get("ga", ""))
        if ga is None:
            return _error("not a group address")
        points = _int(request.query.get("points"), 600)
        return web.json_response(await asyncio.to_thread(self.hub.recorder.series, ga, since, until, points))

    async def _stats(self, request: web.Request) -> web.Response:
        filters = self._filters(request)
        since, until = filters.pop("since"), filters.pop("until")
        stats, availability = await asyncio.gather(
            asyncio.to_thread(self.hub.recorder.stats, since, until, filters),
            asyncio.to_thread(self.hub.recorder.availability, since, until),
        )
        return web.json_response({**stats, "availability": availability, "recorder": self.hub.recorder.summary()})

    async def _recorder_clear(self, request: web.Request) -> web.Response:
        await asyncio.to_thread(self.hub.recorder.clear)
        return web.json_response({"ok": True})


def _int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
