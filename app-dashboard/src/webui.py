"""The dashboard pages, served through Home Assistant's ingress.

The browser only talks to this app: it gets the states and their changes from here, and the
buttons call Home Assistant's services through here.
"""

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiohttp import web

from .config import APP_VERSION, DATA_DIR, Config
from .donate import DonatePrompt
from .ha_client import HAClient, HAClientError, HAWebSocket
from .hub import Hub
from .store import Conflict, Store

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy
INGRESS_ADDRESS = "172.30.32.2"
CALENDAR_MAX_DAYS = 31
CALENDAR_CACHE_SECONDS = 120
FORECAST_CACHE_SECONDS = 600
# The stream's heartbeat, so ingress and the browser keep it open
LIVE_HEARTBEAT = 20
HISTORY_MAX_HOURS = 24 * 31
HISTORY_CACHE_SECONDS = 60
# What a dashboard's buttons may do; anything else (locks, alarms, shell commands) stays in Home Assistant
SERVICES = {
    "light": {"turn_on", "turn_off", "toggle"},
    "switch": {"turn_on", "turn_off", "toggle"},
    "input_boolean": {"turn_on", "turn_off", "toggle"},
    "fan": {"turn_on", "turn_off", "toggle", "set_percentage"},
    "cover": {"open_cover", "close_cover", "stop_cover", "set_cover_position"},
    "climate": {"set_temperature", "set_hvac_mode", "turn_on", "turn_off"},
    "scene": {"turn_on"},
    "script": {"turn_on"},
    "automation": {"trigger", "toggle"},
    "button": {"press"},
    "input_button": {"press"},
    "number": {"set_value"},
    "input_number": {"set_value"},
    "media_player": {
        "media_play_pause",
        "media_next_track",
        "media_previous_track",
        "volume_set",
        "turn_on",
        "turn_off",
        "toggle",
    },
}


def buckets(history: list[dict], start: float, end: float, step: float, agg: str) -> list[list]:
    """The numeric states of one entity as one value per step: its mean, or how much it rose."""
    samples = []
    for item in history:
        try:
            value = float(item["state"])
            ts = datetime.fromisoformat(item.get("last_changed") or item["last_updated"]).timestamp()
        except (KeyError, TypeError, ValueError):
            continue
        samples.append((ts, value))
    samples.sort()
    out, i, last = [], 0, None
    # A reading from before the first step counts as where it starts
    t = start
    while t < end - 1:
        inside = []
        while i < len(samples) and samples[i][0] < t + step:
            inside.append(samples[i][1])
            i += 1
        if agg == "delta":
            before = last
            if inside:
                last = inside[-1]
                first = before if before is not None else inside[0]
                # A counter that started again (a daily total at midnight) counts from zero
                value = last - first if last >= first else last
            else:
                value = 0 if before is not None else None
        else:
            if inside:
                last = inside[-1]
                value = sum(inside) / len(inside)
            else:
                value = last
        out.append([round(t), None if value is None else round(value, 3)])
        t += step
    return out


class WebUI:
    def __init__(self, port: int, config: Config, client: HAClient, ws: HAWebSocket, hub: Hub, store: Store):
        self.port = port
        self.config = config
        self.client = client
        self.ws = ws
        self.hub = hub
        self.store = store
        self._history_cache: dict[tuple, tuple[float, list]] = {}
        self._calendar_cache: dict[tuple, tuple[float, list]] = {}
        self._forecast_cache: dict[tuple, tuple[float, list]] = {}
        self.app = web.Application(client_max_size=2 * 1024 * 1024)
        r = self.app.router
        r.add_get("/", self._index)
        r.add_get("/api/boot", self._boot)
        r.add_get("/api/live", self._live)
        r.add_post("/api/doc", self._save_doc)
        r.add_post("/api/service", self._service)
        r.add_get("/api/history", self._history)
        r.add_get("/api/calendar", self._calendar)
        r.add_get("/api/forecast", self._forecast)
        r.add_get("/api/picture", self._picture)
        r.add_get("/api/camera", self._camera)
        r.add_get("/api/camera-stream", self._camera_stream)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        r.add_get("/favicon.ico", self._favicon)
        r.add_static("/static", WEB_DIR)

    async def start(self) -> None:
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info(f"Dashboard on port {self.port}")
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    async def _favicon(self, request: web.Request) -> web.StreamResponse:
        return web.FileResponse(WEB_DIR / "icon.png", headers={"Cache-Control": "public, max-age=86400"})

    async def _boot(self, request: web.Request) -> web.Response:
        loaded = self.hub.loaded_at is not None
        return web.json_response(
            {
                "version": APP_VERSION,
                "now": time.time(),
                "connected": self.ws.connected,
                "error": self.ws.last_error or self.hub.error,
                "loaded": loaded,
                "doc": self.store.ensure(self.hub.states, self.hub.areas) if loaded or self.store.doc else None,
                "states": self.hub.states,
                "areas": self.hub.areas,
                "hidden": sorted(self.hub.hidden),
            }
        )

    async def _live(self, request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(
            headers={"Content-Type": "text/event-stream", "Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )
        await response.prepare(request)
        queue = self.hub.listen()
        try:
            while True:
                try:
                    message = await asyncio.wait_for(queue.get(), LIVE_HEARTBEAT)
                    batch = [message]
                    while not queue.empty() and len(batch) < 200:
                        batch.append(queue.get_nowait())
                    await response.write(f"data: {json.dumps(batch)}\n\n".encode())
                except TimeoutError:
                    await response.write(b": keep-alive\n\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.hub.leave(queue)
        return response

    @staticmethod
    async def _change_body(request: web.Request) -> dict:
        """What a button or the designer sent: only from Home Assistant's signed-in ingress, as JSON."""
        # DASHBOARD_DEV is only set for development against tools/mock_ha.py
        dev = bool(os.environ.get("DASHBOARD_DEV"))
        if os.environ.get("SUPERVISOR_TOKEN") and not dev and request.remote != INGRESS_ADDRESS:
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

    async def _save_doc(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        try:
            doc = self.store.save(body["doc"], int(body["rev"]))
        except Conflict:
            return web.json_response({"error": "conflict", "doc": self.store.doc}, status=409)
        except (KeyError, TypeError, ValueError) as e:
            return web.json_response({"error": str(e)}, status=400)
        self.hub.publish({"type": "doc", "rev": doc["rev"]})
        return web.json_response({"rev": doc["rev"]})

    async def _service(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        try:
            domain, service, entity_id = str(body["domain"]), str(body["service"]), str(body["entity_id"])
            data = body.get("data") or {}
            if not isinstance(data, dict):
                raise ValueError("data")
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "Bad request"}, status=400)
        if service not in SERVICES.get(domain, ()) or entity_id not in self.hub.states:
            return web.json_response({"error": f"{domain}.{service} is not something a dashboard does"}, status=403)
        try:
            await self.ws.call(
                {
                    "type": "call_service",
                    "domain": domain,
                    "service": service,
                    "service_data": data,
                    "target": {"entity_id": entity_id},
                }
            )
        except HAClientError as e:
            return web.json_response({"error": str(e)}, status=502)
        return web.json_response({"ok": True})

    async def _history(self, request: web.Request) -> web.Response:
        entity_id = request.query.get("entity", "")
        if entity_id not in self.hub.states:
            return web.json_response({"error": "Unknown entity"}, status=404)
        try:
            hours = min(HISTORY_MAX_HOURS, max(1, int(request.query.get("hours", "24"))))
        except ValueError:
            return web.json_response({"error": "Bad request"}, status=400)
        agg = "delta" if request.query.get("agg") == "delta" else "mean"
        daily = request.query.get("step") == "day"
        key = (entity_id, hours, agg, daily)
        cached = self._history_cache.get(key)
        if cached and time.time() - cached[0] < HISTORY_CACHE_SECONDS:
            return web.json_response({"points": cached[1]})
        now = datetime.now().astimezone()
        if daily:
            # Whole days in Home Assistant's time zone, today included
            days = max(1, round(hours / 24))
            start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
            end, step = start + timedelta(days=days), 86400.0
        else:
            start, end = now - timedelta(hours=hours), now
            step = hours * 3600 / 120
        try:
            history = await self.client.get_history(entity_id, start.astimezone(UTC), now.astimezone(UTC))
        except HAClientError as e:
            return web.json_response({"error": str(e)}, status=502)
        points = buckets(history, start.timestamp(), end.timestamp(), step, agg)
        self._history_cache[key] = (time.time(), points)
        if len(self._history_cache) > 200:
            self._history_cache.pop(next(iter(self._history_cache)))
        return web.json_response({"points": points})

    # ------------------------------------------------------------------
    # Calendars, the weather ahead, pictures and cameras
    # ------------------------------------------------------------------
    def _entity(self, request: web.Request, domain: str | None = None) -> str:
        entity_id = request.query.get("entity", "")
        if entity_id not in self.hub.states or (domain and not entity_id.startswith(domain + ".")):
            raise web.HTTPNotFound(text="Unknown entity")
        return entity_id

    async def _calendar(self, request: web.Request) -> web.Response:
        entities = [e for e in request.query.get("entities", "").split(",") if e.startswith("calendar.")][:12]
        entities = [e for e in entities if e in self.hub.states]
        try:
            days = min(CALENDAR_MAX_DAYS, max(1, int(request.query.get("days", "7"))))
        except ValueError:
            return web.json_response({"error": "Bad request"}, status=400)
        key = (tuple(entities), days)
        cached = self._calendar_cache.get(key)
        if cached and time.time() - cached[0] < CALENDAR_CACHE_SECONDS:
            return web.json_response({"events": cached[1]})
        start = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        events = []
        try:
            for entity_id in entities:
                for item in await self.client.get_events(entity_id, start, start + timedelta(days=days)):
                    begin, end = item.get("start") or {}, item.get("end") or {}
                    events.append(
                        {
                            "entity": entity_id,
                            "summary": str(item.get("summary") or ""),
                            "location": str(item.get("location") or ""),
                            "all_day": "date" in begin,
                            "start": begin.get("dateTime") or begin.get("date"),
                            "end": end.get("dateTime") or end.get("date"),
                        }
                    )
        except HAClientError as e:
            return web.json_response({"error": str(e)}, status=502)
        events = sorted(
            (e for e in events if e["start"]), key=lambda e: (e["start"][:10], not e["all_day"], e["start"])
        )
        self._calendar_cache[key] = (time.time(), events)
        if len(self._calendar_cache) > 50:
            self._calendar_cache.pop(next(iter(self._calendar_cache)))
        return web.json_response({"events": events})

    async def _forecast(self, request: web.Request) -> web.Response:
        entity_id = self._entity(request, "weather")
        kind = "hourly" if request.query.get("type") == "hourly" else "daily"
        cached = self._forecast_cache.get((entity_id, kind))
        if cached and time.time() - cached[0] < FORECAST_CACHE_SECONDS:
            return web.json_response({"forecast": cached[1]})
        try:
            result = await self.ws.call(
                {
                    "type": "call_service",
                    "domain": "weather",
                    "service": "get_forecasts",
                    "service_data": {"type": kind},
                    "target": {"entity_id": entity_id},
                    "return_response": True,
                }
            )
        except HAClientError as e:
            return web.json_response({"error": str(e)}, status=502)
        forecast = (((result or {}).get("response") or {}).get(entity_id) or {}).get("forecast") or []
        forecast = forecast[:48] if kind == "hourly" else forecast[:10]
        self._forecast_cache[(entity_id, kind)] = (time.time(), forecast)
        return web.json_response({"forecast": forecast})

    async def _picture(self, request: web.Request) -> web.Response:
        """An entity's picture (an album cover, a person's photo), fetched with the app's token."""
        path = self.hub.pictures.get(self._entity(request), "")
        # Only Home Assistant's own pictures: never fetch an address a state happens to carry
        if not path.startswith("/api/"):
            raise web.HTTPNotFound(text="No picture")
        try:
            body, kind = await self.client.get_bytes(path[4:])
        except HAClientError:
            raise web.HTTPBadGateway(text="No picture") from None
        if not kind.startswith("image/"):
            raise web.HTTPBadGateway(text="Not a picture")
        return web.Response(body=body, content_type=kind, headers={"Cache-Control": "private, max-age=3600"})

    async def _camera(self, request: web.Request) -> web.Response:
        entity_id = self._entity(request, "camera")
        try:
            body, kind = await self.client.get_bytes(f"/camera_proxy/{entity_id}")
        except HAClientError:
            raise web.HTTPBadGateway(text="No picture") from None
        return web.Response(body=body, content_type=kind, headers={"Cache-Control": "no-store"})

    async def _camera_stream(self, request: web.Request) -> web.StreamResponse:
        """The camera's live picture (MJPEG), passed on as it comes."""
        entity_id = self._entity(request, "camera")
        try:
            upstream = await self.client.open_stream(f"/camera_proxy_stream/{entity_id}")
        except HAClientError:
            raise web.HTTPBadGateway(text="No stream") from None
        response = web.StreamResponse(
            headers={
                "Content-Type": upstream.headers.get("Content-Type", "multipart/x-mixed-replace"),
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            }
        )
        try:
            await response.prepare(request)
            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
        except (ConnectionResetError, asyncio.CancelledError, TimeoutError, OSError):
            pass
        finally:
            upstream.release()
        return response
