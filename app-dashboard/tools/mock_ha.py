"""A stand-in Supervisor + Home Assistant with a small house in it.

For development without Home Assistant (see DOCS.md, Development). Serves just enough of the Core
REST and websocket APIs: the states and state_changed events, the area, device and entity
registries, call_service for what a dashboard's buttons do, and the recorder history. The house:
lights, blinds, a thermostat, scenes, a speaker with album covers, three solar strings with
batteries, two cameras and a doorbell (ring it: GET /mock/ring), two calendars, the sun, and the
weather with its forecast. The sensors move every few seconds and their history is the same for
every start.
"""

import asyncio
import contextlib
import functools
import logging
import math
import os
import random
import struct
import time
import zlib
from datetime import UTC, datetime, timedelta

from aiohttp import WSMsgType, web

logger = logging.getLogger("mock_ha")

TOKEN = "mock-token"
PORT = int(os.environ.get("DASHBOARD_MOCK_PORT", "8124"))
TICK_SECONDS = float(os.environ.get("DASHBOARD_MOCK_TICK", "5"))

AREAS = {
    "living_room": "Living room",
    "kitchen": "Kitchen",
    "bedroom": "Bedroom",
    "outside": "Outside",
    "attic": "Attic",
}


def _day_fraction(ts: float) -> float:
    local = time.localtime(ts)
    return (local.tm_hour * 3600 + local.tm_min * 60 + local.tm_sec) / 86400


def _noise(ts: float, seed: int, span: float = 600) -> float:
    """The same wobble (-1..1) for the same moment, smooth over `span` seconds."""
    a, frac = divmod(ts / span, 1)
    r0 = random.Random(int(a) * 7919 + seed).uniform(-1, 1)
    r1 = random.Random((int(a) + 1) * 7919 + seed).uniform(-1, 1)
    return r0 + (r1 - r0) * frac


def _sun(ts: float) -> float:
    """How much sun (0..1): a bell between 8 and 17, less on some days."""
    f = _day_fraction(ts)
    bell = max(0.0, math.sin((f - 8 / 24) / (9 / 24) * math.pi)) if 8 / 24 < f < 17 / 24 else 0.0
    day = int((ts - time.timezone) // 86400)
    return bell * random.Random(day).uniform(0.25, 1.0) * (0.85 + 0.15 * _noise(ts, 5, 900))


@functools.lru_cache(maxsize=4096)
def _solar_until(midnight: int, steps: int) -> float:
    return sum(_sun(midnight + n * 600) * 4.2 / 6 for n in range(steps))


def _solar_day(ts: float, share: float) -> float:
    """kWh made today until ts: the sun's bell, summed in ten minute steps."""
    local = time.localtime(ts)
    seconds = local.tm_hour * 3600 + local.tm_min * 60 + local.tm_sec
    return _solar_until(int(ts - seconds), seconds // 600) * share


# entity_id -> (area, name, attributes, value at a moment) for the sensors that move
SENSORS = {
    "sensor.outside_temperature": (
        "outside",
        "Outside temperature",
        {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement"},
        lambda t: round(9 + 4 * math.sin((_day_fraction(t) - 0.4) * 2 * math.pi) + _noise(t, 1, 3600), 1),
    ),
    "sensor.living_room_temperature": (
        "living_room",
        "Living room temperature",
        {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement"},
        lambda t: round(20.4 + 0.9 * math.sin((_day_fraction(t) - 0.5) * 2 * math.pi) + 0.2 * _noise(t, 2, 1800), 1),
    ),
    "sensor.living_room_humidity": (
        "living_room",
        "Living room humidity",
        {"device_class": "humidity", "unit_of_measurement": "%", "state_class": "measurement"},
        lambda t: round(46 + 4 * _noise(t, 3, 5400)),
    ),
    "sensor.bedroom_temperature": (
        "bedroom",
        "Bedroom temperature",
        {"device_class": "temperature", "unit_of_measurement": "°C", "state_class": "measurement"},
        lambda t: round(18.2 + 0.6 * math.sin((_day_fraction(t) - 0.55) * 2 * math.pi) + 0.2 * _noise(t, 4, 1800), 1),
    ),
    "sensor.power_consumption": (
        None,
        "Power consumption",
        {"device_class": "power", "unit_of_measurement": "W", "state_class": "measurement"},
        lambda t: round(420 + 160 * _noise(t, 6, 240) + (900 if 0.74 < _day_fraction(t) < 0.8 else 0)),
    ),
    "sensor.solar_roof_power": (
        "attic",
        "Roof",
        {"device_class": "power", "unit_of_measurement": "W", "state_class": "measurement"},
        lambda t: round(_sun(t) * 2900),
    ),
    "sensor.solar_balcony_power": (
        "outside",
        "Balcony",
        {"device_class": "power", "unit_of_measurement": "W", "state_class": "measurement"},
        lambda t: round(_sun(t) * 820),
    ),
    "sensor.solar_fence_power": (
        "outside",
        "Fence",
        {"device_class": "power", "unit_of_measurement": "W", "state_class": "measurement"},
        lambda t: round(_sun(t) * 410),
    ),
    "sensor.solar_energy_today": (
        "attic",
        "Solar today",
        {"device_class": "energy", "unit_of_measurement": "kWh", "state_class": "total_increasing"},
        lambda t: round(_solar_day(t, 1.0), 2),
    ),
    "sensor.battery_roof": (
        "attic",
        "Roof battery",
        {"device_class": "battery", "unit_of_measurement": "%", "state_class": "measurement"},
        lambda t: round(70 + 12 * _noise(t, 7, 7200)),
    ),
    "sensor.battery_balcony": (
        "outside",
        "Balcony battery",
        {"device_class": "battery", "unit_of_measurement": "%", "state_class": "measurement"},
        lambda t: round(66 + 12 * _noise(t, 8, 7200)),
    ),
    "sensor.battery_fence": (
        "outside",
        "Fence battery",
        {"device_class": "battery", "unit_of_measurement": "%", "state_class": "measurement"},
        lambda t: round(62 + 12 * _noise(t, 9, 7200)),
    ),
}

# entity_id -> (area, name, state, attributes) for everything else
THINGS = {
    "weather.home": (
        None,
        "Home",
        "partlycloudy",
        {"temperature": 11, "temperature_unit": "°C", "humidity": 78, "wind_speed": 14, "wind_speed_unit": "km/h"},
    ),
    "binary_sensor.front_door": ("outside", "Front door", "off", {"device_class": "door"}),
    "binary_sensor.living_room_presence": ("living_room", "Someone home", "on", {"device_class": "occupancy"}),
    "sensor.dishwasher": ("kitchen", "Dishwasher", "Done", {}),
    "light.dining_table": (
        "kitchen",
        "Dining table",
        "on",
        {"brightness": 153, "supported_color_modes": ["brightness"]},
    ),
    "light.living_room_ceiling": (
        "living_room",
        "Ceiling",
        "off",
        {"brightness": None, "supported_color_modes": ["brightness"]},
    ),
    "light.floor_lamp": (
        "living_room",
        "Floor lamp",
        "on",
        {"brightness": 204, "supported_color_modes": ["brightness"]},
    ),
    "light.cabinet": ("living_room", "Cabinet", "off", {"brightness": None, "supported_color_modes": ["brightness"]}),
    "light.bedroom": ("bedroom", "Bedroom", "off", {"brightness": None, "supported_color_modes": ["brightness"]}),
    "switch.coffee_machine": ("kitchen", "Coffee machine", "off", {}),
    "switch.lava_lamp": ("bedroom", "Lava lamp", "off", {}),
    "cover.blinds_left": ("living_room", "Blinds left", "open", {"current_position": 100, "device_class": "blind"}),
    "cover.blinds_right": ("living_room", "Blinds right", "open", {"current_position": 100, "device_class": "blind"}),
    "cover.garage": ("outside", "Garage", "closed", {"current_position": 0, "device_class": "garage"}),
    "climate.living_room": (
        "living_room",
        "Living room",
        "heat",
        {
            "current_temperature": 20.6,
            "temperature": 21.0,
            "hvac_action": "heating",
            "hvac_modes": ["off", "heat", "auto"],
            "min_temp": 7,
            "max_temp": 30,
            "target_temp_step": 0.5,
        },
    ),
    "scene.good_morning": (None, "Good morning", "2026-11-20T06:45:00+00:00", {}),
    "scene.movie_night": ("living_room", "Movie night", "unknown", {}),
    "scene.cosy": ("living_room", "Cosy", "unknown", {}),
    "script.leaving": (None, "I'm leaving", "off", {}),
    "script.all_off": (None, "Everything off", "off", {}),
    "media_player.living_room": (
        "living_room",
        "Living room speaker",
        "playing",
        {
            "media_title": "Coastlight",
            "media_artist": "Northlight Collective",
            "media_album_name": "Salt Meadow",
            "volume_level": 0.3,
            "entity_picture": "/api/media_player_proxy/media_player.living_room?token=secret&cache=Coastlight",
        },
    ),
    "camera.front_door": (
        "outside",
        "Front door camera",
        "idle",
        {"entity_picture": "/api/camera_proxy/camera.front_door?token=secret"},
    ),
    "camera.garden": (
        "outside",
        "Garden camera",
        "idle",
        {"entity_picture": "/api/camera_proxy/camera.garden?token=secret"},
    ),
    "binary_sensor.doorbell": ("outside", "Doorbell", "off", {"device_class": "occupancy"}),
    "calendar.family": (None, "Family", "off", {}),
    "calendar.house": (None, "House", "off", {}),
    "sun.sun": (None, "Sun", "above_horizon", {}),
    "input_number.volume_limit": (
        None,
        "Volume limit",
        "60",
        {"min": 0, "max": 100, "step": 5, "unit_of_measurement": "%"},
    ),
}


COVERS = {"Coastlight": (233, 138, 75), "Low Tide": (52, 110, 150), "Glasshouse": (70, 140, 96)}
TITLES = [("Coastlight", "Northlight Collective"), ("Low Tide", "Marram"), ("Glasshouse", "The Dune Quartet")]
# (calendar, day from today, start hour or None for all day, hours, what, where)
EVENTS = [
    ("calendar.family", 0, 10.0, 0.5, "Parcel pickup", "Locker, High Street"),
    ("calendar.family", 0, 19.0, 1.5, "Parents' evening", "Primary school"),
    ("calendar.house", 0, 19.0, 0.25, "Recycling out", "Collected tomorrow morning"),
    ("calendar.family", 0, None, 24, "Family day", "Beach"),
    ("calendar.family", 1, 8.25, 0.75, "Dentist", ""),
    ("calendar.family", 1, 10.5, 1.5, "Weekly shopping", ""),
    ("calendar.family", 1, 17.0, 1.5, "Football Mia", "Sports park"),
    ("calendar.house", 2, 9.0, 1, "Boiler service", ""),
    ("calendar.family", 3, 20.0, 2, "Cinema", ""),
    ("calendar.house", 5, None, 24, "Paper collection", ""),
]
CONDITIONS = ["partlycloudy", "rainy", "cloudy", "sunny", "partlycloudy", "pouring", "sunny", "fog"]


def png(width: int, height: int, pixel) -> bytes:
    """A PNG from pixel(x, y) -> (r, g, b), so the stand-in needs no imaging library."""
    rows = b"".join(b"\x00" + bytes(c for x in range(width) for c in pixel(x, y)) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows, 1)) + chunk(b"IEND", b"")


def cover(title: str) -> bytes:
    r, g, b = COVERS.get(title, (120, 120, 120))

    def pixel(x: int, y: int):
        if (x - 48) ** 2 + (y - 48) ** 2 < 22**2:
            return (240, 205, 170)
        f = (x + y) / 192
        return (int(r * (1 - f * 0.55)), int(g * (1 - f * 0.6)), int(b * (1 - f * 0.2) + 60 * f))

    return png(96, 96, pixel)


def camera_frame(entity_id: str, ts: float) -> bytes:
    """A view that moves: sky, a path to the door or a lawn, and someone walking by."""
    garden = entity_id.endswith("garden")
    who = int(ts * 14) % 200 - 20
    night = _day_fraction(ts) < 0.3 or _day_fraction(ts) > 0.72

    def pixel(x: int, y: int):
        if abs(x - who) < 5 and 44 < y < 70:
            return (30, 30, 34)
        if y < 40:
            base = (18, 22, 40) if night else (min(255, 140 + y), min(255, 170 + y), 215)
        elif garden:
            base = (40, 90 + (x * 7 + y * 3) % 12, 45)
        else:
            base = (120, 116, 108) if abs(x - 80) < (y - 30) * 0.6 else (60, 95, 55)
        return tuple(c // 3 for c in base) if night and y >= 40 else base

    return png(160, 90, pixel)


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


class MockHA:
    def __init__(self, port: int = PORT):
        self.port = port
        self.states: dict[str, dict] = {}
        self._subs: list[tuple[web.WebSocketResponse, int, str]] = []
        self._runner: web.AppRunner | None = None
        self._task: asyncio.Task | None = None
        now = time.time()
        for entity_id, (_area, name, attributes, value) in SENSORS.items():
            self.states[entity_id] = self._state(entity_id, str(value(now)), {**attributes, "friendly_name": name}, now)
        for entity_id, (_area, name, state, attributes) in THINGS.items():
            self.states[entity_id] = self._state(entity_id, state, {**attributes, "friendly_name": name}, now - 3600)

    @staticmethod
    def _state(entity_id: str, state: str, attributes: dict, ts: float) -> dict:
        return {
            "entity_id": entity_id,
            "state": state,
            "attributes": attributes,
            "last_changed": iso(ts),
            "last_updated": iso(ts),
        }

    async def set_state(self, entity_id: str, state: str | None = None, **attributes) -> None:
        old = self.states[entity_id]
        new = self._state(
            entity_id, old["state"] if state is None else state, {**old["attributes"], **attributes}, time.time()
        )
        if new["state"] == old["state"]:
            new["last_changed"] = old["last_changed"]
            if new["attributes"] == old["attributes"]:
                return
        self.states[entity_id] = new
        for ws, sub_id, event_type in list(self._subs):
            if event_type == "state_changed" and not ws.closed:
                with contextlib.suppress(ConnectionError):
                    await ws.send_json(
                        {
                            "id": sub_id,
                            "type": "event",
                            "event": {
                                "event_type": "state_changed",
                                "data": {"entity_id": entity_id, "old_state": old, "new_state": new},
                            },
                        }
                    )

    async def _simulate(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            now = time.time()
            for entity_id, (_a, _n, _attrs, value) in SENSORS.items():
                await self.set_state(entity_id, str(value(now)))
            await self.set_state("weather.home", temperature=SENSORS["sensor.outside_temperature"][3](now))
            await self.set_state("sun.sun", "above_horizon" if 0.32 < _day_fraction(now) < 0.7 else "below_horizon")
            climate = self.states["climate.living_room"]["attributes"]
            current = SENSORS["sensor.living_room_temperature"][3](now)
            heating = self.states["climate.living_room"]["state"] != "off" and current < climate["temperature"]
            await self.set_state(
                "climate.living_room", current_temperature=current, hvac_action="heating" if heating else "idle"
            )

    # ------------------------------------------------------------------
    # Services
    # ------------------------------------------------------------------
    async def call_service(self, domain: str, service: str, entity_id: str, data: dict) -> None:
        state = self.states[entity_id]
        on = state["state"] == "on"
        if domain in ("light", "switch", "input_boolean", "fan"):
            turn_on = service == "turn_on" or (service == "toggle" and not on)
            extra = {}
            if domain == "light":
                extra["brightness"] = (
                    (
                        data.get("brightness")
                        or (round(data["brightness_pct"] * 2.55) if "brightness_pct" in data else None)
                        or state["attributes"].get("brightness")
                        or 255
                    )
                    if turn_on
                    else None
                )
            await self.set_state(entity_id, "on" if turn_on else "off", **extra)
        elif domain == "cover":
            if service == "set_cover_position":
                position = int(data["position"])
            elif service == "stop_cover":
                position = state["attributes"]["current_position"]
            else:
                position = 100 if service == "open_cover" else 0
            await self.set_state(entity_id, "open" if position else "closed", current_position=position)
        elif domain == "climate":
            if service == "set_temperature":
                await self.set_state(entity_id, temperature=float(data["temperature"]))
            elif service == "set_hvac_mode":
                await self.set_state(entity_id, data["hvac_mode"])
        elif domain in ("scene", "button", "input_button"):
            await self.set_state(entity_id, iso(time.time()))
            if entity_id == "scene.movie_night":
                await self.call_service("cover", "close_cover", "cover.blinds_left", {})
                await self.call_service("cover", "close_cover", "cover.blinds_right", {})
                await self.call_service("light", "turn_off", "light.living_room_ceiling", {})
                await self.call_service("light", "turn_on", "light.floor_lamp", {"brightness_pct": 30})
            elif entity_id == "scene.cosy":
                await self.call_service("light", "turn_on", "light.floor_lamp", {"brightness_pct": 60})
                await self.call_service("light", "turn_on", "light.cabinet", {"brightness_pct": 40})
            elif entity_id == "scene.good_morning":
                await self.call_service("switch", "turn_on", "switch.coffee_machine", {})
                await self.call_service("cover", "open_cover", "cover.blinds_left", {})
                await self.call_service("cover", "open_cover", "cover.blinds_right", {})
        elif domain == "script":
            for light in [e for e in self.states if e.startswith(("light.", "switch."))]:
                await self.call_service(light.split(".")[0], "turn_off", light, {})
        elif domain == "media_player":
            if service == "media_play_pause":
                await self.set_state(entity_id, "paused" if state["state"] == "playing" else "playing")
            elif service == "volume_set":
                await self.set_state(entity_id, volume_level=float(data["volume_level"]))
            elif service == "media_next_track":
                current = state["attributes"]["media_title"]
                at = next((i for i, t in enumerate(TITLES) if t[0] == current), 0)
                title, artist = TITLES[(at + 1) % len(TITLES)]
                picture = f"/api/media_player_proxy/{entity_id}?token=secret&cache={title.replace(' ', '')}"
                await self.set_state(
                    entity_id, "playing", media_title=title, media_artist=artist, entity_picture=picture
                )
        elif domain in ("number", "input_number"):
            await self.set_state(entity_id, str(data["value"]))

    # ------------------------------------------------------------------
    # HTTP and websocket
    # ------------------------------------------------------------------
    async def start(self) -> None:
        app = web.Application()
        r = app.router

        async def info(_r: web.Request) -> web.Response:
            return web.json_response({"result": "ok", "data": {"timezone": os.environ.get("TZ", "Europe/Amsterdam")}})

        r.add_get("/info", info)
        r.add_get("/core/api/history/period/{start}", self._history)
        r.add_get("/core/websocket", self._websocket)
        r.add_get("/core/api/calendars/{entity_id}", self._events)
        r.add_get("/core/api/camera_proxy/{entity_id}", self._camera)
        r.add_get("/core/api/camera_proxy_stream/{entity_id}", self._camera_stream)
        r.add_get("/core/api/media_player_proxy/{entity_id}", self._cover)
        r.add_get("/mock/ring", self._ring)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "0.0.0.0", self.port).start()
        self._task = asyncio.create_task(self._simulate())
        logger.info(f"Mock Home Assistant on port {self.port} with {len(self.states)} entities")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        if self._runner:
            await self._runner.cleanup()

    async def _history(self, request: web.Request) -> web.Response:
        self._auth(request)
        entity_id = request.query.get("filter_entity_id", "")
        start = datetime.fromisoformat(request.match_info["start"]).timestamp()
        end = (
            datetime.fromisoformat(request.query["end_time"]).timestamp()
            if "end_time" in request.query
            else time.time()
        )
        sensor = SENSORS.get(entity_id)
        if not sensor:
            return web.json_response([[]] if entity_id in self.states else [])
        step = 300 if end - start <= 2 * 86400 else 900
        first = math.ceil(start / step) * step
        return web.json_response(
            [
                [
                    {"state": str(sensor[3](t)), "last_changed": iso(t)}
                    for t in range(first, int(min(end, time.time())), step)
                ]
            ]
        )

    def _auth(self, request: web.Request) -> None:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            raise web.HTTPUnauthorized

    async def _events(self, request: web.Request) -> web.Response:
        self._auth(request)
        start = datetime.fromisoformat(request.query["start"])
        end = datetime.fromisoformat(request.query["end"])
        today = datetime.now().astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        for calendar, day, hour, hours, what, where in EVENTS:
            if calendar != request.match_info["entity_id"]:
                continue
            begin = today + timedelta(days=day, hours=hour or 0)
            if not start <= begin < end:
                continue
            if hour is None:
                until = (begin + timedelta(days=1)).date().isoformat()
                when = {"start": {"date": begin.date().isoformat()}, "end": {"date": until}}
            else:
                until = (begin + timedelta(hours=hours)).isoformat()
                when = {"start": {"dateTime": begin.isoformat()}, "end": {"dateTime": until}}
            out.append({"summary": what, "location": where, **when})
        return web.json_response(out)

    async def _camera(self, request: web.Request) -> web.Response:
        self._auth(request)
        return web.Response(body=camera_frame(request.match_info["entity_id"], time.time()), content_type="image/png")

    async def _camera_stream(self, request: web.Request) -> web.StreamResponse:
        self._auth(request)
        response = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame"})
        await response.prepare(request)
        with contextlib.suppress(ConnectionError, asyncio.CancelledError):
            while True:
                frame = camera_frame(request.match_info["entity_id"], time.time())
                head = f"--frame\r\nContent-Type: image/png\r\nContent-Length: {len(frame)}\r\n\r\n".encode()
                await response.write(head + frame + b"\r\n")
                await asyncio.sleep(0.4)
        return response

    async def _cover(self, request: web.Request) -> web.Response:
        self._auth(request)
        title = self.states[request.match_info["entity_id"]]["attributes"].get("media_title", "")
        return web.Response(body=cover(title), content_type="image/png")

    async def _ring(self, _request: web.Request) -> web.Response:
        """Someone at the door: the doorbell is on for five seconds."""

        async def ring() -> None:
            await self.set_state("binary_sensor.doorbell", "on")
            await asyncio.sleep(5)
            await self.set_state("binary_sensor.doorbell", "off")

        self._ringing = asyncio.create_task(ring())
        return web.Response(text="Ding dong\n")

    @staticmethod
    def _forecast(kind: str) -> list[dict]:
        now = datetime.now().astimezone()
        outside = SENSORS["sensor.outside_temperature"][3]
        if kind == "hourly":
            start = now.replace(minute=0, second=0, microsecond=0)
            hours = [start + timedelta(hours=n) for n in range(48)]
            return [
                {
                    "datetime": hour.isoformat(),
                    "temperature": outside(hour.timestamp()),
                    "condition": CONDITIONS[(hour.hour // 5 + hour.day) % len(CONDITIONS)],
                    "precipitation": round(max(0.0, _noise(hour.timestamp(), 11, 7200)) * 2.4, 1),
                    "wind_speed": round(12 + 6 * _noise(hour.timestamp(), 12, 9000)),
                }
                for hour in hours
            ]
        start = now.replace(hour=12, minute=0, second=0, microsecond=0)
        days = [start + timedelta(days=n) for n in range(7)]
        return [
            {
                "datetime": day.isoformat(),
                "temperature": round(12 + 3 * _noise(day.timestamp(), 13, 86400 * 2), 1),
                "templow": round(6 + 3 * _noise(day.timestamp(), 14, 86400 * 2), 1),
                "condition": CONDITIONS[day.toordinal() % len(CONDITIONS)],
                "precipitation": round(max(0.0, _noise(day.timestamp(), 15, 86400)) * 9, 1),
            }
            for day in days
        ]

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "2026.11.1"})
        auth = await ws.receive_json()
        if auth.get("access_token") != TOKEN:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.11.1"})
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    await self._command(ws, msg.json())
        finally:
            self._subs = [s for s in self._subs if s[0] is not ws]
        return ws

    async def _command(self, ws: web.WebSocketResponse, data: dict) -> None:
        msg_id, kind = data.get("id"), data.get("type")

        async def result(value=None, error: str | None = None):
            if error:
                await ws.send_json(
                    {"id": msg_id, "type": "result", "success": False, "error": {"code": "not_found", "message": error}}
                )
            else:
                await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": value})

        if kind == "get_states":
            await result(list(self.states.values()))
        elif kind == "subscribe_events":
            self._subs.append((ws, msg_id, data.get("event_type", "")))
            await result()
        elif kind == "config/area_registry/list":
            await result([{"area_id": area_id, "name": name} for area_id, name in AREAS.items()])
        elif kind == "config/device_registry/list":
            await result([])
        elif kind == "config/entity_registry/list":
            everything = {**{e: v[0] for e, v in SENSORS.items()}, **{e: v[0] for e, v in THINGS.items()}}
            await result(
                [
                    {"entity_id": e, "area_id": area, "device_id": None, "hidden_by": None, "disabled_by": None}
                    for e, area in everything.items()
                ]
            )
        elif kind == "call_service":
            entity_id = (data.get("target") or {}).get("entity_id")
            if entity_id not in self.states:
                await result(error=f"Unknown entity {entity_id}")
                return
            if data["domain"] == "weather":
                kind = (data.get("service_data") or {}).get("type", "daily")
                await result({"context": {"id": "mock"}, "response": {entity_id: {"forecast": self._forecast(kind)}}})
                return
            await self.call_service(data["domain"], data["service"], entity_id, data.get("service_data") or {})
            await result({"context": {"id": "mock"}})
        else:
            await result(error=f"Unknown command {kind}")


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
    mock = MockHA()
    await mock.start()
    try:
        await asyncio.Event().wait()
    finally:
        await mock.stop()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
