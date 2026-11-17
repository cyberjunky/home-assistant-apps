"""Every device tracker in Home Assistant, with its entity, device and area, as one list of hosts.

A host is one device on the network. Trackers of different integrations that see the same MAC
address (UniFi, Nmap and the router for the same phone) are one host with several trackers; a
tracker without a MAC address is a host of its own.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from datetime import datetime
from typing import Any

from .ha_client import HAClientError, HAWebSocket
from .kinds import guess_kind, is_random_mac, mac_vendor, normalize_mac

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


_PLACEHOLDER = re.compile(r"tracked device", re.I)
_BARE = re.compile(r"^(?:[0-9a-f]{2}[:_-]){5}[0-9a-f]{2}$|^\d{1,3}(?:[._-]\d{1,3}){3}$", re.I)


# Integrations that find devices by scanning the network: they see whatever answers on an address
# (a FRITZ!Box answers for its VPN connections too), where a router or controller knows the address
SCANNERS = {"arpscan_tracker", "nmap_tracker", "ping"}


def _best_ip(group: list[dict]) -> str:
    """The address of a device whose trackers disagree: the one chosen by an integration that knows
    all its addresses (MikroTik Network), else one from a router or controller (DHCP, ARP of the
    router), else the first tracker's (by its name, see build_hosts)."""
    return (
        next((t["ip"] for t in group if t["ip_addresses"] and t["ip"]), "")
        or next((t["ip"] for t in group if t["ip"] and t["platform"] not in SCANNERS), "")
        or next((t["ip"] for t in group if t["ip"]), "")
    )


def _ip_order(ip: str) -> tuple:
    return tuple(int(x) if x.isdigit() else 0 for x in ip.split(".")) if "." in ip else (999, ip)


def _bare_name(tracker: dict) -> bool:
    """A name that is only a MAC or IP address: any other name of the same device is better."""
    return bool(_BARE.match(str(tracker.get("name") or "").strip()))


def _port_order(name: str) -> tuple:
    """ether2 before ether10, Ethernet before SFP before Wi-Fi."""
    kind = 2 if name.startswith(("wlan", "wifi", "cap")) else 1 if name.startswith("sfp") else 0
    parts = re.findall(r"\d+|\D+", name)
    return (kind, *[(0, int(x), "") if x.isdigit() else (1, 0, x) for x in parts])


def port_entity(state: dict) -> dict | None:
    """A switch port or Wi-Fi access point that lists the devices plugged into it, by MAC address:
    `connected` with `switch_mac` and `port`, or with `ap_mac` and `interface` (MikroTik Network does
    this, any integration may)."""
    a = state.get("attributes") or {}
    connected = a.get("connected")
    if not isinstance(connected, list):
        return None
    if a.get("switch_mac") and a.get("port"):
        kind, owner, name = "ethernet", a["switch_mac"], a["port"]
    elif a.get("ap_mac") and a.get("interface"):
        kind, owner, name = "wifi", a["ap_mac"], a["interface"]
    else:
        return None
    owner = normalize_mac(owner)
    if not owner:
        return None
    speed = a.get("speed_mbps")
    return {
        "entity_id": state.get("entity_id", ""),
        "owner": owner,
        "port": str(name),
        "kind": kind,
        "up": state.get("state") == "on" if kind == "ethernet" else not a.get("disabled"),
        "disabled": bool(a.get("disabled")),
        "speed": int(speed) * 1_000_000 if isinstance(speed, int | float) else None,
        "comment": str(a.get("comment") or ""),
        "neighbor": str(a.get("neighbor") or ""),
        "ssid": str(a.get("ssid") or ""),
        "seen": int(a["devices"]) if isinstance(a.get("devices"), int) else None,
        "connected": [m for m in (normalize_mac(x) for x in connected) if m],
    }


def battery_value(state: dict | None) -> float | None:
    """A battery sensor's level in %, or None when it has none now."""
    try:
        return float((state or {}).get("state"))
    except (TypeError, ValueError):
        return None


def is_battery_sensor(state: dict) -> bool:
    a = state.get("attributes") or {}
    return (
        state.get("entity_id", "").startswith("sensor.")
        and a.get("device_class") == "battery"
        and a.get("unit_of_measurement") == "%"
    )


# NETGEAR Plus (the ha-netgear-plus integration) has entities per port without port_entity's
# attributes. Its unique ids tell what each is: "<switch>-port_<n>_status-0" (the link, on or off),
# "<switch>-port_<n>_connection_speed-0" (Mbit/s) and "<switch>-switch_ip-0" (the address the
# switch was added by). The switch has no table of the devices on its ports, so neither have we.
NETGEAR_PLUS = "netgear_plus"
_NETGEAR_ID = re.compile(r"^(.+)-(?:port_(\d+)_(status|connection_speed)|(switch_ip))-0$")


def netgear_roles(entities: list[dict]) -> dict[str, tuple[str, str, str]]:
    """The NETGEAR Plus entities that tell about ports: entity_id -> (switch, port, what), where
    what is status, connection_speed or switch_ip (port "" for that one)."""
    roles = {}
    for e in entities:
        m = _NETGEAR_ID.match(str(e.get("unique_id") or "")) if e.get("platform") == NETGEAR_PLUS else None
        if m and not e.get("disabled_by"):
            roles[e["entity_id"]] = (m[1], m[2] or "", m[3] or m[4])
    return roles


def netgear_ports(roles: dict[str, tuple[str, str, str]], states: dict[str, dict]) -> dict[str, list[dict]]:
    """The ports of every NETGEAR Plus switch, by the address the switch was added by. A port is
    in the shape of port_entity, without an owner (the switch's MAC address is not known here) and
    without devices."""
    address: dict[str, str] = {}
    found: dict[tuple[str, str], dict] = {}
    for entity_id, (switch, number, what) in roles.items():
        value = (states.get(entity_id) or {}).get("state")
        if value in (None, "unavailable", "unknown"):
            continue
        if what == "switch_ip":
            address[switch] = str(value)
        elif what == "status":
            found.setdefault((switch, number), {}).update(entity_id=entity_id, up=value == "on")
        else:
            with contextlib.suppress(ValueError):
                found.setdefault((switch, number), {})["speed"] = int(float(value) * 1_000_000) or None
    out: dict[str, list[dict]] = {}
    for (switch, number), port in found.items():
        if "entity_id" not in port or switch not in address:
            continue
        out.setdefault(address[switch], []).append(
            {
                "entity_id": port["entity_id"],
                "owner": "",
                "port": number,
                "kind": "ethernet",
                "up": port["up"],
                "disabled": False,
                "speed": port.get("speed") if port["up"] else None,
                "comment": "",
                "neighbor": "",
                "ssid": "",
                "seen": None,
                "connected": [],
            }
        )
    return out


def build_hosts(
    entities: dict[str, dict],
    devices: dict[str, dict],
    areas: dict[str, dict],
    states: dict[str, dict],
    ports: list[dict] | None = None,
    batteries: dict[str, float | None] | None = None,
) -> list[dict]:
    """The hosts from the registries and the states. Pure, so the tests can feed it.

    `batteries`: the level of a device's battery sensor, by device id, for a tracker without a
    battery_level of its own (the Companion app has it as a sensor of the phone)."""
    batteries = batteries or {}
    trackers: list[dict] = []
    ids = set(states) | set(entities)
    for entity_id in sorted(i for i in ids if i.startswith(DOMAIN)):
        entry = entities.get(entity_id) or {}
        state = states.get(entity_id) or {}
        attrs = state.get("attributes") or {}
        device = devices.get(entry.get("device_id") or "") or {}
        # A router integration's stand-in for a device it tracks (the FRITZ!Box: "FRITZ!" and "FRITZ!Box
        # Tracked device"): not the device's own maker and model
        placeholder = bool(_PLACEHOLDER.search(str(device.get("model") or "")))
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
                "manufacturer": ""
                if placeholder
                else device.get("manufacturer") or attrs.get("manufacturer") or attrs.get("vendor") or "",
                "model": "" if placeholder else device.get("model") or "",
                "sw_version": device.get("sw_version") or "",
                "via_device_id": device.get("via_device_id") or "",
                "area": (areas.get(area_id) or {}).get("name", ""),
                "ip": str(_first(attrs, _IP) or ""),
                # Every address it answers on, when an integration lists them (MikroTik Network)
                "ip_addresses": [str(x) for x in attrs.get("ip_addresses") or [] if x],
                "mac": mac,
                "host_name": str(_first(attrs, _HOST) or ""),
                "ssid": str(_first(attrs, _SSID) or ""),
                "ap_mac": normalize_mac(_first(attrs, _AP)),
                "sw_mac": normalize_mac(_first(attrs, _SWITCH)),
                "sw_port": str(_first(attrs, _PORT) or ""),
                "vlan": str(attrs.get("vlan") or ""),
                "signal": _first(attrs, _SIGNAL),
                "radio": str(_first(attrs, _RADIO) or ""),
                "wired": attrs.get("is_wired"),
                # The FRITZ!Box integration: the FRITZ! device (box or repeater) it is on, by name, and LAN or WLAN
                "connected_to": str(attrs.get("connected_to") or ""),
                "connection_type": str(attrs.get("connection_type") or ""),
                "latitude": attrs.get("latitude"),
                "longitude": attrs.get("longitude"),
                "gps_accuracy": attrs.get("gps_accuracy"),
                "battery": attrs.get("battery_level")
                if isinstance(attrs.get("battery_level"), int | float)
                else batteries.get(entry.get("device_id") or ""),
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
        # The tracker to name the host after: a network tracker in the registry with a real name (not
        # only its MAC or IP address, as a router's tracker of a device without a host name has)
        group.sort(key=lambda t: (t["gps"], not t["in_registry"], t["disabled"], _bare_name(t), t["entity_id"]))
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
            "ip": _best_ip(group),
            "ips": sorted({ip for t in group for ip in [t["ip"], *t["ip_addresses"]] if ip}, key=_ip_order),
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
            "vlan": pick("vlan"),
            "signal": next((t["signal"] for t in group if t["signal"] is not None), None),
            "radio": pick("radio"),
            "wired": next((t["wired"] for t in group if t["wired"] is not None), None),
            "connected_to": pick("connected_to"),
            "connection_type": pick("connection_type"),
            "platforms": sorted({t["platform"] for t in group if t["platform"]}),
            "gps_only": all(t["gps"] for t in group),
            "disabled": all(t["disabled"] for t in group),
            "via_device_id": pick("via_device_id"),
            "device_ids": sorted({t["device_id"] for t in group if t["device_id"]}),
        }
        if host["wired"] is None and host["connection_type"] in ("LAN", "WLAN"):
            host["wired"] = host["connection_type"] == "LAN"
        # The maker from the MAC address, for trackers that don't say (Nmap, Ping, many routers)
        host["vendor"] = "" if host["manufacturer"] else mac_vendor(host["mac"])
        host["guess"] = guess_kind(
            *(t["name"] for t in group),
            *(t["original_name"] for t in group),
            host["host_name"],
            host["model"],
            manufacturer=host["manufacturer"] or host["vendor"],
        )
        for device_id in host["device_ids"]:
            device_hosts[device_id] = key
        hosts.append(host)
    # A device no tracker says the port of: the switch port or access point that lists its MAC
    placed = {mac: p for p in ports or [] for mac in p["connected"]}
    for host in hosts:
        p = placed.get(host["mac"])
        if not p or host["sw_mac"] or host["ap_mac"]:
            continue
        if p["kind"] == "wifi":
            host["ap_mac"] = p["owner"]
            host["ssid"] = host["ssid"] or p["ssid"]
            if host["wired"] is None:
                host["wired"] = False
        else:
            host["sw_mac"], host["sw_port"] = p["owner"], p["port"]
            if host["wired"] is None:
                host["wired"] = True
    # Registered to an access point: on its Wi-Fi, whatever a router behind it says (the FRITZ!Box
    # sees a client of a MikroTik access point through the cable to it, and calls it LAN)
    for host in hosts:
        if host["ap_mac"]:
            host["wired"] = False
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
        # Switch ports and access points that list the devices on them (port_entity)
        self.ports: dict[str, dict] = {}
        # NETGEAR Plus: what its port entities are (netgear_roles) and their states
        self.netgear: dict[str, tuple[str, str, str]] = {}
        self.netgear_states: dict[str, dict] = {}
        self.areas: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        # Battery sensors (device_class battery, in %) by device id: (entity_id, state)
        self.batteries: dict[str, tuple[str, dict]] = {}
        # Home Assistant's zones: home, work... with their position and radius
        self.zones: dict[str, dict] = {}
        self.ha_version = ""
        self.loaded_at: float | None = None
        self.error = ""
        self._hosts: list[dict] | None = None
        # Goes up with every change of a tracker, a zone or the registries; the dashboard's stream follows it
        self.version = 0
        self.changed: deque[tuple[int, str]] = deque(maxlen=500)
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
        self.ports = {p["entity_id"]: p for p in (port_entity(s) for s in states or []) if p}
        self.netgear = netgear_roles(entities or [])
        self.netgear_states = {s["entity_id"]: s for s in states or [] if s.get("entity_id") in self.netgear}
        device_of = {e["entity_id"]: e.get("device_id") for e in entities or [] if e.get("device_id")}
        tracked = {e.get("device_id") for e in self.entities.values() if e.get("device_id")}
        self.batteries = {}
        for s in states or []:
            device_id = device_of.get(s.get("entity_id", ""))
            if device_id in tracked and is_battery_sensor(s) and device_id not in self.batteries:
                self.batteries[device_id] = (s["entity_id"], s)
        self._hosts = None
        self._bump("*")
        self.loaded_at = time.time()
        self.error = ""
        logger.info(f"{len(self.states)} device trackers, {len(self.hosts())} hosts")
        if self.on_loaded:
            self.on_loaded()

    def _bump(self, entity_id: str) -> None:
        self.version += 1
        self.changed.append((self.version, entity_id))

    def changed_since(self, version: int) -> list[str]:
        """The entities changed after `version` ("*" when everything was read again)."""
        return sorted({e for v, e in self.changed if v > version})

    def hosts(self) -> list[dict]:
        if self._hosts is None:
            entities = self.entities
            if not self.include_disabled:
                entities = {k: v for k, v in entities.items() if not v.get("disabled_by")}
                states = {k: v for k, v in self.states.items() if not (self.entities.get(k) or {}).get("disabled_by")}
            else:
                states = self.states
            batteries = {d: battery_value(s) for d, (_, s) in self.batteries.items()}
            self._hosts = build_hosts(entities, self.devices, self.areas, states, list(self.ports.values()), batteries)
        return self._hosts

    def port_list(self) -> dict[str, list[dict]]:
        """Every switch's and access point's ports, by its MAC address, in port order."""
        out: dict[str, list[dict]] = {}
        for p in self.ports.values():
            out.setdefault(p["owner"], []).append(p)
        # A NETGEAR Plus switch is known by its address: its ports go to the device with that address
        netgear = netgear_ports(self.netgear, self.netgear_states)
        if netgear:
            macs = {h["ip"]: h["mac"] for h in self.hosts() if h["ip"] and h["mac"]}
            for ip, ports in netgear.items():
                if ip in macs:
                    out.setdefault(macs[ip], []).extend({**p, "owner": macs[ip]} for p in ports)
        for ports in out.values():
            ports.sort(key=lambda p: _port_order(p["port"]))
        return out

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

    def battery_sensor(self, tracker_id: str) -> str:
        """The battery sensor of a tracker's device, when it has one."""
        device_id = (self.entities.get(tracker_id) or {}).get("device_id") or ""
        return self.batteries[device_id][0] if device_id in self.batteries else ""

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
            self._bump(entity_id)
            return
        if entity_id in self.netgear:
            if data.get("new_state"):
                self.netgear_states[entity_id] = data["new_state"]
            else:
                self.netgear_states.pop(entity_id, None)
            self._bump(entity_id)
            return
        if entity_id.startswith("sensor."):
            for device_id, (sensor_id, _) in self.batteries.items():
                if sensor_id == entity_id:
                    if data.get("new_state"):
                        self.batteries[device_id] = (sensor_id, data["new_state"])
                    self._hosts = None
                    # The trackers of the device show the battery: they changed
                    for tracker_id, e in self.entities.items():
                        if e.get("device_id") == device_id:
                            self._bump(tracker_id)
                    return
        if not entity_id.startswith(DOMAIN):
            port = port_entity(data["new_state"]) if data.get("new_state") else None
            if port or entity_id in self.ports:
                if port and port == self.ports.get(entity_id):
                    return
                if port:
                    self.ports[entity_id] = port
                else:
                    self.ports.pop(entity_id, None)
                self._hosts = None
                self._bump(entity_id)
            return
        new, old = data.get("new_state"), data.get("old_state")
        if new is None:
            self.states.pop(entity_id, None)
        else:
            self.states[entity_id] = new
        self._hosts = None
        self._bump(entity_id)
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
        the integration's own name.

        Home Assistant (2026.11) names an entity of a device "<device name> <its own name>", also
        when the user sets its own name. So with the device: the device gets the name and every
        tracker of it (arp-scan, MikroTik Network and the FRITZ!Box can each have one) an empty own
        name; the device first, as renaming a device pins the own name of some entities. Without
        the device: the own name is what follows the device's name, when the name starts with it.
        """
        entry = self.entities.get(entity_id)
        if entry is None:
            raise KeyError(entity_id)
        name = name.strip()
        device_id = entry.get("device_id") or ""
        device = self.devices.get(device_id) or {}
        device_too = bool(device_too and device_id)
        if device_too:
            await self.ws.call(
                {"type": "config/device_registry/update", "device_id": device_id, "name_by_user": name or None}
            )
            own: str | None = "" if name else None
            for e in [
                entry,
                *(e for e in self.entities.values() if e is not entry and e.get("device_id") == device_id),
            ]:
                await self._set_name(e, own)
        else:
            own = name or None
            device_name = (device.get("name_by_user") or device.get("name") or "") if device_id else ""
            if own and device_name:
                rest = strip_prefix(own, device_name)
                if rest is not None:
                    own = rest
            await self._set_name(entry, own)
        await self.load()
        return {"entity_id": entity_id, "name": name, "device": device_too}

    async def _set_name(self, entry: dict, own: str | None) -> None:
        """An entity's own name. Home Assistant keeps a name from before 2026.11 (compat_name) as the
        whole name until the own name changes: when it is already this one, it changes once in between."""
        update = {"type": "config/entity_registry/update", "entity_id": entry["entity_id"]}
        if entry.get("name") == own:
            await self.ws.call({**update, "name": None if own is not None else ""})
        await self.ws.call({**update, "name": own})

    async def set_icon(self, entity_id: str, icon: str) -> None:
        if entity_id not in self.entities:
            raise KeyError(entity_id)
        await self.ws.call({"type": "config/entity_registry/update", "entity_id": entity_id, "icon": icon or None})
        await self.load()


def strip_prefix(name: str, prefix: str) -> str | None:
    """The part of a name after a prefix (the device's name), as Home Assistant strips it: "" for
    the prefix itself, None when the name doesn't start with it as a word."""
    if not name.casefold().startswith(prefix.casefold()):
        return None
    rest = name[len(prefix) :]
    if rest and rest[0] not in " -:":
        return None
    return rest.lstrip(" -:")


def on_fritz_wifi(host: dict) -> dict:
    """A device on the Wi-Fi of a FRITZ! device (the FRITZ!Box integration says WLAN): the switch
    port that sees it is only the cable towards that FRITZ! device, so the FRITZ!Box's word wins and
    the port is dropped. A copy then; else the host itself. An access point that has it registered
    (a MikroTik's Wi-Fi) still goes first."""
    if host.get("connection_type") != "WLAN" or host.get("ap_mac"):
        return host
    return {**host, "sw_mac": "", "sw_port": "", "wired": False}


def fritz_devices(devices: dict[str, dict]) -> list[dict]:
    """The FRITZ! devices added to the FRITZ!Box integration (a box, repeaters): the devices it
    registers with its own identifier. The devices of what it tracks have none."""
    return [d for d in devices.values() if any(i and i[0] == "fritz" for i in d.get("identifiers") or [])]


def config_host(device: dict) -> str:
    """The host a FRITZ! device was added with, from its configuration URL (http://<host>)."""
    url = str(device.get("configuration_url") or "")
    host = url.split("://", 1)[-1].split("/", 1)[0]
    if host.startswith("["):  # [IPv6]:port
        return host[1:].split("]", 1)[0].lower()
    return host.rsplit(":", 1)[0].lower() if host.count(":") == 1 else host.lower()


def fritz_nodes(
    hosts: list[dict], devices: dict[str, dict], router: dict, addresses: dict[str, str] | None = None
) -> dict[str, str]:
    """The FRITZ! devices (the box, repeaters) that the FRITZ!Box integration names in a tracker's
    connected_to, by that name (lower case), as the key of their host on the map.

    connected_to is the name the box or repeater has in the FRITZ!Box's home network (its mesh),
    which the user can change and Home Assistant doesn't show anywhere else. So, per name:
    1. a host by that name (a tracker, host name or device name: arp-scan of a repeater);
    2. a device of that name in Home Assistant (a FRITZ!Box integration entry is named after it);
    3. a FRITZ! device added by that name as its host (http://fritz.box);
    4. the name's address in DNS (`addresses`, name -> IP; the FRITZ!Box answers for its mesh):
       the host with that address, or the FRITZ! device added by that address;
    5. the name the router gives over UPnP;
    6. what is left: one name left and one FRITZ! device left belong together; the box itself is
       the one its trackers hang from (via_device).
    A FRITZ! device's host on the map: by its MAC addresses, else by the address it was added by.
    """
    addresses = {k.lower(): v for k, v in (addresses or {}).items()}
    names = sorted({h["connected_to"].lower() for h in hosts if h.get("connected_to")})
    by_mac = {h["mac"]: h["key"] for h in hosts if h.get("mac")}

    def by_ip(ip: str) -> str:
        if not ip:
            return ""
        return next((h["key"] for h in hosts if h.get("ip") == ip), "") or next(
            (h["key"] for h in hosts if ip in (h.get("ips") or [])), ""
        )

    def host_of(device: dict) -> str:
        macs = [normalize_mac(v) for kind, v in device.get("connections") or [] if kind == "mac"]
        if key := next((by_mac[m] for m in macs if m in by_mac), ""):
            return key
        where = config_host(device)
        return by_ip(addresses.get(where, where))

    fritz = fritz_devices(devices)
    out: dict[str, str] = {}
    for name in names:
        key = ""
        for h in hosts:
            own = {str(h.get(k) or "").lower() for k in ("name", "host_name")}
            own |= {str(t.get(k) or "").lower() for t in h.get("trackers", []) for k in ("device_name", "host_name")}
            if name in own:
                key = h["key"]
                break
        if not key:
            named = [
                d for d in devices.values() if name in {str(d.get(k) or "").lower() for k in ("name_by_user", "name")}
            ]
            key = next((k for d in named if (k := host_of(d))), "")
        if not key:
            key = next((k for d in fritz if config_host(d) == name and (k := host_of(d))), "")
        if not key and (ip := addresses.get(name, "")):
            key = by_ip(ip) or next(
                (k for d in fritz if addresses.get(config_host(d), config_host(d)) == ip and (k := host_of(d))), ""
            )
        if not key and router.get("found") and name in {str(v).lower() for v in router["info"].values()}:
            key = by_ip(router["address"])
        if key:
            out[name] = key
    # What is left pairs up: one name, one FRITZ! device
    left_names = [n for n in names if n not in out]
    taken = set(out.values())
    left = [k for d in fritz if (k := host_of(d)) and k not in taken]
    if len(left_names) == 1:
        # The box: the device the FRITZ!Box's trackers hang from
        boxes = {
            t["via_device_id"]
            for h in hosts
            for t in h.get("trackers", [])
            if t.get("platform") == "fritz" and t.get("via_device_id")
        }
        box = [k for d in fritz if d.get("id") in boxes and (k := host_of(d)) and k not in taken]
        if len(left) == 1:
            out[left_names[0]] = left[0]
        elif len(box) == 1:
            out[left_names[0]] = box[0]
    return out


# Devices that others hang behind: an access point (by Wi-Fi), a Proxmox host (its VMs and
# containers, virtually), a switch or a router (by cable)
BRIDGES = {
    "ap": "wifi",
    "proxmox": "virtual",
    "switch": "ethernet",
    "unmanaged_switch": "ethernet",
    "router": "ethernet",
    "firewall": "ethernet",
    "modem": "ethernet",
    "powerline": "ethernet",
}


def behind_bridges(hosts: list[dict], ports: dict[str, list[dict]], kind_of) -> dict[str, tuple[str, str]]:
    """Devices a switch port lists together with an access point, switch or router (a FRITZ!Repeater
    on a MikroTik port): they hang behind that one, not on the port. key -> (its key, wifi/ethernet).
    kind_of(host) gives a host's kind (what is set on the map, else the guess)."""
    by_mac = {h["mac"]: h for h in hosts if h.get("mac")}
    out: dict[str, tuple[str, str]] = {}
    for port_list in ports.values():
        for port in port_list:
            on = [by_mac[m] for m in port["connected"] if m in by_mac]
            bridges = [h for h in on if kind_of(h) in BRIDGES]
            if len(bridges) != 1 or len(on) < 2:
                continue
            bridge = bridges[0]
            for h in on:
                if h is not bridge and not h.get("ap_mac"):
                    out[h["key"]] = (bridge["key"], BRIDGES[kind_of(bridge)])
    return out
