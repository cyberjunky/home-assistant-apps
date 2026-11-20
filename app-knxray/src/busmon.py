"""Bus monitor mode: the line as it really is, frame by frame.

A group monitor shows the telegrams an interface passes on. In bus monitor mode the interface hands over
everything it hears on the wire instead: every frame with its repeat flag, the acknowledgement that follows
it (or the missing one), and whether the interface saw a frame, bit or parity error. That is where a
disturbed line, a telegram nobody acknowledges and a device that is too busy to answer show up.

It is a KNXnet/IP tunnel of its own kind (the bus monitor layer), opened next to the app's other
connection, and it only listens: an interface in this mode cannot send. Not every interface offers it, and
some stop their other tunnels while it is open, so it is started by hand and stops by itself.
"""

import asyncio
import contextlib
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from xknx.knxip import (
    HPAI,
    ConnectionStateRequest,
    ConnectRequest,
    ConnectRequestInformation,
    ConnectResponse,
    DisconnectRequest,
    DisconnectResponse,
    ErrorCode,
    KNXIPFrame,
    TunnellingAck,
    TunnellingRequest,
)
from xknx.knxip.knxip_enum import TunnellingLayer

logger = logging.getLogger(__name__)

L_BUSMON_IND = 0x2B
HEARTBEAT = 55
MAX_MINUTES = 60
ACKS = {0xCC: "ack", 0x0C: "nak", 0xC0: "busy", 0x00: "nak+busy"}
PRIORITIES = ("system", "normal", "urgent", "low")
REFUSALS = {
    ErrorCode.E_TUNNELLING_LAYER: "this interface has no bus monitor mode",
    ErrorCode.E_NO_MORE_CONNECTIONS: "the interface has no tunnel free (bus monitor mode often needs all of them closed)",
    ErrorCode.E_CONNECTION_TYPE: "the interface does not offer tunnelling",
    ErrorCode.E_CONNECTION_OPTION: "this interface has no bus monitor mode",
    ErrorCode.E_NO_TUNNELLING_ADDRESS: "the interface has no tunnel free",
    ErrorCode.E_AUTHORISATION_ERROR: "the interface wants KNX Secure, which is not supported",
}
# The services a device is managed with, by their ten bits; the last six are data in the short ones
SERVICES = {
    0x000: "GroupValueRead",
    0x040: "GroupValueResponse",
    0x080: "GroupValueWrite",
    0x0C0: "IndividualAddressWrite",
    0x100: "IndividualAddressRead",
    0x140: "IndividualAddressResponse",
    0x180: "AdcRead",
    0x1C0: "AdcResponse",
    0x200: "MemoryRead",
    0x240: "MemoryResponse",
    0x280: "MemoryWrite",
    0x300: "DeviceDescriptorRead",
    0x340: "DeviceDescriptorResponse",
    0x380: "Restart",
    0x3D1: "AuthorizeRequest",
    0x3D2: "AuthorizeResponse",
    0x3D5: "PropertyValueRead",
    0x3D6: "PropertyValueResponse",
    0x3D7: "PropertyValueWrite",
    0x3D8: "PropertyDescriptionRead",
    0x3D9: "PropertyDescriptionResponse",
    0x3DC: "IndividualAddressSerialRead",
    0x3DD: "IndividualAddressSerialResponse",
    0x3F1: "SecureService",
}


def checksum(frame: bytes) -> int:
    """The last byte of a frame on the wire: every byte before it, exclusive-ored and inverted."""
    value = 0xFF
    for byte in frame:
        value ^= byte
    return value


def individual(raw: int) -> str:
    return f"{raw >> 12}.{(raw >> 8) & 0x0F}.{raw & 0xFF}"


def group(raw: int) -> str:
    return f"{raw >> 11}/{(raw >> 8) & 0x07}/{raw & 0xFF}"


def parse_frame(frame: bytes) -> dict[str, Any]:
    """One frame as it went over the twisted pair.

    {kind: data|ack|nak|busy|nak+busy|poll|other, raw} and for a data frame: src, dst, group (is dst a group
    address), repeated, priority, hops, service, type (write|read|response for group values), payload (hex),
    bits (a value of up to six bits), checksum_ok.
    """
    out: dict[str, Any] = {"raw": frame.hex(), "kind": "other"}
    if not frame:
        return out
    if len(frame) == 1:
        out["kind"] = ACKS.get(frame[0], "other")
        return out
    control = frame[0]
    if control == 0xF0:
        out["kind"] = "poll"
        return out
    standard = control & 0xD3 == 0x90
    extended = control & 0xD3 == 0x10
    if not (standard or extended) or len(frame) < (8 if standard else 9):
        return out
    try:
        if standard:
            src, dst = int.from_bytes(frame[1:3], "big"), int.from_bytes(frame[3:5], "big")
            is_group, hops, length = bool(frame[5] & 0x80), (frame[5] >> 4) & 0x07, frame[5] & 0x0F
            tpdu = frame[6 : 7 + length]
            expected = 8 + length
        else:
            src, dst = int.from_bytes(frame[2:4], "big"), int.from_bytes(frame[4:6], "big")
            is_group, hops, length = bool(frame[1] & 0x80), (frame[1] >> 4) & 0x07, frame[6]
            tpdu = frame[7 : 8 + length]
            expected = 9 + length
    except IndexError:
        return out
    out.update(
        kind="data",
        src=individual(src),
        dst=group(dst) if is_group else individual(dst),
        group=is_group,
        # The bit is 1 on the first try and 0 on every one after it
        repeated=not control & 0x20,
        priority=PRIORITIES[(control >> 2) & 0x03],
        hops=hops,
        checksum_ok=len(frame) == expected and checksum(frame[:-1]) == frame[-1],
        service="",
        type="",
        payload="",
        bits=None,
    )
    if len(tpdu) < 2:
        if tpdu and tpdu[0] & 0x80:
            # No data: a connection being opened, closed or acknowledged
            out["service"] = {0x80: "Connect", 0x81: "Disconnect"}.get(tpdu[0], "Ack" if tpdu[0] & 0x03 == 2 else "Nak")
        return out
    code = ((tpdu[0] & 0x03) << 8) | tpdu[1]
    name = SERVICES.get(code) or SERVICES.get(code & 0x3C0, f"service {code:03X}")
    out["service"] = name
    if name.startswith("GroupValue") and is_group:
        out["type"] = {"GroupValueRead": "read", "GroupValueResponse": "response", "GroupValueWrite": "write"}[name]
        if out["type"] != "read":
            out["bits"] = len(tpdu) == 2
            out["payload"] = f"{tpdu[1] & 0x3F:02x}" if len(tpdu) == 2 else tpdu[2:].hex()
    return out


def parse_indication(cemi: bytes) -> dict[str, Any] | None:
    """An L_Busmon.ind as the interface sends it: what it noticed about the frame, then the frame itself."""
    if len(cemi) < 2 or cemi[0] != L_BUSMON_IND:
        return None
    info, position, end = {}, 2, 2 + cemi[1]
    if end > len(cemi):
        return None
    while position + 2 <= end:
        kind, length = cemi[position], cemi[position + 1]
        value = cemi[position + 2 : position + 2 + length]
        position += 2 + length
        if kind == 0x03 and value:
            status = value[0]
            info.update(
                frame_error=bool(status & 0x80),
                bit_error=bool(status & 0x40),
                parity_error=bool(status & 0x20),
                lost=bool(status & 0x08),
                sequence=status & 0x07,
            )
        elif kind in (0x04, 0x06) and value:
            # The interface's own clock, in ticks: only good for the time between two frames
            info["ticks"] = int.from_bytes(value, "big")
    return {**info, **parse_frame(cemi[end:])}


class _Tunnel(asyncio.DatagramProtocol):
    def __init__(self, received: Callable[[KNXIPFrame], None], lost: Callable[[str], None]):
        self.received, self.lost = received, lost
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        try:
            frame, _rest = KNXIPFrame.from_knx(data)
        except Exception:
            return
        self.received(frame)

    def error_received(self, exc: Exception) -> None:
        self.lost(str(exc))


class BusMonitor:
    def __init__(self, size: int = 5000):
        self.frames: deque[dict[str, Any]] = deque(maxlen=size)
        self.state = "off"  # off | connecting | on | failed
        self.error = ""
        self.host = ""
        self.since: float | None = None
        self.until: float | None = None
        self.counts = self._zero()
        self.on_change: Callable[[], None] = lambda: None
        # What a group frame carries, decoded by whoever knows the datapoint types
        self.decode: Callable[[dict[str, Any]], None] = lambda frame: None
        self._protocol: _Tunnel | None = None
        self._channel = 0
        self._connected: asyncio.Future | None = None
        self._task: asyncio.Task | None = None
        self._next_id = 1
        self._open: dict[str, Any] | None = None  # the data frame that still waits for its acknowledgement

    @staticmethod
    def _zero() -> dict[str, int]:
        return {
            "frames": 0,
            "data": 0,
            "repeated": 0,
            "acked": 0,
            "nak": 0,
            "busy": 0,
            "unanswered": 0,
            "errors": 0,
            "lost": 0,
            "bad_checksum": 0,
        }

    def status(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "error": self.error,
            "host": self.host,
            "since": self.since,
            "until": self.until,
            "counts": self.counts,
            "last_id": self.frames[-1]["id"] if self.frames else 0,
        }

    def recent(self, after: int = 0, limit: int = 1000) -> list[dict[str, Any]]:
        out = []
        for frame in reversed(self.frames):
            if frame["id"] <= after or len(out) >= limit:
                break
            out.append(frame)
        out.reverse()
        return out

    async def start(self, host: str, port: int = 3671, minutes: int = 10) -> None:
        """Open the tunnel and listen for ``minutes``; raises ValueError with the reason when it cannot."""
        await self.stop()
        if not host or len(host) > 253 or any(c.isspace() or c in "/?#@" for c in host):
            raise ValueError("an interface needs its address")
        minutes = max(1, min(int(minutes), MAX_MINUTES))
        self.state, self.error, self.host = "connecting", "", host
        self.counts, self._open = self._zero(), None
        self.frames.clear()
        self.on_change()
        loop = asyncio.get_running_loop()
        self._connected = loop.create_future()
        try:
            _transport, self._protocol = await loop.create_datagram_endpoint(
                lambda: _Tunnel(self._received, self._lost), remote_addr=(host, port)
            )
            # No address of our own in the request: the interface answers to where the request came from,
            # which also works from inside Home Assistant's container network
            self._send(
                ConnectRequest(
                    control_endpoint=HPAI(),
                    data_endpoint=HPAI(),
                    cri=ConnectRequestInformation(knx_layer=TunnellingLayer.BUSMONITOR_LAYER),
                )
            )
            response: ConnectResponse = await asyncio.wait_for(self._connected, 5)
        except (TimeoutError, OSError) as e:
            await self._fail("the interface does not answer" if isinstance(e, TimeoutError) else str(e))
            raise ValueError(self.error) from None
        if response.status_code is not ErrorCode.E_NO_ERROR:
            await self._fail(REFUSALS.get(response.status_code, f"the interface refused ({response.status_code.name})"))
            raise ValueError(self.error)
        self._channel = response.communication_channel
        self.state, self.since = "on", time.time()
        self.until = self.since + minutes * 60
        self._task = asyncio.create_task(self._keep(), name="busmonitor")
        logger.info("Bus monitor mode on %s for %d minutes", host, minutes)
        self.on_change()

    async def _fail(self, reason: str) -> None:
        self._close()
        self.state, self.error = "failed", reason
        self.on_change()

    def _close(self) -> None:
        if self._protocol is not None and self._protocol.transport is not None:
            self._protocol.transport.close()
        self._protocol = None

    def _send(self, body: Any) -> None:
        if self._protocol is not None and self._protocol.transport is not None:
            self._protocol.transport.sendto(KNXIPFrame.init_from_body(body).to_knx())

    async def _keep(self) -> None:
        """Tell the interface we are still there, and stop when the time is up."""
        try:
            while time.time() < (self.until or 0):
                await asyncio.sleep(min(HEARTBEAT, max(1, (self.until or 0) - time.time())))
                self._send(ConnectionStateRequest(communication_channel_id=self._channel, control_endpoint=HPAI()))
        except asyncio.CancelledError:
            raise
        task, self._task = self._task, None
        await self.stop()
        del task

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self.state == "on":
            self._send(DisconnectRequest(communication_channel_id=self._channel, control_endpoint=HPAI()))
            await asyncio.sleep(0.2)
            logger.info("Bus monitor mode off")
        self._settle()
        self._close()
        if self.state != "failed":
            self.state = "off"
        self.until = None
        self.on_change()

    def _lost(self, reason: str) -> None:
        if self.state in ("on", "connecting"):
            if "refused" in reason.lower():
                reason = "nothing answers at that address and port"
            self._close()
            self.state, self.error = "failed", reason or "the connection was lost"
            if self._connected is not None and not self._connected.done():
                self._connected.set_exception(OSError(self.error))
            self.on_change()

    def _received(self, frame: KNXIPFrame) -> None:
        body = frame.body
        if isinstance(body, ConnectResponse):
            if self._connected is not None and not self._connected.done():
                self._connected.set_result(body)
        elif isinstance(body, TunnellingRequest):
            self._send(
                TunnellingAck(
                    communication_channel_id=body.communication_channel_id, sequence_counter=body.sequence_counter
                )
            )
            self.add(body.raw_cemi)
        elif isinstance(body, DisconnectRequest):
            self._send(DisconnectResponse(communication_channel_id=body.communication_channel_id))
            self._lost("the interface closed the connection")

    def _settle(self) -> None:
        """The data frame that waited for an acknowledgement and got none."""
        if self._open is not None:
            self._open["ack"] = "none"
            self.counts["unanswered"] += 1
            self._open = None

    def add(self, cemi: bytes, now: float | None = None) -> dict[str, Any] | None:
        """Take one indication in; an acknowledgement is noted on the frame it answers, not listed."""
        frame = parse_indication(cemi)
        if frame is None:
            return None
        counts = self.counts
        counts["frames"] += 1
        if frame.get("frame_error") or frame.get("bit_error") or frame.get("parity_error"):
            counts["errors"] += 1
        if frame.get("lost"):
            # The interface says so itself: it could not keep up and dropped a frame before this one
            counts["lost"] += 1
        if frame["kind"] in ("ack", "nak", "busy", "nak+busy"):
            if self._open is not None:
                self._open["ack"] = frame["kind"]
                self._open = None
            counts["acked" if frame["kind"] == "ack" else "busy" if frame["kind"] == "busy" else "nak"] += 1
            return None
        self._settle()
        frame["id"] = self._next_id
        self._next_id += 1
        frame["ts"] = time.time() if now is None else now
        if frame["kind"] == "data":
            counts["data"] += 1
            counts["repeated"] += 1 if frame["repeated"] else 0
            counts["bad_checksum"] += 0 if frame["checksum_ok"] else 1
            frame["ack"] = ""
            self._open = frame
            if frame["type"]:
                self.decode(frame)
        self.frames.append(frame)
        return frame
