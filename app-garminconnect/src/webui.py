"""Web dashboard: the single-page UI and its JSON API (behind Home Assistant ingress)."""

import asyncio
import contextlib
import csv
import io
import json
import logging
import os
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import aiohttp
from aiohttp import web

from . import stats
from .account import GarminAccount, SignInError
from .collector import Collector
from .config import ADDON_VERSION, DATA_DIR, Config
from .database import Database, backup_copy
from .datasets import GROUP_TITLES, activity_curves, activity_laps, activity_sets
from .donate import DonatePrompt
from .ha_client import HAClient, HAClientError

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC_DIR = Path(__file__).resolve().parent / "static"
# Home Assistant's ingress proxy: the only client allowed when running as an app
INGRESS_ADDRESS = "172.30.32.2"
MAP_MAX_ZOOM = 19
MAP_TILE_MAX_AGE = 7 * 24 * 60 * 60
MAX_SERIES_METRICS = 16
MAX_SPAN_DAYS = 20 * 366


def _error(message: str, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _day(value: str | None, default: date) -> str:
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        return default.isoformat()


def _span(request: web.Request, default_days: int = 90) -> tuple[str, str]:
    """start and end (days, both included) from the query; never more than MAX_SPAN_DAYS."""
    today = date.today()
    end = _day(request.query.get("end"), today)
    start = _day(request.query.get("start"), date.fromisoformat(end) - timedelta(days=default_days - 1))
    if start > end:
        start, end = end, start
    if (date.fromisoformat(end) - date.fromisoformat(start)).days > MAX_SPAN_DAYS:
        start = (date.fromisoformat(end) - timedelta(days=MAX_SPAN_DAYS)).isoformat()
    return start, end


async def _json_body(request: web.Request) -> dict:
    # Another site's page can't send this content type without the browser asking first
    if request.content_type != "application/json":
        raise web.HTTPUnsupportedMediaType(text=json.dumps({"error": "Send JSON"}), content_type="application/json")
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        body = None
    if not isinstance(body, dict):
        raise web.HTTPBadRequest(text=json.dumps({"error": "Expected a JSON object"}), content_type="application/json")
    return body


class WebUI:
    def __init__(
        self,
        port: int,
        config: Config,
        database: Database,
        account: GarminAccount,
        collector: Collector,
        ha_client: HAClient,
    ):
        self.port = port
        self.config = config
        self.db = database
        self.account = account
        self.collector = collector
        self.ha_client = ha_client
        self.app = web.Application(middlewares=[self._ingress_only])
        self._runner: web.AppRunner | None = None
        self._tile_session: aiohttp.ClientSession | None = None
        # Statistics are worth keeping until the next write to the database
        self._cache: dict[tuple, tuple[int, object]] = {}
        self._setup_routes()

    def _setup_routes(self) -> None:
        get, post = self.app.router.add_get, self.app.router.add_post
        get("/", self._index)
        self.app.router.add_static("/static/", STATIC_DIR)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        self.app.router.add_static("/web/", WEB_DIR)

        get("/api/status", self._status)
        get("/api/map-tiles/{z:[0-9]+}/{x:[0-9]+}/{y:[0-9]+}.png", self._map_tile)

        post("/api/auth/login", self._login)
        post("/api/auth/mfa", self._mfa)
        post("/api/auth/cancel", self._cancel_login)
        post("/api/auth/signout", self._signout)

        get("/api/day", self._day_view)
        get("/api/series", self._series)
        get("/api/metrics", self._metrics)
        get("/api/insights", self._insights)
        get("/api/calendar", self._calendar)
        get("/api/yoy", self._yoy)
        get("/api/activities", self._activities)
        get("/api/activities/summary", self._activity_summary)
        get("/api/activities/heat", self._heat)
        get("/api/activities/{id:[0-9]+}", self._activity)
        get("/api/records", self._records)
        get("/api/events/{kind:[a-z_]+}", self._events)

        post("/api/update", self._update_now)
        post("/api/reparse", self._reparse)
        get("/api/backup", self._backup)
        get("/api/export/{name:daily|activities}.csv", self._export)
        post("/api/data/delete", self._delete_data)

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app, access_log=None, shutdown_timeout=1.0)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        logger.info(f"Web UI listening on port {self.port}")
        await asyncio.Event().wait()

    async def stop(self) -> None:
        if self._tile_session and not self._tile_session.closed:
            await self._tile_session.close()
        if self._runner:
            await self._runner.cleanup()

    @web.middleware
    async def _ingress_only(self, request: web.Request, handler):
        # The dashboard holds health data and signs in to Garmin: under Home Assistant it only
        # answers the (authenticated) ingress proxy
        if self.ha_client.available and request.remote != INGRESS_ADDRESS:
            return web.Response(status=403, text="Open Garmin Connect from the Home Assistant sidebar")
        return await handler(request)

    def _cached(self, key: tuple, compute):
        hit = self._cache.get(key)
        if hit and hit[0] == self.db.generation:
            return hit[1]
        value = compute()
        if len(self._cache) > 64:
            self._cache.clear()
        self._cache[key] = (self.db.generation, value)
        return value

    # ------------------------------------------------------------------
    # Page and status
    # ------------------------------------------------------------------
    async def _index(self, request: web.Request) -> web.Response:
        base = request.headers.get("X-Ingress-Path", "").rstrip("/")
        html = (WEB_DIR / "index.html").read_text()
        html = html.replace("__ADDON_VERSION__", ADDON_VERSION).replace("__BASE__", base)
        return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "version": ADDON_VERSION,
                "account": self.account.status(),
                "collector": self.collector.status(),
                "groups": [
                    {"key": key, "title": title, "enabled": self.config.enabled(key)}
                    for key, title in GROUP_TITLES.items()
                ],
                "counts": self.db.counts(),
                "database_bytes": self.db.size_bytes(),
                "today": date.today().isoformat(),
            }
        )

    async def _map_tile(self, request: web.Request) -> web.Response:
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
                headers={"User-Agent": f"ha-garmin-app/{ADDON_VERSION} (development)"},
                timeout=aiohttp.ClientTimeout(total=20),
            )
        async with self._tile_session.get(f"https://tile.openstreetmap.org/{z}/{x}/{y}.png") as resp:
            if resp.status != 200:
                raise HAClientError(f"tile server answered {resp.status}")
            return await resp.read()

    # ------------------------------------------------------------------
    # Signing in
    # ------------------------------------------------------------------
    async def _login(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        try:
            result = await self.account.login(str(body.get("email") or ""), str(body.get("password") or ""))
        except SignInError as e:
            return _error(str(e), 401)
        return web.json_response({"result": result})

    async def _mfa(self, request: web.Request) -> web.Response:
        body = await _json_body(request)
        try:
            await self.account.submit_mfa(str(body.get("code") or ""))
        except SignInError as e:
            return _error(str(e), 401)
        return web.json_response({"result": "ok"})

    async def _cancel_login(self, request: web.Request) -> web.Response:
        await _json_body(request)
        self.account.cancel_login()
        return web.json_response({"ok": True})

    async def _signout(self, request: web.Request) -> web.Response:
        await _json_body(request)
        self.account.sign_out()
        return web.json_response({"ok": True})

    # ------------------------------------------------------------------
    # Data
    # ------------------------------------------------------------------
    async def _day_view(self, request: web.Request) -> web.Response:
        day = _day(request.query.get("date"), date.today())
        return web.json_response(self._cached(("day", day), lambda: stats.day_view(self.db, day)))

    async def _series(self, request: web.Request) -> web.Response:
        metrics = [m for m in request.query.get("metrics", "").split(",") if m][:MAX_SERIES_METRICS]
        if not metrics:
            return _error("metrics missing")
        bucket = request.query.get("bucket", "day")
        if bucket not in ("day", "week", "month", "year"):
            bucket = "day"
        start, end = _span(request)
        key = ("series", tuple(metrics), start, end, bucket)
        return web.json_response(self._cached(key, lambda: stats.series(self.db, metrics, start, end, bucket)))

    async def _metrics(self, request: web.Request) -> web.Response:
        return web.json_response(self._cached(("metrics",), lambda: stats.metric_catalog(self.db)))

    async def _insights(self, request: web.Request) -> web.Response:
        start, end = _span(request, 365)
        result = await self._compute(("insights", start, end), lambda: stats.insights(self.db, start, end))
        return web.json_response(result)

    async def _calendar(self, request: web.Request) -> web.Response:
        start, end = _span(request, 365)
        metric = request.query.get("metric", "steps")
        return web.json_response(
            self._cached(("calendar", metric, start, end), lambda: stats.calendar(self.db, metric, start, end))
        )

    async def _yoy(self, request: web.Request) -> web.Response:
        metric = request.query.get("metric", "steps")
        return web.json_response(self._cached(("yoy", metric), lambda: stats.year_over_year(self.db, metric)))

    async def _compute(self, key: tuple, compute):
        """Heavier statistics: cached, and computed without holding up the rest of the app too long."""
        hit = self._cache.get(key)
        if hit and hit[0] == self.db.generation:
            return hit[1]
        await asyncio.sleep(0)
        return self._cached(key, compute)

    async def _activities(self, request: web.Request) -> web.Response:
        start, end = _span(request, 365)
        kind = request.query.get("family", "")
        rows = self.db.query(
            "SELECT id, day, start_local, type, name, distance_m, duration_s, moving_s, elevation_gain_m, avg_hr, max_hr,"
            " calories, avg_speed, training_load, aerobic_te, anaerobic_te, vo2max, location, has_track, body_battery"
            " FROM activities WHERE day >= ? AND day <= ? ORDER BY start_ts DESC LIMIT 5000",
            (start, end),
        )
        if kind:
            rows = [r for r in rows if stats.family(r["type"]) == kind]
        for row in rows:
            row["family"] = stats.family(row["type"])
        return web.json_response(rows)

    async def _activity(self, request: web.Request) -> web.Response:
        activity_id = int(request.match_info["id"])
        row = self.db.query_one("SELECT * FROM activities WHERE id = ?", (activity_id,))
        if not row:
            return _error("No such activity", 404)
        row.pop("body", None)
        body = self.db.activity_body(activity_id) or {}
        row["family"] = stats.family(row["type"])
        row["zones_s"] = [body.get(f"hrTimeInZone_{i}") for i in range(1, 6)]
        row["extra"] = {
            k: body.get(k)
            for k in (
                "averageRunningCadenceInStepsPerMinute",
                "avgStrideLength",
                "averageBikingCadenceInRevPerMinute",
                "avgPower",
                "normPower",
                "maxPower",
                "trainingEffectLabel",
                "waterEstimated",
                "minElevation",
                "maxElevation",
                "lapCount",
                "averageSwimCadenceInStrokesPerMinute",
                "activeLengths",
            )
            if body.get(k) is not None
        }
        row["track"] = self.db.track(activity_id) or []
        details = self.db.activity_details(activity_id) or {}
        row["details_fetched"] = bool(details)
        row["laps"] = activity_laps(details.get("splits"))
        row["curves"] = activity_curves(details.get("details"))
        row["sets"] = activity_sets(details.get("sets"))
        weather = details.get("weather") if isinstance(details.get("weather"), dict) else {}
        row["weather"] = (
            {
                "temp": weather.get("temp"),
                "apparent_temp": weather.get("apparentTemp"),
                "humidity": weather.get("relativeHumidity"),
                "wind_speed": weather.get("windSpeed"),
                "wind_from": weather.get("windDirectionCompassPoint"),
                "description": (weather.get("weatherTypeDTO") or {}).get("desc"),
            }
            if weather
            else None
        )
        # What the day looked like around it: the Body Battery and stress curves of that day
        row["day_bb"] = (self.db.intraday("bb", row["day"], row["day"]) or [(row["day"], [])])[0][1]
        return web.json_response(row)

    async def _activity_summary(self, request: web.Request) -> web.Response:
        start, end = _span(request, 365)
        bucket = request.query.get("bucket", "month")
        if bucket not in ("week", "month", "year"):
            bucket = "month"
        key = ("activity_summary", start, end, bucket)
        return web.json_response(await self._compute(key, lambda: stats.activity_summary(self.db, start, end, bucket)))

    async def _heat(self, request: web.Request) -> web.Response:
        start, end = _span(request, 365)
        kind = request.query.get("family", "")
        key = ("heat", start, end, kind)
        points = await self._compute(key, lambda: stats.heat_cells(self.db, start, end, kind))
        # Right after an update from 0.1.1 the earlier tracks are still being mapped
        return web.json_response({"points": points, "pending": len(self.db.unmapped_tracks())})

    async def _records(self, request: web.Request) -> web.Response:
        result = {}
        for name in (
            "personal_records",
            "gear",
            "devices",
            "lifetime",
            "statistics",
            "goals_active",
            "goals_past",
            "lactate_threshold",
            "jetlag_trips",
            "cycling_ftp",
            "workouts",
            "training_plans",
            "upcoming_events",
            "scheduled_workouts",
        ):
            value, fetched = self.db.snapshot(name)
            result[name] = {"data": value, "fetched_at": fetched}
        result["badges"] = self.db.events("badge", limit=500)
        result["races"] = self.db.events("race", limit=200)
        return web.json_response(result)

    async def _events(self, request: web.Request) -> web.Response:
        kind = request.match_info["kind"]
        start, end = _span(request, 3650)
        return web.json_response(self.db.events(kind, start, end, limit=2000))

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------
    async def _update_now(self, request: web.Request) -> web.Response:
        await _json_body(request)
        if not self.account.signed_in:
            return _error("Sign in to Garmin first", 409)
        self.collector.wake()
        return web.json_response({"ok": True})

    async def _reparse(self, request: web.Request) -> web.Response:
        await _json_body(request)
        # On the event loop: the database connection belongs to it
        rows = self.collector.reparse()
        self._cache.clear()
        return web.json_response({"rows": rows})

    async def _backup(self, request: web.Request) -> web.StreamResponse:
        """The whole database as one file. The Garmin sign-in is stored apart and never part of it."""
        handle, name = tempfile.mkstemp(suffix=".db", dir=DATA_DIR)
        os.close(handle)
        target = Path(name)
        try:
            await asyncio.to_thread(backup_copy, self.db.path, target)
            body = target.read_bytes()
        finally:
            with contextlib.suppress(FileNotFoundError):
                target.unlink()
        stamp = time.strftime("%Y%m%d")
        return web.Response(
            body=body,
            content_type="application/vnd.sqlite3",
            headers={"Content-Disposition": f'attachment; filename="garmin-{stamp}.db"'},
        )

    async def _export(self, request: web.Request) -> web.Response:
        out = io.StringIO()
        writer = csv.writer(out)
        if request.match_info["name"] == "daily":
            metrics = [r["metric"] for r in self.db.query("SELECT DISTINCT metric FROM daily ORDER BY metric")]
            rows: dict[str, dict] = {}
            for row in self.db.query("SELECT day, metric, value FROM daily ORDER BY day"):
                rows.setdefault(row["day"], {})[row["metric"]] = row["value"]
            writer.writerow(["day", *metrics])
            for day, values in rows.items():
                writer.writerow([day, *(values.get(m, "") for m in metrics)])
        else:
            columns = [
                "id", "start_local", "type", "name", "distance_m", "duration_s", "moving_s", "elevation_gain_m",
                "avg_hr", "max_hr", "calories", "avg_speed", "max_speed", "steps", "training_load", "aerobic_te",
                "anaerobic_te", "vo2max", "location",
            ]  # fmt: skip
            writer.writerow(columns)
            for row in self.db.query(f"SELECT {', '.join(columns)} FROM activities ORDER BY start_ts"):
                writer.writerow([row[c] for c in columns])
        name = request.match_info["name"]
        return web.Response(
            text=out.getvalue(),
            content_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="garmin-{name}.csv"'},
        )

    async def _delete_data(self, request: web.Request) -> web.Response:
        """The danger zone: delete everything recorded. The page asks for DELETE to be typed; so does this."""
        body = await _json_body(request)
        if body.get("confirm") != "DELETE":
            return _error("Type DELETE to confirm")
        self.db.wipe()
        self._cache.clear()
        self.collector.wake()
        logger.info("All recorded data deleted from the dashboard; the history import starts over")
        return web.json_response({"ok": True})
