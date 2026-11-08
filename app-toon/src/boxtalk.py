"""BoxTalk: the message bus inside a Toon (hcb_comm, TCP port 1337 on the Toon itself).

Messages are small XML documents separated by a NUL byte, without login or encryption. Everything
the Toon's own screen shows goes over this bus. A client introduces itself with a discovery message
and can then query state variables, invoke actions and subscribe to devices, which makes the bus
push every change as a <notify> message.

The Toon's XML is simple and fixed in shape, so it is read with regular expressions: no entity
expansion to worry about, and one malformed message can't take down the reader.
"""

import asyncio
import html
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SERVICE_ID = "urn:hcb-hae-com:serviceId:"
SERVICE_TYPE = "urn:hcb-hae-com:service:"
DEVICE_TYPE = "urn:schemas-hcb-hae-com:device:"

# The actions this app may invoke on the bus: reading only. Anything that changes the Toon would have
# to be added here deliberately, so a typo or a bug can never send one by accident. Changing the
# thermostat goes through the few fixed commands in control.py instead.
READ_ONLY_ACTIONS = frozenset(
    {
        "GetBoilerType",
        "GetChSettings",
        "GetDataSet",
        "GetDeviceInformation",
        "GetDhwSettings",
        "GetGateWayInfo",
        "GetHolidays",
        "GetRraData",
        "GetTempOffset",
        "GetVersionInfo",
    }
)

_DEVICE = re.compile(
    r'<device uuid="([^"]+)" type="' + re.escape(DEVICE_TYPE) + r'([^"]*)" intAddr="([^"]*)"[^>]*>(.*?)</device>',
    re.S,
)
_SERVICE = re.compile(r'<service type="([^"]+)"')
_LEAF = re.compile(r"<([A-Za-z_][\w.-]*)>([^<]*)</\1>")
_HEAD = re.compile(r'\s*<(\w+)((?:\s+[\w:]+="[^"]*")*)')
_ATTR = re.compile(r'([\w:]+)="([^"]*)"')


class BoxTalkError(Exception):
    """The bus connection ended or a message could not be sent."""


@dataclass
class Device:
    """A device on the bus, as announced in a discovery message."""

    uuid: str
    type: str
    address: str
    services: list[str] = field(default_factory=list)


def parse_devices(xml: str) -> list[Device]:
    """The devices announced in a discovery message."""
    return [Device(m[1], m[2], m[3], _SERVICE.findall(m[4])) for m in _DEVICE.finditer(xml)]


def leaf_values(xml: str) -> dict[str, str]:
    """All <name>value</name> elements of a message, wherever they are nested."""
    return {m[1]: html.unescape(m[2]).strip() for m in _LEAF.finditer(xml)}


def parse_head(xml: str) -> tuple[str, dict[str, str]]:
    """The root element of a message: its tag and attributes."""
    head = _HEAD.match(xml)
    if not head:
        return "", {}
    return head[1], dict(_ATTR.findall(head[2]))


class BoxTalkClient:
    """One client connection to the bus, over any stream pair with read() and write()."""

    def __init__(
        self,
        reader,
        writer,
        on_notify: Callable[[str, dict[str, str]], None] | None = None,
        on_discovery: Callable[[list[Device]], None] | None = None,
    ):
        self._reader = reader
        self._writer = writer
        self._on_notify = on_notify
        self._on_discovery = on_discovery
        self.client_id = f"app-toon-{os.getpid()}"
        self.devices: dict[str, Device] = {}
        self.last_notify = time.monotonic()
        # The bus has no request ids, so there is one request at a time
        self._lock = asyncio.Lock()
        self._pending: tuple[str, str, asyncio.Future] | None = None
        self._buffer = b""

    def _send(self, xml: str) -> None:
        self._writer.write(xml.encode("utf-8") + b"\0")

    def hello(self) -> None:
        """Introduce this client; the bus answers with the devices it knows."""
        self._send(
            f'<discovery nts="ssdp:connect" uuid="{self.client_id}" type="{DEVICE_TYPE}{self.client_id}" version="v"'
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" eofMarker="NULL"'
            f' sessionKey="{os.getpid()}-{int(time.time())}">\n</discovery>'
        )

    async def read_forever(self) -> None:
        """Read and dispatch messages until the connection ends, which raises BoxTalkError."""
        try:
            while True:
                data = await self._reader.read(65536)
                if not data:
                    raise BoxTalkError("the bus closed the connection")
                self._buffer += data
                *frames, self._buffer = self._buffer.split(b"\0")
                for frame in frames:
                    self._dispatch(frame.decode("utf-8", "replace"))
        finally:
            if self._pending and not self._pending[2].done():
                self._pending[2].set_exception(BoxTalkError("connection lost during a request"))

    def _dispatch(self, xml: str) -> None:
        tag, attrs = parse_head(xml)
        if tag == "notify":
            self.last_notify = time.monotonic()
            if self._on_notify:
                self._on_notify(attrs.get("uuid", ""), leaf_values(xml))
        elif tag == "discovery":
            devices = parse_devices(xml)
            if devices:
                for device in devices:
                    self.devices[device.uuid] = device
                if self._on_discovery:
                    self._on_discovery(devices)
        elif attrs.get("class") == "response" and self._pending:
            kind, expect, future = self._pending
            if tag == kind and (kind == "query" or f"<u:{expect}Response" in xml) and not future.done():
                future.set_result(leaf_values(xml))

    async def _request(self, kind: str, expect: str, xml: str, timeout: float) -> dict[str, str]:
        async with self._lock:
            future = asyncio.get_running_loop().create_future()
            self._pending = (kind, expect, future)
            try:
                self._send(xml)
                return await asyncio.wait_for(future, timeout)
            finally:
                self._pending = None

    async def query(self, uuid: str, service: str, variable: str, timeout: float = 5) -> str | None:
        """The value of a state variable, or None when the device doesn't have it."""
        values = await self._request(
            "query",
            variable,
            f'<query class="invoke" uuid="{self.client_id}" destuuid="{uuid}" serviceid="{SERVICE_ID}{service}">\n'
            f'<u:QueryStateVariable xmlns:u="{SERVICE_TYPE}{service}:1"><varName>{variable}</varName>\n'
            "</u:QueryStateVariable>\n</query>",
            timeout,
        )
        return values.get(variable)

    async def action(
        self, destination: str, service: str, name: str, arguments: dict[str, str] | None = None, timeout: float = 5
    ) -> dict[str, str]:
        """Invoke an action and return the values of its response."""
        if name not in READ_ONLY_ACTIONS:
            raise ValueError(f"action {name} is not allowed")
        body = "".join(f"<{key}>{html.escape(str(value))}</{key}>" for key, value in (arguments or {}).items())
        return await self._request(
            "action",
            name,
            f'<action class="invoke" uuid="{self.client_id}" destuuid="{destination}" '
            f'serviceid="{SERVICE_ID}{service}">\n<u:{name} xmlns:u="{SERVICE_TYPE}{service}:1">{body}</u:{name}>\n</action>',
            timeout,
        )

    def subscribe(self, uuid: str, service: str) -> None:
        """Have the bus push the changes of one service of a device."""
        self._send(
            f'<subscribe uuid="{self.client_id}" destuuid="{uuid}">\n'
            f'<target uuid="{uuid}" serviceid="{SERVICE_ID}{service}"></target>\n</subscribe>'
        )
