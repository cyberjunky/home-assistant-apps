"""The internet router: what it tells the home network about its internet connection.

TR-069 can't be read from here: with TR-069 the router reports to its provider's server (the ACS)
and only listens for "call your ACS". Its LAN-side sibling TR-064 (a FRITZ!Box and many others) and
UPnP's Internet Gateway Device (IGD) can be read by anyone on the network: the connection's state
and uptime, the external address, the link speed and the bytes in and out, without a password.

The router is found among the trackers the map calls a router, firewall or modem (or at the address
set in the options): its IGD description on port 49000 (FRITZ!Box), else the one it announces when
asked over SSDP. Then it is read every few seconds; a minute's average and peak are kept for the
charts, and the bytes per hour for the totals.
"""

import asyncio
import contextlib
import logging
import socket
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from xml.sax.saxutils import escape as xml_escape

import aiohttp

logger = logging.getLogger(__name__)

POLL = 10
# The connection's state, uptime and addresses change slowly
STATUS_EVERY = 30
ADDRESS_EVERY = 300
# Not found: look again this often, and when the trackers change
DISCOVER_EVERY = 600
# Read errors in a row before the router counts as not answering
MAX_FAILURES = 3
# The 10-second samples kept in memory, for the live chart
RECENT = 90
DESC_PORT = 49000
DESC_PATHS = ("/igddesc.xml", "/rootDesc.xml", "/tr64desc.xml")
SSDP_PORT = 1900
SSDP_WAIT = 2.0
SSDP_TARGETS = (
    "urn:schemas-upnp-org:device:InternetGatewayDevice:2",
    "urn:schemas-upnp-org:device:InternetGatewayDevice:1",
    "upnp:rootdevice",
)
ROUTER_KINDS = ("router", "firewall", "modem")
COMMON = "WANCommonInterfaceConfig"
CONNECTIONS = ("WANIPConnection", "WANPPPConnection")
UINT32 = 2**32

ENVELOPE = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    '<s:Body><u:{action} xmlns:u="{service}">{args}</u:{action}></s:Body></s:Envelope>'
)
# Port mappings read at most (a router lists them by index until error 713)
MAX_MAPPINGS = 128


class Unsupported(Exception):
    """The router doesn't offer this action, or not without a password."""


@dataclass
class Service:
    service_type: str
    control_url: str


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def child_text(el: ET.Element, name: str) -> str:
    for c in el:
        if local(c.tag) == name:
            return (c.text or "").strip()
    return ""


def parse_description(text: str, location: str) -> tuple[dict, dict[str, Service], str]:
    """A UPnP device description: the root device's names, the WAN services, and the firmware.

    The services are keyed by their short name (WANCommonInterfaceConfig, WANIPConnection, ..).
    """
    root = ET.fromstring(text)
    base = location
    for el in root.iter():
        if local(el.tag) == "URLBase" and (el.text or "").strip():
            base = el.text.strip()
    device = next((el for el in root if local(el.tag) == "device"), None)
    info = {}
    if device is not None:
        info = {
            "name": child_text(device, "friendlyName"),
            "maker": child_text(device, "manufacturer"),
            "model": child_text(device, "modelName") or child_text(device, "modelDescription"),
            "model_number": child_text(device, "modelNumber"),
        }
    firmware = ""
    for el in root.iter():
        if local(el.tag) == "systemVersion":
            firmware = child_text(el, "Display")
    services: dict[str, Service] = {}
    for el in root.iter():
        if local(el.tag) != "service":
            continue
        service_type = child_text(el, "serviceType")
        control = child_text(el, "controlURL")
        # urn:schemas-upnp-org:service:WANIPConnection:1 -> WANIPConnection
        parts = service_type.split(":")
        short = parts[-2] if len(parts) >= 2 else service_type
        if control and short in (COMMON, *CONNECTIONS) and short not in services:
            services[short] = Service(service_type, urljoin(base, control))
    return info, services, firmware


def parse_soap(text: str, action: str) -> dict[str, str]:
    """The out-arguments of a SOAP response, without their namespaces."""
    root = ET.fromstring(text)
    for el in root.iter():
        if local(el.tag) == f"{action}Response":
            return {local(c.tag): (c.text or "").strip() for c in el}
    raise ValueError(f"no {action}Response in the answer")


def as_int(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


def counter_delta(new: int | None, old: int | None, wide: bool) -> int | None:
    """Bytes between two readings of a counter. A 32-bit counter wraps around at 4 GiB; a counter
    that went back otherwise was reset (a new connection, a reboot) and says nothing this time."""
    if new is None or old is None:
        return None
    if new >= old:
        return new - old
    if not wide and old > UINT32 // 2 and new < UINT32 // 2:
        return new + UINT32 - old
    return None


class Router:
    def __init__(self, inventory, store, history, address: str = ""):
        self.inventory = inventory
        self.store = store
        self.history = history
        self.override = address.strip()
        self.session: aiohttp.ClientSession | None = None
        self.address = ""
        self.services: dict[str, Service] = {}
        self.unsupported: set[str] = set()
        self.info: dict = {}
        self.connection: dict = {}
        self.now: dict = {}
        self.error = ""
        # UPnP port mappings: a list, or None when the router doesn't give them
        self.port_mappings: list[dict] | None = None
        self.tried: list[str] = []
        self.checked_at: float | None = None
        self.failures = 0
        # Bumped on every new reading, so the live stream knows
        self.version = 0
        self.recent: deque[tuple[float, float | None, float | None]] = deque(maxlen=RECENT)
        self._last: tuple[float, int | None, int | None] | None = None
        self._wide = False
        self._minute: list[tuple[float, float, float]] = []
        self._minute_start = 0
        self._last_status = 0.0
        self._last_address = 0.0
        self._looked_at: float = 0.0
        self._looked_for: tuple = ()

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------
    async def run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            self.session = session
            while not self.inventory.loaded_at and not self.override:
                await asyncio.sleep(1)
            while True:
                if not self.services:
                    candidates = self.candidates()
                    key = tuple(candidates)
                    if key != self._looked_for or time.time() - self._looked_at >= DISCOVER_EVERY:
                        self._looked_for, self._looked_at = key, time.time()
                        await self.discover(candidates)
                else:
                    await self.poll()
                await asyncio.sleep(POLL)

    def candidates(self) -> list[tuple[str, int | None]]:
        """Where to look: the address in the options, else every tracker that is a router."""
        if self.override:
            host, _, port = self.override.partition(":")
            return [(host, as_int(port))]
        devices = (self.store.doc or {}).get("devices", {})
        found = []
        for host in self.inventory.hosts():
            ip = host.get("ip")
            if not ip or host.get("gps_only"):
                continue
            own = (devices.get(host["key"]) or {}).get("kind")
            kind = own or host.get("guess")
            if kind in ROUTER_KINDS:
                # The kinds set on the map first, then a router before a firewall before a modem
                found.append((0 if own else 1, ROUTER_KINDS.index(kind), ip))
        found.sort()
        seen: list[tuple[str, int | None]] = []
        for _, _, ip in found:
            if (ip, None) not in seen:
                seen.append((ip, None))
        return seen

    # ------------------------------------------------------------------
    # Finding it
    # ------------------------------------------------------------------
    async def discover(self, candidates: list[tuple[str, int | None]]) -> bool:
        self.tried = [f"{h}:{p}" if p else h for h, p in candidates]
        self.checked_at = time.time()
        if not candidates:
            self.error = "No tracker is a router yet: give your router the kind Router on the map, or set its address in the options"
            self.version += 1
            return False
        errors = []
        for host, port in candidates:
            try:
                if await self._try(host, port):
                    self.error = ""
                    logger.info(
                        f"Router: {self.info.get('model') or self.info.get('name') or host} at {host}, "
                        f"reading {', '.join(sorted(self.services))}"
                    )
                    await self.poll(force=True)
                    return True
                errors.append(f"{host}: no internet gateway service")
            except (aiohttp.ClientError, TimeoutError, OSError, ET.ParseError, ValueError) as e:
                errors.append(f"{host}: {e or type(e).__name__}")
        self.error = "No router answers over UPnP or TR-064 (" + "; ".join(errors) + ")"
        logger.info(f"Router: {self.error}")
        self.version += 1
        return False

    async def _try(self, host: str, port: int | None) -> bool:
        locations = [f"http://{host}:{port or DESC_PORT}{path}" for path in DESC_PATHS]
        info: dict = {}
        services: dict[str, Service] = {}
        firmware = ""
        for location in locations:
            found = await self._description(location)
            if found:
                i, s, f = found
                info = info or i
                firmware = firmware or f
                # The IGD's services can be read without a password; TR-064's need one on a FRITZ!Box
                if not location.endswith("tr64desc.xml"):
                    for k, v in s.items():
                        services.setdefault(k, v)
        if not services and port is None:
            for location in await ssdp_locations(host):
                found = await self._description(location)
                if found:
                    i, s, f = found
                    if s:
                        info, firmware = i, f or firmware
                        services.update({k: v for k, v in s.items() if k not in services})
                        break
        if COMMON not in services and not any(c in services for c in CONNECTIONS):
            return False
        self.address = host
        self.services = services
        self.unsupported.clear()
        self.info = {**info, "firmware": firmware or info.get("model_number", "")}
        self.info.pop("model_number", None)
        self.connection = {}
        self._last = None
        self.failures = 0
        self._last_status = self._last_address = 0.0
        return True

    async def _description(self, location: str) -> tuple[dict, dict[str, Service], str] | None:
        try:
            async with self.session.get(location) as r:
                if r.status != 200:
                    return None
                text = await r.text()
        except (aiohttp.ClientError, TimeoutError, OSError):
            return None
        try:
            return parse_description(text, location)
        except ET.ParseError:
            return None

    # ------------------------------------------------------------------
    # Reading it
    # ------------------------------------------------------------------
    async def call(self, short: str, action: str, args: dict | None = None) -> dict[str, str]:
        service = self.services.get(short)
        key = f"{short}#{action}"
        if not service or key in self.unsupported:
            raise Unsupported(key)
        headers = {
            "Content-Type": 'text/xml; charset="utf-8"',
            "SOAPAction": f'"{service.service_type}#{action}"',
        }
        body = ENVELOPE.format(
            action=action,
            service=service.service_type,
            args="".join(f"<{k}>{xml_escape(str(v))}</{k}>" for k, v in (args or {}).items()),
        )
        async with self.session.post(service.control_url, data=body, headers=headers) as r:
            text = await r.text()
            # The end of a list asked by index (GetGenericPortMappingEntry): not "unsupported"
            if r.status == 500 and args and "<errorCode>713</errorCode>" in text:
                raise IndexError(key)
            # An action it doesn't have (a UPnP error), or one that wants a password
            if r.status in (401, 403, 404, 405, 501) or (r.status == 500 and "UPnPError" in text):
                self.unsupported.add(key)
                raise Unsupported(key)
            r.raise_for_status()
        return parse_soap(text, action)

    async def _read_port_mappings(self) -> None:
        """The ports the router opened on request of programs on the network (UPnP: games,
        consoles, torrent clients). Forwards set by hand on the router are not in this list."""
        conn = self.connection_service()
        if not conn:
            return
        mappings = []
        for index in range(MAX_MAPPINGS):
            try:
                m = await self.call(conn, "GetGenericPortMappingEntry", {"NewPortMappingIndex": index})
            except IndexError:
                break
            except Unsupported:
                if index == 0:
                    self.port_mappings = None  # the router doesn't tell (or not without a password)
                    return
                break
            mappings.append(
                {
                    "external_port": as_int(m.get("NewExternalPort")),
                    "protocol": (m.get("NewProtocol") or "").upper(),
                    "internal_ip": m.get("NewInternalClient") or "",
                    "internal_port": as_int(m.get("NewInternalPort")),
                    "description": m.get("NewPortMappingDescription") or "",
                    "enabled": (m.get("NewEnabled") or "1") not in ("0", "false"),
                    "remote_host": m.get("NewRemoteHost") or "",
                    "lease": as_int(m.get("NewLeaseDuration")),
                }
            )
        self.port_mappings = mappings

    async def optional(self, short: str, action: str) -> dict[str, str] | None:
        with contextlib.suppress(Unsupported):
            return await self.call(short, action)
        return None

    def connection_service(self) -> str | None:
        return next((c for c in CONNECTIONS if c in self.services), None)

    async def poll(self, force: bool = False) -> None:
        now = time.time()
        try:
            await self._read_traffic(now)
            if force or now - self._last_status >= STATUS_EVERY:
                await self._read_status(force or now - self._last_address >= ADDRESS_EVERY)
                self._last_status = now
                if force or now - self._last_address >= ADDRESS_EVERY:
                    self._last_address = now
                    await self._read_port_mappings()
            self.failures = 0
            self.error = ""
        except (aiohttp.ClientError, TimeoutError, OSError, ET.ParseError, ValueError) as e:
            self.failures += 1
            self.error = f"The router at {self.address} doesn't answer: {e or type(e).__name__}"
            if self.failures >= MAX_FAILURES:
                logger.warning(f"Router: {self.error}; looking for it again")
                self.services = {}
                self.now = {}
                self._looked_at = 0.0
        self.version += 1

    async def _read_traffic(self, now: float) -> None:
        down = up = None
        rx = tx = None
        # A FRITZ!Box gives its own rates and 64-bit counters in one call
        addon = await self.optional(COMMON, "GetAddonInfos")
        if addon:
            down = as_int(addon.get("NewByteReceiveRate"))
            up = as_int(addon.get("NewByteSendRate"))
            rx = as_int(addon.get("NewX_AVM_DE_TotalBytesReceived64"))
            tx = as_int(addon.get("NewX_AVM_DE_TotalBytesSent64"))
            self._wide = rx is not None
            if rx is None:
                rx, tx = as_int(addon.get("NewTotalBytesReceived")), as_int(addon.get("NewTotalBytesSent"))
            dns = [addon.get(k) for k in ("NewDNSServer1", "NewDNSServer2") if addon.get(k)]
            if dns:
                self.connection["dns"] = dns
        else:
            got = await self.optional(COMMON, "GetTotalBytesReceived")
            sent = await self.optional(COMMON, "GetTotalBytesSent")
            if got is None and sent is None and COMMON not in self.services:
                return
            rx = as_int((got or {}).get("NewTotalBytesReceived"))
            tx = as_int((sent or {}).get("NewTotalBytesSent"))
        last = self._last
        self._last = (now, rx, tx)
        d_rx = d_tx = None
        if last:
            dt = now - last[0]
            d_rx = counter_delta(rx, last[1], self._wide)
            d_tx = counter_delta(tx, last[2], self._wide)
            if down is None and d_rx is not None and dt > 0:
                down = d_rx / dt
            if up is None and d_tx is not None and dt > 0:
                up = d_tx / dt
        if d_rx or d_tx:
            await asyncio.to_thread(self.history.add_router_bytes, now, d_rx or 0, d_tx or 0)
        if down is None and up is None:
            return
        self.now = {"ts": now, "down": down, "up": up}
        self.recent.append((now, down, up))
        await self._add_to_minute(now, down, up)

    async def _add_to_minute(self, now: float, down: float | None, up: float | None) -> None:
        minute = int(now // 60 * 60)
        if self._minute and minute != self._minute_start:
            rows = self._minute
            downs = [r[1] for r in rows]
            ups = [r[2] for r in rows]
            await asyncio.to_thread(
                self.history.add_router_minute,
                self._minute_start,
                sum(downs) / len(downs),
                sum(ups) / len(ups),
                max(downs),
                max(ups),
            )
            self._minute = []
        self._minute_start = minute
        self._minute.append((now, down or 0.0, up or 0.0))

    async def _read_status(self, addresses: bool) -> None:
        c = self.connection
        conn = self.connection_service()
        if conn:
            status = await self.optional(conn, "GetStatusInfo")
            if status:
                c["status"] = status.get("NewConnectionStatus") or ""
                c["uptime"] = as_int(status.get("NewUptime"))
                c["uptime_at"] = time.time()
                error = status.get("NewLastConnectionError") or ""
                c["last_error"] = "" if error in ("", "ERROR_NONE") else error
            if addresses:
                ext = await self.optional(conn, "GetExternalIPAddress")
                if ext is not None:
                    c["external_ip"] = ext.get("NewExternalIPAddress") or ""
                v6 = await self.optional(conn, "X_AVM_DE_GetExternalIPv6Address")
                if v6 is not None:
                    c["external_ipv6"] = v6.get("NewExternalIPv6Address") or ""
                    prefix = await self.optional(conn, "X_AVM_DE_GetIPv6Prefix")
                    if prefix and prefix.get("NewIPv6Prefix"):
                        c["ipv6_prefix"] = f"{prefix['NewIPv6Prefix']}/{prefix.get('NewPrefixLength', '')}".rstrip("/")
        link = await self.optional(COMMON, "GetCommonLinkProperties")
        if link:
            c["access_type"] = link.get("NewWANAccessType") or ""
            c["physical"] = link.get("NewPhysicalLinkStatus") or ""
            c["max_down"] = as_int(link.get("NewX_AVM-DE_DownstreamCurrentMaxSpeed")) or as_int(
                link.get("NewLayer1DownstreamMaxBitRate")
            )
            c["max_up"] = as_int(link.get("NewX_AVM-DE_UpstreamCurrentMaxSpeed")) or as_int(
                link.get("NewLayer1UpstreamMaxBitRate")
            )

    # ------------------------------------------------------------------
    # For the dashboard
    # ------------------------------------------------------------------
    def state(self) -> dict:
        return {
            "found": bool(self.services),
            "address": self.address if self.services else "",
            "info": self.info if self.services else {},
            "connection": self.connection if self.services else {},
            "now": self.now if self.services else {},
            "error": self.error,
            "tried": self.tried,
            "checked_at": self.checked_at,
            "override": self.override,
            "version": self.version,
            "port_mappings": self.port_mappings if self.services else None,
        }

    def recent_series(self) -> dict:
        rows = list(self.recent)
        return {"x": [r[0] for r in rows], "down": [r[1] for r in rows], "up": [r[2] for r in rows]}


async def ssdp_locations(host: str) -> list[str]:
    """Ask one device over SSDP (unicast) where its description is."""
    loop = asyncio.get_running_loop()
    found: list[str] = []

    class Listener(asyncio.DatagramProtocol):
        def datagram_received(self, data: bytes, addr) -> None:
            for line in data.decode("latin-1").split("\r\n"):
                name, _, value = line.partition(":")
                if name.strip().lower() == "location":
                    url = value.strip()
                    if urlsplit(url).hostname == host and url not in found:
                        found.append(url)

    try:
        transport, _ = await loop.create_datagram_endpoint(Listener, family=socket.AF_INET, local_addr=("0.0.0.0", 0))
    except OSError:
        return []
    try:
        for target in SSDP_TARGETS:
            message = (
                "M-SEARCH * HTTP/1.1\r\n"
                f"HOST: {host}:{SSDP_PORT}\r\n"
                'MAN: "ssdp:discover"\r\n'
                "MX: 1\r\n"
                f"ST: {target}\r\n\r\n"
            )
            transport.sendto(message.encode(), (host, SSDP_PORT))
        await asyncio.sleep(SSDP_WAIT)
    finally:
        transport.close()
    return found
