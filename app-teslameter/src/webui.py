"""Web dashboard: serves the single-page UI and its JSON API (behind Home Assistant ingress)."""

import asyncio
import contextlib
import csv
import io
import json
import logging
import os
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

import aiohttp
import httpx
from aiohttp import web

from . import history_import, signin_browser, stats
from .billing import ChargingCosts
from .config import ADDON_VERSION, Config
from .database import Database, place_key
from .ha_client import HAClient, HAClientError
from .monitor import Garage
from .mqtt import MqttPublisher
from .tesla_api import TeslaApiError
from .tesla_auth import AuthError, SignOnUnavailable, TeslaAuth

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Home Assistant's ingress proxy: the only client allowed when running as an add-on
INGRESS_ADDRESS = "172.30.32.2"
MAP_MAX_ZOOM = 19
MAP_TILE_MAX_AGE = 7 * 24 * 60 * 60
STREAM_HEARTBEAT = 15
# Points sent for one drive or charge; more only costs transfer time, not detail on screen
MAX_TRACK_POINTS = 1500
MAX_CHART_POINTS = 600

DRIVE_COLUMNS = (
    "id, car_id, start_ts, end_ts, start_place, end_place, start_lat, start_lon, end_lat, end_lon, distance_km,"
    " duration_s, start_soc, end_soc, start_range_km, end_range_km, energy_kwh, speed_max_kmh, speed_avg_kmh,"
    " power_max_kw, power_min_kw, outside_temp_avg, inside_temp_avg, ascent_m, descent_m,"
    " start_odometer_km, end_odometer_km"
)
CHARGE_COLUMNS = (
    "id, car_id, start_ts, end_ts, lat, lon, place, kind, charger_brand, odometer_km, start_soc, end_soc,"
    " start_range_km, end_range_km, energy_added_kwh, energy_used_kwh, power_max_kw, duration_s,"
    " outside_temp_avg, cost, cost_manual, billed_amount, billed_currency"
)


def _int(request: web.Request, name: str, default: int, minimum: int = 0, maximum: int = 2**40) -> int:
    try:
        value = int(request.query.get(name, default))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _json_body(request: web.Request) -> dict:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Expected a JSON object"}), content_type="application/json")
    return body


def _idle_import_progress() -> dict:
    return {
        "running": False,
        "phase": "idle",
        "message": "",
        "drives": 0,
        "charges": 0,
        "positions": 0,
        "error": "",
        "finished_at": None,
    }


def _start_of_day(ts: float) -> int:
    local = time.localtime(ts)
    return int(time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1)))


class WebUI:
    """The add-on's web server."""

    def __init__(
        self,
        port: int,
        config: Config,
        database: Database,
        auth: TeslaAuth,
        garage: Garage,
        mqtt: MqttPublisher,
        ha_client: HAClient,
        costs: ChargingCosts,
    ):
        self.port = port
        self.config = config
        self.database = database
        self.auth = auth
        self.garage = garage
        self.mqtt = mqtt
        self.ha_client = ha_client
        self.costs = costs
        self.app = web.Application(middlewares=[self._ingress_only])
        self._runner: web.AppRunner | None = None
        self._stopping = False
        self._signin: signin_browser.SignInBrowser | None = None
        self._tile_session: aiohttp.ClientSession | None = None
        self._import_task: asyncio.Task | None = None
        self._import_progress: dict = _idle_import_progress()
        self._visited_cache: dict[tuple, list] = {}
        self._setup_routes()

    def _setup_routes(self) -> None:
        get, post = self.app.router.add_get, self.app.router.add_post
        get("/", self._handle_index)
        self.app.router.add_static("/static/", STATIC_DIR)
        self.app.router.add_static("/web/", WEB_DIR)

        get("/api/status", self._handle_status)
        get("/api/stream", self._handle_stream)
        get("/api/map-tiles/{z:[0-9]+}/{x:[0-9]+}/{y:[0-9]+}.png", self._handle_map_tile)

        post("/api/auth/token", self._handle_auth_token)
        post("/api/auth/signout", self._handle_auth_signout)
        get("/api/auth/browser", self._handle_auth_browser)

        get("/api/cars/{car:[0-9]+}/overview", self._handle_overview)
        get("/api/cars/{car:[0-9]+}/track", self._handle_live_track)
        get("/api/cars/{car:[0-9]+}/heat", self._handle_heat)
        post("/api/cars/{car:[0-9]+}/refresh", self._handle_refresh)
        post("/api/cars/{car:[0-9]+}/wake", self._handle_wake)

        get("/api/drives", self._handle_drives)
        get("/api/drives/{id:[0-9]+}", self._handle_drive)
        get("/api/drives/{id:[0-9]+}/gpx", self._handle_drive_gpx)
        self.app.router.add_delete("/api/drives/{id:[0-9]+}", self._handle_delete_drive)

        get("/api/charges", self._handle_charges)
        get("/api/charges/{id:[0-9]+}", self._handle_charge)
        self.app.router.add_patch("/api/charges/{id:[0-9]+}", self._handle_update_charge)
        self.app.router.add_delete("/api/charges/{id:[0-9]+}", self._handle_delete_charge)

        get("/api/stats/insights", self._handle_insights)
        get("/api/stats/periods", self._handle_periods)
        get("/api/stats/visited", self._handle_visited)

        get("/api/export/{table:drives|charges}.csv", self._handle_export)
        get("/api/backup", self._handle_backup)
        post("/api/data/delete", self._handle_delete_data)
        post("/api/charging/import", self._handle_import_charges)
        post("/api/import/history", self._handle_history_import)
        get("/api/import/history", self._handle_history_import_status)

        post("/api/places/apply-zones", self._handle_apply_zones)
        post("/api/places/rename", self._handle_rename_place)

        get("/api/tags", self._handle_tags)
        post("/api/tags", self._handle_tag_save)
        post("/api/tags/delete", self._handle_tag_delete)
        post("/api/charges/{id:[0-9]+}/tag", self._handle_charge_tag)

    async def start(self) -> None:
        # Open dashboards hold a live stream each: don't wait for them when stopping
        self._runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=1.0)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        logger.info(f"Web UI listening on port {self.port}")
        await asyncio.Event().wait()

    def closing(self) -> None:
        """The add-on is stopping: end the live streams of open dashboards."""
        self._stopping = True

    async def stop(self) -> None:
        self._stopping = True
        if self._signin:
            await self._signin.stop()
        if self._tile_session and not self._tile_session.closed:
            await self._tile_session.close()
        if self._runner:
            await self._runner.cleanup()

    @web.middleware
    async def _ingress_only(self, request: web.Request, handler):
        # The dashboard shows where the car is and has been, and can sign in to
        # Tesla: under Home Assistant it only answers the (authenticated) ingress proxy
        if self.ha_client.available and request.remote != INGRESS_ADDRESS:
            return web.Response(status=403, text="Open TeslaMeter from the Home Assistant sidebar")
        return await handler(request)

    # ------------------------------------------------------------------
    # Page, status, live stream
    # ------------------------------------------------------------------
    async def _handle_index(self, request: web.Request) -> web.Response:
        # Behind Home Assistant ingress the dashboard lives under X-Ingress-Path; the
        # <link>/<script> tags in the head can't compute that like the JS does
        base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        html = (WEB_DIR / "index.html").read_text()
        html = html.replace("__ADDON_VERSION__", ADDON_VERSION).replace("__BASE__", base)
        return web.Response(
            text=html,
            content_type="text/html",
            # Never let the browser serve a stale cached page after an add-on update
            headers={"Cache-Control": "no-cache, must-revalidate"},
        )

    def _live_payload(self) -> dict:
        return {
            "auth": self.auth.status(),
            "cars": self.garage.views(),
            "account_error": self.garage.last_error,
            "mqtt": {"enabled": bool(self.mqtt.broker), "connected": self.mqtt.connected, "broker": self.mqtt.broker},
        }

    async def _handle_status(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                **self._live_payload(),
                "version": ADDON_VERSION,
                "units": self.config.units,
                "currency": self.config.currency,
                "cost_per_kwh": self.config.cost_per_kwh,
                "supercharger_costs": self.costs.status(),
                "signin_browser": signin_browser.available(),
                "streaming": self.config.streaming,
                "geocoding": self.config.geocoding,
                "polling": {
                    "idle_minutes": self.config.idle_minutes,
                    "sleep_window_minutes": self.config.sleep_window_minutes,
                },
                "zones": [{"name": z["name"], "radius": round(z["radius"])} for z in self.garage.places.zones],
                "zones_loaded_at": self.garage.places.zones_loaded_at or None,
                "database_bytes": self.database.size_bytes(),
                "counts": self.database.query_one(
                    "SELECT (SELECT COUNT(*) FROM drives) AS drives, (SELECT COUNT(*) FROM charges) AS charges,"
                    " (SELECT COUNT(*) FROM charges WHERE kind = 'DC') AS charges_dc,"
                    " (SELECT COUNT(*) FROM positions) AS positions"
                ),
                # Earliest and latest recorded activity, for the year selector
                "data_span": self.database.query_one(
                    "SELECT MIN(start_ts) AS first_ts, MAX(start_ts) AS last_ts FROM"
                    " (SELECT start_ts FROM drives UNION ALL SELECT start_ts FROM charges)"
                ),
            }
        )

    async def _handle_stream(self, request: web.Request) -> web.StreamResponse:
        """Server-sent events: the cars' live state, pushed whenever it changes."""
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        await response.prepare(request)
        last_sent = None
        last_write = time.monotonic()
        try:
            while not self._stopping:
                data = json.dumps(self._live_payload())
                if data != last_sent:
                    await response.write(f"data: {data}\n\n".encode())
                    last_sent = data
                    last_write = time.monotonic()
                elif time.monotonic() - last_write >= STREAM_HEARTBEAT:
                    await response.write(b"event: ping\ndata: {}\n\n")
                    last_write = time.monotonic()
                await asyncio.sleep(1)
        except (ConnectionResetError, TimeoutError, asyncio.CancelledError):
            pass
        return response

    async def _handle_map_tile(self, request: web.Request) -> web.Response:
        """An OpenStreetMap raster tile via Home Assistant's map tile proxy."""
        z, x, y = (int(request.match_info[k]) for k in ("z", "x", "y"))
        if z > MAP_MAX_ZOOM or x >= 2**z or y >= 2**z:
            return web.Response(status=404)
        try:
            if self.ha_client.available:
                body = await self.ha_client.get_map_tile(z, x, y)
            else:
                body = await self._dev_tile(z, x, y)
        except (HAClientError, aiohttp.ClientError, TimeoutError) as e:
            logger.debug(f"Map tile {z}/{x}/{y} failed: {e}")
            return web.Response(status=502)
        return web.Response(
            body=body, content_type="image/png", headers={"Cache-Control": f"private, max-age={MAP_TILE_MAX_AGE}"}
        )

    async def _dev_tile(self, z: int, x: int, y: int) -> bytes:
        """Outside Home Assistant (development only): fetch the tile from OpenStreetMap directly."""
        if self._tile_session is None or self._tile_session.closed:
            self._tile_session = aiohttp.ClientSession(
                headers={"User-Agent": f"TeslaMeter/{ADDON_VERSION} (development)"},
                timeout=aiohttp.ClientTimeout(total=20),
            )
        async with self._tile_session.get(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png") as resp:
            resp.raise_for_status()
            return await resp.read()

    # ------------------------------------------------------------------
    # Tesla account
    # ------------------------------------------------------------------
    async def _handle_auth_token(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        try:
            await self.auth.use_refresh_token(str(body.get("refresh_token", "")))
        except AuthError as e:
            return _error(f"Tesla did not accept this token ({e}). Use the refresh token, not the access token.")
        except (SignOnUnavailable, httpx.HTTPError, TimeoutError) as e:
            return _error(f"Could not reach Tesla: {type(e).__name__}", 502)
        return web.json_response(self.auth.status())

    async def _handle_auth_signout(self, request: web.Request) -> web.Response:
        self.auth.sign_out()
        return web.json_response(self.auth.status())

    async def _handle_import_charges(self, request: web.Request) -> web.Response:
        """Import the charging sessions Tesla has on record (also used by the first-run import)."""
        if not self.auth.signed_in:
            return _error("Not signed in to Tesla", 409)
        try:
            imported = await self.costs.import_history()
        except AuthError as e:
            return _error(str(e), 409)
        except TeslaApiError as e:
            return _error(f"Tesla's charging history could not be read: {e}", 502)
        return web.json_response({"imported": imported, **self.costs.status()})

    async def _handle_history_import_status(self, request: web.Request) -> web.Response:
        return web.json_response(self._import_progress)

    async def _handle_history_import(self, request: web.Request) -> web.Response:
        """Import history from a PostgreSQL database in the background; the dashboard polls the status."""
        if self._import_progress.get("running"):
            return _error("An import is already running", 409)
        body = await _json_body(request)
        params = {key: body.get(key) for key in ("host", "port", "database", "user", "password", "ssl")}
        if not params.get("host") or not params.get("user"):
            return _error("Enter at least the database host and user")
        self._import_progress = {
            **_idle_import_progress(),
            "running": True,
            "phase": "connecting",
            "message": "Starting…",
        }

        async def worker() -> None:
            try:
                await asyncio.to_thread(history_import.import_all, params, self.database, self._import_progress)
            except history_import.HistoryImportError as e:
                self._import_progress.update(running=False, phase="error", error=str(e))
            except Exception as e:
                logger.exception("History import failed")
                self._import_progress.update(running=False, phase="error", error=f"{type(e).__name__}: {e}")
            # Refresh the monitors so the imported history shows under the matching car
            for monitor in self.garage.monitors.values():
                monitor.poke()

        self._import_task = asyncio.create_task(worker())
        return web.json_response({"started": True})

    async def _handle_auth_browser(self, request: web.Request) -> web.WebSocketResponse:
        """Sign in through the add-on's own browser: its screen goes out, mouse and keyboard come in.

        The session ends when Tesla sends that browser to its app link (signed in),
        when the dashboard closes the connection, or after ten minutes.
        """
        ws = web.WebSocketResponse(heartbeat=20, max_msg_size=64 * 1024)
        await ws.prepare(request)
        if not signin_browser.available():
            await ws.send_json({"type": "error", "message": "This installation has no sign-in browser."})
            await ws.close()
            return ws
        if self._signin:
            # One sign-in at a time: a new one replaces the old
            await self._signin.stop()
        browser = signin_browser.SignInBrowser()
        self._signin = browser
        helpers: list[asyncio.Task] = []
        try:
            await ws.send_json({"type": "status", "message": "Starting the browser…"})
            await browser.start(self.auth.begin_login())
            await ws.send_json(
                {"type": "ready", "width": signin_browser.VIEW_WIDTH, "height": signin_browser.VIEW_HEIGHT}
            )
            inputs = asyncio.create_task(self._signin_inputs(ws, browser))
            helpers = [inputs, asyncio.create_task(self._signin_frames(ws, browser))]
            done, _ = await asyncio.wait(
                {inputs, browser.callback}, timeout=signin_browser.SESSION_TIMEOUT, return_when=asyncio.FIRST_COMPLETED
            )
            if browser.callback in done and not browser.callback.cancelled() and not browser.callback.exception():
                await ws.send_json({"type": "status", "message": "Signed in at Tesla, connecting…"})
                await self.auth.complete_login(browser.callback.result(), browser)
                await ws.send_json({"type": "done"})
            elif not done:
                await ws.send_json({"type": "error", "message": "The sign-in took too long. Start again."})
            elif browser.callback in done:
                await ws.send_json({"type": "error", "message": "The sign-in browser stopped. Start again."})
        except AuthError as e:
            await ws.send_json({"type": "error", "message": str(e)})
        except (RuntimeError, OSError, TimeoutError, aiohttp.ClientError, httpx.HTTPError) as e:
            logger.warning(f"Sign-in browser: {type(e).__name__}: {e}")
            with contextlib.suppress(Exception):
                await ws.send_json({"type": "error", "message": f"The sign-in browser failed: {e}"})
        finally:
            for task in helpers:
                task.cancel()
            await browser.stop()
            if self._signin is browser:
                self._signin = None
            if not ws.closed:
                await ws.close()
        return ws

    @staticmethod
    async def _signin_frames(ws: web.WebSocketResponse, browser: signin_browser.SignInBrowser) -> None:
        """Send the browser's screen to the dashboard whenever it changed."""
        last = None
        with contextlib.suppress(RuntimeError, ConnectionError, TimeoutError):
            while not ws.closed:
                frame = await browser.frame()
                if frame and frame != last:
                    await ws.send_bytes(frame)
                    last = frame
                await asyncio.sleep(signin_browser.FRAME_INTERVAL)

    @staticmethod
    async def _signin_inputs(ws: web.WebSocketResponse, browser: signin_browser.SignInBrowser) -> None:
        """Pass the dashboard's mouse and keyboard on to the browser. Returns when the dashboard disconnects."""
        async for message in ws:
            if message.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                event = json.loads(message.data)
                if isinstance(event, dict):
                    await browser.handle_input(event)
            except (ValueError, TypeError, RuntimeError, TimeoutError):
                # A malformed or late event is not worth ending the sign-in for (and is never logged)
                continue

    # ------------------------------------------------------------------
    # Cars
    # ------------------------------------------------------------------
    def _car_id(self, request: web.Request) -> int:
        """The car a request is about: ?car= or the path, else the first one."""
        value = request.match_info.get("car") or request.query.get("car")
        try:
            return int(value)
        except (TypeError, ValueError):
            return next(iter(self.garage.monitors), 0)

    def _efficiency(self, car_id: int) -> float:
        monitor = self.garage.monitors.get(car_id)
        return monitor.efficiency if monitor else 160.0

    async def _handle_overview(self, request: web.Request) -> web.Response:
        """What the dashboard shows next to the live state: recent totals, activity and tracks."""
        car_id = self._car_id(request)
        now = int(time.time())
        today = _start_of_day(now)
        activity = self.database.query(
            "SELECT 'drive' AS type, id, start_ts, end_ts, start_place AS place_from, end_place AS place,"
            " distance_km, duration_s, energy_kwh, NULL AS kind, start_soc, end_soc FROM drives"
            " WHERE car_id = :car AND end_ts IS NOT NULL"
            " UNION ALL"
            " SELECT 'charge', id, start_ts, end_ts, NULL, place, NULL, duration_s, energy_added_kwh, kind,"
            " start_soc, end_soc FROM charges WHERE car_id = :car AND end_ts IS NOT NULL"
            " ORDER BY start_ts DESC LIMIT 12",
            {"car": car_id},
        )
        tracks = []
        for drive in self.database.query(
            "SELECT id FROM drives WHERE car_id = ? AND start_ts >= ? ORDER BY start_ts", (car_id, now - 86400)
        ):
            points = self.database.drive_positions(drive["id"], max_points=300)
            tracks.append({"id": drive["id"], "points": [[p["lat"], p["lon"]] for p in points]})
        return web.json_response(
            {
                "today": stats.summary(self.database, car_id, today, now + 1),
                "week": stats.summary(self.database, car_id, today - 6 * 86400, now + 1),
                "month": stats.summary(self.database, car_id, today - 29 * 86400, now + 1),
                "activity": activity,
                "tracks": tracks,
            }
        )

    async def _handle_live_track(self, request: web.Request) -> web.Response:
        """The positions of the drive in progress, optionally only those after ?after=."""
        monitor = self.garage.monitors.get(self._car_id(request))
        if not monitor or not monitor.drive_id:
            return web.json_response({"drive_id": None, "points": []})
        points = self.database.drive_positions(monitor.drive_id, after_ts=_int(request, "after", 0))
        return web.json_response(
            {"drive_id": monitor.drive_id, "points": [[p["lat"], p["lon"], p["ts"]] for p in points]}
        )

    async def _handle_heat(self, request: web.Request) -> web.Response:
        """A heatmap of where this car has been, over the last ?days= (0 = all)."""
        car_id = int(request.match_info["car"])
        days = _int(request, "days", 0, 0, 3650)
        since = int(time.time()) - days * 86400 if days > 0 else 0
        return web.json_response({"points": self.database.heat_positions(car_id, since)})

    async def _handle_refresh(self, request: web.Request) -> web.Response:
        monitor = self.garage.monitors.get(self._car_id(request))
        if not monitor:
            return _error("Unknown car", 404)
        monitor.poke()
        return web.json_response({"success": True})

    async def _handle_wake(self, request: web.Request) -> web.Response:
        monitor = self.garage.monitors.get(self._car_id(request))
        if not monitor:
            return _error("Unknown car", 404)
        try:
            await monitor.wake_up()
        except (AuthError, TeslaApiError) as e:
            return _error(str(e), 502)
        return web.json_response({"success": True})

    # ------------------------------------------------------------------
    # Drives
    # ------------------------------------------------------------------
    def _range(self, request: web.Request) -> tuple[int, int]:
        return _int(request, "from", 0), _int(request, "to", int(time.time()) + 86400)

    async def _handle_drives(self, request: web.Request) -> web.Response:
        car_id = self._car_id(request)
        start, end = self._range(request)
        where = "car_id = ? AND end_ts IS NOT NULL AND start_ts >= ? AND start_ts < ?"
        items = self.database.query(
            f"SELECT {DRIVE_COLUMNS} FROM drives WHERE {where} ORDER BY start_ts DESC LIMIT ? OFFSET ?",
            (car_id, start, end, _int(request, "limit", 200, 1, 2000), _int(request, "offset", 0)),
        )
        return web.json_response({"items": items, "totals": stats.summary(self.database, car_id, start, end)})

    async def _handle_drive(self, request: web.Request) -> web.Response:
        drive_id = int(request.match_info["id"])
        drive = self.database.query_one(f"SELECT {DRIVE_COLUMNS} FROM drives WHERE id = ?", (drive_id,))
        if not drive:
            return _error("Drive not found", 404)
        columns = ("ts", "lat", "lon", "speed_kmh", "power_kw", "elevation_m", "soc", "outside_temp", "inside_temp")
        points = self.database.drive_positions(drive_id, max_points=MAX_TRACK_POINTS)
        return web.json_response(
            {"drive": drive, "columns": columns, "points": [[p[column] for column in columns] for p in points]}
        )

    async def _handle_drive_gpx(self, request: web.Request) -> web.Response:
        """The drive as a GPX track, for other mapping tools."""
        drive_id = int(request.match_info["id"])
        drive = self.database.query_one("SELECT start_ts, start_place, end_place FROM drives WHERE id = ?", (drive_id,))
        if not drive:
            return _error("Drive not found", 404)
        name = f"{drive['start_place'] or 'Drive'} - {drive['end_place'] or drive_id}"
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<gpx version="1.1" creator="TeslaMeter" xmlns="http://www.topografix.com/GPX/1/1">',
            f"<trk><name>{xml_escape(name)}</name><trkseg>",
        ]
        for p in self.database.drive_positions(drive_id):
            stamp = datetime.fromtimestamp(p["ts"], UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            elevation = f"<ele>{p['elevation_m']:.1f}</ele>" if p["elevation_m"] is not None else ""
            lines.append(f'<trkpt lat="{p["lat"]:.6f}" lon="{p["lon"]:.6f}">{elevation}<time>{stamp}</time></trkpt>')
        lines.append("</trkseg></trk></gpx>")
        day = time.strftime("%Y%m%d-%H%M", time.localtime(drive["start_ts"]))
        return web.Response(
            text="\n".join(lines),
            content_type="application/gpx+xml",
            headers={"Content-Disposition": f'attachment; filename="drive-{day}.gpx"'},
        )

    async def _handle_delete_drive(self, request: web.Request) -> web.Response:
        drive_id = int(request.match_info["id"])
        if any(monitor.drive_id == drive_id for monitor in self.garage.monitors.values()):
            return _error("This drive is still in progress", 409)
        return web.json_response({"success": self.database.delete_drive(drive_id)})

    # ------------------------------------------------------------------
    # Charges
    # ------------------------------------------------------------------
    async def _handle_charges(self, request: web.Request) -> web.Response:
        car_id = self._car_id(request)
        start, end = self._range(request)
        items = self.database.query(
            f"SELECT {CHARGE_COLUMNS} FROM charges WHERE car_id = ? AND end_ts IS NOT NULL"
            " AND start_ts >= ? AND start_ts < ? ORDER BY start_ts DESC LIMIT ? OFFSET ?",
            (car_id, start, end, _int(request, "limit", 200, 1, 2000), _int(request, "offset", 0)),
        )
        return web.json_response({"items": items, "totals": stats.summary(self.database, car_id, start, end)})

    async def _handle_charge(self, request: web.Request) -> web.Response:
        charge_id = int(request.match_info["id"])
        charge = self.database.query_one(f"SELECT {CHARGE_COLUMNS} FROM charges WHERE id = ?", (charge_id,))
        if not charge:
            return _error("Charge not found", 404)
        columns = ("ts", "soc", "power_kw", "voltage", "current", "energy_added_kwh", "range_km")
        samples = self.database.charge_samples(charge_id, max_points=MAX_CHART_POINTS)
        # How many times this car charged at this same place (by name, else within ~100 m)
        place = charge.get("place")
        if place:
            site = self.database.query_one(
                "SELECT COUNT(*) AS visits, SUM(energy_added_kwh) AS energy, SUM(cost) AS cost FROM charges"
                " WHERE car_id = ? AND end_ts IS NOT NULL AND place = ?",
                (charge["car_id"], place),
            )
        else:
            site = self.database.query_one(
                "SELECT COUNT(*) AS visits, SUM(energy_added_kwh) AS energy, SUM(cost) AS cost FROM charges"
                " WHERE car_id = ? AND end_ts IS NOT NULL AND ROUND(lat, 3) = ROUND(?, 3) AND ROUND(lon, 3) = ROUND(?, 3)",
                (charge["car_id"], charge.get("lat"), charge.get("lon")),
            )
        charge["site"] = site
        # The location's price tag (so the detail view can show and change it)
        key = place_key(charge.get("place"), charge.get("lat"), charge.get("lon"))
        charge["location_key"] = key
        charge["tag"] = self.database.location_tag(key) if key else None
        charge["billed"] = charge.get("billed_amount") is not None
        return web.json_response(
            {"charge": charge, "columns": columns, "points": [[s[column] for column in columns] for s in samples]}
        )

    async def _handle_update_charge(self, request: web.Request) -> web.Response:
        """Set what a charging session cost (an empty value goes back to the billed or calculated cost)."""
        charge_id = int(request.match_info["id"])
        body = await _json_body(request)
        charge = self.database.query_one(
            "SELECT place, lat, lon, energy_used_kwh, billed_amount, billed_currency FROM charges WHERE id = ?",
            (charge_id,),
        )
        if not charge:
            return _error("Charge not found", 404)
        cost = body.get("cost")
        if cost in (None, ""):
            price = self.config.cost_per_kwh
            if charge["billed_amount"] is not None and charge["billed_currency"] == self.config.currency.upper():
                # What Tesla billed beats the estimate
                restored = charge["billed_amount"]
            else:
                rate, have = self.database.effective_rate(charge["place"], charge["lat"], charge["lon"], price)
                restored = round((charge["energy_used_kwh"] or 0) * rate, 2) if have else None
            self.database.execute("UPDATE charges SET cost = ?, cost_manual = 0 WHERE id = ?", (restored, charge_id))
        else:
            try:
                cost = round(float(cost), 2)
            except (TypeError, ValueError):
                return _error("Cost must be a number")
            if not 0 <= cost < 100000:
                return _error("Cost is out of range")
            self.database.execute("UPDATE charges SET cost = ?, cost_manual = 1 WHERE id = ?", (cost, charge_id))
        return web.json_response(
            self.database.query_one(f"SELECT {CHARGE_COLUMNS} FROM charges WHERE id = ?", (charge_id,))
        )

    async def _handle_apply_zones(self, request: web.Request) -> web.Response:
        """Rename the recorded places that lie inside a Home Assistant zone after the zone."""
        places = self.garage.places
        if not places.zones:
            return _error("No Home Assistant zones are loaded; see the add-on log", 409)
        result = await asyncio.to_thread(places.apply_zones)
        logger.info(f"Zones applied to recorded places: {result}")
        return web.json_response(result)

    async def _handle_rename_place(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        old, new = str(body.get("from") or "").strip(), str(body.get("to") or "").strip()
        if not old or not new:
            return _error("Enter the current name and the new name")
        if len(new) > 120:
            return _error("That name is too long")
        result = await asyncio.to_thread(self.garage.places.rename_place, old, new)
        logger.info(f"Place renamed: {old!r} -> {new!r} ({result})")
        return web.json_response(result)

    async def _handle_tags(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "tags": self.database.list_tags(),
                "default_price": self.config.cost_per_kwh,
                "currency": self.config.currency,
            }
        )

    async def _handle_tag_save(self, request: web.Request) -> web.Response:
        """Create or update a price tag, then recalculate the estimated sessions."""
        body = await _json_body(request)
        name = str(body.get("name") or "").strip()
        if not name:
            return _error("A tag needs a name")
        if len(name) > 40:
            return _error("That name is too long")
        price = body.get("price")
        if price in (None, ""):
            price = None
        else:
            try:
                price = round(float(price), 4)
            except (TypeError, ValueError):
                return _error("Price must be a number")
            if not 0 <= price < 100:
                return _error("Price is out of range")
        self.database.upsert_tag(name, price)
        self.database.recompute_charge_costs(self.config.cost_per_kwh)
        return web.json_response({"tags": self.database.list_tags()})

    async def _handle_tag_delete(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        name = str(body.get("name") or "").strip()
        self.database.delete_tag(name)
        self.database.recompute_charge_costs(self.config.cost_per_kwh)
        return web.json_response({"tags": self.database.list_tags()})

    async def _handle_charge_tag(self, request: web.Request) -> web.Response:
        """Assign (or clear) the price tag of a charging session's location, then recalculate."""
        charge_id = int(request.match_info["id"])
        body = await _json_body(request)
        charge = self.database.query_one("SELECT place, lat, lon FROM charges WHERE id = ?", (charge_id,))
        if not charge:
            return _error("Charge not found", 404)
        key = place_key(charge["place"], charge["lat"], charge["lon"])
        if not key:
            return _error("This session has no location to tag")
        tag = body.get("tag")
        tag = str(tag).strip() if tag else None
        if tag and not self.database.query_one("SELECT 1 FROM charge_tags WHERE name = ?", (tag,)):
            return _error("Unknown tag")
        self.database.set_location_tag(key, tag)
        self.database.recompute_charge_costs(self.config.cost_per_kwh)
        return web.json_response(
            self.database.query_one(f"SELECT {CHARGE_COLUMNS} FROM charges WHERE id = ?", (charge_id,))
        )

    async def _handle_delete_charge(self, request: web.Request) -> web.Response:
        charge_id = int(request.match_info["id"])
        if any(monitor.charge_id == charge_id for monitor in self.garage.monitors.values()):
            return _error("This charge is still in progress", 409)
        deleted = self.database.execute("DELETE FROM charges WHERE id = ?", (charge_id,)) > 0
        return web.json_response({"success": deleted})

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------
    async def _handle_insights(self, request: web.Request) -> web.Response:
        car_id = self._car_id(request)
        start, end = self._range(request)
        result = await asyncio.to_thread(
            stats.insights, self.database, car_id, start, end, int(time.time()), self._efficiency(car_id)
        )
        return web.json_response(result)

    async def _handle_periods(self, request: web.Request) -> web.Response:
        bucket = request.query.get("bucket", "month")
        if bucket not in stats.BUCKETS:
            return _error("Unknown bucket")
        start, end = self._range(request)
        result = await asyncio.to_thread(stats.periods, self.database, self._car_id(request), bucket, start, end)
        return web.json_response(result)

    async def _handle_visited(self, request: web.Request) -> web.Response:
        """The cells for the "where you've been" map. Remembered per range until a drive is mapped or
        removed, so switching between periods doesn't aggregate the same cells again and again."""
        car_id = self._car_id(request)
        start, end = self._range(request)
        now = int(time.time())
        pending = self.database.unmapped_drives()
        mapped = self.database.query_one("SELECT COUNT(*) AS n, COALESCE(SUM(cell_count), 0) AS cells FROM drives")
        # A range that runs up to now is the same range a minute later
        key = (car_id, start, None if end >= now else end, mapped["n"], mapped["cells"])
        points = self._visited_cache.get(key)
        if points is None:
            points = await asyncio.to_thread(stats.visited, self.database, car_id, start, end)
            if len(self._visited_cache) >= 24:
                self._visited_cache.pop(next(iter(self._visited_cache)))
            self._visited_cache[key] = points
        return web.json_response({"points": points, "pending": pending})

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    async def _handle_export(self, request: web.Request) -> web.Response:
        """All drives or charges of a car as CSV (metric units, local times)."""
        table = request.match_info["table"]
        columns = (DRIVE_COLUMNS if table == "drives" else CHARGE_COLUMNS).replace(" ", "").split(",")
        rows = self.database.query(
            f"SELECT {', '.join(columns)} FROM {table} WHERE car_id = ? AND end_ts IS NOT NULL ORDER BY start_ts",
            (self._car_id(request),),
        )
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(columns)
        for row in rows:
            for key in ("start_ts", "end_ts"):
                row[key] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(row[key]))
            writer.writerow([row[column] for column in columns])
        return web.Response(
            text=buffer.getvalue(),
            content_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="teslameter-{table}.csv"'},
        )

    async def _handle_delete_data(self, request: web.Request) -> web.Response:
        """Remove everything recorded. The cars and the Tesla sign-in stay, so logging continues."""
        if any(monitor.drive_id or monitor.charge_id for monitor in self.garage.monitors.values()):
            return _error("A drive or charge is in progress; try again when the car is parked", 409)
        if self._import_progress.get("running"):
            return _error("An import is running; wait until it has finished", 409)
        await asyncio.to_thread(self.database.delete_recorded_data)
        # The state log starts again from the state each car is in now
        now = int(time.time())
        for monitor in self.garage.monitors.values():
            if monitor.state and monitor.state != "unknown":
                self.database.set_state(monitor.car_id, monitor.state, now)
            monitor.poke()
        logger.warning("All recorded data was deleted from the dashboard")
        return web.json_response({"success": True})

    async def _handle_backup(self, request: web.Request) -> web.StreamResponse:
        """Download a copy of the database (the Tesla tokens are stored elsewhere and not included)."""
        handle, name = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        path = Path(name)
        try:
            await asyncio.to_thread(self.database.backup_to, path)
            body = await asyncio.to_thread(path.read_bytes)
        finally:
            path.unlink(missing_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M")
        return web.Response(
            body=body,
            content_type="application/vnd.sqlite3",
            headers={"Content-Disposition": f'attachment; filename="teslameter-{stamp}.db"'},
        )
