"""Follows Home Assistant's device trackers and scans the devices that are due.

Every minute the trackers are read. A device that is home is scanned when it is new, when you asked for
it, or when its last scan is older than the rescan period: a laptop that was away for two weeks is
scanned soon after it comes home. One device is scanned at a time, so the network stays usable.
"""

import asyncio
import contextlib
import logging
import time

from .database import Database
from .engine import Engine
from .trackers import HAError, Trackers

logger = logging.getLogger(__name__)

POLL_EVERY = 60


class Scheduler:
    def __init__(self, engine: Engine, database: Database, trackers: Trackers):
        self.engine = engine
        self.database = database
        self.trackers = trackers
        self.ha_error: str | None = None
        self.last_sync: float | None = None
        self.queue: list[dict] = []
        self._wake = asyncio.Event()
        self._day = time.strftime("%Y-%m-%d")

    def wake(self) -> None:
        """Look again now, after a scan was asked for."""
        self._wake.set()

    async def sync(self) -> None:
        try:
            self.database.sync_trackers(await self.trackers.fetch())
            self.ha_error = None
            self.last_sync = time.time()
        except HAError as e:
            if self.ha_error != str(e):
                logger.warning("Cannot read the device trackers: %s", e)
            self.ha_error = str(e)
        self.queue = self.database.due_devices(self.engine.config.rescan_days)

    def _new_day(self) -> None:
        day = time.strftime("%Y-%m-%d")
        if day != self._day:
            # The last word on yesterday, then today's first row
            self.database.snapshot_day(self._day)
            self._day = day
            self.database.prune(self.engine.config.retention_days)
        self.database.snapshot_day(day)

    async def run(self) -> None:
        while True:
            await self.sync()
            self._new_day()
            if self.queue:
                device = self.queue[0]
                await self.engine.scan_device(device, device["reason"])
                continue
            if self.engine.config.update_templates:
                try:
                    # Kept current while idle too, so the dashboard's freshness is true between scans
                    await self.engine.update_templates()
                except Exception as e:
                    logger.warning("Template update failed: %s", e)
            self._wake.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), POLL_EVERY)
