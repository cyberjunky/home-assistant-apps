"""A made-up KNX installation for development and screenshots: a small house on two lines, with the kind
of mistakes the app is meant to find.

``knxproject()`` is the ETS project the way Home Assistant hands it out, ``Simulation`` produces what the
devices would send, and the HA_* tables are what the stand-in Home Assistant has configured.
"""

import math
import random
import struct
import time
from typing import Any

# address: (name, maker, product, order number, room)
DEVICES: dict[str, tuple[str, str, str, str, str]] = {
    "1.0.1": ("IP router", "Weinzierl", "KNX IP Router 751", "5243", "Meter cupboard"),
    "1.1.1": ("Switch actuator 8-fold", "MDT", "AKS-0816.03", "AKS-0816.03", "Meter cupboard"),
    "1.1.2": ("Dimming actuator 4-fold", "MDT", "AKD-0401.02", "AKD-0401.02", "Meter cupboard"),
    "1.1.3": ("Shutter actuator 4-fold", "MDT", "JAL-0410.02", "JAL-0410.02", "Meter cupboard"),
    "1.1.4": ("Heating actuator 6-fold", "MDT", "AKH-0600.03", "AKH-0600.03", "Meter cupboard"),
    "1.1.10": ("Glass push button living room", "MDT", "BE-GT2TW.02", "BE-GT2TW.02", "Living room"),
    "1.1.11": ("Push button kitchen", "Gira", "Tastsensor 4", "5002 00", "Kitchen"),
    "1.1.12": ("Push button hall", "Gira", "Tastsensor 4", "5001 00", "Hall"),
    "1.1.20": ("Room controller living room", "MDT", "SCN-RT1GS.01", "SCN-RT1GS.01", "Living room"),
    "1.1.21": ("Presence detector hall", "Steinel", "True Presence KNX", "056353", "Hall"),
    "1.1.30": ("Power supply 640 mA", "MDT", "STV-0640.02", "STV-0640.02", "Meter cupboard"),
    "1.2.1": ("Switch actuator 4-fold", "ABB", "SA/S 4.16.6.2", "2CDG110260R0011", "Garage"),
    "1.2.10": ("Weather station", "Elsner", "Suntracer KNX sl", "3093", "Roof"),
    "1.2.11": ("Push button bedroom", "Jung", "LS 4092 TSM", "4092 TSM", "Bedroom"),
    "1.2.12": ("Energy meter", "ABB", "EM/S 3.16.1", "2CDG110148R0011", "Garage"),
}

# address: (name, dpt main, dpt sub) - dpt None for an address without a type
GROUPS: dict[str, tuple[str, int | None, int | None]] = {
    "0/0/1": ("Central off", 1, 1),
    "0/0/2": ("Time", 10, 1),
    "0/0/3": ("Date", 11, 1),
    "1/0/1": ("Living room ceiling switch", 1, 1),
    "1/0/2": ("Living room ceiling dim", 3, 7),
    "1/0/3": ("Living room ceiling brightness", 5, 1),
    "1/0/4": ("Kitchen ceiling switch", 1, 1),
    "1/0/5": ("Kitchen worktop switch", 1, 1),
    "1/0/6": ("Hall switch", 1, 1),
    "1/0/7": ("Bedroom ceiling switch", 1, 1),
    "1/0/8": ("Garage switch", 1, 1),
    "1/0/9": ("Garden lights switch", 1, 1),
    "1/1/1": ("Living room ceiling status", 1, 11),
    "1/1/3": ("Living room ceiling brightness status", 5, 1),
    "1/1/4": ("Kitchen ceiling status", 1, 11),
    "1/1/5": ("Kitchen worktop status", 1, 11),
    "1/1/6": ("Hall status", 1, 11),
    "1/1/7": ("Bedroom ceiling status", 1, 11),
    "1/1/8": ("Garage status", 1, 11),
    "1/1/9": ("Garden lights status", 1, 11),
    "2/0/1": ("Living room blind up/down", 1, 8),
    "2/0/2": ("Living room blind stop", 1, 7),
    "2/0/3": ("Living room blind position", 5, 1),
    "2/1/3": ("Living room blind position status", 5, 1),
    "2/0/4": ("Bedroom blind up/down", 1, 8),
    "2/0/5": ("Bedroom blind stop", 1, 7),
    "2/0/6": ("Bedroom blind position", 5, 1),
    "2/1/6": ("Bedroom blind position status", 5, 1),
    "3/0/1": ("Living room temperature", 9, 1),
    "3/0/2": ("Living room setpoint", 9, 1),
    "3/0/3": ("Living room setpoint status", 9, 1),
    "3/0/4": ("Living room operating mode", 20, 102),
    "3/0/5": ("Living room valve", 5, 1),
    "3/0/6": ("Bedroom temperature", 9, 1),
    "3/0/7": ("Bedroom valve", 5, 1),
    "3/0/9": ("Heating demand", None, None),
    "4/0/1": ("Outside temperature", 9, 1),
    "4/0/2": ("Wind speed", 9, 5),
    "4/0/3": ("Brightness south", 9, 4),
    "4/0/4": ("Rain", 1, 5),
    "4/0/5": ("Hall presence", 1, 18),
    "4/0/6": ("Hall brightness", 9, 4),
    "5/0/1": ("Total power", 14, 56),
    "5/0/2": ("Energy consumed", 13, 10),
    "5/0/3": ("Garage socket current", 9, 21),
    "6/0/1": ("Scene living room", 17, 1),
    "6/0/9": ("Spare", 1, 1),
    "6/0/10": ("Old doorbell", 1, 1),
}

FLAGS_SEND = {"read": False, "write": False, "communication": True, "transmit": True, "update": False}
FLAGS_STATUS = {"read": True, "write": False, "communication": True, "transmit": True, "update": False}
FLAGS_LISTEN = {"read": False, "write": True, "communication": True, "transmit": False, "update": False}
FLAGS_SILENT = {"read": False, "write": False, "communication": True, "transmit": False, "update": False}

# (device, object number, text, function, dpt main, dpt sub, flags, group addresses)
OBJECTS: list[tuple[str, int, str, str, int, int | None, dict[str, bool], list[str]]] = [
    ("1.1.1", 0, "Channel A", "Switch", 1, 1, FLAGS_LISTEN, ["1/0/4", "0/0/1"]),
    ("1.1.1", 1, "Channel A", "Status", 1, 11, FLAGS_STATUS, ["1/1/4"]),
    ("1.1.1", 4, "Channel B", "Switch", 1, 1, FLAGS_LISTEN, ["1/0/5", "0/0/1"]),
    ("1.1.1", 5, "Channel B", "Status", 1, 11, FLAGS_STATUS, ["1/1/5"]),
    ("1.1.1", 8, "Channel C", "Switch", 1, 1, FLAGS_LISTEN, ["1/0/6", "0/0/1"]),
    # Two status objects that both answer reads: the hall status is answered twice
    ("1.1.1", 9, "Channel C", "Status", 1, 11, FLAGS_STATUS, ["1/1/6"]),
    ("1.1.21", 3, "Light output", "Status", 1, 11, FLAGS_STATUS, ["1/1/6"]),
    ("1.1.1", 12, "Channel D", "Switch", 1, 1, FLAGS_LISTEN, ["1/0/7", "0/0/1"]),
    # A status object without the read flag: nobody answers a read of the bedroom status
    ("1.1.1", 13, "Channel D", "Status", 1, 11, FLAGS_SEND, ["1/1/7"]),
    ("1.1.1", 16, "Channel E", "Switch", 1, 1, FLAGS_LISTEN, ["1/0/9", "0/0/1"]),
    ("1.1.1", 17, "Channel E", "Status", 1, 11, FLAGS_STATUS, ["1/1/9"]),
    ("1.1.2", 0, "Channel A", "Switch", 1, 1, FLAGS_LISTEN, ["1/0/1", "0/0/1"]),
    ("1.1.2", 1, "Channel A", "Dim relative", 3, 7, FLAGS_LISTEN, ["1/0/2"]),
    ("1.1.2", 2, "Channel A", "Dim absolute", 5, 1, FLAGS_LISTEN, ["1/0/3"]),
    ("1.1.2", 3, "Channel A", "Status", 1, 11, FLAGS_STATUS, ["1/1/1"]),
    ("1.1.2", 4, "Channel A", "Dim value status", 5, 1, FLAGS_STATUS, ["1/1/3"]),
    ("1.1.3", 0, "Channel A", "Up/down", 1, 8, FLAGS_LISTEN, ["2/0/1"]),
    ("1.1.3", 1, "Channel A", "Stop", 1, 7, FLAGS_LISTEN, ["2/0/2"]),
    ("1.1.3", 2, "Channel A", "Absolute position", 5, 1, FLAGS_LISTEN, ["2/0/3"]),
    ("1.1.3", 3, "Channel A", "Position status", 5, 1, FLAGS_STATUS, ["2/1/3"]),
    ("1.1.3", 10, "Channel B", "Up/down", 1, 8, FLAGS_LISTEN, ["2/0/4"]),
    ("1.1.3", 11, "Channel B", "Stop", 1, 7, FLAGS_LISTEN, ["2/0/5"]),
    ("1.1.3", 12, "Channel B", "Absolute position", 5, 1, FLAGS_LISTEN, ["2/0/6"]),
    ("1.1.3", 13, "Channel B", "Position status", 5, 1, FLAGS_STATUS, ["2/1/6"]),
    ("1.1.4", 0, "Channel A", "Control value", 5, 1, FLAGS_LISTEN, ["3/0/5"]),
    ("1.1.4", 5, "Channel B", "Control value", 5, 1, FLAGS_LISTEN, ["3/0/7"]),
    ("1.1.4", 30, "General", "Heating demand", 1, 1, FLAGS_SEND, ["3/0/9"]),
    ("1.1.10", 0, "Button 1", "Switch", 1, 1, FLAGS_SEND, ["1/0/1"]),
    ("1.1.10", 1, "Button 1", "Dim", 3, 7, FLAGS_SEND, ["1/0/2"]),
    ("1.1.10", 4, "Button 2", "Blind up/down", 1, 8, FLAGS_SEND, ["2/0/1"]),
    ("1.1.10", 5, "Button 2", "Blind stop", 1, 7, FLAGS_SEND, ["2/0/2"]),
    ("1.1.10", 8, "Button 3", "Scene", 17, 1, FLAGS_SEND, ["6/0/1"]),
    ("1.1.11", 0, "Rocker 1", "Switch", 1, 1, FLAGS_SEND, ["1/0/4"]),
    ("1.1.11", 2, "Rocker 2", "Switch", 1, 1, FLAGS_SEND, ["1/0/5"]),
    ("1.1.11", 4, "Rocker 3", "Central off", 1, 1, FLAGS_SEND, ["0/0/1"]),
    ("1.1.12", 0, "Rocker 1", "Switch", 1, 1, FLAGS_SEND, ["1/0/6"]),
    ("1.1.20", 0, "Temperature", "Actual value", 9, 1, FLAGS_STATUS, ["3/0/1"]),
    ("1.1.20", 1, "Setpoint", "Setpoint", 9, 1, FLAGS_LISTEN, ["3/0/2"]),
    ("1.1.20", 2, "Setpoint", "Current setpoint", 9, 1, FLAGS_STATUS, ["3/0/3"]),
    ("1.1.20", 3, "Mode", "Operating mode", 20, 102, {**FLAGS_STATUS, "write": True}, ["3/0/4"]),
    ("1.1.20", 4, "Controller", "Heating control value", 5, 1, FLAGS_SEND, ["3/0/5"]),
    ("1.1.21", 0, "Presence", "Presence", 1, 18, FLAGS_STATUS, ["4/0/5"]),
    ("1.1.21", 1, "Brightness", "Measured value", 9, 4, FLAGS_STATUS, ["4/0/6"]),
    ("1.1.21", 2, "Light output", "Switch", 1, 1, FLAGS_SEND, ["1/0/6"]),
    ("1.2.1", 0, "Output A", "Switch", 1, 1, FLAGS_LISTEN, ["1/0/8", "0/0/1"]),
    ("1.2.1", 1, "Output A", "Status", 1, 11, FLAGS_STATUS, ["1/1/8"]),
    # Linked to a 2-byte float address with a 1-byte object: the bedroom temperature cannot be read
    ("1.2.11", 0, "Rocker 1", "Switch", 1, 1, FLAGS_SEND, ["1/0/7"]),
    ("1.2.11", 2, "Rocker 2", "Blind up/down", 1, 8, FLAGS_SEND, ["2/0/4"]),
    ("1.2.11", 3, "Rocker 2", "Blind stop", 1, 7, FLAGS_SEND, ["2/0/5"]),
    ("1.2.11", 6, "Temperature", "Measured value", 5, 10, FLAGS_SEND, ["3/0/6"]),
    ("1.2.10", 0, "Temperature", "Outside temperature", 9, 1, FLAGS_STATUS, ["4/0/1"]),
    ("1.2.10", 1, "Wind", "Wind speed", 9, 5, FLAGS_STATUS, ["4/0/2"]),
    ("1.2.10", 2, "Brightness", "South", 9, 4, FLAGS_STATUS, ["4/0/3"]),
    ("1.2.10", 3, "Rain", "Rain alarm", 1, 5, FLAGS_STATUS, ["4/0/4"]),
    ("1.2.10", 8, "Clock", "Time", 10, 1, FLAGS_SEND, ["0/0/2"]),
    ("1.2.10", 9, "Clock", "Date", 11, 1, FLAGS_SEND, ["0/0/3"]),
    ("1.2.12", 0, "Total", "Active power", 14, 56, FLAGS_STATUS, ["5/0/1"]),
    ("1.2.12", 1, "Total", "Active energy", 13, 10, FLAGS_STATUS, ["5/0/2"]),
    ("1.2.12", 2, "Output A", "Current", 9, 21, FLAGS_STATUS, ["5/0/3"]),
    # In the project but linked to nothing
    ("1.1.30", 0, "Diagnosis", "Bus voltage", 9, 20, FLAGS_SILENT, []),
]

RANGES = {
    "0": "Central",
    "1": "Lighting",
    "2": "Blinds",
    "3": "Heating",
    "4": "Sensors",
    "5": "Energy",
    "6": "Scenes",
}
MIDDLE = {"1/0": "Switching", "1/1": "Status", "2/0": "Control", "2/1": "Status"}

# What the devices answer when asked directly (bus.py): the hall push button is dead, the kitchen one has its
# programming LED on, the garage actuator lost its application, and 1.1.99 is on the bus but in no project
OFFLINE = {"1.1.12"}
PROGMODE = {"1.1.11"}
UNLOADED = {"1.2.1"}
EXTRA_DEVICES = {"1.1.99": ("", "", "", "", "")}
MAKER_IDS = {"MDT": 131, "Gira": 8, "ABB": 2, "Jung": 4, "Elsner": 163, "Steinel": 291, "Weinzierl": 197}
HA_ADDRESS = "1.0.250"


def knxproject() -> dict[str, Any]:
    """The project as xknxproject (and Home Assistant's ``knx/get_knx_project``) gives it."""
    objects: dict[str, dict[str, Any]] = {}
    devices: dict[str, dict[str, Any]] = {}
    spaces: dict[str, dict[str, Any]] = {}
    for address, (name, maker, product, order, room) in DEVICES.items():
        devices[address] = {
            "name": name,
            "hardware_name": product,
            "order_number": order,
            "description": "",
            "manufacturer_name": maker,
            "individual_address": address,
            "application": f"{product} application",
            "communication_object_ids": [],
            "channels": {},
            # The kitchen push button's links were changed in ETS and not downloaded since
            "individual_address_loaded": True,
            "application_program_loaded": True,
            "parameters_loaded": address != "1.2.10",
            "communication_part_loaded": address != "1.1.11",
            "medium_config_loaded": True,
        }
        spaces.setdefault(room, {"type": "Room", "name": room, "devices": [], "spaces": {}, "functions": []})
        spaces[room]["devices"].append(address)
    for device, number, text, function, main, sub, flags, links in OBJECTS:
        object_id = f"{device}/O-{number}"
        objects[object_id] = {
            "name": text,
            "number": number,
            "text": f"{text} {function}",
            "function_text": function,
            "description": "",
            "device_address": device,
            "dpts": [{"main": main, "sub": sub}],
            "object_size": "",
            "group_address_links": links,
            "flags": {**flags, "read_on_init": False},
        }
        devices[device]["communication_object_ids"].append(object_id)

    group_addresses: dict[str, dict[str, Any]] = {}
    group_ranges: dict[str, dict[str, Any]] = {}
    for address, (name, main, sub) in GROUPS.items():
        top, middle, _sub = address.split("/")
        group_addresses[address] = {
            "name": name,
            "identifier": f"GA-{address}",
            "raw_address": (int(top) << 11) | (int(middle) << 8) | int(_sub),
            "address": address,
            "dpt": {"main": main, "sub": sub} if main is not None else None,
            "data_secure": False,
            "communication_object_ids": [],
            "description": "",
            "comment": "",
        }
        main_range = group_ranges.setdefault(
            top, {"name": RANGES.get(top, top), "group_addresses": [], "group_ranges": {}, "comment": ""}
        )
        middle_key = f"{top}/{middle}"
        middle_range = main_range["group_ranges"].setdefault(
            middle_key,
            {"name": MIDDLE.get(middle_key, "General"), "group_addresses": [], "group_ranges": {}, "comment": ""},
        )
        middle_range["group_addresses"].append(address)

    return {
        "info": {
            "project_id": "P-0421",
            "name": "Klinkerstraat 12",
            "last_modified": "2026-10-02T19:41:08.000Z",
            "group_address_style": "ThreeLevel",
            "guid": "c2b1a8e0-5a0c-4d5e-9f0e-1c2d3e4f5a6b",
            "created_by": "ETS6",
            "schema_version": "23",
            "tool_version": "6.3.1",
            "xknxproject_version": "3.10.0",
            "language_code": "en-US",
        },
        "communication_objects": objects,
        "devices": devices,
        "topology": {
            "1": {
                "name": "House",
                "description": "",
                "lines": {
                    "0": {"name": "Backbone", "medium_type": "IP", "description": "", "devices": ["1.0.1"]},
                    "1": {
                        "name": "Ground floor",
                        "medium_type": "TP",
                        "description": "",
                        "devices": [a for a in DEVICES if a.startswith("1.1.")],
                    },
                    "2": {
                        "name": "Upstairs and outside",
                        "medium_type": "TP",
                        "description": "",
                        "devices": [a for a in DEVICES if a.startswith("1.2.")],
                    },
                },
            }
        },
        "locations": {"House": {"type": "Building", "name": "House", "devices": [], "spaces": spaces, "functions": []}},
        "group_addresses": group_addresses,
        "group_ranges": group_ranges,
        "functions": {},
    }


# ----------------------------------------------------------------------
# What Home Assistant has configured
# ----------------------------------------------------------------------
def _ga(write: str | None = None, state: str | None = None, dpt: str | None = None) -> dict[str, Any]:
    return {"write": write, "state": state, "passive": [], **({"dpt": dpt} if dpt else {})}


# Entities made in Home Assistant's KNX panel: platform -> unique id -> configuration
HA_UI: dict[str, dict[str, dict[str, Any]]] = {
    "light": {
        "ui_living_ceiling": {
            "entity": {"name": "Living room ceiling"},
            "knx": {"ga_switch": _ga("1/0/1", "1/1/1"), "ga_brightness": _ga("1/0/3", "1/1/3")},
        },
        "ui_kitchen_ceiling": {"entity": {"name": "Kitchen ceiling"}, "knx": {"ga_switch": _ga("1/0/4", "1/1/4")}},
        "ui_kitchen_worktop": {"entity": {"name": "Kitchen worktop"}, "knx": {"ga_switch": _ga("1/0/5", "1/1/5")}},
        "ui_hall": {"entity": {"name": "Hall"}, "knx": {"ga_switch": _ga("1/0/6", "1/1/6")}},
        "ui_bedroom_ceiling": {"entity": {"name": "Bedroom ceiling"}, "knx": {"ga_switch": _ga("1/0/7", "1/1/7")}},
        # A typing mistake: 1/0/19 is not in the project
        "ui_garden": {"entity": {"name": "Garden lights"}, "knx": {"ga_switch": _ga("1/0/19", "1/1/9")}},
    },
    "switch": {
        "ui_garage": {"entity": {"name": "Garage"}, "knx": {"ga_switch": _ga("1/0/8", "1/1/8")}},
        "ui_central_off": {"entity": {"name": "Everything off"}, "knx": {"ga_switch": _ga("0/0/1")}},
    },
    "sensor": {
        "ui_power": {"entity": {"name": "Total power"}, "knx": {"ga_sensor": _ga(None, "5/0/1", "14.056")}},
        # The wrong type: the address carries a 2-byte float, the entity wants a percentage
        "ui_wind": {"entity": {"name": "Wind speed"}, "knx": {"ga_sensor": _ga(None, "4/0/2", "5.001")}},
    },
}

# Entities written in configuration.yaml
HA_YAML: dict[str, list[dict[str, Any]]] = {
    "cover": [
        {
            "name": "Living room blind",
            "move_long_address": "2/0/1",
            "stop_address": "2/0/2",
            "position_address": "2/0/3",
            "position_state_address": "2/1/3",
        },
        {
            "name": "Bedroom blind",
            "move_long_address": "2/0/4",
            "stop_address": "2/0/5",
            "position_address": "2/0/6",
            "position_state_address": "2/1/6",
        },
    ],
    "climate": [
        {
            "name": "Living room",
            "temperature_address": "3/0/1",
            "target_temperature_address": "3/0/2",
            "target_temperature_state_address": "3/0/3",
            "operation_mode_address": "3/0/4",
            "operation_mode_state_address": "3/0/4",
        }
    ],
    "sensor": [
        {"name": "Outside temperature", "state_address": "4/0/1", "type": "temperature"},
        {"name": "Bedroom temperature", "state_address": "3/0/6", "type": "temperature"},
        {"name": "Brightness south", "state_address": "4/0/3", "type": "illuminance"},
        {"name": "Energy consumed", "state_address": "5/0/2", "type": "active_energy_kwh"},
    ],
    "binary_sensor": [
        {"name": "Hall presence", "state_address": "4/0/5"},
        {"name": "Rain", "state_address": "4/0/4"},
    ],
    # The same light as the one made in the UI
    "light": [{"name": "Kitchen ceiling (old)", "address": "1/0/4", "state_address": "1/1/4"}],
    "expose": [{"type": "temperature", "entity_id": "sensor.living_room_temperature", "address": "3/0/8"}],
}

# Left in Home Assistant's registry after their configuration was removed
HA_STRAY = [
    ("switch.old_doorbell", "switch", "6/0/10", "Old doorbell"),
    ("light.attic", "light", "1/0/20_1/1/20", "Attic"),
]


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_").replace("__", "_")


def yaml_unique_id(item: dict[str, Any]) -> str:
    return "_".join(str(v) for k, v in item.items() if k.endswith("address"))


def ha_entities() -> list[dict[str, Any]]:
    """Every KNX entity of the stand-in Home Assistant: {entity_id, platform, unique_id, name, ui, addresses}."""
    out = []
    for platform, items in HA_UI.items():
        for unique_id, config in items.items():
            addresses = []
            for value in config["knx"].values():
                addresses += [a for a in (value.get("write"), value.get("state")) if a]
            out.append(
                {
                    "entity_id": f"{platform}.{_slug(config['entity']['name'])}",
                    "platform": platform,
                    "unique_id": unique_id,
                    "name": config["entity"]["name"],
                    "ui": True,
                    "addresses": addresses,
                }
            )
    for platform, items in HA_YAML.items():
        if platform == "expose":
            continue
        for item in items:
            out.append(
                {
                    "entity_id": f"{platform}.{_slug(item['name'])}",
                    "platform": platform,
                    "unique_id": yaml_unique_id(item),
                    "name": item["name"],
                    "ui": False,
                    "addresses": [v for k, v in item.items() if k.endswith("address")],
                }
            )
    return out


# ----------------------------------------------------------------------
# What the devices send
# ----------------------------------------------------------------------
def float16(value: float) -> bytes:
    """DPT 9."""
    mantissa, exponent = round(value * 100), 0
    while not -2048 <= mantissa <= 2047:
        mantissa >>= 1
        exponent += 1
    return bytes([((mantissa >> 8) & 0x87) | (exponent << 3), mantissa & 0xFF])


def bit(value: bool) -> int:
    return 1 if value else 0


class Simulation:
    """Produces (source, group address, kind, payload) the way the house would: ``payload`` is an int for a
    value of up to 6 bits, bytes for anything longer, None for a read."""

    def __init__(self, seed: int = 7, pace: float = 1.0):
        # ``pace`` stretches the time between things people do (buttons, presence): 1 keeps the monitor
        # lively to look at, a larger number is a house as it really is
        self.random = random.Random(seed)
        self.pace = pace
        self.lights = {"1/0/1": False, "1/0/4": True, "1/0/5": False, "1/0/6": False, "1/0/7": False, "1/0/8": False}
        self.temperature = 20.6
        self.outside = 8.4
        self.wind = 3.0
        self.power = 420.0
        self.energy = 18233
        self.next: dict[str, float] = {}

    def due(self, key: str, now: float, every: float) -> bool:
        if now >= self.next.get(key, 0):
            self.next[key] = now + every * self.random.uniform(0.8, 1.2)
            return True
        return False

    def answer(self, address: str) -> list[tuple[str, Any]]:
        """Who answers a read of a group address, and with what."""
        answers: dict[str, list[tuple[str, Any]]] = {
            "1/1/1": [("1.1.2", bit(self.lights["1/0/1"]))],
            "1/1/3": [("1.1.2", bytes([140 if self.lights["1/0/1"] else 0]))],
            "1/1/4": [("1.1.1", bit(self.lights["1/0/4"]))],
            "1/1/5": [("1.1.1", bit(self.lights["1/0/5"]))],
            # Two devices answer for the hall
            "1/1/6": [("1.1.1", bit(self.lights["1/0/6"])), ("1.1.21", bit(self.lights["1/0/6"]))],
            "1/1/8": [("1.2.1", bit(self.lights["1/0/8"]))],
            "1/1/9": [("1.1.1", 0)],
            "2/1/3": [("1.1.3", bytes([64]))],
            "2/1/6": [("1.1.3", bytes([255]))],
            "3/0/1": [("1.1.20", float16(self.temperature))],
            "3/0/3": [("1.1.20", float16(21.0))],
            "3/0/4": [("1.1.20", bytes([1]))],
            "4/0/1": [("1.2.10", float16(self.outside))],
            "4/0/2": [("1.2.10", float16(self.wind))],
            "4/0/3": [("1.2.10", float16(18400.0))],
            "4/0/4": [("1.2.10", 0)],
            "4/0/5": [("1.1.21", 0)],
            "5/0/1": [("1.2.12", struct.pack(">f", self.power))],
            "5/0/2": [("1.2.12", struct.pack(">i", self.energy))],
        }
        return answers.get(address, [])

    def startup(self) -> list[tuple[str, str, str, Any]]:
        """Home Assistant asks every state address once when it starts."""
        out: list[tuple[str, str, str, Any]] = []
        for address in (
            "1/1/1",
            "1/1/3",
            "1/1/4",
            "1/1/5",
            "1/1/6",
            "1/1/7",
            "1/1/8",
            "2/1/3",
            "3/0/1",
            "4/0/1",
            "5/0/1",
        ):
            out.append((HA_ADDRESS, address, "read", None))
            out += [(src, address, "response", payload) for src, payload in self.answer(address)]
        return out

    def step(self, now: float | None = None) -> list[tuple[str, str, str, Any]]:
        now = time.time() if now is None else now
        r = self.random
        out: list[tuple[str, str, str, Any]] = []
        day = (now % 86400) / 86400 * 2 * math.pi
        if self.due("temperature", now, 45):
            self.temperature = 20.4 + 1.1 * math.sin(day - 2.4) + r.uniform(-0.08, 0.08)
            out.append(("1.1.20", "3/0/1", "write", float16(self.temperature)))
            out.append(("1.1.20", "3/0/5", "write", bytes([max(0, min(255, int((21 - self.temperature) * 120)))])))
        if self.due("bedroom", now, 60):
            # A 1-byte value on an address typed as a 2-byte float
            out.append(("1.2.11", "3/0/6", "write", bytes([int(19 + r.random() * 2)])))
        if self.due("outside", now, 90):
            self.outside = 8.0 + 4.5 * math.sin(day - 2.9) + 2.0 * math.sin(now / 86400 / 3) + r.uniform(-0.15, 0.15)
            out.append(("1.2.10", "4/0/1", "write", float16(self.outside)))
            out.append(("1.2.10", "4/0/3", "write", float16(18000 + r.uniform(-900, 900))))
        if self.due("wind", now, 1.4):
            # A weather station set to report far too often
            self.wind = max(0.0, 4.0 + 2.5 * math.sin(now / 5400) + 1.5 * math.sin(now / 700) + r.uniform(-0.5, 0.5))
            out.append(("1.2.10", "4/0/2", "write", float16(self.wind)))
        if self.due("power", now, 12):
            self.power = max(
                90.0, 380 + 260 * math.sin(day - 3.6) + 180 * abs(math.sin(now / 1900)) + r.uniform(-40, 40)
            )
            self.energy += 1
            out.append(("1.2.12", "5/0/1", "write", struct.pack(">f", self.power)))
            out.append(("1.2.12", "5/0/3", "write", float16(self.power / 230 * 1000)))
        if self.due("energy", now, 120):
            out.append(("1.2.12", "5/0/2", "write", struct.pack(">i", self.energy)))
        if self.due("clock", now, 300):
            t = time.localtime(now)
            out.append(("1.2.10", "0/0/2", "write", bytes([(t.tm_wday + 1) << 5 | t.tm_hour, t.tm_min, t.tm_sec])))
        if self.due("light", now, 25 * self.pace):
            address, button = r.choice(
                [
                    ("1/0/1", "1.1.10"),
                    ("1/0/4", "1.1.11"),
                    ("1/0/5", "1.1.11"),
                    ("1/0/7", "1.2.11"),
                    ("1/0/8", HA_ADDRESS),
                ]
            )
            self.lights[address] = not self.lights[address]
            status = address.replace("/0/", "/1/")
            actuator = "1.1.2" if address == "1/0/1" else "1.2.1" if address == "1/0/8" else "1.1.1"
            out.append((button, address, "write", bit(self.lights[address])))
            out.append((actuator, status, "write", bit(self.lights[address])))
            if address == "1/0/1":
                out.append((actuator, "1/1/3", "write", bytes([140 if self.lights[address] else 0])))
        if self.due("presence", now, 70 * self.pace):
            self.lights["1/0/6"] = not self.lights["1/0/6"]
            on = bit(self.lights["1/0/6"])
            out += [("1.1.21", "4/0/5", "write", on), ("1.1.21", "1/0/6", "write", on), ("1.1.1", "1/1/6", "write", on)]
        if self.due("heating", now, 180):
            out.append(("1.1.4", "3/0/9", "write", r.choice([0, 1])))
        if self.due("stranger", now, 150):
            # A device nobody put in the project, sending to an address the project does not have
            out.append(("1.1.99", "7/3/1", "write", r.choice([0, 1])))
        if self.due("foreign", now, 200 * self.pace):
            # The kitchen push button also switches the garage: it was programmed from another project
            out.append(("1.1.11", "1/0/8", "write", 1))
        if self.due("ha-read", now, 240 * self.pace):
            for address in ("1/1/7", "1/1/6"):
                out.append((HA_ADDRESS, address, "read", None))
                out += [(src, address, "response", payload) for src, payload in self.answer(address)]
        return out
