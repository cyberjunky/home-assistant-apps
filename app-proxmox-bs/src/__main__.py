"""Proxmox Backup Server - the app's own part, next to PBS's two daemons.

It writes PBS's log into the app's log, and keeps the disks PBS set up mounted (see mounts.py).
PBS itself (its API, web interface and console on port 8007) runs as its own services.
"""

import asyncio
import contextlib
import logging
import signal
import sys

from . import journal
from .config import APP_VERSION, log_level
from .mounts import Watcher

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

CHECK_SECONDS = 10


async def watch_disks() -> None:
    watcher = Watcher()
    while True:
        try:
            await asyncio.to_thread(watcher.check)
        except Exception as e:  # keep watching whatever happened
            logger.exception(f"Disks: {e}")
        await asyncio.sleep(CHECK_SECONDS)


async def main() -> int:
    level = log_level()
    if level in ("debug", "warning", "error"):
        logging.getLogger().setLevel(level.upper())
    logger.info(f"Proxmox Backup Server app {APP_VERSION}")
    services = [
        asyncio.create_task(journal.relay(), name="journal"),
        asyncio.create_task(watch_disks(), name="disks"),
    ]
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    stopper = asyncio.create_task(stop.wait(), name="stop")
    done, _ = await asyncio.wait([*services, stopper], return_when=asyncio.FIRST_COMPLETED)
    exit_code = 0
    for task in done:
        if task is not stopper and not task.cancelled() and task.exception():
            logger.error(f"{task.get_name()} failed: {task.exception()!r}")
            exit_code = 2
    for task in [*services, stopper]:
        task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await asyncio.gather(*services, return_exceptions=True)
    return exit_code


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
