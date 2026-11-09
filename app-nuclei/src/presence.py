"""Is a known device online? Checked on a timer, apart from the heavier scans.

A device counts as online when it answers: a TCP connection to one of its known open ports succeeds, or is
actively refused (a refusal means something replied), or, failing that, a ping is answered. Silence for all
of them means offline. Only devices that belong to an enabled, authorised scan object are checked, so the
ownership gate covers this too. It sends a handful of packets per device, not a scan.
"""

import asyncio
import logging
import shutil

from .database import Database

logger = logging.getLogger(__name__)

TCP_TIMEOUT = 1.5
# What to try when nothing is known about a device yet
FALLBACK_PORTS = (443, 80, 22, 445, 53)
MAX_PORTS_TRIED = 4


async def tcp_answers(host: str, port: int, timeout: float = TCP_TIMEOUT) -> bool:
    """Whether a TCP connect to host:port got any answer: accepted, or refused."""
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    except ConnectionRefusedError:
        return True
    except (TimeoutError, OSError):
        return False
    writer.close()
    return True


async def ping_answers(host: str) -> bool:
    """Whether one ICMP echo was answered. False where ping isn't available."""
    if not shutil.which("ping"):
        return False
    proc = await asyncio.create_subprocess_exec(
        "ping", "-c", "1", "-W", "1", host, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
    )
    try:
        return await asyncio.wait_for(proc.wait(), 4) == 0
    except TimeoutError:
        proc.kill()
        return False


async def is_online(host: str, ports: list[int], tcp=tcp_answers, ping=ping_answers) -> bool:
    candidates = (list(ports) + [p for p in FALLBACK_PORTS if p not in ports])[:MAX_PORTS_TRIED]
    results = await asyncio.gather(*(tcp(host, port) for port in candidates))
    if any(results):
        return True
    return await ping(host)


class PresenceMonitor:
    def __init__(self, database: Database, interval_minutes: int, concurrency: int = 25, check=is_online):
        self.database = database
        self.interval = interval_minutes * 60
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        self._check = check

    async def check_all(self) -> int:
        """Check every eligible device once. Returns how many were checked."""
        devices = self.database.devices_to_check()

        async def one(device: dict) -> None:
            async with self._semaphore:
                online = await self._check(device["host"], device["open_ports"])
            self.database.set_presence(device["id"], online)

        await asyncio.gather(*(one(d) for d in devices))
        return len(devices)

    async def run(self) -> None:
        if not self.interval:
            logger.info("Online check is off")
            return
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.check_all()
            except Exception:
                logger.exception("Online check failed")
