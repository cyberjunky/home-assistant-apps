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


def build_hosts(
    entities: dict[str, dict],
    devices: dict[str, dict],
    areas: dict[str, dict],
    states: dict[str, dict],
    ports: list[dict] | None = None,
) -> list[dict]:
    """The hosts from the registries and the states. Pure, so the tests can feed it."""
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
        self.areas: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
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
            self._hosts = build_hosts(entities, self.devices, self.areas, states, list(self.ports.values()))
        return self._hosts

    def port_list(self) -> dict[str, list[dict]]:
        """Every switch's and access point's ports, by its MAC address, in port order."""
        out: dict[str, list[dict]] = {}
        for p in self.ports.values():
            out.setdefault(p["owner"], []).append(p)
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
        the integration's own name."""
        entry = self.entities.get(entity_id)
        if entry is None:
            raise KeyError(entity_id)
        name = name.strip()
        device_id = entry.get("device_id")
        device_too = bool(device_too and device_id)
        # An entity with has_entity_name is called "<device> <its own name>": when the device gets the
        # name, the entity gives up its own, or the name shows twice. That goes for every tracker of
        # the device (arp-scan, MikroTik Network and the FRITZ!Box can each have one on it)
        targets = [entry]
        if device_too:
            targets += [e for e in self.entities.values() if e is not entry and e.get("device_id") == device_id]
        for e in targets:
            own = None if device_too and e.get("has_entity_name") else name or None
            if e is entry or e.get("name") != own:
                await self.ws.call({"type": "config/entity_registry/update", "entity_id": e["entity_id"], "name": own})
        renamed_device = False
        if device_too:
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


def on_fritz_wifi(host: dict) -> dict:
    """A device on the Wi-Fi of a FRITZ! device (the FRITZ!Box integration says WLAN): the switch
    port that sees it is only the cable towards that FRITZ! device, so the FRITZ!Box's word wins and
    the port is dropped. A copy then; else the host itself. An access point that has it registered
    (a MikroTik's Wi-Fi) still goes first."""
    if host.get("connection_type") != "WLAN" or host.get("ap_mac"):
        return host
    return {**host, "sw_mac": "", "sw_port": "", "wired": False}


def fritz_nodes(hosts: list[dict], devices: dict[str, dict], router: dict) -> dict[str, str]:
    """The FRITZ! devices (the box, repeaters) that the FRITZ!Box integration names in a tracker's
    connected_to, by that name (lower case), as the key of their host.

    By the name of a tracker; else by the device of that name in Home Assistant (the FRITZ!Box
    integration makes one per box and repeater) and its MAC address, tracked by any integration;
    else, for the box itself, the router's tracker.
    """
    names = {h["connected_to"].lower() for h in hosts if h.get("connected_to")}
    by_mac = {h["mac"]: h["key"] for h in hosts if h.get("mac")}
    out: dict[str, str] = {}
    for name in names:
        for h in hosts:
            tracker_names = {str(h.get(k) or "").lower() for k in ("name", "host_name")}
            tracker_names |= {str(t.get("device_name") or "").lower() for t in h.get("trackers", [])}
            if name in tracker_names:
                out[name] = h["key"]
                break
        else:
            for device in devices.values():
                if name in {str(device.get(k) or "").lower() for k in ("name_by_user", "name")}:
                    macs = [normalize_mac(v) for kind, v in device.get("connections") or [] if kind == "mac"]
                    if key := next((by_mac[m] for m in macs if m in by_mac), ""):
                        out[name] = key
                        break
            else:
                if router.get("found") and name in {str(v).lower() for v in router["info"].values()}:
                    out[name] = next((h["key"] for h in hosts if h.get("ip") == router["address"]), "")
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
