"""Web UI for the FireServiceRota dashboard, served through Home Assistant ingress."""

import asyncio
import contextlib
import csv
import io
import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import aiohttp
from aiohttp import web

from . import abbreviation_import, stats
from .config import DATA_DIR
from .database import CSV_TABLES, SPEECH_CSV, Database
from .donate import DonatePrompt
from .ha_client import HAClientError
from .speech import is_default, spell
from .timeutil import now_ts
from .tracker import Tracker

logger = logging.getLogger(__name__)


def _addon_version() -> str:
    """The app version from config.yaml (copied next to src/ in the image), so it can't drift."""
    for path in (Path(__file__).resolve().parent.parent / "config.yaml",):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
        except OSError:
            pass
    return "dev"


ADDON_VERSION = _addon_version()
SRC_DIR = Path(__file__).resolve().parent
STATIC_DIR = SRC_DIR / "static"
INDEX_HTML = (SRC_DIR / "web" / "index.html").read_text(encoding="utf-8")

# Map tiles come from Home Assistant's built-in map_tiles proxy: browsers loading tiles
# straight from OpenStreetMap inside the ingress iframe get 403 "Access blocked", and core
# identifies its requests the way OSM's tile usage policy requires.
MAP_MAX_ZOOM = 19
MAP_TILE_MAX_AGE = 7 * 24 * 60 * 60
# Seconds between live-stream heartbeats while nothing changes
STREAM_HEARTBEAT = 15
LATEST = 50
MAX_RESTORE_SIZE = 512 * 1024 * 1024


def _int_param(request: web.Request, name: str, default: int, minimum: int = 1, maximum: int = 1000) -> int:
    try:
        value = int(request.query.get(name, default))
    except ValueError:
        value = default
    return max(minimum, min(maximum, value))


class WebUI:
    def __init__(self, port: int, database: Database, tracker: Tracker):
        self.port = port
        self.db = database
        self.tracker = tracker
        self.app = web.Application(client_max_size=MAX_RESTORE_SIZE)
        self._runner: web.AppRunner | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        r = self.app.router
        r.add_get("/", self._handle_index)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        r.add_static("/static/", STATIC_DIR, name="static")
        r.add_get("/api/state", self._handle_state)
        r.add_get("/api/stream", self._handle_stream)
        r.add_get("/api/incidents", self._handle_incidents)
        r.add_get("/api/incidents/{id}", self._handle_incident)
        r.add_post("/api/respond", self._handle_respond)
        r.add_get("/api/stats", self._handle_stats)
        r.add_get("/api/timeline", self._handle_timeline)
        r.add_get("/api/db/info", self._handle_db_info)
        r.add_get("/api/db/export/{table}", self._handle_export)
        r.add_get("/api/db/backup", self._handle_backup)
        r.add_post("/api/db/restore", self._handle_restore)
        r.add_post("/api/db/reimport", self._handle_reimport)
        r.add_delete("/api/db/reset", self._handle_reset)
        r.add_post("/api/speech/test", self._handle_speech_test)
        r.add_post("/api/speech/abbreviations/import-list", self._handle_speech_import_list)
        r.add_get("/api/speech/{table}", self._handle_speech_list)
        r.add_post("/api/speech/{table}", self._handle_speech_save)
        r.add_post("/api/speech/{table}/import", self._handle_speech_import_csv)
        r.add_delete("/api/speech/{table}/{id:[0-9]+}", self._handle_speech_delete)
        r.add_get("/api/map-tiles/{z:[0-9]+}/{x:[0-9]+}/{y:[0-9]+}.png", self._handle_map_tile)

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        logger.info(f"Web UI listening on port {self.port}")

    async def stop(self) -> None:
        if self._runner:
            with contextlib.suppress(Exception):
                await self._runner.cleanup()
            self._runner = None

    # ------------------------------------------------------------------ pages

    async def _handle_index(self, request: web.Request) -> web.Response:
        # Behind ingress the dashboard lives under X-Ingress-Path; the <link>/<script> tags need it
        base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        html = INDEX_HTML.replace("__ADDON_VERSION__", ADDON_VERSION).replace("__BASE__", base)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    def _payload(self) -> dict:
        latest = [self.tracker.enrich(i) for i in self.db.list_incidents(limit=LATEST)["items"]]
        return {
            "status": self.tracker.status(),
            "current": self.tracker.snapshot(),
            "latest": latest,
            "counts": self.db.counts(),
            "server_time": now_ts(),
        }

    async def _handle_state(self, request: web.Request) -> web.Response:
        return web.json_response(self._payload())

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        await response.prepare(request)
        version = -1
        last_sent = None
        try:
            while True:
                if version != self.tracker.version:
                    version = self.tracker.version
                    payload = self._payload()
                    payload.pop("server_time")
                    data = json.dumps(payload, default=str)
                    if data != last_sent:
                        await response.write(f"data: {data}\n\n".encode())
                        last_sent = data
                    else:
                        await response.write(b"event: ping\ndata: {}\n\n")
                else:
                    await response.write(b"event: ping\ndata: {}\n\n")
                await self.tracker.wait_changed(version, STREAM_HEARTBEAT)
        except (ConnectionResetError, TimeoutError, asyncio.CancelledError):
            pass
        return response

    async def _handle_incidents(self, request: web.Request) -> web.Response:
        result = self.db.list_incidents(
            limit=_int_param(request, "limit", 50, maximum=500),
            before=request.query.get("before") or None,
            search=(request.query.get("search") or "").strip(),
        )
        result["items"] = [self.tracker.enrich(i) for i in result["items"]]
        return web.json_response(result)

    async def _handle_incident(self, request: web.Request) -> web.Response:
        incident = self.db.get_incident(request.match_info["id"])
        if incident is None:
            return web.json_response({"error": "Incident not found"}, status=404)
        return web.json_response(self.tracker.enrich(incident))

    async def _handle_respond(self, request: web.Request) -> web.Response:
        try:
            action = (await request.json()).get("action")
        except (ValueError, AttributeError):
            action = None
        if action not in ("acknowledge", "reject"):
            return web.json_response({"error": "Unknown action"}, status=400)
        try:
            await self.tracker.respond(action)
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=409)
        except HAClientError as e:
            return web.json_response({"error": f"Home Assistant refused: {e}"}, status=502)
        return web.json_response({"ok": True})

    async def _handle_stats(self, request: web.Request) -> web.Response:
        period = request.query.get("period", "30")
        return web.json_response(await asyncio.to_thread(stats.compute, self.db, period))

    async def _handle_timeline(self, request: web.Request) -> web.Response:
        hours = _int_param(request, "hours", 48, maximum=24 * 31)
        return web.json_response({"hours": hours, "items": stats.timeline(self.db, hours)})

    # ------------------------------------------------------------------- data

    async def _handle_db_info(self, request: web.Request) -> web.Response:
        return web.json_response({**self.db.counts(), **self.db.speech_counts(), "backfill": self.tracker.backfill})

    async def _handle_export(self, request: web.Request) -> web.Response:
        table = request.match_info["table"].removesuffix(".csv")
        if table in SPEECH_CSV:
            columns, rows = self.db.speech_csv(table)
        elif table in CSV_TABLES:
            columns, rows = self.db.table_rows(table)
        else:
            return web.json_response({"error": "Unknown table"}, status=404)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(columns)
        writer.writerows(rows)
        name = f"fireservicerota-{table}-{datetime.now():%Y%m%d-%H%M}.csv"
        return web.Response(
            text=out.getvalue(),
            content_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    async def _handle_backup(self, request: web.Request) -> web.StreamResponse:
        path = self.db.db_path + ".backup.tmp"
        try:
            await asyncio.to_thread(self.db.backup_to, path)
            name = f"fireservicerota-{datetime.now():%Y%m%d-%H%M}.db"
            response = web.StreamResponse(
                headers={
                    "Content-Type": "application/vnd.sqlite3",
                    "Content-Disposition": f'attachment; filename="{name}"',
                    "Content-Length": str(os.path.getsize(path)),
                }
            )
            await response.prepare(request)
            with open(path, "rb") as f:
                while chunk := f.read(1024 * 1024):
                    await response.write(chunk)
            await response.write_eof()
            return response
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)

    async def _handle_restore(self, request: web.Request) -> web.Response:
        path = self.db.db_path + ".restore.tmp"
        size = 0
        try:
            reader = await request.multipart()
            with open(path, "wb") as f:
                async for part in reader:
                    if not isinstance(part, aiohttp.BodyPartReader) or part.name != "file":
                        continue
                    while chunk := await part.read_chunk(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_RESTORE_SIZE:
                            return web.json_response({"error": "Backup too large"}, status=400)
                        f.write(chunk)
            if not size:
                return web.json_response({"error": "No file uploaded"}, status=400)
            if problem := self.db.check_backup(path):
                return web.json_response({"error": problem}, status=400)
            await asyncio.to_thread(self.db.replace_with, path)
            # A backup from before 0.2.0 has no speech tables yet: they get the built-in list
            self.tracker.speech.seed()
            counts = self.db.counts()
            logger.info(f"Database restored from an uploaded backup ({size} bytes, {counts['incidents']} incidents)")
            self.tracker.changed()
            return web.json_response({"success": True, "incidents": counts["incidents"]})
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return web.json_response({"error": f"Restore failed: {e}"}, status=500)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.remove(path)

    async def _handle_reimport(self, request: web.Request) -> web.Response:
        if self.tracker.backfill["running"]:
            return web.json_response({"error": "An import is already running"}, status=409)
        days = self.tracker.config.history_days or 30
        self.tracker.background(self.tracker.run_backfill(days=days))
        return web.json_response({"ok": True, "days": days})

    async def _handle_reset(self, request: web.Request) -> web.Response:
        if self.tracker.backfill["running"]:
            return web.json_response({"error": "An import is running, try again when it's done"}, status=409)
        await asyncio.to_thread(self.db.reset)
        logger.info("Database reset from the dashboard")
        self.tracker.changed()
        return web.json_response({"success": True})

    # ----------------------------------------------------------------- speech

    async def _speech_changed(self) -> None:
        self.tracker.speech.reload()
        # The spoken text of the incidents on the dashboards follows the change
        self.tracker.changed()

    async def _handle_speech_test(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except ValueError:
            data = {}
        text = str(data.get("text") or "")[:2000]
        return web.json_response(self.tracker.speech.explain(text, str(data.get("prio") or "") or None))

    async def _handle_speech_list(self, request: web.Request) -> web.Response:
        table = request.match_info["table"]
        if table not in SPEECH_CSV:
            return web.json_response({"error": "Unknown table"}, status=404)
        result = self.db.list_speech(
            table,
            search=(request.query.get("search") or "").strip(),
            page=_int_param(request, "page", 1, maximum=100000),
            per_page=_int_param(request, "per_page", 50, maximum=500),
        )
        speech = self.tracker.speech
        for item in result["items"]:
            item["default"] = is_default(table, item)
            if table == "abbreviations":
                meaning = speech.meaning(item["abbreviation"])
                # As the code is spoken in a text: a rule (e.g. AED -> A E D) comes before the meaning
                item["spoken"] = speech.speak(item["abbreviation"]) if meaning else spell(item["abbreviation"])
                item["certain"] = meaning is not None
            elif table == "tts_replacements":
                item["enabled"] = bool(item["enabled"])
                item["usable"] = bool((item["replacement"] or "").strip())
        return web.json_response(result)

    async def _handle_speech_save(self, request: web.Request) -> web.Response:
        table = request.match_info["table"]
        if table not in SPEECH_CSV:
            return web.json_response({"error": "Unknown table"}, status=404)
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"error": "Invalid request"}, status=400)
        if not isinstance(data, dict):
            return web.json_response({"error": "Invalid request"}, status=400)
        try:
            row_id = data.get("id")
            row_id = int(row_id) if row_id not in (None, "") else None
            row_id = self.db.save_speech(table, data, row_id)
        except (ValueError, TypeError) as e:
            return web.json_response({"error": str(e)}, status=400)
        except sqlite3.Error as e:
            return web.json_response({"error": f"Database error: {e}"}, status=500)
        await self._speech_changed()
        return web.json_response({"success": True, "id": row_id})

    async def _handle_speech_delete(self, request: web.Request) -> web.Response:
        table = request.match_info["table"]
        if table not in SPEECH_CSV:
            return web.json_response({"error": "Unknown table"}, status=404)
        if not self.db.delete_speech(table, int(request.match_info["id"])):
            return web.json_response({"error": "Not found"}, status=404)
        await self._speech_changed()
        return web.json_response({"success": True})

    async def _handle_speech_import_list(self, request: web.Request) -> web.Response:
        """The public abbreviation list (the same one and the same import as the Meldkamer app).
        Codes already in your list get the list's meaning again; your own codes stay."""
        count = abbreviation_import.import_abbreviations(self.db, replace=True)
        await self._speech_changed()
        return web.json_response({"success": True, "count": count})

    async def _handle_speech_import_csv(self, request: web.Request) -> web.Response:
        table = request.match_info["table"]
        if table not in SPEECH_CSV:
            return web.json_response({"error": "Unknown table"}, status=404)
        try:
            data = await request.json()
        except ValueError:
            return web.json_response({"error": "Invalid request"}, status=400)
        try:
            result = self.db.import_speech_csv(table, data.get("csv"), replace=bool(data.get("replace")))
        except ValueError as e:
            return web.json_response({"error": str(e)}, status=400)
        except sqlite3.Error as e:
            return web.json_response({"error": f"Database error: {e}"}, status=500)
        await self._speech_changed()
        logger.info(f"Imported {result['imported']} row(s) into {table} from CSV, skipped {len(result['skipped'])}")
        return web.json_response({"success": True, **result})

    async def _handle_map_tile(self, request: web.Request) -> web.Response:
        """Serve an OpenStreetMap raster tile via Home Assistant's map tile proxy."""
        z, x, y = (int(request.match_info[k]) for k in ("z", "x", "y"))
        if z > MAP_MAX_ZOOM or x >= 2**z or y >= 2**z:
            return web.Response(status=404)
        try:
            body = await self.tracker.ha.get_map_tile(z, x, y)
        except HAClientError as e:
            logger.debug(f"Map tile {z}/{x}/{y} failed: {e}")
            return web.Response(status=502)
        return web.Response(
            body=body, content_type="image/png", headers={"Cache-Control": f"private, max-age={MAP_TILE_MAX_AGE}"}
        )
