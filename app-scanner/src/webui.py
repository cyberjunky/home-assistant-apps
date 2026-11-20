"""The app's pages and its API, served through Home Assistant's ingress.

The browser only talks to this app. Everything that changes something (a scan, a document, a
setting) is only accepted from Home Assistant's signed-in ingress proxy, as JSON.
"""

import asyncio
import logging
import os
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from . import sane, share
from .archive import Archive
from .config import APP_VERSION, DATA_DIR, DESTINATION_ROOTS, OCR_LANGUAGES, Config
from .donate import DonatePrompt
from .pages import Tray
from .settings import Settings
from .station import Busy, Station

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy
INGRESS_ADDRESS = "172.30.32.2"
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


def error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


class WebUI:
    def __init__(self, port: int, config: Config, settings: Settings, tray: Tray, archive: Archive, station: Station):
        self.port = port
        self.config = config
        self.settings = settings
        self.tray = tray
        self.archive = archive
        self.station = station
        self.app = web.Application(client_max_size=4 * 1024 * 1024)
        r = self.app.router
        r.add_get("/", self._index)
        r.add_get("/api/status", self._status)
        r.add_get("/api/devices", self._devices)
        r.add_post("/api/devices/search", self._search)
        r.add_post("/api/options", self._options)
        r.add_post("/api/preview", self._preview)
        r.add_get("/api/preview.jpg", self._preview_image)
        r.add_post("/api/scan", self._scan)
        r.add_post("/api/cancel", self._cancel)
        r.add_get("/api/tray", self._tray)
        r.add_get("/api/tray/{id}/{what:thumb|image}", self._tray_file)
        r.add_post("/api/tray/rotate", self._tray_rotate)
        r.add_post("/api/tray/delete", self._tray_delete)
        r.add_post("/api/tray/order", self._tray_order)
        r.add_post("/api/tray/arrange", self._tray_arrange)
        r.add_post("/api/tray/save", self._tray_save)
        r.add_get("/api/documents", self._documents)
        r.add_get("/api/documents/{id:\\d+}", self._document)
        r.add_get("/api/documents/{id:\\d+}/{what:file|thumb}", self._document_file)
        r.add_post("/api/documents/{id:\\d+}", self._document_update)
        r.add_post("/api/documents/{id:\\d+}/delete", self._document_delete)
        r.add_post("/api/documents/{id:\\d+}/copy", self._document_copy)
        r.add_get("/api/settings", self._settings)
        r.add_post("/api/settings", self._settings_update)
        r.add_post("/api/profiles", self._profile_save)
        r.add_post("/api/profiles/delete", self._profile_delete)
        r.add_get("/api/folders", self._folders)
        r.add_post("/api/folders", self._folder_make)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        r.add_static("/static", WEB_DIR)

    async def start(self) -> None:
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info(f"Scanner app on port {self.port}")
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(text=page, content_type="text/html", headers=NO_CACHE)

    # ------------------------------------------------------------------
    # Changes: only from Home Assistant's signed-in ingress, as JSON
    # ------------------------------------------------------------------
    @staticmethod
    async def _body(request: web.Request) -> dict:
        # SCANNER_DEV is only set for development
        if (
            os.environ.get("SUPERVISOR_TOKEN")
            and not os.environ.get("SCANNER_DEV")
            and request.remote != INGRESS_ADDRESS
        ):
            raise web.HTTPForbidden(text="Use the sidebar")
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
    def _device_values(body: dict) -> tuple[str, dict]:
        device = str(body.get("device") or "")
        values = body.get("values") if isinstance(body.get("values"), dict) else {}
        if not device or device.startswith("-"):
            raise web.HTTPBadRequest(text="Choose a scanner")
        return device, {str(k): str(v) for k, v in values.items()}

    # ------------------------------------------------------------------
    # Status and scanners
    # ------------------------------------------------------------------
    async def status(self) -> dict:
        return {
            "version": APP_VERSION,
            "now": time.time(),
            "job": self.station.job,
            "tray": self.tray.summary(),
            "archive": self.archive.summary(),
            "devices": len(self.station.devices),
            "devices_at": self.station.devices_at,
            "searching": self.station.searching,
            "preview": self.station.preview,
            "share": {**await self.station.share_state(), "auto": self.settings["share_auto"]},
            "config": {
                "test_scanner": self.config.test_scanner,
                "driverless_usb": self.config.driverless_usb,
                "network_scanners": [{"name": n, "url": u} for n, u in self.config.network_scanners],
            },
        }

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(await self.status())

    def _devices_answer(self) -> web.Response:
        return web.json_response(
            {
                "devices": self.station.devices,
                "at": self.station.devices_at,
                "error": self.station.devices_error,
                "searching": self.station.searching,
            }
        )

    async def _devices(self, request: web.Request) -> web.Response:
        return self._devices_answer()

    async def _search(self, request: web.Request) -> web.Response:
        await self._body(request)
        try:
            await self.station.find_devices(restart_usb=True)
        except Busy as e:
            return error(str(e), 409)
        return self._devices_answer()

    async def _options(self, request: web.Request) -> web.Response:
        """A scanner's options with the chosen values applied. Not a change, but slow and one at a time."""
        body = await self._body(request)
        device, values = self._device_values(body)
        try:
            return web.json_response(await self.station.options(device, values))
        except Busy as e:
            return error(str(e), 409)
        except sane.ScanError as e:
            return error(str(e), 502)

    # ------------------------------------------------------------------
    # Preview, scan, cancel
    # ------------------------------------------------------------------
    async def _preview(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        device, values = self._device_values(body)
        try:
            return web.json_response({"job": self.station.start_preview(device, values)})
        except Busy as e:
            return error(str(e), 409)

    async def _preview_image(self, request: web.Request) -> web.StreamResponse:
        path = self.station.work / "preview.jpg"
        if not path.is_file():
            raise web.HTTPNotFound(text="No preview yet")
        return web.FileResponse(path, headers=NO_CACHE)

    async def _scan(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        device, values = self._device_values(body)
        batch = (
            self.settings.update({"batch": body.get("batch")})["batch"] if "batch" in body else self.settings["batch"]
        )
        try:
            return web.json_response({"job": self.station.start_scan(device, values, dict(batch))})
        except Busy as e:
            return error(str(e), 409)

    async def _cancel(self, request: web.Request) -> web.Response:
        await self._body(request)
        await self.station.cancel()
        return web.json_response({"job": self.station.job})

    # ------------------------------------------------------------------
    # The tray: the pages scanned so far
    # ------------------------------------------------------------------
    def _tray_answer(self) -> web.Response:
        return web.json_response(
            {
                "pages": self.tray.pages,
                "size": self.tray.summary()["size"],
                "suggested_name": self.station.suggested_name(),
            }
        )

    async def _tray(self, request: web.Request) -> web.Response:
        return self._tray_answer()

    async def _tray_file(self, request: web.Request) -> web.StreamResponse:
        page = self.tray.get(request.match_info["id"])
        if page is None:
            raise web.HTTPNotFound(text="No such page")
        path = self.tray.thumb(page) if request.match_info["what"] == "thumb" else self.tray.path(page)
        if not path.is_file():
            raise web.HTTPNotFound(text="No such page")
        return web.FileResponse(path, headers=NO_CACHE)

    def _not_while_saving(self) -> None:
        if self.station.busy and self.station.job["kind"] == "save":
            raise web.HTTPConflict(text="The document is being saved")

    async def _tray_rotate(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        self._not_while_saving()
        try:
            degrees = int(body.get("degrees") or 90)
        except (TypeError, ValueError):
            degrees = 0
        if degrees % 360 not in (90, 180, 270):
            return error("Turn by 90, 180 or 270 degrees")
        ids = body.get("ids") if isinstance(body.get("ids"), list) else [body.get("id")]
        for page_id in ids:
            await asyncio.to_thread(self.tray.rotate, str(page_id), degrees, int(self.settings["jpeg_quality"]))
        return self._tray_answer()

    async def _tray_delete(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        self._not_while_saving()
        ids = body.get("ids") if isinstance(body.get("ids"), list) else None
        if body.get("all"):
            self.tray.clear()
        elif ids is not None:
            self.tray.clear([str(i) for i in ids])
        else:
            self.tray.delete(str(body.get("id") or ""))
        return self._tray_answer()

    async def _tray_order(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        self._not_while_saving()
        if not isinstance(body.get("ids"), list):
            return error("Give the pages in their new order")
        self.tray.order([str(i) for i in body["ids"]])
        return self._tray_answer()

    async def _tray_arrange(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        self._not_while_saving()
        how = body.get("how")
        if how == "reverse":
            self.tray.reverse()
        elif how == "interleave":
            if not self.tray.interleave():
                return error("That needs an even number of pages: first all fronts, then all backs")
        else:
            return error("Unknown arrangement")
        return self._tray_answer()

    async def _tray_save(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        ids = [str(i) for i in body["ids"]] if isinstance(body.get("ids"), list) else None
        ocr = bool(body["ocr"]) if "ocr" in body else bool(self.settings["ocr"])
        try:
            job = self.station.start_save(
                str(body.get("name") or ""), body.get("tags") or [], ocr, ids, str(body.get("device") or "")
            )
        except Busy as e:
            return error(str(e), 409)
        except ValueError as e:
            return error(str(e))
        return web.json_response({"job": job})

    # ------------------------------------------------------------------
    # The archive
    # ------------------------------------------------------------------
    async def _documents(self, request: web.Request) -> web.Response:
        q = request.query
        result = await asyncio.to_thread(
            self.archive.search,
            q.get("q", ""),
            q.get("tag", ""),
            q.get("sort", "newest"),
            _int(q.get("offset"), 0),
            _int(q.get("limit"), 60),
        )
        result["tags"] = self.archive.tags()
        result["summary"] = self.archive.summary()
        return web.json_response(result)

    def _doc(self, request: web.Request, with_text: bool = False) -> dict:
        doc = self.archive.get(int(request.match_info["id"]), with_text)
        if doc is None:
            raise web.HTTPNotFound(text="No such document")
        return doc

    async def _document(self, request: web.Request) -> web.Response:
        return web.json_response(self._doc(request, with_text=True))

    async def _document_file(self, request: web.Request) -> web.StreamResponse:
        doc = self._doc(request)
        if request.match_info["what"] == "thumb":
            path = self.archive.thumb(doc["id"])
            headers = NO_CACHE
        else:
            path = self.archive.pdf(doc["id"])
            kind = "attachment" if request.query.get("download") == "1" else "inline"
            # The name for the browser, also when it has letters outside ASCII
            plain = doc["file_name"].encode("ascii", "replace").decode().replace('"', "'")
            headers = {
                **NO_CACHE,
                "Content-Type": "application/pdf",
                "Content-Disposition": f"{kind}; filename=\"{plain}\"; filename*=UTF-8''{quote(doc['file_name'])}",
            }
        if not path.is_file():
            raise web.HTTPNotFound(text="The file is gone")
        return web.FileResponse(path, headers=headers)

    async def _document_update(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        doc = self._doc(request)
        name = str(body["name"]) if "name" in body else None
        tags = body.get("tags", None)
        return web.json_response(self.archive.update(doc["id"], name, tags))

    async def _document_delete(self, request: web.Request) -> web.Response:
        await self._body(request)
        doc = self._doc(request)
        self.archive.delete(doc["id"])
        logger.info(f"Deleted document {doc['id']}: {doc['name']}")
        return web.json_response({"ok": True})

    async def _document_copy(self, request: web.Request) -> web.Response:
        await self._body(request)
        doc = self._doc(request)
        try:
            return web.json_response(await self.station.copy(doc["id"]))
        except share.ShareError as e:
            return error(str(e), 502)

    # ------------------------------------------------------------------
    # Settings, profiles and folders
    # ------------------------------------------------------------------
    def _settings_answer(self) -> web.Response:
        return web.json_response(
            {
                "settings": self.settings.data,
                "languages": OCR_LANGUAGES,
                "ocr_available": shutil.which("tesseract") is not None,
                "roots": [str(r) for r in DESTINATION_ROOTS],
            }
        )

    async def _settings(self, request: web.Request) -> web.Response:
        return self._settings_answer()

    async def _settings_update(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        folder = body.get("share_folder")
        if isinstance(folder, str) and folder and share.inside(Path(folder), DESTINATION_ROOTS) is None:
            return error("Choose a folder in Home Assistant's share or media folder")
        # The scanner's own values and profiles have their own ways in
        allowed = (
            "name_pattern",
            "jpeg_quality",
            "ocr",
            "ocr_languages",
            "share_folder",
            "share_pattern",
            "share_auto",
        )
        self.settings.update({k: body[k] for k in allowed if k in body})
        self.station.forget_share_state()
        return self._settings_answer()

    async def _profile_save(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        name = " ".join(str(body.get("name") or "").split())[:60]
        if not name:
            return error("Give the profile a name")
        device, values = self._device_values(body)
        self.settings.save_profile(name, device, values, body.get("batch") or self.settings["batch"])
        return self._settings_answer()

    async def _profile_delete(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        self.settings.delete_profile(str(body.get("name") or ""))
        return self._settings_answer()

    async def _folders(self, request: web.Request) -> web.Response:
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(share.list_folder, request.query.get("path", ""), DESTINATION_ROOTS), 15
            )
        except share.ShareError as e:
            return error(str(e))
        except TimeoutError:
            return error("The folder doesn't answer: is the network storage connected?", 504)
        return web.json_response(result)

    async def _folder_make(self, request: web.Request) -> web.Response:
        body = await self._body(request)
        try:
            path = share.make_folder(str(body.get("path") or ""), str(body.get("name") or ""), DESTINATION_ROOTS)
        except share.ShareError as e:
            return error(str(e))
        return web.json_response({"path": path})


def _int(text: str | None, default: int) -> int:
    try:
        return int(text) if text is not None else default
    except ValueError:
        return default
