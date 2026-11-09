"""A stand-in Supervisor + Home Assistant with the FireServiceRota integration's three entities.

For development without Home Assistant (see DOCS.md, Development). Serves just enough of
the Core REST and websocket APIs: the entity registry, state_changed events, states, the
recorder history, switch.turn_on / turn_off, events, tts.speak and map tiles. You go on
and off duty and get an incident every couple of minutes; history of the past two weeks
is made up at start, the same for every start on the same day, and the live states are
kept in FSR_DATA_DIR/mock_ha_history.json so a restart sees them again.

Control endpoints for tests: POST /mock/incident, /mock/update, /mock/duty.
"""

import asyncio
import contextlib
import json
import logging
import os
import random
import struct
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiohttp import WSMsgType, web

logger = logging.getLogger("mock_ha")

TOKEN = "mock-token"
HOME = {"latitude": 52.0907, "longitude": 5.1214}
INCIDENT_SECONDS = int(os.environ.get("FSR_MOCK_INCIDENT_SECONDS", "120"))
DUTY_SECONDS = int(os.environ.get("FSR_MOCK_DUTY_SECONDS", "600"))
HISTORY_DAYS = 14
DATA_DIR = Path(os.environ.get("FSR_DATA_DIR", "/tmp/fsr-mock"))

INCIDENTS = "sensor.ron_klinkien_incidents"
DUTY = "binary_sensor.ron_klinkien_duty"
RESPONSE = "switch.ron_klinkien_incident_response"
REGISTRY = [
    {
        "entity_id": INCIDENTS,
        "platform": "fireservicerota",
        "unique_id": "ron@example.com_Incidents",
        "translation_key": "incidents",
        "config_entry_id": "mockentry",
        "disabled_by": None,
    },
    {
        "entity_id": DUTY,
        "platform": "fireservicerota",
        "unique_id": "ron@example.com_Duty",
        "translation_key": "duty",
        "config_entry_id": "mockentry",
        "disabled_by": None,
    },
    {
        "entity_id": RESPONSE,
        "platform": "fireservicerota",
        "unique_id": "ron@example.com_Response",
        "translation_key": "incident_response",
        "config_entry_id": "mockentry",
        "disabled_by": None,
    },
    {"entity_id": "sensor.other", "platform": "met", "unique_id": "x", "config_entry_id": "o", "disabled_by": None},
]

STREETS = [
    ("Oudegracht 123, 3511 AX Utrecht", 52.0890, 5.1190),
    ("Biltstraat 45, 3572 AC Utrecht", 52.0936, 5.1340),
    ("Amsterdamsestraatweg 300, 3551 CV Utrecht", 52.1060, 5.0960),
    ("Kanaalweg 12, 3526 KL Utrecht", 52.0800, 5.0950),
    ("Rijksweg A2 hmp 67.4, Utrecht", 52.0650, 5.0560),
    ("Dorpsstraat 8, 3732 HJ De Bilt", 52.1100, 5.1800),
    ("Nieuwegeinseweg 2, 3432 AB Nieuwegein", 52.0330, 5.0900),
]
KINDS = [
    ("p1", "Brand", "P 1 BRT-01 Brand woning {street}"),
    ("p2", "OMS", "P 2 BRT-02 Automatische brandmelding {street}"),
    ("p1", "Hulpverlening", "P 1 Hulpverlening ongeval wegvervoer {street}"),
    ("a1", "Ambulance", "A1 Reanimatie {street}"),
    ("p2", "Hulpverlening", "P 2 Assistentie ambulance {street}"),
    ("p3", "Dienstverlening", "P 3 Dier in problemen {street}"),
    ("p1", "Brand", "P 1 Brand bedrijfspand {street}"),
]


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def local_iso(dt: datetime) -> str:
    return dt.astimezone().isoformat(timespec="seconds")


def png(width: int, height: int, pixel) -> bytes:
    """A small RGB PNG, pixel(x, y) -> (r, g, b)."""
    raw = b"".join(b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def tile(z: int, x: int, y: int) -> bytes:
    """A plain map-like tile: land colour, a grid of 'streets' and a blue 'canal'."""
    shade = (x * 7 + y * 13 + z) % 3

    def pixel(px, py):
        if px % 64 in (0, 1) or py % 64 in (0, 1):
            return (255, 255, 255)
        if (px + py + x * 17) % 256 in range(120, 128):
            return (170, 211, 223)
        return (236 - shade * 3, 233 - shade * 2, 226)

    return png(256, 256, pixel)


def beep_wav() -> bytes:
    """Half a second of a two-tone beep, as a stand-in for message_to_speech_url."""
    rate = 8000
    samples = bytearray()
    for i in range(rate // 2):
        freq = 880 if i < rate // 4 else 660
        value = (100 * (1 if (i * freq * 2 // rate) % 2 else -1)) + 128
        samples.append(value)
    header = (
        b"RIFF"
        + struct.pack("<I", 36 + len(samples))
        + b"WAVEfmt "
        + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate, 1, 8)
    )
    return header + b"data" + struct.pack("<I", len(samples)) + bytes(samples)


class MockHomeAssistant:
    def __init__(self, port: int):
        self.port = port
        self.history: list[dict] = []
        self.states: dict[str, dict] = {}
        self.sockets: dict[web.WebSocketResponse, set[int]] = {}
        self.events: list[dict] = []
        self.services: list[dict] = []
        self._runner: web.AppRunner | None = None
        self._tasks: list[asyncio.Task] = []
        self._next_id = 0
        self._live_file = DATA_DIR / "mock_ha_history.json"

    # --------------------------------------------------------------- states

    def _set(
        self, entity_id: str, state: str, attributes: dict, when: datetime | None = None, record: bool = True
    ) -> dict:
        when = when or datetime.now(UTC)
        old = self.states.get(entity_id)
        changed = old is None or old["state"] != state
        obj = {
            "entity_id": entity_id,
            "state": state,
            "attributes": attributes,
            "last_changed": iso(when) if changed or old is None else old["last_changed"],
            "last_updated": iso(when),
        }
        self.states[entity_id] = obj
        self.history.append(obj)
        if record:
            self._save_live(obj)
            for ws, subs in list(self.sockets.items()):
                for sub in subs:
                    event = {
                        "id": sub,
                        "type": "event",
                        "event": {
                            "event_type": "state_changed",
                            "data": {"entity_id": entity_id, "old_state": old, "new_state": obj},
                            "time_fired": iso(when),
                        },
                    }
                    asyncio.get_running_loop().create_task(self._send(ws, event))
        return obj

    async def _send(self, ws: web.WebSocketResponse, data: dict) -> None:
        with contextlib.suppress(Exception):
            await ws.send_json(data)

    def _save_live(self, obj: dict) -> None:
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with open(self._live_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(obj) + "\n")
        except OSError:
            pass

    def _incident_attrs(self, rng: random.Random, created: datetime, incident_id: int) -> tuple[str, dict]:
        prio, kind, text = rng.choice(KINDS)
        street, lat, lon = rng.choice(STREETS)
        body = text.format(street=street.split(",")[0] + " " + street.split(" ")[-1])
        attrs = {
            "id": incident_id,
            "trigger": "new",
            "state": "active",
            "created_at": local_iso(created),
            "message_to_speech_url": f"http://127.0.0.1:{self.port}/mock/speech/{incident_id}.wav",
            "prio": prio,
            "type": kind,
            "responder_mode": rng.choice(["available_in_schedule_is_acknowledgment", "all_users_can_respond"]),
            "can_respond_until": local_iso(created + timedelta(minutes=30)),
            "task_ids": [rng.randint(1000, 9999)],
            "latitude": lat + rng.uniform(-0.002, 0.002),
            "longitude": lon + rng.uniform(-0.002, 0.002),
            "address_type": "address",
            "formatted_address": street,
        }
        return body, attrs

    def _duty_attrs(self, on: bool, start: datetime, end: datetime) -> dict:
        return {
            "start_time": local_iso(start),
            "end_time": local_iso(end),
            "available": on,
            "active": on,
            "assigned_function_ids": [12],
            "skill_ids": [3, 7],
            "type": "standby_duty",
            "assigned_function": "Bevelvoerder" if on else None,
            "friendly_name": "Ron Klinkien Duty",
        }

    def _response_attrs(self, status: str | None, responded: datetime | None, incident_start: datetime) -> dict:
        return {
            "user_name": "Ron Klinkien",
            "assigned_skill_ids": [3],
            "responded_at": local_iso(responded) if responded else None,
            "start_time": local_iso(incident_start),
            "status": status,
            "reported_status": None,
            "arrived_at_station": False,
            "available_at_incident_creation": True,
            "active_duty_function_ids": [12],
            "friendly_name": "Ron Klinkien Incident response",
        }

    def _seed(self) -> None:
        """Two weeks of made-up history, the same for every start on the same day."""
        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        first = today - timedelta(days=HISTORY_DAYS)
        self._set(DUTY, "off", self._duty_attrs(False, first, first), when=first, record=False)
        self._set(
            RESPONSE, "unavailable", {"friendly_name": "Ron Klinkien Incident response"}, when=first, record=False
        )
        for d in range(HISTORY_DAYS + 1):
            day = first + timedelta(days=d)
            rng = random.Random(day.toordinal())
            # Evening and night standby duty, most days
            if rng.random() < 0.8:
                start = day + timedelta(hours=rng.choice([17, 18, 19]))
                end = start + timedelta(hours=rng.choice([6, 8, 12, 14]))
                if start < now - timedelta(hours=1):
                    self._set(DUTY, "on", self._duty_attrs(True, start, end), when=start, record=False)
                    self._set(
                        RESPONSE,
                        "unknown",
                        {"friendly_name": "Ron Klinkien Incident response"},
                        when=start,
                        record=False,
                    )
                    if end < now - timedelta(hours=1):
                        self._set(DUTY, "off", self._duty_attrs(False, end, end), when=end, record=False)
                        self._set(RESPONSE, "unavailable", {"friendly_name": "x"}, when=end, record=False)
                    duty = (start, end)
                else:
                    duty = None
            else:
                duty = None
            for n in range(rng.choice([0, 1, 1, 2, 3])):
                created = day + timedelta(minutes=rng.randint(6 * 60, 23 * 60 + 50))
                if created > now - timedelta(hours=1):
                    continue
                incident_id = 300000 + day.toordinal() % 1000 * 10 + n
                body, attrs = self._incident_attrs(rng, created, incident_id)
                seen = created + timedelta(seconds=rng.randint(2, 20))
                self._set(
                    INCIDENTS,
                    body,
                    {**attrs, "icon": "mdi:fire-truck", "friendly_name": "Incidents"},
                    when=seen,
                    record=False,
                )
                on_duty = duty and duty[0] <= created <= duty[1]
                if on_duty:
                    resp_attrs = self._response_attrs(None, None, created)
                    self._set(RESPONSE, "off", resp_attrs, when=seen + timedelta(seconds=1), record=False)
                    pick = rng.random()
                    if pick < 0.75:
                        status = "acknowledged" if pick < 0.6 else "rejected"
                        answered = created + timedelta(seconds=rng.randint(30, 400))
                        self._set(
                            RESPONSE,
                            "on" if status == "acknowledged" else "off",
                            self._response_attrs(status, answered, created),
                            when=answered + timedelta(seconds=2),
                            record=False,
                        )
                if rng.random() < 0.3:
                    upd = seen + timedelta(minutes=rng.randint(3, 15))
                    self._set(
                        INCIDENTS,
                        body + " (aanvulling: meerdere voertuigen)",
                        {**attrs, "trigger": "update", "state": "active", "friendly_name": "Incidents"},
                        when=upd,
                        record=False,
                    )
        # States kept from earlier runs of the mock
        if self._live_file.exists():
            for line in self._live_file.read_text(encoding="utf-8").splitlines():
                with contextlib.suppress(ValueError):
                    obj = json.loads(line)
                    self.history.append(obj)
        self.history.sort(key=lambda s: s["last_updated"])
        for obj in self.history:
            self.states[obj["entity_id"]] = obj

    # ------------------------------------------------------------ simulation

    def _on_duty(self) -> bool:
        return (self.states.get(DUTY) or {}).get("state") == "on"

    def new_incident(self) -> dict:
        now = datetime.now(UTC)
        self._next_id = max(self._next_id, int(now.timestamp()))
        self._next_id += 1
        rng = random.Random(self._next_id)
        body, attrs = self._incident_attrs(rng, now, self._next_id)
        obj = self._set(INCIDENTS, body, {**attrs, "icon": "mdi:fire-truck", "friendly_name": "Incidents"})
        if self._on_duty():
            self._set(RESPONSE, "unknown", self._response_attrs(None, None, now))
        logger.info(f"Mock: new incident {self._next_id}: {body}")
        return obj

    def update_incident(self) -> dict | None:
        current = self.states.get(INCIDENTS)
        if not current:
            return None
        attrs = {**current["attributes"], "trigger": "update", "state": "active"}
        return self._set(INCIDENTS, current["state"].split(" (aanvulling")[0] + " (aanvulling: extra eenheid)", attrs)

    def toggle_duty(self) -> None:
        now = datetime.now(UTC)
        on = not self._on_duty()
        self._set(DUTY, "on" if on else "off", self._duty_attrs(on, now, now + timedelta(seconds=DUTY_SECONDS)))
        if on:
            self._set(RESPONSE, "unknown", {"friendly_name": "Ron Klinkien Incident response"})
        else:
            self._set(RESPONSE, "unavailable", {"friendly_name": "Ron Klinkien Incident response"})
        logger.info(f"Mock: {'on' if on else 'off'} duty")

    async def _arrive_later(self) -> None:
        await asyncio.sleep(45)
        current = self.states.get(RESPONSE)
        if current and current["state"] == "on":
            self._set(
                RESPONSE, "on", {**current["attributes"], "arrived_at_station": True, "reported_status": "on_the_way"}
            )

    async def _simulate(self) -> None:
        if not self._on_duty():
            self.toggle_duty()
        elapsed = 0
        while True:
            await asyncio.sleep(5)
            elapsed += 5
            if elapsed % DUTY_SECONDS == 0:
                self.toggle_duty()
            if elapsed % INCIDENT_SECONDS == 0:
                self.new_incident()
            elif elapsed % INCIDENT_SECONDS == 40 and random.random() < 0.4:
                self.update_incident()

    # ----------------------------------------------------------------- server

    def _auth(self, request: web.Request) -> None:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            raise web.HTTPUnauthorized()

    async def start(self) -> None:
        self._seed()
        app = web.Application()
        r = app.router
        r.add_get("/addons/self/info", self._addon_info)
        r.add_get("/info", lambda _r: web.json_response({"data": {"timezone": "Europe/Amsterdam"}}))
        r.add_get("/core/api/config", self._config)
        r.add_get("/core/api/states/{entity_id}", self._state)
        r.add_get("/core/api/history/period/{start}", self._history)
        r.add_post("/core/api/services/{domain}/{service}", self._service)
        r.add_post("/core/api/events/{event_type}", self._event)
        r.add_get("/core/api/map_tiles/raster/{z}/{x}/{y}.png", self._tile)
        r.add_get("/core/websocket", self._websocket)
        r.add_get("/mock/speech/{id}.wav", lambda _r: web.Response(body=beep_wav(), content_type="audio/wav"))
        r.add_post("/mock/incident", lambda _r: web.json_response(self.new_incident()))
        r.add_post("/mock/update", lambda _r: web.json_response(self.update_incident()))
        r.add_post("/mock/duty", self._mock_duty)
        r.add_get("/mock/log", lambda _r: web.json_response({"events": self.events, "services": self.services}))
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()
        self._tasks.append(asyncio.create_task(self._simulate()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        for ws in list(self.sockets):
            await ws.close()
        if self._runner:
            await self._runner.cleanup()

    async def _mock_duty(self, _request: web.Request) -> web.Response:
        self.toggle_duty()
        return web.json_response(self.states[DUTY])

    async def _addon_info(self, request: web.Request) -> web.Response:
        self._auth(request)
        return web.json_response({"result": "ok", "data": {"slug": "local_app-fireservicerota"}})

    async def _config(self, request: web.Request) -> web.Response:
        self._auth(request)
        return web.json_response(
            {**HOME, "location_name": "Thuis", "time_zone": "Europe/Amsterdam", "version": "2026.11.0"}
        )

    async def _state(self, request: web.Request) -> web.Response:
        self._auth(request)
        state = self.states.get(request.match_info["entity_id"])
        return web.json_response(state) if state else web.json_response({"message": "Entity not found."}, status=404)

    async def _history(self, request: web.Request) -> web.Response:
        self._auth(request)
        start = datetime.fromisoformat(request.match_info["start"])
        end = datetime.fromisoformat(request.query["end_time"]) if "end_time" in request.query else datetime.now(UTC)
        ids = request.query.get("filter_entity_id", "").split(",")
        result = []
        for entity_id in ids:
            rows = [s for s in self.history if s["entity_id"] == entity_id]
            before = [s for s in rows if datetime.fromisoformat(s["last_updated"]) <= start]
            inside = [s for s in rows if start < datetime.fromisoformat(s["last_updated"]) <= end]
            states = before[-1:] + inside
            if states:
                result.append(states)
        return web.json_response(result)

    async def _service(self, request: web.Request) -> web.Response:
        self._auth(request)
        domain, service = request.match_info["domain"], request.match_info["service"]
        data = await request.json()
        self.services.append({"domain": domain, "service": service, "data": data})
        logger.info(f"Mock: {domain}.{service} {data}")
        if domain == "switch" and data.get("entity_id") == RESPONSE:
            if not self._on_duty():
                # The integration logs "Cannot send incident response when not on duty" and does nothing
                return web.json_response([])
            now = datetime.now(UTC)
            incident = self.states.get(INCIDENTS) or {"attributes": {}}
            created = datetime.fromisoformat(incident["attributes"].get("created_at") or iso(now))
            status = "acknowledged" if service == "turn_on" else "rejected"
            self._set(RESPONSE, "on" if service == "turn_on" else "off", self._response_attrs(status, now, created))
            if service == "turn_on":
                self._tasks.append(asyncio.create_task(self._arrive_later()))
        return web.json_response([])

    async def _event(self, request: web.Request) -> web.Response:
        self._auth(request)
        self.events.append({"event_type": request.match_info["event_type"], "data": await request.json()})
        logger.info(f"Mock: event {request.match_info['event_type']}")
        return web.json_response({"message": "Event fired."})

    async def _tile(self, request: web.Request) -> web.Response:
        self._auth(request)
        z, x, y = (int(request.match_info[k]) for k in ("z", "x", "y"))
        return web.Response(body=await asyncio.to_thread(tile, z, x, y), content_type="image/png")

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "2026.11.0"})
        auth = await ws.receive_json()
        if auth.get("access_token") != TOKEN:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.11.0"})
        self.sockets[ws] = set()
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                data = json.loads(msg.data)
                msg_id = data.get("id")
                if data.get("type") == "config/entity_registry/list":
                    await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": REGISTRY})
                elif data.get("type") == "subscribe_events":
                    self.sockets[ws].add(msg_id)
                    await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": None})
                else:
                    await ws.send_json(
                        {
                            "id": msg_id,
                            "type": "result",
                            "success": False,
                            "error": {"code": "unknown_command", "message": "Unknown command."},
                        }
                    )
        finally:
            self.sockets.pop(ws, None)
        return ws


async def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    mock = MockHomeAssistant(int(os.environ.get("FSR_MOCK_PORT", "8124")))
    await mock.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(_main())
