"""Home Assistant's MQTT discovery, read from the retained configs on the broker.

<prefix>/<component>/[<node_id>/]<object_id>/config makes one entity, <prefix>/device/<id>/config a
device with several ("cmps"). Configs may use the short keys (stat_t, avty_t, dev, ids, mf…) and a
base topic "~". From them: the devices with their entities, which integration sent them (origin),
whether they are available, and what looks wrong (bad JSON, a unique_id used twice).
"""

import json
import time

from .monitor import DISCOVERY_PREFIX

# The short keys this module reads (Home Assistant's mqtt/abbreviations.py has them all)
ABBREVIATIONS = {
    "avty": "availability",
    "avty_mode": "availability_mode",
    "avty_t": "availability_topic",
    "stat_t": "state_topic",
    "cmd_t": "command_topic",
    "json_attr_t": "json_attributes_topic",
    "pl_avail": "payload_available",
    "pl_not_avail": "payload_not_available",
    "uniq_id": "unique_id",
    "obj_id": "object_id",
    "def_ent_id": "default_entity_id",
    "dev": "device",
    "o": "origin",
    "cmps": "components",
    "p": "platform",
    "dev_cla": "device_class",
    "unit_of_meas": "unit_of_measurement",
    "ent_cat": "entity_category",
    "en": "enabled_by_default",
}
DEVICE_ABBREVIATIONS = {
    "ids": "identifiers",
    "mf": "manufacturer",
    "mdl": "model",
    "mdl_id": "model_id",
    "sw": "sw_version",
    "hw": "hw_version",
    "sa": "suggested_area",
    "cns": "connections",
    "cu": "configuration_url",
    "via_device": "via_device",
}
ORIGIN_ABBREVIATIONS = {"sw": "sw_version", "url": "support_url"}
AVAILABILITY_ABBREVIATIONS = {"t": "topic", "pl_avail": "payload_available", "pl_not_avail": "payload_not_available"}
TOPIC_KEYS = ("state_topic", "command_topic", "availability_topic", "json_attributes_topic")
# A state that hasn't changed for this long, while the dashboard has been watching, is called stale
STALE_AFTER = 24 * 3600


def expand(data: dict, table: dict[str, str]) -> dict:
    return {table.get(k, k): v for k, v in data.items()}


def _base(config: dict) -> dict:
    """Replace "~" at the start or end of a topic with the base topic."""
    base = config.get("~")
    if not isinstance(base, str):
        return config
    out = dict(config)
    for key, value in config.items():
        if isinstance(value, str) and (key.endswith("_topic") or key in ("stat_t", "cmd_t", "avty_t")):
            if value.startswith("~"):
                out[key] = base + value[1:]
            elif value.endswith("~"):
                out[key] = value[:-1] + base
    return out


def normalise(config: dict) -> dict:
    """A config with the long keys and the base topic filled in, also in its availability and device."""
    config = expand(_base(config), ABBREVIATIONS)
    base = config.get("~")
    if isinstance(config.get("device"), dict):
        config["device"] = expand(config["device"], DEVICE_ABBREVIATIONS)
    if isinstance(config.get("origin"), dict):
        config["origin"] = expand(config["origin"], ORIGIN_ABBREVIATIONS)
    if isinstance(config.get("availability"), list):
        items = []
        for a in config["availability"]:
            if isinstance(a, dict):
                a = expand(a, AVAILABILITY_ABBREVIATIONS)
                t = a.get("topic")
                if isinstance(base, str) and isinstance(t, str):
                    a["topic"] = base + t[1:] if t.startswith("~") else t[:-1] + base if t.endswith("~") else t
                items.append(a)
        config["availability"] = items
    return config


def parse_topic(topic: str, prefix: str = DISCOVERY_PREFIX) -> tuple[str, str, str] | None:
    """(component, node_id, object_id) of a discovery topic, or None."""
    parts = topic.split("/")
    if len(parts) not in (4, 5) or parts[0] != prefix or parts[-1] != "config":
        return None
    if len(parts) == 4:
        return parts[1], "", parts[2]
    return parts[1], parts[2], parts[3]


def availability_topics(config: dict) -> list[tuple[str, str, str]]:
    """(topic, available payload, not-available payload) of a normalised config."""
    out = []
    if isinstance(config.get("availability_topic"), str):
        out.append(
            (
                config["availability_topic"],
                str(config.get("payload_available", "online")),
                str(config.get("payload_not_available", "offline")),
            )
        )
    for a in config.get("availability") or []:
        if isinstance(a, dict) and isinstance(a.get("topic"), str):
            out.append(
                (
                    a["topic"],
                    str(a.get("payload_available", config.get("payload_available", "online"))),
                    str(a.get("payload_not_available", config.get("payload_not_available", "offline"))),
                )
            )
    return out


def availability(config: dict, values: dict[str, tuple[str, float]]) -> str:
    """online, offline or unknown, following availability_mode (latest, all, any)."""
    items = availability_topics(config)
    if not items:
        return "unknown"
    states = []
    for topic, on, off in items:
        value = values.get(topic, (None, 0))
        state = "online" if value[0] == on else "offline" if value[0] == off else "unknown"
        states.append((state, value[1]))
    mode = config.get("availability_mode", "latest")
    known = [s for s in states if s[0] != "unknown"]
    if not known:
        return "unknown"
    if mode == "all":
        return "online" if all(s[0] == "online" for s in known) else "offline"
    if mode == "any":
        return "online" if any(s[0] == "online" for s in known) else "offline"
    return max(known, key=lambda s: s[1])[0]


def entities(payloads: dict[str, bytes], prefix: str = DISCOVERY_PREFIX) -> tuple[list[dict], list[dict]]:
    """The entities in the configs, and the configs that can't be read."""
    out: list[dict] = []
    problems: list[dict] = []
    for topic, payload in sorted(payloads.items()):
        where = parse_topic(topic, prefix)
        if where is None or not payload:
            continue
        component, node_id, object_id = where
        try:
            config = json.loads(payload)
        except ValueError as e:
            problems.append({"topic": topic, "problem": f"Not valid JSON: {e}"})
            continue
        if not isinstance(config, dict):
            problems.append({"topic": topic, "problem": "Not a JSON object"})
            continue
        config = normalise(config)
        if component == "device":
            shared = {k: v for k, v in config.items() if k not in ("components",)}
            parts = config.get("components")
            if not isinstance(parts, dict) or not parts:
                problems.append({"topic": topic, "problem": "A device config without components (cmps)"})
                continue
            for key, part in parts.items():
                if not isinstance(part, dict):
                    continue
                part = normalise(part)
                merged = {**shared, **part}
                platform = str(merged.get("platform") or "")
                out.append(_entity(topic, platform, node_id or object_id, str(key), merged))
        else:
            out.append(_entity(topic, component, node_id, object_id, config))
    return out, problems


def _entity(config_topic: str, component: str, node_id: str, object_id: str, config: dict) -> dict:
    device = config.get("device") if isinstance(config.get("device"), dict) else {}
    origin = config.get("origin") if isinstance(config.get("origin"), dict) else {}
    ids = device.get("identifiers")
    if isinstance(ids, str):
        ids = [ids]
    connections = device.get("connections") or []
    device_key = ""
    if isinstance(ids, list) and ids:
        device_key = "id:" + str(ids[0])
    elif isinstance(connections, list) and connections and isinstance(connections[0], list):
        device_key = "cn:" + ":".join(str(x) for x in connections[0])
    return {
        "config_topic": config_topic,
        "component": component,
        "node_id": node_id,
        "object_id": object_id,
        "name": config.get("name") if isinstance(config.get("name"), str) else "",
        "unique_id": str(config.get("unique_id") or ""),
        "device_class": str(config.get("device_class") or ""),
        "unit": str(config.get("unit_of_measurement") or ""),
        "category": str(config.get("entity_category") or ""),
        "topics": {k: config[k] for k in TOPIC_KEYS if isinstance(config.get(k), str)},
        "availability": availability_topics(config),
        "availability_mode": config.get("availability_mode", "latest"),
        "device_key": device_key,
        "device": {
            "name": str(device.get("name") or ""),
            "manufacturer": str(device.get("manufacturer") or ""),
            "model": str(device.get("model") or device.get("model_id") or ""),
            "sw_version": str(device.get("sw_version") or ""),
            "area": str(device.get("suggested_area") or ""),
            "url": str(device.get("configuration_url") or ""),
            "via": str(device.get("via_device") or ""),
        },
        "origin": str(origin.get("name") or ""),
    }


def devices(
    items: list[dict], values: dict[str, tuple[str, float]], watching_since: float, now: float | None = None
) -> list[dict]:
    """The entities grouped by device, with each device's availability and its last state update."""
    now = now or time.time()
    by_key: dict[str, dict] = {}
    seen_ids: dict[str, set[str]] = {}
    for e in items:
        cfg = {
            "availability": [
                {"topic": t, "payload_available": on, "payload_not_available": off} for t, on, off in e["availability"]
            ]
        }
        cfg["availability_mode"] = e["availability_mode"]
        e["available"] = availability(cfg, values)
        state_topic = e["topics"].get("state_topic", "")
        state = values.get(state_topic) if state_topic else None
        e["state"] = state[0] if state else None
        e["state_at"] = state[1] if state else None
        if e["unique_id"]:
            seen_ids.setdefault(e["unique_id"], set()).add(e["config_topic"])
        key = e["device_key"] or "none:" + (e["origin"] or e["node_id"] or "")
        d = by_key.get(key)
        if d is None:
            d = by_key[key] = {
                "key": key,
                "name": e["device"]["name"] if e["device_key"] else "",
                "manufacturer": e["device"]["manufacturer"],
                "model": e["device"]["model"],
                "sw_version": e["device"]["sw_version"],
                "area": e["device"]["area"],
                "url": e["device"]["url"],
                "origin": e["origin"],
                "node_id": e["node_id"],
                "entities": [],
            }
        else:
            # Configs of one device may each say a part of it: take what's missing from this one
            for field, value in (
                ("name", e["device"]["name"] if e["device_key"] else ""),
                ("manufacturer", e["device"]["manufacturer"]),
                ("model", e["device"]["model"]),
                ("sw_version", e["device"]["sw_version"]),
                ("area", e["device"]["area"]),
                ("url", e["device"]["url"]),
                ("origin", e["origin"]),
            ):
                if value and not d[field]:
                    d[field] = value
        d["entities"].append(e)
    # A unique_id in two configs: Home Assistant takes the one it got first and ignores the other
    for e in items:
        e["duplicate"] = len(seen_ids.get(e["unique_id"], ())) > 1
    out = []
    for d in by_key.values():
        states = [e["available"] for e in d["entities"]]
        d["available"] = "offline" if "offline" in states else "online" if "online" in states else "unknown"
        updates = [e["state_at"] for e in d["entities"] if e["state_at"]]
        d["last_update"] = max(updates) if updates else None
        d["has_state"] = any(e["topics"].get("state_topic") for e in d["entities"])
        # Stale: it has states, none changed for a day, and the dashboard has been watching that long
        d["stale"] = bool(
            d["has_state"]
            and now - watching_since > STALE_AFTER
            and (d["last_update"] is None or now - d["last_update"] > STALE_AFTER)
        )
        d["duplicates"] = sum(1 for e in d["entities"] if e["duplicate"])
        if not d["name"]:
            d["name"] = d["origin"] or d["node_id"] or "Without a device"
        out.append(d)
    out.sort(key=lambda d: (d["name"].lower(), d["key"]))
    return out


def watched_topics(items: list[dict]) -> set[str]:
    """The availability topics: their values are kept also when the dashboard doesn't keep payloads."""
    return {t for e in items for t, _, _ in e["availability"]}
