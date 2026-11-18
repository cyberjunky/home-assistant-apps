"""The broker's clients, from Mosquitto's log.

Mosquitto has no API that lists its clients, but it logs every connection (address, protocol,
clean session, keepalive and username), every subscription and every disconnection with its
reason. The log pipe (logpipe.py) keeps that log in a file; this module reads it from the start
and then follows it, so it knows every client since the broker started.

What the log can't tell: the subscriptions of a persistent session made before the broker
started (they come back from its database without a log line).
"""

import asyncio
import contextlib
import logging
import re
import time
from collections import deque
from datetime import datetime
from pathlib import Path

from .config import LISTENERS

logger = logging.getLogger(__name__)

PROTOCOLS = {3: "3.1", 4: "3.1.1", 5: "5"}
# Clients that left this long ago are forgotten (a persistent session is kept as long as it has subscriptions)
FORGET_AFTER = 7 * 86400
MAX_CLIENTS = 5000
MAX_EVENTS = 2000
MAX_LOG = 3000

_LINE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d): (.*)$")
_NEW_CONNECTION = re.compile(r"^New connection from (\S+) on port (\d+)\.$")
_CONNECTED = re.compile(r"^New client connected from (\S+) as (\S+) \(p(\d+), c(\d), k(\d+)(?:, u'(.*)')?\)\.$")
_DISCONNECTED = re.compile(r"^Client (\S+) \[(\S+)\] disconnected(?:: (.*?))?\.?$")
_DISCONNECTED_OLD = re.compile(r"^Client (\S+) (?:has exceeded timeout, disconnecting|closed its connection)\.?$")
_TAKEOVER = re.compile(r"^Client (\S+) already connected, closing old connection\.$")
_NOT_AUTHORISED = re.compile(r"^Client (\S+) (?:\[(\S+)\] )?disconnected[,:]? not authori[sz]ed\.?$")
_SUBSCRIBE = re.compile(r"^(\S+) (\d+) (\S+)$")
_UNSUBSCRIBE = re.compile(r"^(\S+) (\S+)$")
_STARTED = re.compile(r"^mosquitto version (\S+) (?:starting|running)$")
# Lines that are about the broker itself, not a subscription (two or three words, like one)
_NOT_SUBSCRIPTION = re.compile(r"^(Bridge|Persistence|TLS|TLS-PSK|Websockets|Opening|Info:|Warning:|Error:|Config)\b")
# An address as Mosquitto writes it: 1.2.3.4:5678 or [::1]:5678 or ::1:5678
_ADDRESS = re.compile(r"^\[?(.*?)\]?:(\d+)$")


def _ts(text: str) -> float:
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S").timestamp()
    except ValueError:
        return time.time()


def split_address(address: str) -> tuple[str, int | None]:
    m = _ADDRESS.match(address)
    return (m.group(1), int(m.group(2))) if m else (address, None)


def level_of(message: str) -> str:
    """error, warning, notice: how the Log page colours a line (Mosquitto doesn't write the type)."""
    low = message.lower()
    if low.startswith("error") or "not authori" in low or "unable" in low or "failed" in low:
        return "error"
    if low.startswith("warning") or "exceeded timeout" in low or "already connected" in low or "protocol error" in low:
        return "warning"
    return "info"


class Clients:
    """Every client since the broker started, its subscriptions, and what happened to it."""

    def __init__(self) -> None:
        self.clients: dict[str, dict] = {}
        # Connections opened but not yet logged in: address -> listener port
        self._pending: dict[str, int] = {}
        self.events: deque[dict] = deque(maxlen=MAX_EVENTS)
        self.log: deque[dict] = deque(maxlen=MAX_LOG)
        self.broker_version = ""
        self.broker_started: float | None = None
        # Goes up with every change; the dashboard's stream follows it
        self.version = 0
        self.log_seq = 0
        # Counted for the history (history.py takes and resets them every minute)
        self.connects = 0
        self.disconnects = 0
        self.refused = 0
        # Connects per client, for the hourly history
        self.connects_by_client: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------
    def feed(self, line: str) -> None:
        """One line of the log."""
        line = line.rstrip("\n")
        m = _LINE.match(line)
        if not m:
            return
        ts, message = _ts(m.group(1)), m.group(2)
        changed = self._parse(ts, message)
        if changed != "subscription":
            self.log_seq += 1
            self.log.append({"seq": self.log_seq, "ts": ts, "level": level_of(message), "text": message})
        self.version += 1

    def _parse(self, ts: float, message: str) -> str:
        if m := _STARTED.match(message):
            if message.endswith("starting"):
                self._broker_restarted(ts, m.group(1))
            return "broker"
        if m := _NEW_CONNECTION.match(message):
            self._pending[m.group(1)] = int(m.group(2))
            if len(self._pending) > 1000:
                self._pending.pop(next(iter(self._pending)))
            return "connection"
        if m := _CONNECTED.match(message):
            address, cid, proto, clean, keepalive, user = m.groups()
            self._connected(ts, cid, address, int(proto), clean == "1", int(keepalive), user or "")
            return "client"
        if m := _NOT_AUTHORISED.match(message):
            self._refused(ts, m.group(1), m.group(2) or "")
            return "client"
        if m := _DISCONNECTED.match(message):
            self._disconnected(ts, m.group(1), m.group(2), m.group(3) or "")
            return "client"
        if m := _DISCONNECTED_OLD.match(message):
            reason = "keepalive timeout" if "timeout" in message else "connection closed by client"
            self._disconnected(ts, m.group(1), "", reason)
            return "client"
        if m := _TAKEOVER.match(message):
            self._event(ts, m.group(1), "takeover", "Another connection with the same client ID took over")
            return "client"
        if _NOT_SUBSCRIPTION.match(message):
            return "broker"
        if (m := _SUBSCRIBE.match(message)) and m.group(1) in self.clients:
            self._subscribe(m.group(1), m.group(3), int(m.group(2)))
            return "subscription"
        if (m := _UNSUBSCRIBE.match(message)) and m.group(1) in self.clients:
            self.clients[m.group(1)]["subs"].pop(m.group(2), None)
            return "subscription"
        return "other"

    def _client(self, cid: str) -> dict:
        c = self.clients.get(cid)
        if c is None:
            if len(self.clients) >= MAX_CLIENTS:
                self._forget(force=True)
            c = self.clients[cid] = {
                "id": cid,
                "user": "",
                "ip": "",
                "port": None,
                "listener": None,
                "protocol": "",
                "clean": True,
                "keepalive": None,
                "connected": False,
                "since": None,
                "first_seen": None,
                "last_seen": None,
                "left_at": None,
                "reason": "",
                "connects": 0,
                "drops": 0,
                "subs": {},
            }
        return c

    def _connected(self, ts: float, cid: str, address: str, proto: int, clean: bool, keepalive: int, user: str) -> None:
        c = self._client(cid)
        ip, port = split_address(address)
        if c["clean"] or clean:
            # A clean session starts without subscriptions; a persistent one keeps them
            c["subs"] = {}
        c.update(
            user=user,
            ip=ip,
            port=port,
            listener=self._pending.pop(address, c["listener"]),
            protocol=PROTOCOLS.get(proto, str(proto)),
            clean=clean,
            keepalive=keepalive,
            connected=True,
            since=ts,
            last_seen=ts,
            reason="",
        )
        c["first_seen"] = c["first_seen"] or ts
        c["connects"] += 1
        self.connects += 1
        self.connects_by_client[cid] = self.connects_by_client.get(cid, 0) + 1
        listener = LISTENERS.get(c["listener"] or 0, "")
        self._event(
            ts, cid, "connect", f"from {ip}{f' over {listener}' if listener else ''}{f' as {user}' if user else ''}"
        )

    def _disconnected(self, ts: float, cid: str, address: str, reason: str) -> None:
        c = self.clients.get(cid)
        if c is None:
            return
        reason = reason or "disconnected"
        if c["connected"]:
            self.disconnects += 1
        c.update(connected=False, left_at=ts, last_seen=ts, reason=reason)
        # Anything but a goodbye from the client: it dropped off
        if reason not in ("disconnected", "connection closed by client") and "closed by client" not in reason:
            c["drops"] += 1
        if c["clean"]:
            c["subs"] = {}
        self._event(ts, cid, "disconnect", reason)

    def _refused(self, ts: float, cid: str, address: str) -> None:
        self.refused += 1
        c = self.clients.get(cid)
        if c is not None and c["connected"] and address and f"{c['ip']}:{c['port']}" != address:
            # The refused login is a second connection with the same ID, the first stays
            pass
        elif c is not None:
            c.update(connected=False, left_at=ts, last_seen=ts, reason="not authorised")
        self._event(ts, cid, "refused", f"login refused{f' from {split_address(address)[0]}' if address else ''}")

    def _subscribe(self, cid: str, topic: str, options: int) -> None:
        subs = self.clients[cid]["subs"]
        if len(subs) < 500:
            # MQTT 5 logs the subscription options; the QoS is the lowest two bits
            subs[topic] = options & 3

    def _broker_restarted(self, ts: float, version: str) -> None:
        self.broker_version = version
        self.broker_started = ts
        for c in self.clients.values():
            if c["connected"]:
                c.update(connected=False, left_at=ts, reason="broker restarted")
            if c["clean"]:
                c["subs"] = {}
        self._pending.clear()
        self._event(ts, "", "broker", f"Mosquitto {version} started")

    def _event(self, ts: float, cid: str, kind: str, detail: str) -> None:
        self.events.append({"ts": ts, "client": cid, "kind": kind, "detail": detail})

    def _forget(self, force: bool = False) -> None:
        now = time.time()
        old = [
            cid
            for cid, c in self.clients.items()
            if not c["connected"]
            and (force or now - (c["left_at"] or now) > FORGET_AFTER)
            and (c["clean"] or not c["subs"])
        ]
        if force and not old:
            old = [cid for cid, c in self.clients.items() if not c["connected"]][:100]
        for cid in sorted(old, key=lambda k: self.clients[k]["left_at"] or 0)[: max(1, len(old))]:
            self.clients.pop(cid, None)

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def connected(self) -> list[dict]:
        return [c for c in self.clients.values() if c["connected"]]

    def take_counts(self) -> tuple[int, int, int, dict[str, int]]:
        """The connects, disconnects, refused logins and connects per client since the last call."""
        counts = (self.connects, self.disconnects, self.refused, self.connects_by_client)
        self.connects = self.disconnects = self.refused = 0
        self.connects_by_client = {}
        return counts

    def summary(self) -> dict:
        connected = self.connected()
        return {
            "connected": len(connected),
            "known": len(self.clients),
            "subscriptions": sum(len(c["subs"]) for c in connected),
            "broker_version": self.broker_version,
            "broker_started": self.broker_started,
        }

    # ------------------------------------------------------------------
    # Following the file
    # ------------------------------------------------------------------
    async def follow(self, path: Path) -> None:
        """Read the log from the start (the file before it first, if the pipe rotated it), then follow it."""
        rotated = path.with_name(path.name + ".1")
        with contextlib.suppress(OSError):
            for line in rotated.read_text(errors="replace").splitlines():
                self.feed(line)
        handle = None
        inode = None
        forgot = time.monotonic()
        while True:
            try:
                if handle is None:
                    handle = path.open("rb")
                    inode = path.stat().st_ino
                line = handle.readline()
                if line:
                    if line.endswith(b"\n"):
                        self.feed(line.decode(errors="replace"))
                    else:
                        # Half a line: wait for the rest
                        handle.seek(-len(line), 1)
                        await asyncio.sleep(0.2)
                    continue
                await asyncio.sleep(0.5)
                # The pipe rotated the file: read the new one from its start
                if path.stat().st_ino != inode:
                    handle.close()
                    handle = None
                if time.monotonic() - forgot > 3600:
                    forgot = time.monotonic()
                    self._forget()
            except FileNotFoundError:
                await asyncio.sleep(2)
            except OSError as e:
                logger.warning(f"Cannot read the broker's log {path}: {e}")
                handle = None
                await asyncio.sleep(5)
