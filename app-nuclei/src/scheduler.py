"""Runs scheduled scans. Each object may carry a schedule: 'daily', 'weekly', 'monthly' or ''.

The loop wakes every minute, and starts a scan of any due object that isn't already running. Due means
its schedule's interval has passed since its last scan. Scans are started one at a time to keep the
host's network sane; the engine's own lock stops overlap.
"""

import asyncio
import logging
import time

from .engine import Engine, NotAuthorised

logger = logging.getLogger(__name__)

INTERVALS = {"daily": 86400, "weekly": 7 * 86400, "monthly": 30 * 86400}
CHECK_EVERY = 60


def is_due(obj: dict, now: float) -> bool:
    interval = INTERVALS.get((obj.get("schedule") or "").lower())
    if not interval or not obj["enabled"] or not obj["authorised"]:
        return False
    last = obj.get("last_scan_at")
    return last is None or now - last >= interval


class Scheduler:
    def __init__(self, engine: Engine):
        self.engine = engine

    async def run(self) -> None:
        while True:
            await asyncio.sleep(CHECK_EVERY)
            now = time.time()
            for obj in self.engine.database.objects():
                if is_due(obj, now) and obj["id"] not in self.engine.running:
                    try:
                        logger.info("Scheduled scan of %s", obj["target"])
                        await self.engine.scan_object(obj["id"], trigger="scheduled")
                    except NotAuthorised as e:
                        logger.debug("Skipping scheduled scan of %s: %s", obj["target"], e)
                    except Exception:
                        logger.exception("Scheduled scan of %s failed", obj["target"])
