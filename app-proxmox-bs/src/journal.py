"""PBS's log, into the app's log.

Proxmox Backup Server logs to systemd's journal, which a container doesn't have. This listens on the
journal's socket and writes each message to the app log, so it shows on the app's Log tab.
"""

import asyncio
import contextlib
import logging
import os
import socket
import struct
from pathlib import Path

logger = logging.getLogger("pbs")

SOCKET = Path(os.environ.get("PBSAPP_JOURNAL", "/run/systemd/journal/socket"))
# The journal's priorities: 0 emergency .. 7 debug
LEVELS = {0: logging.CRITICAL, 1: logging.CRITICAL, 2: logging.CRITICAL, 3: logging.ERROR, 4: logging.WARNING}
# PBS logs a few lines on every start and reload; they say nothing on their own
QUIET = ("catching shutdown signal", "catching reload signal")


def parse_entry(data: bytes) -> dict[str, str]:
    """One message in the journal's native format.

    Each field is NAME=value and a newline, or, for a value that holds a newline,
    NAME, a newline, the value's length as 64-bit little endian, the value and a newline."""
    fields: dict[str, str] = {}
    at = 0
    while at < len(data):
        end = data.find(b"\n", at)
        if end < 0:
            end = len(data)
        line = data[at:end]
        if b"=" in line:
            name, _, value = line.partition(b"=")
            fields[name.decode(errors="replace")] = value.decode(errors="replace")
            at = end + 1
            continue
        if end + 9 > len(data):
            break
        (length,) = struct.unpack("<Q", data[end + 1 : end + 9])
        start = end + 9
        fields[line.decode(errors="replace")] = data[start : start + length].decode(errors="replace")
        at = start + length + 1
    return fields


class _Protocol(asyncio.DatagramProtocol):
    def datagram_received(self, data: bytes, addr) -> None:
        entry = parse_entry(data)
        message = entry.get("MESSAGE", "").strip()
        if not message or message in QUIET:
            return
        try:
            priority = int(entry.get("PRIORITY", "6"))
        except ValueError:
            priority = 6
        level = LEVELS.get(priority, logging.INFO if priority <= 6 else logging.DEBUG)
        name = entry.get("SYSLOG_IDENTIFIER", "pbs").replace("proxmox-backup-", "")
        logger.log(level, f"[{name}] {message}")


async def relay() -> None:
    """Listen on the journal's socket until cancelled."""
    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        SOCKET.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    sock.bind(str(SOCKET))
    # The proxy runs as the backup user
    os.chmod(SOCKET, 0o666)
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(_Protocol, sock=sock)
    try:
        await asyncio.Event().wait()
    finally:
        transport.close()
