"""A stand-in Supervisor + Home Assistant with a home network's device trackers.

For development without Home Assistant (see DOCS.md, Development). Serves just enough of the Core
REST and websocket APIs: the entity, device and area registries, the states and state_changed
events, renaming (config/entity_registry/update, config/device_registry/update) and the recorder
history. About fifty trackers from UniFi, Nmap, Ping and the mobile app: infrastructure that stays
up, phones that come and go, a printer that sleeps, an ESP with bad Wi-Fi that drops all day, a
guest phone that's new, and a Kindle that hasn't been seen for over a week. The history of the past
two weeks is the same for every start on the same day.
"""

import asyncio
import contextlib
import json
import logging
import os
import random
from datetime import UTC, datetime, timedelta

from aiohttp import WSMsgType, web

logger = logging.getLogger("mock_ha")

TOKEN = "mock-token"
HISTORY_DAYS = 14
TICK_SECONDS = float(os.environ.get("NETWORK_MOCK_TICK", "15"))

AREAS = [
    ("office", "Office"),
    ("living_room", "Living room"),
    ("kitchen", "Kitchen"),
    ("bedroom", "Bedroom"),
    ("garage", "Garage"),
    ("garden", "Garden"),
    ("attic", "Attic"),
]

# Home Assistant's zones: (entity, name, lat, lon, radius)
ZONES = [
    ("zone.home", "Home", 52.0907, 5.1214, 100),
    ("zone.work", "Work", 52.3702, 4.8952, 250),
    ("zone.gym", "Gym", 52.0812, 5.1365, 80),
    ("zone.school", "School", 52.0981, 5.1043, 120),
]
ZONE_STATE = {"zone.home": "home", "zone.work": "Work", "zone.gym": "Gym", "zone.school": "School"}
# Places that aren't zones, for weekend trips
PLACES = [(52.0938, 5.1180), (52.1090, 5.0790), (52.0640, 5.1550), (52.1052, 4.2726), (52.1561, 5.3878)]

# name, ip, mac, platforms, maker, model, area, profile, extra
#   platforms: "unifi", "nmap" (Nmap without a registry entry), "ping" (known_devices.yaml), "gps" (mobile app)
#   profile: always, flappy, phone, laptop, evening, sleepy, guest, stale, daytime
AP_LIVING, AP_UP, AP_GARDEN = "f4:e2:c6:10:00:04", "f4:e2:c6:10:00:05", "f4:e2:c6:10:00:06"
SW_OFFICE, SW_LIVING = "f4:e2:c6:10:00:02", "f4:e2:c6:10:00:03"
DEVICES = [
    (
        "Dream Machine Pro",
        "192.168.1.1",
        "f4:e2:c6:10:00:01",
        ["unifi"],
        "Ubiquiti",
        "UDM-Pro",
        "attic",
        "always",
        {"wired": True},
    ),
    (
        "USW-24-PoE",
        "192.168.1.2",
        SW_OFFICE,
        ["unifi"],
        "Ubiquiti",
        "USW-24-PoE",
        "attic",
        "always",
        {"wired": True, "sw": ("f4:e2:c6:10:00:01", 9)},
    ),
    (
        "USW-Lite-8 Living room",
        "192.168.1.3",
        SW_LIVING,
        ["unifi"],
        "Ubiquiti",
        "USW-Lite-8-PoE",
        "living_room",
        "always",
        {"wired": True, "sw": (SW_OFFICE, 3)},
    ),
    (
        "U6-Pro Living room",
        "192.168.1.4",
        AP_LIVING,
        ["unifi"],
        "Ubiquiti",
        "U6-Pro",
        "living_room",
        "always",
        {"wired": True, "sw": (SW_LIVING, 1)},
    ),
    (
        "U6-Lite Upstairs",
        "192.168.1.5",
        AP_UP,
        ["unifi"],
        "Ubiquiti",
        "U6-Lite",
        "bedroom",
        "always",
        {"wired": True, "sw": (SW_OFFICE, 5)},
    ),
    (
        "U6-Mesh Garden",
        "192.168.1.6",
        AP_GARDEN,
        ["unifi"],
        "Ubiquiti",
        "U6-Mesh",
        "garden",
        "always",
        {"wired": False, "ap": AP_LIVING, "ssid": "Backhaul"},
    ),
    (
        "pve1",
        "192.168.10.11",
        "a8:a1:59:00:11:01",
        ["unifi", "nmap"],
        "ASRock",
        "",
        "attic",
        "always",
        {"wired": True, "sw": (SW_OFFICE, 11)},
    ),
    (
        "pve2",
        "192.168.10.12",
        "a8:a1:59:00:11:02",
        ["unifi"],
        "ASRock",
        "",
        "attic",
        "always",
        {"wired": True, "sw": (SW_OFFICE, 12)},
    ),
    (
        "homeassistant",
        "192.168.10.20",
        "dc:a6:32:00:20:01",
        ["unifi", "nmap"],
        "Raspberry Pi Trading Ltd",
        "",
        "attic",
        "always",
        {"wired": True, "sw": (SW_OFFICE, 13)},
    ),
    (
        "DS920plus",
        "192.168.10.30",
        "00:11:32:00:30:01",
        ["unifi", "nmap"],
        "Synology",
        "",
        "attic",
        "always",
        {"wired": True, "sw": (SW_OFFICE, 14)},
    ),
    (
        "docker01",
        "192.168.10.40",
        "bc:24:11:00:40:01",
        ["unifi"],
        "Proxmox Server Solutions",
        "",
        "attic",
        "always",
        {"wired": True, "sw": (SW_OFFICE, 11)},
    ),
    (
        "iPhone Ron",
        "192.168.1.101",
        "6a:3f:21:9c:00:01",
        ["unifi", "nmap", "gps"],
        "Apple",
        "",
        "",
        "phone",
        {"ap": AP_UP, "ssid": "Thuis"},
    ),
    (
        "iPhone Sanne",
        "192.168.1.102",
        "7e:11:a0:5b:00:02",
        ["unifi", "gps"],
        "Apple",
        "",
        "",
        "phone",
        {"ap": AP_LIVING, "ssid": "Thuis"},
    ),
    (
        "Pixel-8",
        "192.168.1.103",
        "3c:28:6d:00:01:03",
        ["unifi"],
        "Google",
        "",
        "",
        "phone",
        {"ap": AP_UP, "ssid": "Thuis"},
    ),
    (
        "iPad",
        "192.168.1.104",
        "8a:2b:33:00:01:04",
        ["unifi"],
        "Apple",
        "",
        "living_room",
        "evening",
        {"ap": AP_LIVING, "ssid": "Thuis"},
    ),
    (
        "MacBook-Pro",
        "192.168.1.105",
        "f0:18:98:00:01:05",
        ["unifi", "nmap"],
        "Apple",
        "",
        "office",
        "laptop",
        {"ap": AP_UP, "ssid": "Thuis"},
    ),
    (
        "LT-WORK-4471",
        "192.168.1.106",
        "54:e1:ad:00:01:06",
        ["unifi"],
        "Lenovo",
        "",
        "office",
        "laptop",
        {"ap": AP_UP, "ssid": "Thuis"},
    ),
    (
        "gaming-pc",
        "192.168.1.107",
        "d8:bb:c1:00:01:07",
        ["unifi", "nmap"],
        "Micro-Star",
        "",
        "office",
        "evening",
        {"wired": True, "sw": (SW_OFFICE, 18)},
    ),
    (
        "Apple-TV",
        "192.168.1.110",
        "f0:b3:ec:00:01:10",
        ["unifi"],
        "Apple",
        "AppleTV14,1",
        "living_room",
        "always",
        {"wired": True, "sw": (SW_LIVING, 4)},
    ),
    (
        "LG-webOS-TV",
        "192.168.1.111",
        "a8:23:fe:00:01:11",
        ["unifi"],
        "LG Electronics",
        "",
        "living_room",
        "evening",
        {"wired": True, "sw": (SW_LIVING, 5)},
    ),
    (
        "Chromecast-Bedroom",
        "192.168.1.112",
        "54:60:09:00:01:12",
        ["unifi"],
        "Google",
        "",
        "bedroom",
        "always",
        {"ap": AP_UP, "ssid": "Thuis"},
    ),
    (
        "PS5",
        "192.168.1.113",
        "00:d9:d1:00:01:13",
        ["unifi"],
        "Sony Interactive Entertainment",
        "",
        "living_room",
        "evening",
        {"wired": True, "sw": (SW_LIVING, 6)},
    ),
    (
        "Sonos-Living",
        "192.168.1.120",
        "48:a6:b8:00:01:20",
        ["unifi"],
        "Sonos",
        "",
        "living_room",
        "always",
        {"ap": AP_LIVING, "ssid": "Thuis"},
    ),
    (
        "Sonos-Kitchen",
        "192.168.1.121",
        "48:a6:b8:00:01:21",
        ["unifi"],
        "Sonos",
        "",
        "kitchen",
        "always",
        {"ap": AP_LIVING, "ssid": "Thuis"},
    ),
    (
        "HP7C3B8A",
        "192.168.1.130",
        "7c:4d:8f:7c:3b:8a",
        ["unifi", "nmap"],
        "HP",
        "",
        "office",
        "sleepy",
        {"ap": AP_UP, "ssid": "Thuis"},
    ),
    (
        "Nintendo-Switch",
        "192.168.1.131",
        "98:41:5c:00:01:31",
        ["unifi"],
        "Nintendo",
        "",
        "living_room",
        "evening",
        {"ap": AP_LIVING, "ssid": "Thuis"},
    ),
    (
        "Kindle-Paperwhite",
        "192.168.1.77",
        "74:c2:46:00:01:77",
        ["unifi"],
        "Amazon",
        "",
        "bedroom",
        "stale",
        {"ap": AP_UP, "ssid": "Thuis"},
    ),
    (
        "garage-door-esp32",
        "192.168.30.10",
        "24:0a:c4:00:30:10",
        ["unifi"],
        "Espressif",
        "",
        "garage",
        "flappy",
        {"ap": AP_GARDEN, "ssid": "Thuis-IoT"},
    ),
    (
        "shellyplug-s-washer",
        "192.168.30.11",
        "e8:68:e7:00:30:11",
        ["unifi"],
        "Shelly",
        "",
        "kitchen",
        "always",
        {"ap": AP_LIVING, "ssid": "Thuis-IoT"},
    ),
    (
        "shellyplug-s-dryer",
        "192.168.30.12",
        "e8:68:e7:00:30:12",
        ["unifi"],
        "Shelly",
        "",
        "kitchen",
        "always",
        {"ap": AP_LIVING, "ssid": "Thuis-IoT"},
    ),
    (
        "shelly1pm-garden",
        "192.168.30.13",
        "e8:68:e7:00:30:13",
        ["unifi"],
        "Shelly",
        "",
        "garden",
        "flappy-mild",
        {"ap": AP_GARDEN, "ssid": "Thuis-IoT"},
    ),
    (
        "Philips-hue",
        "192.168.30.20",
        "ec:b5:fa:00:30:20",
        ["unifi"],
        "Signify",
        "",
        "living_room",
        "always",
        {"wired": True, "sw": (SW_LIVING, 7)},
    ),
    (
        "esp-test",
        "192.168.30.21",
        "24:0a:c4:00:30:21",
        ["unifi"],
        "Espressif",
        "",
        "office",
        "always",
        {"ap": AP_UP, "ssid": "Thuis-IoT"},
    ),
    (
        "tasmota-desklamp",
        "192.168.30.21",
        "c4:5b:be:00:30:22",
        ["unifi"],
        "Espressif",
        "",
        "office",
        "always",
        {"ap": AP_UP, "ssid": "Thuis-IoT"},
    ),
    (
        "eneco-001-123456",
        "192.168.30.30",
        "00:0e:c6:00:30:30",
        ["unifi"],
        "Quby",
        "Toon",
        "living_room",
        "always",
        {"ap": AP_LIVING, "ssid": "Thuis-IoT"},
    ),
    (
        "GoodWe-ES",
        "192.168.30.31",
        "f0:fe:6b:00:30:31",
        ["unifi"],
        "GoodWe",
        "",
        "garage",
        "daytime",
        {"ap": AP_GARDEN, "ssid": "Thuis-IoT"},
    ),
    (
        "Tesla-Wall-Connector",
        "192.168.30.32",
        "98:ed:5c:00:30:32",
        ["unifi"],
        "Tesla",
        "",
        "garage",
        "always",
        {"ap": AP_GARDEN, "ssid": "Thuis-IoT"},
    ),
    (
        "bitaxe-gamma-1",
        "192.168.30.40",
        "24:0a:c4:00:30:40",
        ["unifi"],
        "Espressif",
        "",
        "attic",
        "always",
        {"ap": AP_UP, "ssid": "Thuis-IoT"},
    ),
    (
        "bitaxe-gamma-2",
        "192.168.30.41",
        "24:0a:c4:00:30:41",
        ["unifi"],
        "Espressif",
        "",
        "attic",
        "flappy-mild",
        {"ap": AP_UP, "ssid": "Thuis-IoT"},
    ),
    (
        "Automower-430X",
        "192.168.30.52",
        "68:27:19:00:30:52",
        ["unifi"],
        "Husqvarna",
        "Automower 430X",
        "garden",
        "daytime",
        {"ap": AP_GARDEN, "ssid": "Thuis-IoT"},
    ),
    (
        "BRWADS-1700W",
        "192.168.1.132",
        "3c:2a:f4:00:01:32",
        ["unifi"],
        "Brother",
        "ADS-1700W",
        "office",
        "sleepy",
        {"ap": AP_UP, "ssid": "Thuis"},
    ),
    (
        "Denon-AVR-X2800H",
        "192.168.1.122",
        "00:05:cd:00:01:22",
        ["unifi"],
        "Denon",
        "AVR-X2800H",
        "living_room",
        "evening",
        {"wired": True, "sw": (SW_LIVING, 8)},
    ),
    ("", "192.168.10.50", "dc:a6:32:00:10:50", ["nmap"], "", "", "", "always", {}),
    (
        "roborock-s8",
        "192.168.30.50",
        "b0:4a:39:00:30:50",
        ["unifi"],
        "Roborock",
        "",
        "living_room",
        "always",
        {"ap": AP_LIVING, "ssid": "Thuis-IoT"},
    ),
    (
        "Miele-WashingMachine",
        "192.168.30.51",
        "00:1d:63:00:30:51",
        ["unifi"],
        "Miele",
        "",
        "kitchen",
        "daytime",
        {"ap": AP_LIVING, "ssid": "Thuis-IoT"},
    ),
    (
        "Reolink-Driveway",
        "192.168.40.10",
        "ec:71:db:00:40:10",
        ["unifi"],
        "Reolink",
        "",
        "garden",
        "always",
        {"wired": True, "sw": (SW_OFFICE, 20)},
    ),
    (
        "Reolink-Backyard",
        "192.168.40.11",
        "ec:71:db:00:40:11",
        ["unifi"],
        "Reolink",
        "",
        "garden",
        "always",
        {"wired": True, "sw": (SW_OFFICE, 21)},
    ),
    (
        "Reolink-Doorbell",
        "192.168.40.12",
        "ec:71:db:00:40:12",
        ["unifi"],
        "Reolink",
        "Video Doorbell",
        "garden",
        "always",
        {"wired": True, "sw": (SW_OFFICE, 22)},
    ),
    (
        "Galaxy-S23",
        "192.168.50.20",
        "5e:9a:00:11:50:20",
        ["unifi"],
        "",
        "",
        "",
        "guest",
        {"ap": AP_LIVING, "ssid": "Thuis-Gast"},
    ),
    ("", "192.168.50.21", "4a:1c:00:22:50:21", ["unifi"], "", "", "", "guest", {"ap": AP_LIVING, "ssid": "Thuis-Gast"}),
    ("Internet (ping 1.1.1.1)", "1.1.1.1", "", ["ping"], "", "", "", "always", {}),
]


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat()


def slug(text: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "_" for c in text)
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_") or "unknown"


def profile_up(profile: str, ts: float, rng: random.Random) -> bool:
    """Whether a device with this profile is usually on the network at this time."""
    when = datetime.fromtimestamp(ts)
    h, weekday = when.hour + when.minute / 60, when.weekday() < 5
    if profile == "phone":
        return not (weekday and 8.2 < h < 17.6) and not (not weekday and 13 < h < 16.5)
    if profile == "laptop":
        return (weekday and 8.5 < h < 17.5) or 20 < h < 22.5
    if profile == "evening":
        return 18.5 < h < 23.5 or (not weekday and 10 < h < 13)
    if profile == "daytime":
        return 7.5 < h < 19
    if profile == "sleepy":
        return 8 < h < 9.5 or 13 < h < 13.5 or 19 < h < 20
    return True


class MockHA:
    def __init__(self, port: int):
        self.port = port
        self.rng = random.Random(datetime.now().strftime("%Y%m%d"))
        self.entities: dict[str, dict] = {}
        self.devices: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        self.profiles: dict[str, str] = {}
        self.history: dict[str, list[tuple[float, str]]] = {}
        # Phones: (ts, state, lat, lon, accuracy, battery), from two weeks ago to tomorrow
        self.track: dict[str, list[tuple]] = {}
        self.tiles: dict[str, bytes] = {}
        self._tile_session = None
        self.clients: set[web.WebSocketResponse] = set()
        self.subscriptions: dict[web.WebSocketResponse, dict[int, str]] = {}
        self._runner: web.AppRunner | None = None
        self._sim: asyncio.Task | None = None
        self._seed()

    # ------------------------------------------------------------------
    def _seed(self) -> None:
        now = datetime.now(UTC).timestamp()
        for n, (name, ip, mac, platforms, maker, model, area, profile, extra) in enumerate(DEVICES):
            device_id = f"dev{n:03d}"
            base = slug(name or mac.replace(":", ""))
            if "unifi" in platforms:
                self.devices[device_id] = {
                    "id": device_id,
                    "name": name or None,
                    "name_by_user": None,
                    "manufacturer": maker or None,
                    "model": model or None,
                    "connections": [["mac", mac]] if mac else [],
                    "area_id": area or None,
                    "via_device_id": None,
                    "sw_version": None,
                    "config_entries": ["unifi_entry"],
                    "identifiers": [],
                }
            for platform in platforms:
                entity_id = f"device_tracker.{base}" + ("" if platform == "unifi" else f"_{platform}")
                if platform == "gps":
                    entity_id = f"device_tracker.{slug(name)}_app"
                attrs: dict = {"source_type": "gps" if platform == "gps" else "router"}
                if platform == "gps":
                    attrs.update({"latitude": 52.09, "longitude": 5.12, "gps_accuracy": 12, "battery_level": 80})
                else:
                    attrs.update({"ip": ip, "host_name": name or None})
                    if mac:
                        attrs["mac"] = mac
                if platform == "unifi":
                    if extra.get("ap"):
                        attrs.update({"essid": extra.get("ssid", ""), "ap_mac": extra["ap"], "is_wired": False})
                    elif "sw" in extra:
                        attrs.update({"is_wired": True, "sw_mac": extra["sw"][0], "sw_port": extra["sw"][1]})
                friendly = name or (mac.upper() if mac else entity_id)
                if platform == "nmap":
                    friendly = f"{name or mac} (nmap)"
                if platform == "gps":
                    friendly = f"{name} (app)"
                attrs["friendly_name"] = friendly
                if platform in ("unifi", "gps"):
                    self.entities[entity_id] = {
                        "entity_id": entity_id,
                        "id": f"reg{len(self.entities):04d}",
                        "platform": {"unifi": "unifi", "gps": "mobile_app"}[platform],
                        "device_id": device_id if platform == "unifi" else None,
                        "unique_id": f"{platform}-{mac or name}",
                        "name": None,
                        "original_name": None if platform == "unifi" else f"{name}",
                        "has_entity_name": platform == "unifi",
                        "icon": None,
                        "area_id": None,
                        "disabled_by": None,
                        "hidden_by": None,
                        "entity_category": None,
                    }
                elif platform == "nmap":
                    self.entities[entity_id] = {
                        "entity_id": entity_id,
                        "id": f"reg{len(self.entities):04d}",
                        "platform": "nmap_tracker",
                        "device_id": None,
                        "unique_id": f"nmap-{mac}",
                        "name": None,
                        "original_name": friendly,
                        "has_entity_name": False,
                        "icon": None,
                        "area_id": None,
                        "disabled_by": None,
                        "hidden_by": None,
                        "entity_category": None,
                    }
                self.profiles[entity_id] = "gps" if platform == "gps" else profile
                if platform == "gps":
                    self.track[entity_id] = self._make_track(name, now)
                    self.history[entity_id] = self._states_of(self.track[entity_id], now)
                    p = self._point_at(entity_id, now)
                    attrs.update(latitude=p[2], longitude=p[3], gps_accuracy=p[4], battery_level=p[5])
                else:
                    self.history[entity_id] = self._make_history(profile, now, platform)
                ts, state = self.history[entity_id][-1]
                self.states[entity_id] = {
                    "entity_id": entity_id,
                    "state": state,
                    "attributes": attrs,
                    "last_changed": iso(ts),
                    "last_updated": iso(ts),
                }

    @staticmethod
    def _zone_state(lat: float, lon: float) -> str:
        for entity, _name, zlat, zlon, radius in ZONES:
            dy = (lat - zlat) * 111320
            dx = (lon - zlon) * 111320 * 0.61
            if (dx * dx + dy * dy) ** 0.5 <= radius:
                return ZONE_STATE[entity]
        return "not_home"

    def _make_track(self, name: str, now: float) -> list[tuple]:
        """A person's days: a commute or a school run on weekdays, the gym, shopping and a day out."""
        rng = random.Random(name + datetime.now().strftime("%Y%m%d"))
        home = (52.0907, 5.1214)
        zone = {e: (lat, lon) for e, _n, lat, lon, _r in ZONES}
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        plan: list[tuple[float, tuple[float, float]]] = []  # (ts, place): stays, travel in between
        ron = "Ron" in name
        for d in range(-HISTORY_DAYS, 2):
            day = today + timedelta(days=d)
            at = lambda h, m=0, day=day: (day + timedelta(hours=h, minutes=m + rng.randint(-15, 15))).timestamp()  # noqa: E731
            if day.weekday() < 5:
                if ron:
                    plan += [
                        (at(7, 50), home),
                        (at(8, 40), zone["zone.work"]),
                        (at(17, 5), zone["zone.work"]),
                        (at(17, 55), home),
                    ]
                else:
                    plan += [
                        (at(8, 15), home),
                        (at(8, 30), zone["zone.school"]),
                        (at(8, 40), zone["zone.school"]),
                        (at(8, 55), home),
                    ]
                    if day.weekday() in (1, 3):
                        plan += [
                            (at(19, 0), home),
                            (at(19, 15), zone["zone.gym"]),
                            (at(20, 30), zone["zone.gym"]),
                            (at(20, 45), home),
                        ]
            else:
                place = PLACES[rng.randrange(3)] if day.weekday() == 5 else PLACES[3 + rng.randrange(2)]
                start = 11 if day.weekday() == 5 else 13
                trip = 0.4 if day.weekday() == 5 else 1.1
                plan += [
                    (at(start), home),
                    (at(start, int(trip * 60)), place),
                    (at(start + 2, int(trip * 60)), place),
                    (at(start + 2, int(trip * 120)), home),
                ]
        plan.sort()
        points: list[tuple] = []
        battery = 80.0
        t = plan[0][0] - 86400
        prev_ts, prev_place = t, home
        for ts, place in plan:
            if ts <= prev_ts:
                continue
            moving = place != prev_place
            step = 180 if moving else 1800
            k = prev_ts
            while k < ts:
                f = (k - prev_ts) / (ts - prev_ts)
                lat = prev_place[0] + (place[0] - prev_place[0]) * f + rng.gauss(0, 0.0004 if moving else 0.00008)
                lon = prev_place[1] + (place[1] - prev_place[1]) * f + rng.gauss(0, 0.0004 if moving else 0.00008)
                hour = datetime.fromtimestamp(k).hour
                battery = (
                    min(100.0, battery + 8)
                    if (hour >= 23 or hour < 6)
                    else max(5.0, battery - (0.2 if moving else 0.7))
                )
                acc = rng.choice([5, 8, 12, 16, 25, 40]) if moving else rng.choice([8, 12, 16, 20, 30])
                points.append((k, self._zone_state(lat, lon), round(lat, 6), round(lon, 6), acc, int(battery)))
                k += step + rng.randint(-30, 30)
            prev_ts, prev_place = ts, place
        return points

    @staticmethod
    def _states_of(points: list[tuple], until: float) -> list[tuple[float, str]]:
        out: list[tuple[float, str]] = []
        for p in points:
            if p[0] > until:
                break
            if not out or out[-1][1] != p[1]:
                out.append((p[0], p[1]))
        return out

    def _point_at(self, entity_id: str, ts: float) -> tuple:
        last = self.track[entity_id][0]
        for p in self.track[entity_id]:
            if p[0] > ts:
                break
            last = p
        return last

    def _make_history(self, profile: str, now: float, platform: str) -> list[tuple[float, str]]:
        rng = self.rng
        start = now - HISTORY_DAYS * 86400
        away = "not_home" if platform != "gps" else "Work"
        if profile == "guest":
            # A visit the day before yesterday and one now
            first = now - 2 * 86400 + rng.randint(0, 3600)
            return [(first, "home"), (first + 3 * 3600, "not_home"), (now - 1800, "home")]
        if profile == "stale":
            gone = now - 9 * 86400 - rng.randint(0, 20000)
            return [(start, "home"), (gone, "not_home")]
        out: list[tuple[float, str]] = []
        state = None
        t = start
        step = 300
        while t < now:
            if profile == "flappy":
                up = rng.random() > 0.04 if state != "not_home" else rng.random() > 0.35
            elif profile == "flappy-mild":
                up = rng.random() > 0.006 if state != "not_home" else rng.random() > 0.3
            elif profile == "always":
                up = rng.random() > 0.0004 if state != "not_home" else True
            else:
                up = profile_up(profile, t, rng) if rng.random() > 0.02 else state == "home"
                if profile == "phone" and platform == "gps":
                    pass
            new = "home" if up else away
            if new != state:
                out.append((t, new))
                state = new
            t += step + rng.randint(-60, 60)
        return out

    # ------------------------------------------------------------------
    async def _send_event(self, event_type: str, data: dict) -> None:
        for ws, subs in list(self.subscriptions.items()):
            for sub_id, kind in subs.items():
                if kind == event_type:
                    with contextlib.suppress(Exception):
                        await ws.send_json(
                            {
                                "id": sub_id,
                                "type": "event",
                                "event": {
                                    "event_type": event_type,
                                    "data": data,
                                    "time_fired": iso(datetime.now(UTC).timestamp()),
                                },
                            }
                        )

    async def set_state(self, entity_id: str, state: str | None = None, attrs: dict | None = None) -> None:
        old = self.states[entity_id]
        new = json.loads(json.dumps(old))
        now = iso(datetime.now(UTC).timestamp())
        if state is not None and state != old["state"]:
            new["state"] = state
            new["last_changed"] = now
            self.history[entity_id].append((datetime.now(UTC).timestamp(), state))
        if attrs:
            new["attributes"].update(attrs)
        new["last_updated"] = now
        self.states[entity_id] = new
        await self._send_event("state_changed", {"entity_id": entity_id, "old_state": old, "new_state": new})

    async def _simulate(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            now = datetime.now(UTC).timestamp()
            for entity_id, profile in self.profiles.items():
                if profile == "gps":
                    p = self._point_at(entity_id, now)
                    attrs = self.states[entity_id]["attributes"]
                    if (attrs.get("latitude"), attrs.get("longitude")) != (p[2], p[3]):
                        await self.set_state(
                            entity_id,
                            p[1],
                            {"latitude": p[2], "longitude": p[3], "gps_accuracy": p[4], "battery_level": p[5]},
                        )
                    continue
                state = self.states[entity_id]["state"]
                gps = self.states[entity_id]["attributes"].get("source_type") == "gps"
                if profile == "flappy":
                    flip = self.rng.random() < (0.12 if state == "home" else 0.5)
                elif profile == "flappy-mild":
                    flip = self.rng.random() < (0.02 if state == "home" else 0.4)
                elif profile in ("always", "stale", "guest"):
                    flip = False
                else:
                    want = profile_up(profile, now, self.rng)
                    flip = (state == "home") != want
                if flip:
                    away = "Work" if gps else "not_home"
                    await self.set_state(entity_id, away if state == "home" else "home")

    # ------------------------------------------------------------------
    def _auth(self, request: web.Request) -> None:
        if request.headers.get("Authorization") != f"Bearer {TOKEN}":
            raise web.HTTPUnauthorized()

    async def start(self) -> None:
        app = web.Application()
        r = app.router

        async def info(_r: web.Request) -> web.Response:
            return web.json_response({"data": {"timezone": "Europe/Amsterdam"}})

        async def config(_r: web.Request) -> web.Response:
            return web.json_response({"version": "2026.11.1", "time_zone": "Europe/Amsterdam"})

        r.add_get("/info", info)
        r.add_get("/core/api/config", config)
        r.add_get("/core/api/history/period/{start}", self._history)
        r.add_get("/core/websocket", self._websocket)
        r.add_get("/core/api/map_tiles/raster/{z}/{x}/{y}.png", self._tile)
        r.add_post("/mock/state/{entity_id}/{state}", self._mock_state)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", self.port).start()
        self._sim = asyncio.create_task(self._simulate())
        logger.info(f"Mock Home Assistant on http://127.0.0.1:{self.port}, token {TOKEN}")

    async def stop(self) -> None:
        if self._sim:
            self._sim.cancel()
        if self._tile_session:
            await self._tile_session.close()
        for ws in list(self.clients):
            await ws.close()
        if self._runner:
            await self._runner.cleanup()

    async def _history(self, request: web.Request) -> web.Response:
        self._auth(request)
        start = datetime.fromisoformat(request.match_info["start"]).timestamp()
        end_text = request.query.get("end_time")
        end = datetime.fromisoformat(end_text).timestamp() if end_text else datetime.now(UTC).timestamp()
        out = []
        full = "minimal_response" not in request.query
        for entity_id in request.query.get("filter_entity_id", "").split(","):
            if full and entity_id in self.track:
                pts = [p for p in self.track[entity_id] if start <= p[0] <= min(end, datetime.now(UTC).timestamp())]
                out.append(
                    [
                        {
                            "entity_id": entity_id,
                            "state": p[1],
                            "last_changed": iso(p[0]),
                            "last_updated": iso(p[0]),
                            "attributes": {
                                "latitude": p[2],
                                "longitude": p[3],
                                "gps_accuracy": p[4],
                                "battery_level": p[5],
                                "source_type": "gps",
                            },
                        }
                        for p in pts
                    ]
                )
                continue
            rows = self.history.get(entity_id)
            if not rows:
                continue
            before = [r for r in rows if r[0] < start]
            series = [(start, before[-1][1])] if before else []
            series += [r for r in rows if start <= r[0] <= end]
            if not series:
                continue
            items = [{"entity_id": entity_id, "state": series[0][1], "last_changed": iso(series[0][0])}]
            items += [{"state": s, "last_changed": iso(ts)} for ts, s in series[1:]]
            out.append(items)
        return web.json_response(out)

    async def _mock_state(self, request: web.Request) -> web.Response:
        """For tests: set a tracker's state, as an integration would."""
        entity_id = request.match_info["entity_id"]
        if entity_id not in self.states:
            raise web.HTTPNotFound()
        await self.set_state(entity_id, request.match_info["state"])
        return web.json_response(self.states[entity_id])

    async def _tile(self, request: web.Request) -> web.Response:
        """For development only: the real OpenStreetMap tile, like Home Assistant's proxy fetches it."""
        self._auth(request)
        key = "{z}/{x}/{y}".format(**request.match_info)
        if key not in self.tiles:
            import aiohttp

            if self._tile_session is None:
                self._tile_session = aiohttp.ClientSession(
                    headers={"User-Agent": "home-assistant-apps-network-mock/dev"},
                    timeout=aiohttp.ClientTimeout(total=20),
                )
            try:
                async with self._tile_session.get(f"https://tile.openstreetmap.org/{key}.png") as resp:
                    resp.raise_for_status()
                    self.tiles[key] = await resp.read()
            except Exception:
                return web.Response(status=502)
        return web.Response(body=self.tiles[key], content_type="image/png")

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": "2026.11.1"})
        auth = await ws.receive_json()
        if auth.get("access_token") != TOKEN:
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            await ws.close()
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.11.1"})
        self.clients.add(ws)
        self.subscriptions[ws] = {}
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                data = msg.json()
                await self._command(ws, data)
        finally:
            self.clients.discard(ws)
            self.subscriptions.pop(ws, None)
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

        if kind == "subscribe_events":
            self.subscriptions[ws][msg_id] = data.get("event_type")
            await result(None)
        elif kind == "get_states":
            zones = [
                {
                    "entity_id": e,
                    "state": "0",
                    "attributes": {
                        "friendly_name": n,
                        "latitude": lat,
                        "longitude": lon,
                        "radius": r,
                        "passive": False,
                    },
                }
                for e, n, lat, lon, r in ZONES
            ]
            await result(list(self.states.values()) + zones)
        elif kind == "config/entity_registry/list":
            await result(list(self.entities.values()))
        elif kind == "config/device_registry/list":
            await result(list(self.devices.values()))
        elif kind == "config/area_registry/list":
            await result([{"area_id": a, "name": n} for a, n in AREAS])
        elif kind == "config/entity_registry/update":
            entry = self.entities.get(data.get("entity_id"))
            if entry is None:
                return await result(error="Entity not found")
            for field in ("name", "icon"):
                if field in data:
                    entry[field] = data[field]
            await result({"entity_entry": entry})
            await self._send_event("entity_registry_updated", {"action": "update", "entity_id": entry["entity_id"]})
            await self._refresh_name(entry["entity_id"])
        elif kind == "config/device_registry/update":
            device = self.devices.get(data.get("device_id"))
            if device is None:
                return await result(error="Device not found")
            device["name_by_user"] = data.get("name_by_user")
            await result(device)
            await self._send_event("device_registry_updated", {"action": "update", "device_id": device["id"]})
            for entry in self.entities.values():
                if entry.get("device_id") == device["id"]:
                    await self._refresh_name(entry["entity_id"])
        else:
            await result(error=f"Unknown command {kind}")

    async def _refresh_name(self, entity_id: str) -> None:
        """The friendly name as Home Assistant makes it: the registry's name, else the device's."""
        entry = self.entities[entity_id]
        device = self.devices.get(entry.get("device_id") or "") or {}
        name = entry.get("name") or (
            (device.get("name_by_user") or device.get("name"))
            if entry.get("has_entity_name")
            else entry.get("original_name")
        )
        attrs = {"friendly_name": name or entity_id}
        if entry.get("icon"):
            attrs["icon"] = entry["icon"]
        await self.set_state(entity_id, attrs=attrs)


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    mock = MockHA(int(os.environ.get("NETWORK_MOCK_PORT", "8124")))
    await mock.start()
    try:
        await asyncio.Event().wait()
    finally:
        await mock.stop()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_main())
