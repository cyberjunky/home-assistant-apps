"""A stand-in Home Assistant for development: a home with a little of everything wrong in it.

    python3 -m tools.mock_ha                 # on port 18123
    REPAIR_SUPERVISOR_URL=http://127.0.0.1:18123 REPAIR_DATA_DIR=/tmp/repair python3 -m src

It answers the Supervisor's proxy paths the app uses: Core's REST API under /core/api, Core's
websocket under /core/websocket, and the resolution centre under /resolution. Repairs change it
the way Home Assistant would, so a fix can be followed to the next scan.
"""

import argparse
import contextlib
import fnmatch
import json
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from aiohttp import WSMsgType, web

VERSION = "2026.11.2"
TRACEBACK = """Traceback (most recent call last):
  File "/usr/src/homeassistant/homeassistant/helpers/update_coordinator.py", line 386, in _async_refresh
    self.data = await self._async_update_data()
  File "/config/custom_components/solarfox/coordinator.py", line 58, in _async_update_data
    return self._parse(await self.client.read())
  File "/config/custom_components/solarfox/coordinator.py", line 71, in _parse
    return {"power": payload["pv"]["power"], "energy": payload["pv"]["total"]}
KeyError: 'pv'
"""


def iso(seconds_ago: float) -> str:
    return (datetime.now(UTC) - timedelta(seconds=seconds_ago)).isoformat()


class MockHome:
    def __init__(self):
        self.calls: list[tuple] = []  # what the app asked to change, for the tests
        self.config_dir: Path | None = None  # set by make_config_dir
        self.entries = [
            self._entry("e-hue", "hue", "Philips Hue"),
            self._entry("e-zha", "zha", "Zigbee Home Automation", removable=True),
            self._entry(
                "e-shelly",
                "shelly",
                "Washing machine plug",
                "setup_retry",
                "Cannot connect to host 192.168.1.61:80 (timeout)",
            ),
            self._entry("e-tado", "tado", "Tado", "setup_error", "Authentication failed: invalid_auth"),
            self._entry("e-fox", "solarfox", "SolarFox inverter", removable=True),
            self._entry("e-mqtt", "mqtt", "Mosquitto broker", removable=True),
            self._entry("e-met", "met", "Home"),
        ]
        self.devices = [
            self._device("d-bridge", "Hue Bridge", "Signify", "BSB002", ["e-hue"]),
            self._device("d-living", "Living room lamp", "Signify", "LCT015", ["e-hue"], via="d-bridge"),
            self._device("d-hall", "Hallway lamp", "Signify", "LWB010", ["e-hue"], via="d-bridge"),
            self._device("d-door", "Front door sensor", "Aqara", "MCCGQ11LM", ["e-zha"]),
            self._device("d-motion", "Old motion sensor", "IKEA", "E1525", ["e-zha"]),
            self._device("d-remote", "IKEA remote", "IKEA", "E1524", ["e-zha"]),
            self._device("d-plug", "Washing machine plug", "Shelly", "Plug S", ["e-shelly"]),
            self._device("d-thermo", "Thermostat", "Tado", "RU02", ["e-tado"]),
            self._device("d-weather", "Old weather station", "Netatmo", "NWS01", ["e-netatmo-gone"]),
            self._device("d-fox", "SolarFox inverter", "SolarFox", "SF-3000", ["e-fox"]),
        ]
        self.entities: list[dict] = []
        self.states: dict[str, dict] = {}
        add = self._entity
        add("light.living_room", "Living room lamp", "hue", "e-hue", "d-living", "on")
        add("sensor.living_room_lamp_power", "Living room lamp power", "hue", "e-hue", "d-living", "8.4")
        add("light.hallway", "Hallway lamp", "hue", "e-hue", "d-hall", "unavailable", ago=3 * 86400)
        add("sensor.hallway_lamp_power", "Hallway lamp power", "hue", "e-hue", "d-hall", "unavailable", ago=3 * 86400)
        add("binary_sensor.front_door", "Front door", "zha", "e-zha", "d-door", "off")
        add("sensor.front_door_battery", "Front door battery", "zha", "e-zha", "d-door", "71")
        add(
            "sensor.front_door_temperature", "Front door temperature", "zha", "e-zha", "d-door", "unavailable", ago=7200
        )
        add(
            "binary_sensor.old_motion",
            "Old motion sensor",
            "zha",
            "e-zha",
            "d-motion",
            "unavailable",
            restored=True,
            ago=40 * 86400,
        )
        add(
            "sensor.old_motion_battery",
            "Old motion sensor battery",
            "zha",
            "e-zha",
            "d-motion",
            "unavailable",
            restored=True,
            ago=40 * 86400,
        )
        add("switch.washing_machine", "Washing machine", "shelly", "e-shelly", "d-plug", "unavailable", ago=5400)
        add(
            "sensor.washing_machine_power",
            "Washing machine power",
            "shelly",
            "e-shelly",
            "d-plug",
            "unavailable",
            ago=5400,
        )
        add("climate.thermostat", "Thermostat", "tado", "e-tado", "d-thermo", "unavailable", ago=86400)
        add(
            "sensor.weather_station_temperature",
            "Weather station temperature",
            "netatmo",
            "e-netatmo-gone",
            "d-weather",
            "unavailable",
            restored=True,
            ago=90 * 86400,
        )
        add("sensor.solarfox_power", "SolarFox power", "solarfox", "e-fox", "d-fox", "1840")
        add("sensor.solarfox_energy", "SolarFox energy", "solarfox", "e-fox", "d-fox", "12480.5")
        add("sensor.gas_meter", "Gas meter", "mqtt", "e-mqtt", None, "4521.3")
        add("sensor.outside_temperature", "Outside temperature", "met", "e-met", None, "7.2")
        add("sensor.unused_helper", "Unused helper", "template", None, None, None)  # registered, no state
        add("sensor.disabled_thing", "Disabled thing", "mqtt", "e-mqtt", None, None, disabled=True)
        add(
            "automation.morning_lights", "Morning lights", "automation", None, None, "on", attrs={"id": "1700000000001"}
        )
        add(
            "automation.broken_one",
            "Broken automation",
            "automation",
            None,
            None,
            "unavailable",
            attrs={"id": "1700000000002"},
            ago=600,
        )
        add("script.good_night", "Good night", "script", None, None, "off")
        add(
            "scene.movie_night",
            "Movie night",
            "homeassistant",
            None,
            None,
            "2026-11-01T20:00:00+00:00",
            attrs={"id": "1700000000003", "entity_id": ["light.living_room", "light.tv_backlight_gone"]},
        )
        # Made in YAML without a unique id: a state, but nothing in the registry
        self.states["sensor.yaml_template"] = self._state(
            "sensor.yaml_template", "YAML template", "unavailable", 1800, {}
        )
        self.automations = {
            "automation.morning_lights": {
                "id": "1700000000001",
                "alias": "Morning lights",
                "triggers": [{"trigger": "state", "entity_id": "binary_sensor.front_door", "to": "on"}],
                "conditions": [
                    {
                        "condition": "template",
                        "value_template": "{{ is_state('sun.sun', 'below_horizon') and states('sensor.kitchen_lux_gone') | int < 20 }}",
                    }
                ],
                "actions": [
                    {"action": "light.turn_on", "target": {"entity_id": ["light.living_room", "light.kitchen_old"]}},
                    {
                        "device_id": "0123456789abcdef0123456789abcdef",
                        "domain": "light",
                        "type": "turn_on",
                        "entity_id": "light.hallway",
                    },
                ],
            },
        }
        self.scripts = {
            "script.good_night": {
                "alias": "Good night",
                "sequence": [{"action": "light.turn_off", "target": {"entity_id": "light.living_room"}}],
            }
        }
        self.dashboards = {
            None: {
                "views": [
                    {
                        "title": "Home",
                        "cards": [
                            {
                                "type": "entities",
                                "entities": [
                                    "light.living_room",
                                    "sensor.removed_one",
                                    {"entity": "sensor.outside_temperature"},
                                ],
                            }
                        ],
                    }
                ]
            },
            "energy-wall": {"views": [{"cards": [{"type": "gauge", "entity": "sensor.solarfox_power"}]}]},
        }
        self.statistic_ids = [
            {"statistic_id": "sensor.solarfox_energy", "source": "recorder"},
            {"statistic_id": "sensor.gas_meter", "source": "recorder"},
            {"statistic_id": "sensor.old_power_meter", "source": "recorder"},
            {"statistic_id": "sensor.attic_humidity", "source": "recorder"},
            {"statistic_id": "energy:imported_from_grid", "source": "energy"},
        ]
        self.statistic_issues = {
            "sensor.gas_meter": [
                {
                    "type": "units_changed",
                    "data": {"statistic_id": "sensor.gas_meter", "state_unit": "m³", "metadata_unit": "ft³"},
                }
            ],
        }
        self.repairs = [
            {
                "domain": "homeassistant",
                "issue_domain": "mqtt",
                "issue_id": "deprecated_yaml_mqtt_sensor",
                "translation_key": "deprecated_yaml",
                "translation_placeholders": {"domain": "mqtt", "integration_title": "MQTT"},
                "severity": "warning",
                "breaks_in_ha_version": "2027.6.0",
                "created": iso(12 * 86400),
                "dismissed_version": None,
                "ignored": False,
                "is_fixable": False,
                "learn_more_url": None,
            }
        ]
        now = time.time()
        self.log = [
            {
                "name": "custom_components.solarfox.coordinator",
                "message": ["Unexpected error fetching solarfox data"],
                "level": "ERROR",
                "source": ["/config/custom_components/solarfox/coordinator.py", 58],
                "timestamp": now - 40,
                "exception": TRACEBACK,
                "count": 34,
                "first_occurred": now - 6 * 3600,
            },
            {
                "name": "homeassistant.components.shelly",
                "message": ["Error fetching Washing machine plug data: Timeout connecting to 192.168.1.61"],
                "level": "ERROR",
                "source": ["components/shelly/coordinator.py", 301],
                "timestamp": now - 300,
                "exception": "",
                "count": 18,
                "first_occurred": now - 5400,
            },
            {
                "name": "homeassistant.components.tado",
                "message": ["Authentication failed for Tado: invalid credentials"],
                "level": "ERROR",
                "source": ["components/tado/__init__.py", 77],
                "timestamp": now - 86000,
                "exception": "",
                "count": 1,
                "first_occurred": now - 86000,
            },
            {
                "name": "homeassistant.helpers.template",
                "message": [
                    "Template variable error: 'None' has no attribute 'state' when rendering '{{ states.sensor.missing.state }}'"
                ],
                "level": "ERROR",
                "source": ["helpers/template.py", 2748],
                "timestamp": now - 900,
                "exception": "",
                "count": 210,
                "first_occurred": now - 2 * 86400,
            },
            {
                "name": "homeassistant.components.mqtt.sensor",
                "message": [
                    "The 'value_template' option near /config/configuration.yaml:41 is deprecated and will be removed in 2027.6"
                ],
                "level": "WARNING",
                "source": ["components/mqtt/sensor.py", 120],
                "timestamp": now - 7000,
                "exception": "",
                "count": 1,
                "first_occurred": now - 7000,
            },
            {
                "name": "homeassistant.components.hue.light",
                "message": ["Updating hue light took longer than the scheduled update interval 0:00:05"],
                "level": "WARNING",
                "source": ["helpers/entity_platform.py", 1034],
                "timestamp": now - 12000,
                "exception": "",
                "count": 6,
                "first_occurred": now - 80000,
            },
        ]
        self.manifests = [
            {
                "domain": "hue",
                "name": "Philips Hue",
                "is_built_in": True,
                "documentation": "https://www.home-assistant.io/integrations/hue",
            },
            {
                "domain": "zha",
                "name": "Zigbee Home Automation",
                "is_built_in": True,
                "documentation": "https://www.home-assistant.io/integrations/zha",
            },
            {
                "domain": "shelly",
                "name": "Shelly",
                "is_built_in": True,
                "documentation": "https://www.home-assistant.io/integrations/shelly",
            },
            {
                "domain": "tado",
                "name": "Tado",
                "is_built_in": True,
                "documentation": "https://www.home-assistant.io/integrations/tado",
            },
            {
                "domain": "mqtt",
                "name": "MQTT",
                "is_built_in": True,
                "documentation": "https://www.home-assistant.io/integrations/mqtt",
            },
            {"domain": "met", "name": "Meteorologisk institutt (Met.no)", "is_built_in": True},
            {
                "domain": "solarfox",
                "name": "SolarFox",
                "is_built_in": False,
                "issue_tracker": "https://github.com/example/solarfox/issues",
                "documentation": "https://github.com/example/solarfox",
            },
        ]
        self.resolution = {
            "unsupported": [],
            "unhealthy": [],
            "issues": [{"uuid": "a1", "type": "free_space", "context": "system", "reference": None}],
            "suggestions": [{"uuid": "s1", "type": "clear_full_backup", "context": "system", "reference": None}],
            "checks": [],
        }

    # ------------------------------------------------------------------ building the home
    @staticmethod
    def _entry(entry_id, domain, title, state="loaded", reason=None, removable=False) -> dict:
        return {
            "entry_id": entry_id,
            "domain": domain,
            "title": title,
            "state": state,
            "reason": reason,
            "disabled_by": None,
            "supports_remove_device": removable,
            "source": "user",
        }

    @staticmethod
    def _device(device_id, name, maker, model, entries, via=None) -> dict:
        return {
            "id": device_id,
            "name": name,
            "name_by_user": None,
            "manufacturer": maker,
            "model": model,
            "config_entries": entries,
            "primary_config_entry": entries[0],
            "via_device_id": via,
            "disabled_by": None,
            "entry_type": None,
            "area_id": None,
        }

    @staticmethod
    def _state(entity_id, name, state, ago, attrs) -> dict:
        return {
            "entity_id": entity_id,
            "state": state,
            "attributes": {"friendly_name": name, **attrs},
            "last_changed": iso(ago),
            "last_updated": iso(ago),
        }

    def _entity(
        self, entity_id, name, platform, entry_id, device_id, state, restored=False, ago=600, attrs=None, disabled=False
    ) -> None:
        self.entities.append(
            {
                "entity_id": entity_id,
                "name": None,
                "original_name": name,
                "platform": platform,
                "config_entry_id": entry_id,
                "device_id": device_id,
                "disabled_by": "user" if disabled else None,
                "hidden_by": None,
                "unique_id": entity_id,
            }
        )
        if state is not None:
            self.states[entity_id] = self._state(
                entity_id, name, state, ago, {**(attrs or {}), **({"restored": True} if restored else {})}
            )

    # ------------------------------------------------------------------ changes
    def remove_entity(self, entity_id: str) -> None:
        if not any(e["entity_id"] == entity_id for e in self.entities):
            raise LookupError("Entity not found")
        self.entities = [e for e in self.entities if e["entity_id"] != entity_id]
        self.states.pop(entity_id, None)
        # A device that no integration holds goes once nothing refers to it
        known = {e["entry_id"] for e in self.entries}
        self.devices = [
            d
            for d in self.devices
            if any(c in known for c in d["config_entries"]) or any(e["device_id"] == d["id"] for e in self.entities)
        ]

    def update_entity(self, entity_id: str, disabled_by) -> dict:
        entity = next((e for e in self.entities if e["entity_id"] == entity_id), None)
        if entity is None:
            raise LookupError("Entity not found")
        entity["disabled_by"] = disabled_by
        if disabled_by:
            self.states.pop(entity_id, None)
        return {"entity_entry": entity}

    def remove_device(self, device_id: str, entry_id: str) -> None:
        entry = next((e for e in self.entries if e["entry_id"] == entry_id), None)
        if entry is None or not any(d["id"] == device_id for d in self.devices):
            raise LookupError("Unknown config entry or device")
        if not entry["supports_remove_device"]:
            raise LookupError("Config entry does not support device removal")
        self.devices = [d for d in self.devices if d["id"] != device_id]

    def clear_statistics(self, ids: list[str]) -> None:
        self.statistic_ids = [s for s in self.statistic_ids if s["statistic_id"] not in ids]
        for statistic_id in ids:
            self.statistic_issues.pop(statistic_id, None)

    # ------------------------------------------------------------------ the websocket's commands
    def command(self, kind: str, msg: dict):
        self.calls.append((kind, {k: v for k, v in msg.items() if k not in ("id", "type")}))
        match kind:
            case "get_states":
                return list(self.states.values())
            case "config/entity_registry/list":
                return self.entities
            case "config/device_registry/list":
                return self.devices
            case "config_entries/get":
                return self.entries
            case "repairs/list_issues":
                return {"issues": self.repairs}
            case "system_log/list":
                return self.log
            case "recorder/validate_statistics":
                return self.statistic_issues
            case "recorder/list_statistic_ids":
                return self.statistic_ids
            case "manifest/list":
                return self.manifests
            case "frontend/get_translations":
                return {
                    "resources": {
                        "component.homeassistant.issues.deprecated_yaml.title": "The {integration_title} YAML configuration is being removed",
                        "component.homeassistant.issues.deprecated_yaml.description": "Configuring {integration_title} using YAML is being removed. Remove the `{domain}` configuration from your configuration.yaml file and restart Home Assistant.",
                    }
                }
            case "automation/config":
                return {"config": self.automations.get(msg["entity_id"])}
            case "script/config":
                return {"config": self.scripts.get(msg["entity_id"])}
            case "lovelace/dashboards/list":
                return [
                    {"url_path": path, "title": "Energy wall", "mode": "storage"} for path in self.dashboards if path
                ]
            case "lovelace/config":
                return self.dashboards[msg.get("url_path")]
            case "config/entity_registry/remove":
                return self.remove_entity(msg["entity_id"])
            case "config/entity_registry/update":
                return self.update_entity(msg["entity_id"], msg.get("disabled_by"))
            case "config/device_registry/remove_config_entry":
                return self.remove_device(msg["device_id"], msg["config_entry_id"])
            case "recorder/clear_statistics":
                return self.clear_statistics(msg["statistic_ids"])
        raise KeyError("Unknown command.")


# Entities that were removed long ago but still have rows in the recorder's database
GONE_WITH_HISTORY = {
    "sensor.old_power_meter": 48210,
    "sensor.attic_humidity": 9120,
    "binary_sensor.garden_gate": 356,
    "light.christmas_tree": 41,
}


def make_config_dir(path: Path, home: MockHome) -> Path:
    """A configuration folder as Home Assistant keeps it: the recorder's database and the registry files."""
    path = Path(path)
    (path / ".storage").mkdir(parents=True, exist_ok=True)
    database = path / "home-assistant_v2.db"
    database.unlink(missing_ok=True)
    db = sqlite3.connect(database)
    db.executescript(
        "CREATE TABLE states_meta (metadata_id INTEGER PRIMARY KEY, entity_id TEXT);"
        "CREATE TABLE states (state_id INTEGER PRIMARY KEY, metadata_id INTEGER, state TEXT, last_updated_ts REAL);"
        "CREATE INDEX ix_states_metadata_id_last_updated_ts ON states (metadata_id, last_updated_ts);"
    )
    recorded = {entity_id: 500 for entity_id in home.states} | GONE_WITH_HISTORY
    for number, (entity_id, rows) in enumerate(recorded.items(), 1):
        db.execute("INSERT INTO states_meta VALUES (?, ?)", (number, entity_id))
        db.executemany(
            "INSERT INTO states (metadata_id, state, last_updated_ts) VALUES (?, 'x', ?)",
            ((number, i) for i in range(rows)),
        )
    db.commit()
    db.close()
    deleted = [
        {"entity_id": e, "platform": "zha", "orphaned_timestamp": None}
        for e in ("sensor.replaced_plug_power", "switch.replaced_plug")
    ]
    (path / ".storage" / "core.entity_registry").write_text(
        json.dumps({"version": 1, "data": {"entities": [], "deleted_entities": deleted}})
    )
    (path / ".storage" / "core.device_registry").write_text(
        json.dumps({"version": 1, "data": {"devices": [], "deleted_devices": [{"id": "x1"}]}})
    )
    home.config_dir = path
    return path


def purge_from_database(home: MockHome, globs: list[str]) -> None:
    """What the recorder does with purge_entities: drop the states of what matches."""
    db = sqlite3.connect(home.config_dir / "home-assistant_v2.db")
    for meta, entity_id in db.execute("SELECT metadata_id, entity_id FROM states_meta").fetchall():
        if any(fnmatch.fnmatchcase(entity_id, glob) for glob in globs):
            db.execute("DELETE FROM states WHERE metadata_id = ?", (meta,))
            db.execute("DELETE FROM states_meta WHERE metadata_id = ?", (meta,))
    db.commit()
    db.close()


def build_app(home: MockHome) -> web.Application:
    async def websocket(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required", "ha_version": VERSION})
        auth = await ws.receive_json()
        if auth.get("type") != "auth":
            await ws.send_json({"type": "auth_invalid", "message": "Invalid access token"})
            return ws
        await ws.send_json({"type": "auth_ok", "ha_version": VERSION})
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            msg = message.json()
            try:
                result = home.command(msg.get("type", ""), msg)
                await ws.send_json({"id": msg["id"], "type": "result", "success": True, "result": result})
            except KeyError as e:
                await ws.send_json(
                    {
                        "id": msg["id"],
                        "type": "result",
                        "success": False,
                        "error": {"code": "unknown_command", "message": str(e).strip("'")},
                    }
                )
            except LookupError as e:
                await ws.send_json(
                    {
                        "id": msg["id"],
                        "type": "result",
                        "success": False,
                        "error": {"code": "not_found", "message": str(e)},
                    }
                )
        return ws

    async def check_config(request: web.Request) -> web.Response:
        return web.json_response({"result": "valid", "errors": None, "warnings": None})

    async def reload_entry(request: web.Request) -> web.Response:
        home.calls.append(("reload", request.match_info["entry"]))
        if not any(e["entry_id"] == request.match_info["entry"] for e in home.entries):
            return web.json_response({"message": "Invalid entry specified"}, status=404)
        return web.json_response({"require_restart": False})

    async def delete_entry(request: web.Request) -> web.Response:
        home.calls.append(("delete_entry", request.match_info["entry"]))
        home.entries = [e for e in home.entries if e["entry_id"] != request.match_info["entry"]]
        return web.json_response({"require_restart": False})

    async def service(request: web.Request) -> web.Response:
        data = await request.json()
        home.calls.append((f"{request.match_info['domain']}.{request.match_info['service']}", data))
        if request.match_info["service"] == "purge_entities" and home.config_dir:
            purge_from_database(home, [*data.get("entity_globs", []), *data.get("entity_id", [])])
        return web.json_response([])

    async def delete_state(request: web.Request) -> web.Response:
        home.calls.append(("delete_state", request.match_info["entity"]))
        if home.states.pop(request.match_info["entity"], None) is None:
            return web.json_response({"message": "Entity not found."}, status=404)
        return web.json_response({"message": "Entity removed."})

    async def resolution(request: web.Request) -> web.Response:
        return web.json_response({"result": "ok", "data": home.resolution})

    async def suggestion(request: web.Request) -> web.Response:
        home.calls.append(("suggestion", request.match_info["uuid"]))
        home.resolution["suggestions"] = [
            s for s in home.resolution["suggestions"] if s["uuid"] != request.match_info["uuid"]
        ]
        home.resolution["issues"] = []
        return web.json_response({"result": "ok", "data": {}})

    app = web.Application()
    app.router.add_get("/core/websocket", websocket)
    app.router.add_post("/core/api/config/core/check_config", check_config)
    app.router.add_post("/core/api/config/config_entries/entry/{entry}/reload", reload_entry)
    app.router.add_delete("/core/api/config/config_entries/entry/{entry}", delete_entry)
    app.router.add_post("/core/api/services/{domain}/{service}", service)
    app.router.add_delete("/core/api/states/{entity}", delete_state)
    app.router.add_get("/resolution/info", resolution)
    app.router.add_post("/resolution/suggestion/{uuid}", suggestion)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18123)
    parser.add_argument(
        "--config-dir", help="make a configuration folder here, with a database and registry files, for the deep scan"
    )
    args = parser.parse_args()
    print(f"Stand-in Home Assistant on http://{args.host}:{args.port}")
    with contextlib.suppress(KeyboardInterrupt):
        home = MockHome()
        if args.config_dir:
            print(f"Configuration folder in {make_config_dir(Path(args.config_dir), home)}")
        web.run_app(build_app(home), host=args.host, port=args.port, print=None)


if __name__ == "__main__":
    main()
