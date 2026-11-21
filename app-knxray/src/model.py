"""What is known about the installation, without the bus: the ETS project as Home Assistant's KNX
integration holds it, and what Home Assistant itself has configured for KNX.

Both are plain data built from what Home Assistant answers, so the checks can be run and tested without
a Home Assistant or a bus.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from .telegrams import dpt_main, dpt_text, transcoder_for

GA_PATTERN = re.compile(r"^\d{1,2}/\d{1,4}(/\d{1,3})?$|^\d{1,5}$")


def is_ga(value: Any) -> bool:
    return isinstance(value, str | int) and bool(GA_PATTERN.match(str(value)))


def line_of(address: str) -> str:
    """``1.1`` for the device ``1.1.12``."""
    return address.rsplit(".", 1)[0] if address.count(".") == 2 else ""


# ----------------------------------------------------------------------
# The ETS project
# ----------------------------------------------------------------------
# The parts of a device ETS downloads, and the letters it shows them by
PENDING_PARTS = (
    ("individual_address_loaded", "Adr"),
    ("application_program_loaded", "Prg"),
    ("parameters_loaded", "Par"),
    ("communication_part_loaded", "Grp"),
    ("medium_config_loaded", "Cfg"),
)
PENDING_NAMES = {
    "Adr": "the individual address",
    "Prg": "the application",
    "Par": "the parameters",
    "Grp": "the group addresses",
    "Cfg": "the configuration",
}


# ----------------------------------------------------------------------
# The ETS project (its own part)
# ----------------------------------------------------------------------
@dataclass
class Project:
    loaded: bool = False
    info: dict[str, Any] = field(default_factory=dict)
    # address -> {address, name, description, dpt, range, secure, objects: [id], devices: [address],
    #             senders, listeners, answerers, object_dpts: [dpt]}
    group_addresses: dict[str, dict[str, Any]] = field(default_factory=dict)
    # address -> {address, name, maker, product, order_number, application, line, medium, room, objects: [id]}
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    # id -> {id, device, number, name, function, dpts: [dpt], flags: {...}, group_addresses: [address]}
    objects: dict[str, dict[str, Any]] = field(default_factory=dict)
    lines: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_knxproject(cls, data: dict[str, Any] | None) -> "Project":
        """Build from the dict xknxproject makes (Home Assistant's ``knx/get_knx_project``)."""
        if not data or not isinstance(data.get("group_addresses"), dict):
            return cls()
        project = cls(loaded=True, info=dict(data.get("info") or {}))

        ranges: dict[str, str] = {}

        def walk_ranges(items: dict[str, Any], path: str) -> None:
            for key, item in (items or {}).items():
                name = f"{path} › {item.get('name') or key}" if path else str(item.get("name") or key)
                for address in item.get("group_addresses") or []:
                    ranges[str(address)] = name
                walk_ranges(item.get("group_ranges") or {}, name)

        walk_ranges(data.get("group_ranges") or {}, "")

        rooms: dict[str, str] = {}

        def walk_spaces(items: dict[str, Any], path: str) -> None:
            for key, item in (items or {}).items():
                name = f"{path} › {item.get('name') or key}" if path else str(item.get("name") or key)
                for address in item.get("devices") or []:
                    rooms[str(address)] = name
                walk_spaces(item.get("spaces") or {}, name)

        walk_spaces(data.get("locations") or {}, "")

        for area_key, area in (data.get("topology") or {}).items():
            for line_key, line in (area.get("lines") or {}).items():
                project.lines[f"{area_key}.{line_key}"] = {
                    "name": line.get("name") or "",
                    "medium": line.get("medium_type") or "",
                    "area": area.get("name") or "",
                }

        for object_id, co in (data.get("communication_objects") or {}).items():
            flags = co.get("flags") or {}
            project.objects[object_id] = {
                "id": object_id,
                "device": str(co.get("device_address") or ""),
                "number": co.get("number"),
                "name": co.get("text") or co.get("name") or "",
                "function": co.get("function_text") or "",
                "dpts": [d for d in (dpt_text(x.get("main"), x.get("sub")) for x in co.get("dpts") or []) if d],
                "flags": {
                    "read": bool(flags.get("read")),
                    "write": bool(flags.get("write")),
                    "communication": bool(flags.get("communication")),
                    "transmit": bool(flags.get("transmit")),
                    "update": bool(flags.get("update")),
                    "read_on_init": bool(flags.get("read_on_init", flags.get("readOnInit"))),
                },
                "group_addresses": [str(a) for a in co.get("group_address_links") or []],
            }

        for address, device in (data.get("devices") or {}).items():
            address = str(address)
            line = line_of(address)
            project.devices[address] = {
                "address": address,
                "name": device.get("name") or device.get("hardware_name") or "",
                "description": device.get("description") or "",
                "maker": device.get("manufacturer_name") or "",
                "product": device.get("hardware_name") or "",
                "order_number": device.get("order_number") or "",
                "application": device.get("application") or "",
                "line": line,
                "medium": (project.lines.get(line) or {}).get("medium", ""),
                "room": rooms.get(address, ""),
                "objects": [o for o in device.get("communication_object_ids") or [] if o in project.objects],
                # What ETS has not downloaded since the project changed, by ETS's own letters
                "pending": [letter for key, letter in PENDING_PARTS if device.get(key) is False],
            }

        for address, ga in data["group_addresses"].items():
            address = str(ga.get("address") or address)
            dpt = ga.get("dpt") or None
            item = {
                "address": address,
                "name": ga.get("name") or "",
                "description": ga.get("description") or ga.get("comment") or "",
                "dpt": dpt_text(dpt.get("main"), dpt.get("sub")) if dpt else None,
                "range": ranges.get(address, ""),
                "secure": bool(ga.get("data_secure")),
                "objects": [],
                "devices": [],
                "senders": 0,
                "listeners": 0,
                "answerers": 0,
                "object_dpts": [],
            }
            project.group_addresses[address] = item

        # Which objects are linked to an address: from the objects themselves, as the address's own list of
        # object ids is not in every project file
        for co in project.objects.values():
            if not co["flags"]["communication"]:
                continue
            for index, address in enumerate(co["group_addresses"]):
                ga = project.group_addresses.get(address)
                if ga is None:
                    continue
                ga["objects"].append(co["id"])
                if co["device"] and co["device"] not in ga["devices"]:
                    ga["devices"].append(co["device"])
                # An object sends on its first address only; it listens on all of them. An input that takes
                # values (W and U, a push button's status LED) can carry T without being a sender
                flags = co["flags"]
                if flags["transmit"] and index == 0 and not (flags["write"] and flags["update"]):
                    ga["senders"] += 1
                if co["flags"]["write"] or co["flags"]["update"]:
                    ga["listeners"] += 1
                if co["flags"]["read"] and index == 0:
                    ga["answerers"] += 1
                for dpt in co["dpts"]:
                    if dpt not in ga["object_dpts"]:
                        ga["object_dpts"].append(dpt)
        # An address without a type of its own has the type its objects agree on, as in ETS
        for ga in project.group_addresses.values():
            if not ga["dpt"] and ga["object_dpts"]:
                if len(set(ga["object_dpts"])) == 1:
                    ga["dpt_objects"] = ga["object_dpts"][0]
                elif len({d.split(".")[0] for d in ga["object_dpts"]}) == 1:
                    ga["dpt_objects"] = ga["object_dpts"][0].split(".")[0]
        return project

    def dpt_of(self, address: str) -> str | None:
        """The type a value on this address is read as: its own, or the one its objects agree on."""
        ga = self.group_addresses.get(address)
        return (ga["dpt"] or ga.get("dpt_objects")) if ga else None

    def name_of(self, address: str) -> str:
        """The name of a group address or of a device."""
        if "/" in address:
            return (self.group_addresses.get(address) or {}).get("name", "")
        device = self.devices.get(address)
        return f"{device['maker']} {device['name']}".strip() if device else ""


# ----------------------------------------------------------------------
# Home Assistant's KNX configuration
# ----------------------------------------------------------------------
# Which datapoint type an entity needs on an address, from what the address is for. Only the main type:
# a wrong sub type still reads a value. First match wins.
ROLE_DPT: tuple[tuple[str, int], ...] = (
    (r"rgbw", 251),
    (r"xyy", 242),
    (r"color_temp", 0),  # 5, 7 or 9, depending on the mode: not checked
    (r"(^|_)(color|rgb)", 232),
    (r"temperature|humidity", 9),
    (r"operation_mode|controller_mode", 20),
    (r"brightness|position|angle|hue|saturation|command_value|valve|percent", 5),
    (r"setpoint_shift|fan_speed|speed|step$|controller_status|active|scene|wind|rain|illuminance|pressure", 0),
)
PLATFORM_DPT = {"switch": 1, "binary_sensor": 1, "light": 1, "cover": 1, "date": 11, "time": 10, "datetime": 19}


def expected_dpt(platform: str, role: str, explicit: str | None) -> str | None:
    """The datapoint type an entity expects on an address: what its configuration says (a ``dpt`` or a
    sensor ``type``), or what follows from the address's role. None when it cannot be told."""
    if explicit:
        transcoder = transcoder_for(explicit)
        if transcoder is not None and transcoder.dpt_main_number is not None:
            return dpt_text(transcoder.dpt_main_number, transcoder.dpt_sub_number)
        return None
    role = role.lower()
    for pattern, main in ROLE_DPT:
        if re.search(pattern, role):
            return str(main) if main else None
    if platform == "climate":
        return "1" if re.search(r"on_off|heat_cool|frost|night|comfort|standby|swing", role) else None
    main = PLATFORM_DPT.get(platform)
    return str(main) if main else None


def _ui_addresses(knx: Any, path: str = "") -> list[dict[str, Any]]:
    """The group addresses in the ``knx`` part of an entity made in the UI: every {write, state, passive}."""
    found: list[dict[str, Any]] = []
    if isinstance(knx, dict):
        if any(key in knx for key in ("write", "state", "passive")) and not isinstance(knx.get("state"), dict):
            dpt = knx.get("dpt") if isinstance(knx.get("dpt"), str) else None
            for use in ("write", "state"):
                if is_ga(knx.get(use)):
                    found.append({"ga": str(knx[use]), "role": path, "use": use, "dpt": dpt})
            for address in knx.get("passive") or []:
                if is_ga(address):
                    found.append({"ga": str(address), "role": path, "use": "passive", "dpt": dpt})
            return found
        for key, value in knx.items():
            found += _ui_addresses(value, key if key.startswith("ga_") or not path else path)
    elif isinstance(knx, list):
        for value in knx:
            found += _ui_addresses(value, path)
    return found


# YAML keys that Home Assistant only listens on although their name does not say "state": the measured
# temperature of a climate entity (the keys of a weather entity, address_..., are all listened on too)
YAML_READ_KEYS = {"temperature_address"}


def _yaml_addresses(item: dict[str, Any]) -> list[dict[str, Any]]:
    """The group addresses of an entity written in YAML: every key that ends in ``address``. The first of
    a list is the one written to (or asked for a state); the others are only listened to."""
    found: list[dict[str, Any]] = []
    explicit = item.get("type") if isinstance(item.get("type"), str) else None
    for key, value in item.items():
        if not isinstance(key, str) or key == "individual_address":
            continue
        if not (key.endswith("address") or key.startswith("address_")):
            continue
        values = value if isinstance(value, list) else [value]
        for index, address in enumerate(values):
            if not is_ga(address):
                continue
            listens = "state" in key or key in YAML_READ_KEYS or key.startswith("address_")
            use = "passive" if index else "state" if listens else "write"
            found.append({"ga": str(address), "role": key, "use": use, "dpt": explicit})
    return found


def build_entities(
    registry: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
    by_group: dict[str, list[dict[str, Any]]],
    config_store: dict[str, Any] | None,
    yaml_config: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Home Assistant's KNX entities with their group addresses, and the stray ones.

    ``registry`` are the entity registry entries of the KNX integration, ``by_group`` the integration's own
    list of which entity uses which address, and the two
    configurations come from the integration's diagnostics.

    An entity is stray when the registry still has it but the integration no longer provides it: Home
    Assistant shows it as unavailable ("restored") for ever.
    """
    by_uid = {(e.get("entity_id", "").split(".")[0], str(e.get("unique_id"))): e for e in registry}
    entities: dict[tuple[str, str], dict[str, Any]] = {}

    def entity(platform: str, unique_id: str, source: str, name: str = "") -> dict[str, Any]:
        key = (platform, unique_id)
        if key not in entities:
            entry = by_uid.get(key) or {}
            entity_id = entry.get("entity_id") or ""
            state = states.get(entity_id) or {}
            entities[key] = {
                "entity_id": entity_id,
                "unique_id": unique_id,
                "platform": platform,
                "name": entry.get("name") or entry.get("original_name") or name or entity_id,
                "source": source,
                "disabled": bool(entry.get("disabled_by")),
                "state": state.get("state"),
                "device_id": entry.get("device_id"),
                "addresses": [],
            }
        return entities[key]

    def add(target: dict[str, Any], address: dict[str, Any]) -> None:
        if not any(a["ga"] == address["ga"] and a["role"] == address["role"] for a in target["addresses"]):
            target["addresses"].append(address)

    # Entities made in the UI: their unique id is the key they are stored under
    ui_store = (config_store or {}).get("entities") or {}
    for platform, items in ui_store.items():
        for unique_id, config in (items or {}).items():
            target = entity(platform, str(unique_id), "ui", str(((config or {}).get("entity") or {}).get("name") or ""))
            for address in _ui_addresses((config or {}).get("knx")):
                add(target, address)

    # Entities written in YAML have no id in the file: they are matched to what the integration reports
    # per address
    yaml_items: dict[str, list[dict[str, Any]]] = {}
    for platform, items in (yaml_config or {}).items():
        if isinstance(items, dict):
            items = [items]
        if isinstance(items, list):
            yaml_items[platform] = [i for i in items if isinstance(i, dict) and _yaml_addresses(i)]

    known_uids: set[tuple[str, str]] = set(entities)
    for ga, users in by_group.items():
        for user in users or []:
            platform, unique_id = str(user.get("platform")), str(user.get("unique_id"))
            known_uids.add((platform, unique_id))
            target = entity(platform, unique_id, "ui" if user.get("ui") else "yaml")
            if user.get("disabled"):
                target["disabled"] = True
            if target["source"] == "ui" and any(a["ga"] == ga for a in target["addresses"]):
                continue
            role = {"ga": str(ga), "role": "", "use": "", "dpt": None}
            for item in yaml_items.get(platform, []):
                addresses = _yaml_addresses(item)
                match = next((a for a in addresses if a["ga"] == str(ga)), None)
                # The right YAML entity is the one whose addresses are all reported for this entity
                if match and all(
                    any(u.get("unique_id") == user.get("unique_id") for u in by_group.get(a["ga"], []))
                    for a in addresses
                ):
                    role = match
                    if not target["name"] or target["name"] == target["entity_id"]:
                        target["name"] = str(item.get("name") or target["name"])
                    break
            add(target, role)

    stray = []
    for (platform, unique_id), entry in by_uid.items():
        state = states.get(entry.get("entity_id", "")) or {}
        restored = bool((state.get("attributes") or {}).get("restored"))
        # What Home Assistant itself marks as restored, or an entry the integration does not know
        unknown = (platform, unique_id) not in known_uids
        if (restored or unknown) and not entry.get("disabled_by"):
            stray.append(
                {
                    "entity_id": entry.get("entity_id"),
                    "unique_id": unique_id,
                    "platform": platform,
                    "name": entry.get("name") or entry.get("original_name") or entry.get("entity_id"),
                    "state": state.get("state"),
                    "restored": restored,
                }
            )
            entities.pop((platform, unique_id), None)

    result = sorted(entities.values(), key=lambda e: (e["platform"], e["entity_id"] or e["name"]))
    for item in result:
        for address in item["addresses"]:
            address["expects"] = expected_dpt(item["platform"], address["role"], address.pop("dpt", None))
    return result, sorted(stray, key=lambda e: str(e["entity_id"]))


@dataclass
class HAConfig:
    """Home Assistant's side: is the KNX integration there, and what does it have configured."""

    present: bool = False  # the KNX integration is set up
    loaded: bool = False  # and running
    error: str = ""
    entry_id: str = ""
    connection: dict[str, Any] = field(default_factory=dict)  # connected, current_address, version
    project_info: dict[str, Any] | None = None
    entities: list[dict[str, Any]] = field(default_factory=list)
    stray_entities: list[dict[str, Any]] = field(default_factory=list)
    stray_devices: list[dict[str, Any]] = field(default_factory=list)
    # entity that is sent to the bus -> the group addresses it is sent to
    exposes: dict[str, list[str]] = field(default_factory=dict)
    yaml_error: str = ""

    @property
    def address(self) -> str:
        return str(self.connection.get("current_address") or "")

    def users(self) -> dict[str, list[dict[str, Any]]]:
        """Group address -> what in Home Assistant uses it."""
        users: dict[str, list[dict[str, Any]]] = {}
        for entity in self.entities:
            for address in entity["addresses"]:
                users.setdefault(address["ga"], []).append(
                    {
                        "entity_id": entity["entity_id"],
                        "name": entity["name"],
                        "platform": entity["platform"],
                        "role": address["role"],
                        "use": address["use"],
                        "expects": address.get("expects"),
                        "disabled": entity["disabled"],
                    }
                )
        for entity_id, addresses in self.exposes.items():
            for ga in addresses:
                users.setdefault(str(ga), []).append(
                    {"entity_id": entity_id, "name": entity_id, "platform": "expose", "role": "expose", "use": "write"}
                )
        return users


def main_differs(a: str | None, b: str | None) -> bool:
    """Two datapoint types that cannot carry the same value: different main types."""
    return bool(a and b) and dpt_main(a) != dpt_main(b)
