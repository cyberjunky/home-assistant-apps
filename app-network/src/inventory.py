"""Every device tracker in Home Assistant, with its entity, device and area, as one list of hosts.

A host is one device on the network. Trackers of different integrations that see the same MAC
address (UniFi, Nmap and the router for the same phone) are one host with several trackers; a
tracker without a MAC address is a host of its own.
"""

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .ha_client import HAClientError, HAWebSocket
from .kinds import guess_kind, is_random_mac, normalize_mac

logger = logging.getLogger(__name__)

DOMAIN = "device_tracker."
# The states of a tracker that mean it is on the network
UP_STATES = {"home"}
NO_STATES = {"unavailable", "unknown", ""}
# Attributes that integrations use for the same thing
_IP = ("ip", "ip_address", "ipv4", "local_ip")
_MAC = ("mac", "mac_address")
_HOST = ("host_name", "hostname", "host")
_SSID = ("essid", "ssid", "wifi_ssid", "network")
_AP = ("ap_mac", "access_point", "ap")
_SWITCH = ("sw_mac", "switch_mac")
_PORT = ("sw_port", "switch_port", "port")
_SIGNAL = ("signal", "rssi", "signal_strength")
_RADIO = ("radio", "band", "radio_proto", "wifi_band")


def _first(attrs: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = attrs.get(key)
        if value not in (None, "", []):
            return value
    return None


def _ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def is_up(state: str | None) -> bool | None:
    """True on the network, False away, None when the tracker doesn't know."""
    if state in NO_STATES or state is None:
        return None
    return state in UP_STATES


def host_key(mac: str, entity_id: str) -> str:
    return f"mac:{mac}" if mac else f"ent:{entity_id}"


def build_hosts(
    entities: dict[str, dict], devices: dict[str, dict], areas: dict[str, dict], states: dict[str, dict]
) -> list[dict]:
    """The hosts from the registries and the states. Pure, so the tests can feed it."""
    trackers: list[dict] = []
    ids = set(states) | set(entities)
    for entity_id in sorted(i for i in ids if i.startswith(DOMAIN)):
        entry = entities.get(entity_id) or {}
        state = states.get(entity_id) or {}
        attrs = state.get("attributes") or {}
        device = devices.get(entry.get("device_id") or "") or {}
        connections = {kind: value for kind, value in device.get("connections") or []}
        mac = normalize_mac(_first(attrs, _MAC) or connections.get("mac"))
        area_id = entry.get("area_id") or device.get("area_id") or ""
        trackers.append(
            {
                "entity_id": entity_id,
                "name": attrs.get("friendly_name") or entry.get("name") or entry.get("original_name") or entity_id,
                "platform": entry.get("platform") or "",
                "state": state.get("state", "unknown") if state else "unknown",
                "up": is_up(state.get("state")) if state else None,
                "last_changed": _ts(state.get("last_changed")),
                "source_type": attrs.get("source_type") or "",
                # Entities from known_devices.yaml aren't in the registry: Home Assistant can't rename those
                "in_registry": bool(entry),
                "registry_name": entry.get("name"),
                "original_name": entry.get("original_name"),
                "has_entity_name": bool(entry.get("has_entity_name")),
                "disabled": bool(entry.get("disabled_by")),
                "icon": entry.get("icon") or attrs.get("icon") or "",
                "device_id": entry.get("device_id") or "",
                "device_name": device.get("name_by_user") or device.get("name") or "",
                "device_name_by_user": device.get("name_by_user") or "",
                "manufacturer": device.get("manufacturer") or attrs.get("manufacturer") or attrs.get("vendor") or "",
                "model": device.get("model") or "",
                "sw_version": device.get("sw_version") or "",
                "via_device_id": device.get("via_device_id") or "",
                "area": (areas.get(area_id) or {}).get("name", ""),
                "ip": str(_first(attrs, _IP) or ""),
                "mac": mac,
                "host_name": str(_first(attrs, _HOST) or ""),
                "ssid": str(_first(attrs, _SSID) or ""),
                "ap_mac": normalize_mac(_first(attrs, _AP)),
                "sw_mac": normalize_mac(_first(attrs, _SWITCH)),
                "sw_port": str(_first(attrs, _PORT) or ""),
                "signal": _first(attrs, _SIGNAL),
                "radio": str(_first(attrs, _RADIO) or ""),
                "wired": attrs.get("is_wired"),
                "latitude": attrs.get("latitude"),
                "longitude": attrs.get("longitude"),
                "gps_accuracy": attrs.get("gps_accuracy"),
                "battery": attrs.get("battery_level"),
                "updated": _ts(state.get("last_updated")),
                "gps": attrs.get("source_type") == "gps",
            }
        )

    by_key: dict[str, list[dict]] = {}
    for tracker in trackers:
        by_key.setdefault(host_key(tracker["mac"], tracker["entity_id"]), []).append(tracker)

    device_hosts: dict[str, str] = {}
    hosts = []
    for key, group in by_key.items():
        # The tracker to name the host after: a network tracker in the registry, the newest change first
        group.sort(key=lambda t: (t["gps"], not t["in_registry"], t["disabled"], t["entity_id"]))
        main = group[0]
        ups = [t["up"] for t in group if t["up"] is not None and not t["disabled"]]

        def pick(field: str, group: list[dict] = group) -> str:
            return next((t[field] for t in group if t[field]), "")

        changes = [t["last_changed"] for t in group if t["last_changed"]]
        host = {
            "key": key,
            "name": main["name"],
            "entity_id": main["entity_id"],
            "trackers": group,
            "up": any(ups) if ups else None,
            "last_changed": max(changes) if changes else None,
            "ip": pick("ip"),
            "mac": main["mac"],
            "random_mac": is_random_mac(main["mac"]),
            "host_name": pick("host_name"),
            "manufacturer": pick("manufacturer"),
            "model": pick("model"),
            "sw_version": pick("sw_version"),
            "area": pick("area"),
            "ssid": pick("ssid"),
            "ap_mac": pick("ap_mac"),
            "sw_mac": pick("sw_mac"),
            "sw_port": pick("sw_port"),
            "signal": next((t["signal"] for t in group if t["signal"] is not None), None),
            "radio": pick("radio"),
            "wired": next((t["wired"] for t in group if t["wired"] is not None), None),
            "platforms": sorted({t["platform"] for t in group if t["platform"]}),
            "gps_only": all(t["gps"] for t in group),
            "disabled": all(t["disabled"] for t in group),
            "via_device_id": pick("via_device_id"),
            "device_ids": sorted({t["device_id"] for t in group if t["device_id"]}),
        }
        host["guess"] = guess_kind(
            *(t["name"] for t in group),
            *(t["original_name"] for t in group),
            host["host_name"],
            host["model"],
            manufacturer=host["manufacturer"],
        )
        for device_id in host["device_ids"]:
            device_hosts[device_id] = key
        hosts.append(host)
    # The host of the device a device is connected through (Home Assistant's via_device)
    for host in hosts:
        host["via"] = device_hosts.get(host.pop("via_device_id"), "") if host.get("via_device_id") else ""
        host.pop("via_device_id", None)
    hosts.sort(key=lambda h: h["name"].lower())
    return hosts


class Inventory:
    """Keeps the registries and the trackers' states, live over the websocket."""

    def __init__(self, ws: HAWebSocket, include_disabled: bool = False):
        self.ws = ws
        self.include_disabled = include_disabled
        self.entities: dict[str, dict] = {}
        self.devices: dict[str, dict] = {}
        self.areas: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        # Home Assistant's zones: home, work... with their position and radius
        self.zones: dict[str, dict] = {}
        self.ha_version = ""
        self.loaded_at: float | None = None
        self.error = ""
        self._hosts: list[dict] | None = None
        self._reload: asyncio.Task | None = None
        # Called with (entity_id, ts, up) for every change of a tracker between on and off the network
        self.on_change: Callable[[str, float, bool | None], None] | None = None
        self.on_loaded: Callable[[], None] | None = None

    async def connected(self) -> None:
        """After every (re)connect: read everything and follow the changes."""
        await self.ws.subscribe_events("state_changed", self._state_changed)
        for event in ("entity_registry_updated", "device_registry_updated", "area_registry_updated"):
            await self.ws.subscribe_events(event, self._registry_changed)
        await self.load()

    async def load(self) -> None:
        try:
            entities, devices, areas, states = await asyncio.gather(
                self.ws.call({"type": "config/entity_registry/list"}),
                self.ws.call({"type": "config/device_registry/list"}),
                self.ws.call({"type": "config/area_registry/list"}),
                self.ws.call({"type": "get_states"}),
            )
        except HAClientError as e:
            self.error = str(e)
            logger.warning(f"Could not read Home Assistant's registries: {e}")
            return
        self.entities = {e["entity_id"]: e for e in entities or [] if e.get("entity_id", "").startswith(DOMAIN)}
        self.devices = {d["id"]: d for d in devices or []}
        self.areas = {a["area_id"]: a for a in areas or []}
        self.states = {s["entity_id"]: s for s in states or [] if s.get("entity_id", "").startswith(DOMAIN)}
        self.zones = {s["entity_id"]: s for s in states or [] if s.get("entity_id", "").startswith("zone.")}
        self._hosts = None
        self.loaded_at = time.time()
        self.error = ""
        logger.info(f"{len(self.states)} device trackers, {len(self.hosts())} hosts")
        if self.on_loaded:
            self.on_loaded()

    def hosts(self) -> list[dict]:
        if self._hosts is None:
            entities = self.entities
            if not self.include_disabled:
                entities = {k: v for k, v in entities.items() if not v.get("disabled_by")}
                states = {k: v for k, v in self.states.items() if not (self.entities.get(k) or {}).get("disabled_by")}
            else:
                states = self.states
            self._hosts = build_hosts(entities, self.devices, self.areas, states)
        return self._hosts

    def zone_list(self) -> list[dict]:
        out = []
        for entity_id, z in sorted(self.zones.items()):
            a = z.get("attributes") or {}
            if isinstance(a.get("latitude"), int | float) and isinstance(a.get("longitude"), int | float):
                out.append(
                    {
                        "entity_id": entity_id,
                        "name": a.get("friendly_name") or entity_id,
                        "state": entity_id.removeprefix("zone."),
                        "lat": a["latitude"],
                        "lon": a["longitude"],
                        "radius": a.get("radius") or 100,
                        "passive": bool(a.get("passive")),
                    }
                )
        return out

    def home(self) -> tuple[float, float] | None:
        a = (self.zones.get("zone.home") or {}).get("attributes") or {}
        if isinstance(a.get("latitude"), int | float):
            return a["latitude"], a["longitude"]
        return None

    def host_of(self) -> dict[str, str]:
        """entity_id -> host key"""
        return {t["entity_id"]: h["key"] for h in self.hosts() for t in h["trackers"]}

    def _state_changed(self, event: dict) -> None:
        data = event.get("data") or {}
        entity_id = data.get("entity_id", "")
        if entity_id.startswith("zone."):
            if data.get("new_state"):
                self.zones[entity_id] = data["new_state"]
            else:
                self.zones.pop(entity_id, None)
            return
        if not entity_id.startswith(DOMAIN):
            return
        new, old = data.get("new_state"), data.get("old_state")
        if new is None:
            self.states.pop(entity_id, None)
        else:
            self.states[entity_id] = new
        self._hosts = None
        before = is_up((old or {}).get("state"))
        after = is_up((new or {}).get("state"))
        if before != after and self.on_change:
            ts = _ts((new or {}).get("last_changed")) or time.time()
            self.on_change(entity_id, ts, after)

    def _registry_changed(self, event: dict) -> None:
        # Several of these come at once (a rename sends one per entity and device): read once
        if self._reload is None or self._reload.done():
            self._reload = asyncio.create_task(self._reload_soon())

    async def _reload_soon(self) -> None:
        await asyncio.sleep(1)
        with contextlib.suppress(HAClientError):
            await self.load()

    # ------------------------------------------------------------------
    # Changes in Home Assistant
    # ------------------------------------------------------------------
    async def rename(self, entity_id: str, name: str, device_too: bool) -> dict:
        """Rename a tracker in Home Assistant, and its device when asked. An empty name gives back
        the integration's own name."""
        entry = self.entities.get(entity_id)
        if entry is None:
            raise KeyError(entity_id)
        name = name.strip()
        await self.ws.call({"type": "config/entity_registry/update", "entity_id": entity_id, "name": name or None})
        device_id = entry.get("device_id")
        renamed_device = False
        if device_too and device_id:
            await self.ws.call(
                {"type": "config/device_registry/update", "device_id": device_id, "name_by_user": name or None}
            )
            renamed_device = True
        await self.load()
        return {"entity_id": entity_id, "name": name, "device": renamed_device}

    async def set_icon(self, entity_id: str, icon: str) -> None:
        if entity_id not in self.entities:
            raise KeyError(entity_id)
        await self.ws.call({"type": "config/entity_registry/update", "entity_id": entity_id, "icon": icon or None})
        await self.load()
