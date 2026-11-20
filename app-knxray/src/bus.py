"""The app's own connection to the KNX bus, through an IP interface or router that you choose.

It is what the device checks need: asking one device whether it is there and what it is can only be done
from a connection of one's own; Home Assistant's connection cannot be borrowed for that. It also sees every
group telegram on the bus, so with it the monitor does not depend on Home Assistant.

The connection only reads. Whatever leaves it passes ``allowed()``: reads and the handshake of a
connection to a device, never a write, a restart or a download.
"""

import asyncio
import contextlib
import json
import logging
import os
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from xknx import XKNX
from xknx.cemi import CEMIHandler
from xknx.exceptions import ManagementConnectionRefused, ManagementConnectionTimeout, XKNXException
from xknx.io import ConnectionConfig, ConnectionType, GatewayScanner
from xknx.management.procedures import (
    dmp_interface_object_read_r,
    dmp_interface_object_scan_r,
    dmp_mem_read_r_co,
    nm_individual_address_read,
    nm_individual_address_serial_number_read,
)
from xknx.telegram import GroupAddress, IndividualAddress, Telegram, apci, tpci
from xknx.telegram.address import GroupAddressType

from .config import MULTICAST_PORT

logger = logging.getLogger(__name__)

MODES = ("ha", "routing", "tunneling", "tunneling_tcp")
KNX_PORT = 3671
# What may be sent: asking, and what a connection to a device needs to be opened, kept and closed
READ_ONLY = (
    apci.GroupValueRead,
    apci.DeviceDescriptorRead,
    apci.PropertyValueRead,
    apci.PropertyDescriptionRead,
    apci.MemoryRead,
    apci.IndividualAddressRead,
    apci.IndividualAddressSerialRead,
)
CONTROL = (tpci.TConnect, tpci.TDisconnect, tpci.TAck, tpci.TNak)

# The mask version says what kind of device answered: the medium in the first digit, the system after it
MEDIA = {0: "TP", 1: "PL", 2: "RF", 5: "IP"}
MASKS = {
    0x0010: "BCU 1",
    0x0011: "BCU 1",
    0x0012: "BCU 1",
    0x0013: "BCU 1",
    0x0020: "BCU 2",
    0x0021: "BCU 2",
    0x0025: "BCU 2",
    0x0300: "BIM M112",
    0x0700: "BIM M112",
    0x0701: "BIM M112",
    0x0705: "BIM M112",
    0x07B0: "System B",
    0x0810: "Line coupler",
    0x0911: "Line coupler",
    0x0912: "Line coupler",
    0x091A: "IP router",
    0x17B0: "System B",
    0x27B0: "System B",
    0x57B0: "System B",
    0x2010: "RF, bidirectional",
    0x5705: "System 7",
}
LOAD_STATES = {0: "unloaded", 1: "loaded", 2: "loading", 3: "error", 4: "unloading", 5: "load completing"}
PID_OBJECT_TYPE, PID_LOAD_STATE, PID_FIRMWARE, PID_SERIAL, PID_MAKER, PID_PROGRAM, PID_ORDER, PID_PROGMODE = (
    1,
    5,
    9,
    11,
    12,
    13,
    15,
    54,
)
OT_APPLICATION = 3
PID_TABLE_REFERENCE, PID_DEVICE_CONTROL, PID_TABLE, PID_ERROR, PID_DOWNLOADS, PID_MAX_APDU = 7, 14, 23, 28, 30, 56
OBJECT_TYPES = {
    0: "Device",
    1: "Address table",
    2: "Association table",
    3: "Application",
    4: "Interface program",
    5: "Object association table",
    6: "Router",
    7: "Routing table",
    8: "cEMI server",
    9: "Group object table",
    10: "Polling master",
    11: "KNXnet/IP",
    17: "Security",
    19: "RF medium",
}
# What a line coupler or router says about what it passes (its router object, type 6)
ROUTER_PROPERTIES = {
    52: "main_frames",
    53: "sub_frames",
    54: "main_groups",
    55: "sub_groups",
    59: "coupler_type",
    61: "hop_count",
    63: "medium",
    67: "filter_table",
}
# Two bits say what happens to group telegrams: for main groups 0 to 13, and for 14 and up
ROUTING = {0: "not set", 1: "passes all", 2: "blocks all", 3: "filters"}
# Flags of a group object as a System B device keeps them
OBJECT_FLAGS = {"communication": 0x04, "read": 0x08, "write": 0x10, "transmit": 0x40, "update": 0x80}
# Where the older systems keep their address table, by the first three digits of the mask
FIXED_ADDRESS_TABLE = {"001": 0x0116, "002": 0x0116, "070": 0x4000}
MAX_TABLE_ENTRIES = 1000
PING_TIMEOUT = 2.0


class BusError(Exception):
    """Something asked of the bus could not be done; the text is for the person."""


def _ga_key(address: str) -> tuple[int, ...]:
    return tuple(int(part) for part in address.split("/"))


def allowed(telegram: Telegram) -> bool:
    """Whether a telegram may leave: a read, or a step of a connection's handshake."""
    if telegram.payload is None:
        return isinstance(telegram.tpci, CONTROL)
    return isinstance(telegram.payload, READ_ONLY)


class ReadOnlyCEMIHandler(CEMIHandler):
    """Nothing but reads leaves this connection, whoever asks. It also keeps what xknx drops on the way in:
    the flags of a frame (was it sent again, how many couplers may it still pass, its priority)."""

    flags: dict[int, Any]
    _current: Any = None

    def handle_cemi_frame(self, cemi: Any) -> None:
        self._current = getattr(cemi.data, "flags", None)
        try:
            super().handle_cemi_frame(cemi)
        finally:
            self._current = None

    def telegram_received(self, telegram: Telegram) -> None:
        if self._current is not None and isinstance(telegram.tpci, tpci.TDataGroup):
            if len(self.flags) > 5000:
                self.flags.clear()
            self.flags[id(telegram)] = self._current
        super().telegram_received(telegram)

    async def send_telegram(self, telegram: Telegram) -> None:
        if not allowed(telegram):
            raise BusError(f"this app only reads: {type(telegram.payload).__name__} was not sent")
        await super().send_telegram(telegram)


def mask_text(mask: int) -> str:
    """``07B0 (TP, System B)``."""
    known = ", ".join(part for part in (MEDIA.get(mask >> 12, ""), MASKS.get(mask, "")) if part)
    return f"{mask:04X} ({known})" if known else f"{mask:04X}"


def local_addresses() -> list[str]:
    """The IPv4 addresses of this machine, to search for interfaces from each of its networks."""
    found: list[str] = []
    with contextlib.suppress(OSError):
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            if not info[4][0].startswith("127.") and info[4][0] not in found:
                found.append(info[4][0])
    with contextlib.suppress(OSError), socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.connect(("224.0.23.12", 3671))
        if probe.getsockname()[0] not in found:
            found.insert(0, probe.getsockname()[0])
    return found


class Bus:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.choice: dict[str, Any] = self._load()
        self.xknx: XKNX | None = None
        self.state = "off"  # off | connecting | connected | failed
        self.error = ""
        self.connected_since: float | None = None
        self.interfaces: list[dict[str, Any]] = []
        self.scanned_at: float | None = None
        self.scanning = False
        self.secure_issues = 0
        self.drops = 0  # how often the link was lost since the app started
        # Set by the app: a group telegram arrived, the state changed
        self.on_telegram: Callable[[Telegram, Any], None] = lambda telegram, flags: None
        self.on_change: Callable[[], None] = lambda: None
        self.on_link: Callable[[bool], None] = lambda up: None
        self._lock = asyncio.Lock()  # one conversation with a device at a time
        self._runner: asyncio.Task | None = None

    # ---- the chosen interface ----

    def _load(self) -> dict[str, Any]:
        try:
            choice = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {"mode": "ha"}
        return choice if isinstance(choice, dict) and choice.get("mode") in MODES else {"mode": "ha"}

    def _save(self) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.choice))
        os.replace(tmp, self.path)

    @property
    def connected(self) -> bool:
        return self.state == "connected"

    @property
    def address(self) -> str:
        return str(self.xknx.current_address) if self.xknx is not None and self.connected else ""

    def status(self) -> dict[str, Any]:
        return {
            "mode": self.choice.get("mode", "ha"),
            "choice": self.choice,
            "state": self.state,
            "error": self.error,
            "address": self.address,
            "connected_since": self.connected_since,
            "interfaces": self.interfaces,
            "scanned_at": self.scanned_at,
            "scanning": self.scanning,
            "counters": self.counters(),
        }

    def counters(self) -> dict[str, Any]:
        """What the connection itself counted: frames in and out, the ones that went wrong, lost links."""
        if self.xknx is None:
            return {}
        manager = self.xknx.connection_manager
        return {
            "type": manager.connection_type.value or "",
            "frames_in": manager.cemi_count_incoming,
            "frames_in_error": manager.cemi_count_incoming_error,
            "frames_out": manager.cemi_count_outgoing,
            "frames_out_error": manager.cemi_count_outgoing_error,
            "secure_undecoded": manager.undecoded_data_secure,
            "drops": self.drops,
        }

    async def scan(self, timeout: float = 3.0) -> list[dict[str, Any]]:
        """Search every network of this machine for KNX IP interfaces and routers."""
        if self.scanning:
            raise BusError("a search is already running")
        self.scanning = True
        self.on_change()
        found: dict[tuple[str, int], dict[str, Any]] = {}
        try:
            searches = [
                (ip, port) for port in dict.fromkeys((KNX_PORT, MULTICAST_PORT)) for ip in local_addresses() or [None]
            ]
            for local_ip, multicast_port in searches:
                scanner_xknx = XKNX(multicast_port=multicast_port)
                try:
                    gateways = await GatewayScanner(scanner_xknx, local_ip=local_ip, timeout_in_seconds=timeout).scan()
                except (XKNXException, OSError) as e:
                    logger.warning("Searching for KNX interfaces from %s failed: %s", local_ip, e)
                    continue
                for gateway in gateways:
                    slots = getattr(gateway, "tunnelling_slots", {}) or {}
                    found.setdefault(
                        (gateway.ip_addr, gateway.port),
                        {
                            "name": gateway.name,
                            "host": gateway.ip_addr,
                            "port": gateway.port,
                            "address": str(gateway.individual_address) if gateway.individual_address else "",
                            "local_ip": gateway.local_ip,
                            "routing": bool(gateway.supports_routing),
                            "tunneling": bool(gateway.supports_tunnelling),
                            "tunneling_tcp": bool(gateway.supports_tunnelling_tcp),
                            "routing_secure": bool(gateway.routing_requires_secure),
                            "tunneling_secure": bool(gateway.tunnelling_requires_secure),
                            "multicast": gateway.multicast_address or "",
                            "multicast_port": multicast_port,
                            "serial": gateway.serial_number or "",
                            "mac": gateway.mac_address or "",
                            "slots": len(slots),
                            "slots_free": sum(1 for s in slots.values() if getattr(s, "free", False)),
                        },
                    )
            self.interfaces = sorted(found.values(), key=lambda g: (g["name"], g["host"]))
            self.scanned_at = time.time()
            return self.interfaces
        finally:
            self.scanning = False
            self.on_change()

    async def select(self, choice: dict[str, Any]) -> None:
        """Choose how the app reaches the bus and connect that way. ``mode`` ha uses Home Assistant only."""
        mode = str(choice.get("mode") or "")
        if mode not in MODES:
            raise BusError("unknown connection type")
        clean: dict[str, Any] = {"mode": mode}
        if mode in ("tunneling", "tunneling_tcp"):
            host = str(choice.get("host") or "").strip()
            if not host or len(host) > 253 or any(c.isspace() or c in "/?#@" for c in host):
                raise BusError("an interface needs its address")
            try:
                port = int(choice.get("port") or 3671)
            except (TypeError, ValueError):
                raise BusError("the port must be a number") from None
            if not 1 <= port <= 65535:
                raise BusError("the port must be between 1 and 65535")
            clean.update(host=host, port=port)
        if mode == "routing":
            group = str(choice.get("multicast") or "224.0.23.12").strip()
            try:
                socket.inet_aton(group)
            except OSError:
                raise BusError("the multicast address is not an address") from None
            clean["multicast"] = group
            clean["multicast_port"] = MULTICAST_PORT if choice.get("multicast_port") == MULTICAST_PORT else KNX_PORT
        for key in ("name", "local_ip"):
            if choice.get(key):
                clean[key] = str(choice[key])[:100]
        self.choice = clean
        self._save()
        await self.restart()

    # ---- connecting ----

    def _config(self) -> ConnectionConfig:
        mode, local_ip = self.choice["mode"], self.choice.get("local_ip") or None
        if mode == "routing":
            return ConnectionConfig(
                connection_type=ConnectionType.ROUTING,
                local_ip=local_ip,
                multicast_group=self.choice.get("multicast", "224.0.23.12"),
                multicast_port=self.choice.get("multicast_port", KNX_PORT),
            )
        return ConnectionConfig(
            connection_type=ConnectionType.TUNNELING_TCP if mode == "tunneling_tcp" else ConnectionType.TUNNELING,
            gateway_ip=self.choice["host"],
            gateway_port=self.choice.get("port", 3671),
            local_ip=local_ip,
            # Home Assistant's apps reach the interface through address translation
            route_back=mode == "tunneling",
        )

    async def restart(self) -> None:
        await self.stop()
        if self.choice.get("mode", "ha") != "ha":
            self._runner = asyncio.create_task(self._run(), name="bus")
        self.on_change()

    async def _run(self) -> None:
        """Keep the connection up: xknx reconnects by itself once it has been up; getting up the first time
        is tried again here."""
        delay = 5
        while True:
            self.state, self.error = "connecting", ""
            self.on_change()
            xknx = XKNX(
                connection_config=self._config(),
                address_format=GroupAddressType.LONG,
                connection_state_changed_cb=self._state_changed,
                multicast_group=self.choice.get("multicast", "224.0.23.12"),
                multicast_port=self.choice.get("multicast_port", KNX_PORT),
            )
            handler = ReadOnlyCEMIHandler(xknx)
            handler.flags = {}
            xknx.cemi_handler = handler
            xknx.telegram_queue.register_telegram_received_cb(self._received, match_for_outgoing=True)
            xknx.telegram_queue.register_data_secure_group_key_issue_cb(self._secure_issue)
            try:
                await xknx.start()
            except (XKNXException, OSError, TimeoutError) as e:
                self.state, self.error = "failed", str(e) or type(e).__name__
                logger.warning("Connecting to the bus failed: %s; again in %ss", self.error, delay)
                self.on_change()
                with contextlib.suppress(Exception):
                    await xknx.stop()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)
                continue
            self.xknx = xknx
            logger.info("Connected to the bus (%s) as %s", self.choice["mode"], xknx.current_address)
            return

    def _state_changed(self, state: Any) -> None:
        name = getattr(state, "value", str(state))
        was = self.connected
        if name == "CONNECTED":
            self.state, self.error, self.connected_since = "connected", "", time.time()
        elif self.xknx is not None:
            self.state, self.connected_since = "connecting", None
        if was != self.connected:
            self.drops += 1 if was else 0
            self.on_link(self.connected)
        self.on_change()

    def _received(self, telegram: Telegram) -> None:
        handler = self.xknx.cemi_handler if self.xknx is not None else None
        flags = handler.flags.pop(id(telegram), None) if isinstance(handler, ReadOnlyCEMIHandler) else None
        self.on_telegram(telegram, flags)

    def _secure_issue(self, telegram: Telegram) -> None:
        self.secure_issues += 1

    async def stop(self) -> None:
        if self._runner is not None:
            self._runner.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._runner
            self._runner = None
        xknx, self.xknx = self.xknx, None
        was = self.connected
        self.state, self.error, self.connected_since = "off", "", None
        if xknx is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(xknx.stop(), 8)
        if was:
            self.on_link(False)

    def _need(self) -> XKNX:
        if self.xknx is None or not self.connected:
            raise BusError("no connection to the bus: choose a KNX interface on the Interface page")
        return self.xknx

    # ---- asking the bus ----

    async def read_group(self, address: str) -> None:
        """Send a read to a group address; the answers arrive as telegrams."""
        xknx = self._need()
        await xknx.telegrams.put(Telegram(destination_address=GroupAddress(address), payload=apci.GroupValueRead()))

    async def ping(self, address: str, thorough: bool = True) -> dict[str, Any]:
        """Is there a device at this individual address? It is asked for its mask without a connection first,
        which a device answers at once and an empty address costs two seconds. ``thorough`` then also tries
        to open a connection, for the few older devices that only answer that way."""
        xknx = self._need()
        result: dict[str, Any] = {"address": address, "checked": time.time(), "reachable": False}
        target = IndividualAddress(address)
        try:
            async with xknx.management.connectionless(target) as channel:
                response = await channel.request(apci.DeviceDescriptorRead(descriptor=0), timeout=PING_TIMEOUT)
            result.update(reachable=True, mask=mask_text(response.payload.value))
            return result
        except ManagementConnectionTimeout:
            result["note"] = "no answer"
        except XKNXException as e:
            result["note"] = str(e)
        if not thorough:
            return result
        async with self._lock:
            try:
                async with xknx.management.connection(target) as connection:
                    response = await connection.request(payload=apci.DeviceDescriptorRead(descriptor=0))
                    result.update(reachable=True, mask=mask_text(response.payload.value))
                    result.pop("note", None)
            except ManagementConnectionRefused:
                # A device that refuses a connection is there; one that says nothing is not
                result.update(reachable=True, note="refuses a connection")
            except XKNXException:
                result["note"] = "no answer"
        return result

    async def info(self, address: str) -> dict[str, Any]:
        """What a device says about itself: mask, serial number, maker, order number, firmware, whether its
        programming mode is on and whether its application is loaded. Devices of older systems only give
        their mask."""
        xknx = self._need()
        result: dict[str, Any] = {"address": address, "checked": time.time(), "reachable": False, "details": True}
        async with self._lock:
            try:
                async with xknx.management.connection(IndividualAddress(address)) as connection:
                    response = await connection.request(payload=apci.DeviceDescriptorRead(descriptor=0))
                    result.update(reachable=True, mask=mask_text(response.payload.value))

                    async def prop(index: int, pid: int) -> bytes | None:
                        try:
                            return await dmp_interface_object_read_r(connection, index, pid)
                        except (XKNXException, TimeoutError):
                            return None

                    if (serial := await prop(0, PID_SERIAL)) and len(serial) == 6:
                        result["serial"] = f"{serial[:2].hex()}:{serial[2:].hex()}".upper()
                    if (maker := await prop(0, PID_MAKER)) and len(maker) == 2:
                        result["maker_id"] = int.from_bytes(maker, "big")
                    if order := await prop(0, PID_ORDER):
                        text = order.rstrip(b"\x00")
                        readable = bool(text) and text.isascii() and text.decode("ascii").isprintable()
                        result["order_info"] = text.decode("ascii") if readable else order.hex()
                    if firmware := await prop(0, PID_FIRMWARE):
                        result["firmware"] = f"{firmware[0] >> 4}.{firmware[0] & 0x0F}"
                    if mode := await prop(0, PID_PROGMODE):
                        result["progmode"] = bool(mode[0] & 1)
                    if "serial" not in result and "maker_id" not in result:
                        # No properties at all: an older system, nothing more to ask
                        return result
                    for index in range(1, 12):
                        kind = await prop(index, PID_OBJECT_TYPE)
                        if kind is None:
                            break
                        if int.from_bytes(kind, "big") != OT_APPLICATION:
                            continue
                        if state := await prop(index, PID_LOAD_STATE):
                            result["load_state"] = LOAD_STATES.get(state[0], f"state {state[0]}")
                        if (program := await prop(index, PID_PROGRAM)) and len(program) == 5:
                            result["program"] = (
                                f"{int.from_bytes(program[:2], 'big'):04X} "
                                f"{int.from_bytes(program[2:4], 'big'):04X} v{program[4] >> 4}.{program[4] & 0x0F}"
                            )
                        break
            except ManagementConnectionRefused:
                result.update(reachable=True, note="refuses a connection")
            except XKNXException as e:
                result["note"] = str(e) if result["reachable"] else "no answer"
        return result

    async def deep(self, address: str) -> dict[str, Any]:
        """Everything a device can be asked without changing it: what info() reads, every interface object it
        has, the load state of each of its tables, how often it was programmed, whether it noticed another
        device using its address, and the group addresses it really holds. Slow: dozens of questions."""
        result = await self.info(address)
        if not result["reachable"] or "note" in result:
            return result
        xknx = self._need()
        own = IndividualAddress(address).raw
        async with self._lock:
            try:
                async with xknx.management.connection(IndividualAddress(address)) as connection:
                    apdu = 15

                    async def prop(index: int, pid: int, count: int = 1, start: int = 1) -> bytes | None:
                        try:
                            return await dmp_interface_object_read_r(connection, index, pid, count, start, apdu)
                        except (XKNXException, TimeoutError, ValueError):
                            return None

                    if (limit := await prop(0, PID_MAX_APDU)) and len(limit) == 2:
                        apdu = max(15, min(int.from_bytes(limit, "big"), 220))
                        result["max_apdu"] = int.from_bytes(limit, "big")
                    if control := await prop(0, PID_DEVICE_CONTROL):
                        result["stopped"] = bool(control[0] & 0x01)
                        # The device saw a telegram from somebody else with its own address as the sender
                        result["address_clash"] = bool(control[0] & 0x02)
                    if (downloads := await prop(0, PID_DOWNLOADS)) and len(downloads) == 2:
                        result["downloads"] = int.from_bytes(downloads, "big")
                    try:
                        scanned = await dmp_interface_object_scan_r(connection)
                    except XKNXException:
                        scanned = []
                    objects, table_index = [], None
                    for item in scanned:
                        entry: dict[str, Any] = {
                            "index": item.object_index,
                            "type": OBJECT_TYPES.get(item.object_type, f"Type {item.object_type}"),
                            "properties": len(item.properties),
                        }
                        pids = {p.property_id for p in item.properties}
                        if PID_LOAD_STATE in pids and (state := await prop(item.object_index, PID_LOAD_STATE)):
                            entry["load_state"] = LOAD_STATES.get(state[0], f"state {state[0]}")
                        if PID_ERROR in pids and (error := await prop(item.object_index, PID_ERROR)) and error[0]:
                            entry["error"] = error[0]
                        if item.object_type == 1 and table_index is None:
                            table_index = item.object_index
                        objects.append(entry)
                    if objects:
                        result["objects"] = objects

                    raw: list[int] | None = None
                    # Older systems keep the table at a fixed place, with a one-byte count and the device's
                    # own address first: a BCU 1 or BCU 2 at 0116, a BIM M112 at 4000
                    fixed = FIXED_ADDRESS_TABLE.get((result.get("mask") or "")[:3])
                    old = fixed is not None
                    if table_index is not None:
                        # The table as a property: asking for element 0 gives the number of entries
                        size = await prop(table_index, PID_TABLE, 1, 0)
                        count = int.from_bytes(size, "big") if size else 0
                        if 0 < count <= MAX_TABLE_ENTRIES:
                            data = await prop(table_index, PID_TABLE, count, 1)
                            if data and len(data) == count * 2:
                                raw = [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]
                        where = await prop(table_index, PID_TABLE_REFERENCE) if raw is None else None
                        if where and len(where) in (2, 4) and not old:
                            # Or in memory: two bytes with the number of entries, then the addresses
                            raw = await self._table_from_memory(connection, int.from_bytes(where, "big"), 2, apdu)
                    if raw is None and old:
                        raw = await self._table_from_memory(connection, fixed, 1, apdu)
                    if raw is not None:
                        result["group_addresses"] = sorted(
                            {str(GroupAddress(value)) for value in raw if value and value != own}, key=_ga_key
                        )
                    kinds = {item.object_type: item.object_index for item in reversed(scanned)}
                    if raw and not old and 2 in kinds and 9 in kinds:
                        # Which object is linked to which address, and with which flags: the two other tables
                        held = await self._objects_from_memory(connection, prop, kinds[2], kinds[9], raw, apdu)
                        if held is not None:
                            result["held_objects"] = held
                    if 6 in kinds:
                        router: dict[str, Any] = {}
                        for pid, name in ROUTER_PROPERTIES.items():
                            if (value := await prop(kinds[6], pid)) is not None and value:
                                router[name] = int.from_bytes(value, "big")
                        for side in ("main_groups", "sub_groups"):
                            if side in router:
                                router[side + "_low"] = ROUTING[router[side] & 3]
                                router[side + "_high"] = ROUTING[(router[side] >> 2) & 3]
                        if router:
                            result["router"] = router
                    result["deep"] = True
            except ManagementConnectionRefused:
                result["note"] = "refuses a connection"
            except XKNXException as e:
                result["note"] = str(e)
        return result

    @staticmethod
    async def _objects_from_memory(
        connection: Any, prop: Any, association_index: int, objects_index: int, addresses: list[int], apdu: int
    ) -> list[dict[str, Any]] | None:
        """The group objects of a System B device as the device itself holds them: for each object its flags
        and the group addresses it is linked to. None when the tables cannot be read or make no sense.

        The association table is a count and then pairs of (place in the address table, object number); the
        group object table is the highest object number and then two bytes per object: flags and size."""
        try:
            where = await prop(association_index, PID_TABLE_REFERENCE)
            flags_at = await prop(objects_index, PID_TABLE_REFERENCE)
            if not where or not flags_at:
                return None
            start, flags_start = int.from_bytes(where, "big"), int.from_bytes(flags_at, "big")
            count = int.from_bytes(await dmp_mem_read_r_co(connection, start, 2, apdu), "big")
            highest = int.from_bytes(await dmp_mem_read_r_co(connection, flags_start, 2, apdu), "big")
            if not 0 < count <= 4 * MAX_TABLE_ENTRIES or not 0 < highest <= MAX_TABLE_ENTRIES:
                return None
            # Wide entries (two bytes each) when there are more addresses or objects than a byte can count
            width = 2 if len(addresses) > 255 or highest > 255 else 1
            pairs = await dmp_mem_read_r_co(connection, start + 2, count * width * 2, apdu)
            flags = await dmp_mem_read_r_co(connection, flags_start + 2, highest * 2, apdu)
        except (XKNXException, TimeoutError, ValueError):
            return None
        if len(pairs) != count * width * 2 or len(flags) != highest * 2:
            return None
        links: dict[int, list[str]] = {}
        for i in range(0, len(pairs), width * 2):
            place = int.from_bytes(pairs[i : i + width], "big")
            number = int.from_bytes(pairs[i + width : i + 2 * width], "big")
            if 1 <= place <= len(addresses) and addresses[place - 1]:
                links.setdefault(number, []).append(str(GroupAddress(addresses[place - 1])))
        out = []
        for number in range(1, highest + 1):
            byte = flags[(number - 1) * 2]
            if not byte and not flags[(number - 1) * 2 + 1] and number not in links:
                continue  # an object the application does not use
            out.append(
                {
                    "number": number,
                    "flags": {name: bool(byte & bit) for name, bit in OBJECT_FLAGS.items()},
                    "group_addresses": links.get(number, []),
                }
            )
        return out

    async def read_raw(self, address: str, what: str, a: int, b: int, c: int = 1, d: int = 1) -> dict[str, Any]:
        """One thing read from a device as it is, for whoever knows what to look for: a property (object
        index ``a``, property id ``b``, ``c`` elements from element ``d``) or memory (``b`` bytes from ``a``)."""
        xknx = self._need()
        async with self._lock:
            try:
                async with xknx.management.connection(IndividualAddress(address)) as connection:
                    if what == "property":
                        if not (0 <= a <= 255 and 1 <= b <= 255 and 1 <= c <= 64 and 0 <= d <= 4095):
                            raise BusError("object 0 to 255, property 1 to 255, at most 64 elements")
                        data = await dmp_interface_object_read_r(connection, a, b, c, d)
                    elif what == "memory":
                        if not (0 <= a <= 0xFFFF and 1 <= b <= 256 and a + b <= 0x10000):
                            raise BusError("an address up to FFFF and at most 256 bytes")
                        data = await dmp_mem_read_r_co(connection, a, b)
                    else:
                        raise BusError("property or memory")
            except ManagementConnectionRefused as e:
                raise BusError("the device refuses a connection") from e
            except (XKNXException, ValueError) as e:
                raise BusError(f"no answer, or the device does not give this: {e}") from e
        return {"hex": data.hex(), "length": len(data)}

    async def find_serial(self, serial: str) -> str | None:
        """The individual address of the device with this serial number (asked of the whole bus)."""
        xknx = self._need()
        digits = "".join(ch for ch in serial if ch.isalnum())
        try:
            wanted = bytes.fromhex(digits)
        except ValueError:
            raise BusError("a serial number is twelve hexadecimal digits") from None
        if len(wanted) != 6:
            raise BusError("a serial number is twelve hexadecimal digits")
        async with self._lock:
            try:
                found = await nm_individual_address_serial_number_read(xknx, wanted, timeout=3)
            except XKNXException as e:
                raise BusError(str(e)) from e
        return str(found) if found is not None else None

    @staticmethod
    async def _table_from_memory(connection: Any, where: int, count_bytes: int, apdu: int) -> list[int] | None:
        """An address table read from a device's memory; None when it cannot be read or makes no sense."""
        try:
            head = await dmp_mem_read_r_co(connection, where, count_bytes, apdu)
            count = int.from_bytes(head, "big")
            if not 0 < count <= MAX_TABLE_ENTRIES or where + count_bytes + count * 2 > 0xFFFF:
                return None
            data = await dmp_mem_read_r_co(connection, where + count_bytes, count * 2, apdu)
        except (XKNXException, TimeoutError, ValueError):
            return None
        if len(data) != count * 2:
            return None
        return [int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2)]

    async def programming_mode(self) -> list[str]:
        """The devices whose programming mode is on: they answer a question asked of the whole bus."""
        xknx = self._need()
        async with self._lock:
            try:
                return sorted(str(a) for a in await nm_individual_address_read(xknx, timeout=3))
            except XKNXException as e:
                raise BusError(str(e)) from e
