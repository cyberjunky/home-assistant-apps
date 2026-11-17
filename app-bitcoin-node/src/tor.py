"""Tor, read over its control port: how far it is started, its circuits and its traffic.

The control port is optional. Without it the app still knows the onion addresses and whether the
node is reachable through them, from Bitcoin Core and LND. Only GETINFO is sent: nothing is changed.
"""

import asyncio
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime

from .config import Config

logger = logging.getLogger(__name__)

TIMEOUT = 15
# Polls that may fail in a row before the service counts as not answering
MISSES_ALLOWED = 2
INFO_KEYS = ("version", "status/bootstrap-phase", "traffic/read", "traffic/written")


class TorError(Exception):
    """Tor's control port can't be reached, or refused the password."""


def quoted(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def parse_bootstrap(line: str) -> tuple[int | None, str]:
    """NOTICE BOOTSTRAP PROGRESS=100 TAG=done SUMMARY="Done" as (100, "Done")."""
    progress = re.search(r"PROGRESS=(\d+)", line)
    summary = re.search(r'SUMMARY="([^"]*)"', line)
    return (int(progress.group(1)) if progress else None, summary.group(1) if summary else "")


def parse_circuit(line: str) -> dict | None:
    """One line of circuit-status: 5 BUILT $FP~name,$FP~name PURPOSE=GENERAL TIME_CREATED=2026-11-16T20:01:02.5."""
    parts = line.split()
    if len(parts) < 2 or not parts[0].isdigit():
        return None
    path = []
    if len(parts) > 2 and "=" not in parts[2]:
        for hop in parts[2].split(","):
            fingerprint, _, name = hop.partition("~")
            path.append(name or fingerprint.lstrip("$")[:8])
    fields = dict(p.split("=", 1) for p in parts[2:] if "=" in p)
    created = None
    if fields.get("TIME_CREATED"):
        try:
            created = int(datetime.fromisoformat(fields["TIME_CREATED"][:19]).replace(tzinfo=UTC).timestamp())
        except ValueError:
            created = None
    return {
        "id": int(parts[0]),
        "status": parts[1],
        "purpose": fields.get("PURPOSE", ""),
        "path": path,
        "created": created,
    }


def parse_reply(lines: list[str]) -> dict[str, str]:
    """The key=value answers of GETINFO, also those that span lines (250+key= ... a line with only a dot)."""
    out: dict[str, str] = {}
    multi: str | None = None
    for line in lines:
        if multi is not None:
            if line == ".":
                multi = None
            else:
                out[multi] += ("\n" if out[multi] else "") + line
            continue
        if line.startswith("250+") and "=" in line:
            multi, _, first = line[4:].partition("=")
            out[multi] = first
        elif line.startswith("250-") and "=" in line:
            key, _, value = line[4:].partition("=")
            out[key] = value
    return out


class TorClient:
    """Tor's state, kept current by run()."""

    def __init__(self, config: Config, emit: Callable[..., None]):
        self.config = config
        self.emit = emit
        self.configured = bool(config.tor_control)
        self.connected = False
        self.error = ""
        self.version = ""
        self.bootstrap: int | None = None
        self.bootstrap_text = ""
        self.read: int | None = None
        self.written: int | None = None
        self.read_rate: float | None = None
        self.written_rate: float | None = None
        self.circuits: list[dict] = []
        self._previous: tuple[float, int, int] | None = None
        self._misses = 0

    async def run(self) -> None:
        if not self.configured:
            return
        while True:
            try:
                await asyncio.wait_for(self.poll(), TIMEOUT)
                self._set_link(True, "")
            except TorError as e:
                self._set_link(False, str(e))
            except (OSError, TimeoutError, asyncio.IncompleteReadError) as e:
                self._set_link(False, f"no answer from {self.config.tor_control}: {str(e) or type(e).__name__}")
            except Exception as e:
                logger.exception("Tor: reading failed")
                self._set_link(False, repr(e))
            await asyncio.sleep(self.config.poll_seconds)

    def _set_link(self, connected: bool, error: str) -> None:
        if connected:
            self._misses = 0
        elif self.connected:
            # A busy node answers late now and then: one missed poll is not an outage.
            # What was read last stays on the pages until MISSES_ALLOWED polls in a row failed
            self._misses += 1
            if self._misses <= MISSES_ALLOWED:
                logger.debug(f"Tor: missed a poll ({self._misses}): {error}")
                return
        if connected and not self.connected:
            logger.info(f"Tor {self.version} answers on its control port")
        elif not connected and (self.connected or error != self.error):
            logger.warning(f"Tor: {error}")
            if self.connected:
                self.emit("tor", "control", "bad", f"Tor's control port does not answer: {error}")
        self.connected, self.error = connected, error
        if not connected:
            self.read_rate = self.written_rate = None
            self._previous = None

    async def _command(self, reader, writer, command: str) -> list[str]:
        """Send one command and collect its reply, up to the line that ends it ("250 OK" or an error)."""
        writer.write((command + "\r\n").encode())
        await writer.drain()
        lines: list[str] = []
        in_data = False
        while True:
            raw = await reader.readline()
            if not raw:
                raise TorError("the control port closed the connection")
            line = raw.decode(errors="replace").rstrip("\r\n")
            lines.append(line)
            if in_data:
                in_data = line != "."
            elif line[:4].endswith("+"):
                in_data = True
            elif len(line) >= 4 and line[3] == " ":
                if not line.startswith("250"):
                    raise TorError(line[4:] or line)
                return lines

    async def poll(self) -> None:
        host, _, port = self.config.tor_control.rpartition(":")
        reader, writer = await asyncio.open_connection(host, int(port))
        try:
            try:
                await self._command(reader, writer, f"AUTHENTICATE {quoted(self.config.tor_password)}")
            except TorError as e:
                raise TorError(f"it answers, but refused the password in tor.password ({e})") from e
            info = parse_reply(await self._command(reader, writer, "GETINFO " + " ".join(INFO_KEYS)))
            circuits = parse_reply(await self._command(reader, writer, "GETINFO circuit-status"))
            writer.write(b"QUIT\r\n")
        finally:
            writer.close()
        previous_bootstrap = self.bootstrap
        self.version = info.get("version", "").split(" ")[0]
        self.bootstrap, self.bootstrap_text = parse_bootstrap(info.get("status/bootstrap-phase", ""))
        if previous_bootstrap is not None and previous_bootstrap != self.bootstrap and self.bootstrap == 100:
            self.emit("tor", "ready", "ok", "Tor is connected to its network again")
        self.circuits = [c for c in map(parse_circuit, circuits.get("circuit-status", "").splitlines()) if c]
        now = time.time()
        try:
            read, written = int(info["traffic/read"]), int(info["traffic/written"])
        except (KeyError, ValueError):
            return
        if self._previous and now > self._previous[0] and read >= self._previous[1] and written >= self._previous[2]:
            seconds = now - self._previous[0]
            self.read_rate = round((read - self._previous[1]) / seconds / 1000, 2)
            self.written_rate = round((written - self._previous[2]) / seconds / 1000, 2)
        self._previous = (now, read, written)
        self.read, self.written = read, written

    def status(self, onion_services: list[dict]) -> dict:
        return {
            "configured": self.configured,
            "connected": self.connected,
            "error": self.error,
            "version": self.version,
            "bootstrap": self.bootstrap,
            "bootstrap_text": self.bootstrap_text,
            "circuits": len(self.circuits),
            "circuits_built": sum(1 for c in self.circuits if c["status"] == "BUILT"),
            "onion_services": onion_services,
            "read": self.read,
            "written": self.written,
            "read_rate": self.read_rate,
            "written_rate": self.written_rate,
        }
