"""The syslog receiver: UDP and TCP on one port, RFC 5424 and RFC 3164 (BSD) messages.

What devices actually send varies a lot, so the parser is forgiving:
  <PRI>1 TIMESTAMP HOST APP PROCID MSGID [SD] MSG     RFC 5424 (rsyslog, syslog-ng)
  <PRI>Mmm dd hh:mm:ss HOST TAG[pid]: MSG             RFC 3164 (most routers and servers)
  <PRI>Mmm dd hh:mm:ss TAG: MSG                       the same without a host (Netgear, some APs)
  <PRI>Mmm dd hh:mm:ss HOST topic,topic MSG           MikroTik (the app is the first topic)
  plain text                                          no PRI: severity notice (5)
A message without a host gets the sender's address as host.

TCP carries several messages per connection, either octet-counted ("123 <PRI>...") or one per
line (RFC 6587); both are accepted, even mixed.
"""

import asyncio
import contextlib
import logging
import re
import socket
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from .records import MAX_MESSAGE, LogEntry, clip, parse_time

logger = logging.getLogger(__name__)

MAX_FRAME = 64 * 1024
UDP_BUFFER = 4 * 1024 * 1024
MAX_CONNECTIONS = 256
IDLE_TIMEOUT = 3600  # a TCP sender silent this long is disconnected (it reconnects when it has news)

_PRI = re.compile(r"^<(\d{1,3})>")
_BSD_TIME = re.compile(r"^([A-Z][a-z]{2}) {1,2}(\d{1,2}) (\d{2}):(\d{2}):(\d{2})(?:\.\d+)? ")
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
_TAG = re.compile(r"^([\w./@\-]{1,64})(?:\[([^\]\s]{1,32})\])?:$")
_TOPICS = re.compile(r"^[a-z][a-z0-9\-]*(,[a-z0-9\-]+)+$")
_HOST = re.compile(r"^[\w.:\-]{1,255}$")
_SD_ELEMENT = re.compile(r'\[([^\s\]=]+)((?:\s+[^\s\]=]+="(?:[^"\\]|\\.)*")*)\s*\]')
_SD_PARAM = re.compile(r'([^\s\]=]+)="((?:[^"\\]|\\.)*)"')

Sink = Callable[[LogEntry, str], None]


def _bsd_time(match: re.Match, now: datetime) -> datetime | None:
    """A BSD timestamp (no year, the sender's local time): this year, or last year when that
    would be more than a day in the future (December's lines arriving in January)."""
    month = _MONTHS.index(match.group(1)) + 1 if match.group(1) in _MONTHS else None
    if month is None:
        return None
    local_now = now.astimezone()
    try:
        ts = local_now.replace(
            month=month,
            day=int(match.group(2)),
            hour=int(match.group(3)),
            minute=int(match.group(4)),
            second=int(match.group(5)),
            microsecond=0,
        )
    except ValueError:
        return None
    if ts > local_now + timedelta(days=1):
        with contextlib.suppress(ValueError):
            ts = ts.replace(year=ts.year - 1)
    return ts.astimezone(UTC)


def _unescape_sd(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value)


def _structured_data(text: str) -> tuple[dict, str]:
    """RFC 5424 STRUCTURED-DATA ('-' or [id k="v" ...]...) as attrs, and the rest of the line."""
    if text.startswith("-"):
        return {}, text[1:].lstrip(" ")
    attrs: dict = {}
    pos = 0
    while pos < len(text) and text[pos] == "[":
        match = _SD_ELEMENT.match(text, pos)
        if not match:
            break
        sd_id = match.group(1)
        for key, value in _SD_PARAM.findall(match.group(2)):
            attrs[f"{sd_id}.{key}"] = _unescape_sd(value)
        pos = match.end()
    return attrs, text[pos:].lstrip(" ")


def _nil(value: str) -> str | None:
    return None if value in ("-", "") else value


def _parse_5424(rest: str, now: datetime) -> dict | None:
    # VERSION TIMESTAMP HOSTNAME APP-NAME PROCID MSGID SD [MSG]
    parts = rest.split(" ", 6)
    if len(parts) < 7 or parts[0] != "1":
        if len(parts) == 6 and parts[0] == "1":
            parts.append("-")
        else:
            return None
    _, stamp, host, app, procid, msgid, tail = parts
    attrs, message = _structured_data(tail)
    if _nil(procid):
        attrs["procid"] = procid
    if _nil(msgid):
        attrs["msgid"] = msgid
    message = message.removeprefix("﻿")
    return {
        "ts": parse_time(stamp, now) if _nil(stamp) else now,
        "host": _nil(host),
        "app": _nil(app),
        "message": message,
        "attrs": attrs,
    }


def _parse_3164(rest: str, now: datetime) -> dict:
    ts = now
    match = _BSD_TIME.match(rest)
    if match:
        ts = _bsd_time(match, now) or now
        rest = rest[match.end() :]
    else:
        # Some senders use an ISO time in the BSD layout (rsyslog's RSYSLOG_ForwardFormat)
        first, _, after = rest.partition(" ")
        if first[:4].isdigit() and "T" in first:
            parsed = parse_time(first)
            if parsed is not None:
                ts, rest = parsed, after
    host = app = None
    attrs: dict = {}
    words = rest.split(" ", 2)

    def tag(word: str) -> bool:
        nonlocal app
        tag_match = _TAG.match(word)
        if tag_match:
            app = tag_match.group(1)
            if tag_match.group(2):
                attrs["procid"] = tag_match.group(2)
            return True
        if _TOPICS.match(word):
            topics = word.split(",")
            app = topics[0]
            attrs["topics"] = topics
            return True
        return False

    if match and words and tag(words[0]):
        # No host: the tag right after the time
        message = rest.split(" ", 1)[1] if " " in rest else ""
    elif match and len(words) >= 2 and _HOST.match(words[0]):
        host = words[0]
        if tag(words[1]):
            message = words[2] if len(words) > 2 else ""
        else:
            message = rest.split(" ", 1)[1]
    else:
        # No time (or no host after it): the whole text is the message, a leading tag if any
        if words and tag(words[0]):
            message = rest.split(" ", 1)[1] if " " in rest else ""
        else:
            message = rest
    return {"ts": ts, "host": host, "app": app, "message": message, "attrs": attrs}


def parse(data: bytes | str, sender: str | None = None, now: datetime | None = None) -> LogEntry | None:
    """One syslog message; None for an empty one."""
    now = now or datetime.now(UTC)
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    text = text.strip("\r\n\x00 ")
    if not text:
        return None
    severity, facility = 5, None
    match = _PRI.match(text)
    if match and int(match.group(1)) <= 191:
        pri = int(match.group(1))
        severity, facility = pri & 7, pri >> 3
        rest = text[match.end() :]
        fields = _parse_5424(rest, now) if rest.startswith("1 ") else None
        if fields is None:
            fields = _parse_3164(rest, now)
    else:
        fields = _parse_3164(text, now)
    message = fields["message"].strip() or text
    return LogEntry(
        ts=fields["ts"],
        source="syslog",
        host=clip(fields["host"]) or clip(sender),
        app=clip(fields["app"]),
        severity=severity,
        facility=facility,
        message=message[:MAX_MESSAGE],
        attrs=fields["attrs"],
    )


class Framer:
    """Splits a TCP stream into messages: octet-counted ("LEN SP MSG") or newline-terminated."""

    def __init__(self, max_frame: int = MAX_FRAME):
        self.max_frame = max_frame
        self.buffer = bytearray()
        self.oversized = 0

    def feed(self, data: bytes) -> list[bytes]:
        self.buffer += data
        frames: list[bytes] = []
        while self.buffer:
            if self.buffer[:1].isdigit():
                space = self.buffer.find(b" ", 0, 12)
                if space > 0 and self.buffer[:space].isdigit():
                    length = int(self.buffer[:space])
                    if length > self.max_frame:
                        # Can't be a sane octet count: read it as a line instead
                        pass
                    elif len(self.buffer) >= space + 1 + length:
                        frames.append(bytes(self.buffer[space + 1 : space + 1 + length]))
                        del self.buffer[: space + 1 + length]
                        continue
                    else:
                        break
            newline = self.buffer.find(b"\n")
            if newline < 0:
                if len(self.buffer) > self.max_frame:
                    # A line without an end: keep what fits, drop the rest
                    frames.append(bytes(self.buffer[: self.max_frame]))
                    self.buffer.clear()
                    self.oversized += 1
                break
            frame = bytes(self.buffer[:newline])
            del self.buffer[: newline + 1]
            if frame.strip(b"\r\x00 "):
                frames.append(frame[: self.max_frame])
        return frames

    def rest(self) -> bytes:
        """What is left when the connection closes (a last line without a newline)."""
        data, self.buffer = bytes(self.buffer), bytearray()
        return data


class _UdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, sink: Sink):
        self.sink = sink

    def datagram_received(self, data: bytes, addr) -> None:
        sender = addr[0] if addr else None
        try:
            entry = parse(data, sender)
        except Exception as e:  # a parser bug must not stop the receiver
            logger.debug(f"Cannot parse a syslog message from {sender}: {e}")
            return
        if entry is not None:
            self.sink(entry, sender or "")


class SyslogServer:
    """UDP and TCP syslog on one port; every message goes to sink(entry, sender address)."""

    def __init__(self, port: int, sink: Sink, host: str = "0.0.0.0"):
        self.port = port
        self.host = host
        self.sink = sink
        self.udp: asyncio.DatagramTransport | None = None
        self.tcp: asyncio.Server | None = None
        self._connections: set[asyncio.Task] = set()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            self.udp, _ = await loop.create_datagram_endpoint(
                lambda: _UdpProtocol(self.sink), local_addr=(self.host, self.port)
            )
            sock = self.udp.get_extra_info("socket")
            if sock is not None:
                # A bigger receive buffer rides out bursts (a router's boot log) while a batch is written
                with contextlib.suppress(OSError):
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, UDP_BUFFER)
            logger.info(f"Syslog listening on port {self.port}/udp")
        except OSError as e:
            logger.error(f"Cannot listen for syslog on {self.port}/udp: {e}")
        try:
            self.tcp = await asyncio.start_server(self._handle, self.host, self.port, limit=MAX_FRAME * 2)
            logger.info(f"Syslog listening on port {self.port}/tcp")
        except OSError as e:
            logger.error(f"Cannot listen for syslog on {self.port}/tcp: {e}")

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        sender = peer[0] if peer else ""
        task = asyncio.current_task()
        if len(self._connections) >= MAX_CONNECTIONS:
            logger.warning(f"Too many syslog connections; refusing {sender}")
            writer.close()
            return
        if task:
            self._connections.add(task)
        framer = Framer()
        try:
            while True:
                data = await asyncio.wait_for(reader.read(65536), IDLE_TIMEOUT)
                if not data:
                    break
                for frame in framer.feed(data):
                    self._emit(frame, sender)
            self._emit(framer.rest(), sender)
        except (TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            if task:
                self._connections.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    def _emit(self, frame: bytes, sender: str) -> None:
        if not frame:
            return
        try:
            entry = parse(frame, sender)
        except Exception as e:
            logger.debug(f"Cannot parse a syslog message from {sender}: {e}")
            return
        if entry is not None:
            self.sink(entry, sender)

    async def stop(self) -> None:
        if self.udp is not None:
            self.udp.close()
        if self.tcp is not None:
            self.tcp.close()
        for task in list(self._connections):
            task.cancel()
        if self.tcp is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self.tcp.wait_closed(), 2)
