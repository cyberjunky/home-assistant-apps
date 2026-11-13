"""When each tracker went on and off the network, kept by the app itself, and the statistics.

Home Assistant's recorder keeps 10 days by default; this keeps a small table of the changes only
(keep_days, a year by default), filled from the recorder at start and from the live events after.
"""

import asyncio
import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .ha_client import HAClient, HAClientError
from .inventory import is_up

logger = logging.getLogger(__name__)

DAY = 86400
BATCH = 40


class History:
    def __init__(self, path: Path, keep_days: int = 365):
        self.path = Path(path)
        self.keep_days = keep_days
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS changes (entity_id TEXT NOT NULL, ts REAL NOT NULL, up INTEGER,"
            " PRIMARY KEY (entity_id, ts)) WITHOUT ROWID"
        )
        self.db.execute("CREATE INDEX IF NOT EXISTS changes_ts ON changes (ts)")
        self.db.commit()
        self.backfill_state = {"done": False, "entities": 0, "rows": 0, "error": ""}

    def close(self) -> None:
        self.db.close()

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def record(self, entity_id: str, ts: float, up: bool | None) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO changes VALUES (?, ?, ?)", (entity_id, ts, None if up is None else int(up))
        )
        self.db.commit()

    def add_many(self, rows: list[tuple[str, float, bool | None]]) -> int:
        before = self.db.total_changes
        self.db.executemany(
            "INSERT OR IGNORE INTO changes VALUES (?, ?, ?)",
            [(e, ts, None if up is None else int(up)) for e, ts, up in rows],
        )
        self.db.commit()
        return self.db.total_changes - before

    def prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self.db.execute("DELETE FROM changes WHERE ts < ?", (now - self.keep_days * DAY,))
        self.db.commit()

    async def backfill(self, client: HAClient, entity_ids: list[str], days: int) -> None:
        """Read the recorder's history of the trackers, for the days the app has no record of."""
        if days <= 0 or not entity_ids:
            self.backfill_state["done"] = True
            return
        end = datetime.now(UTC)
        start = end - timedelta(days=days)
        rows: list[tuple[str, float, bool | None]] = []
        try:
            for i in range(0, len(entity_ids), BATCH):
                batch = entity_ids[i : i + BATCH]
                for series in await client.get_history(batch, start, end):
                    entity_id = series[0].get("entity_id") if series else None
                    for item in series:
                        entity_id = item.get("entity_id", entity_id)
                        when = item.get("last_changed") or item.get("lu")
                        try:
                            ts = datetime.fromisoformat(str(when).replace("Z", "+00:00")).timestamp()
                        except ValueError:
                            continue
                        rows.append((entity_id, ts, is_up(item.get("state"))))
        except HAClientError as e:
            self.backfill_state["error"] = str(e)
            logger.warning(f"Could not read the recorder's history: {e}")
        added = await asyncio.to_thread(self.add_many, rows)
        self.backfill_state.update(done=True, entities=len(entity_ids), rows=added)
        logger.info(f"History: {added} changes read from the recorder for {len(entity_ids)} trackers")

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def changes(self, since: float) -> tuple[dict[str, bool | None], dict[str, list[tuple[float, bool | None]]]]:
        """The state of every tracker at `since`, and its changes after."""
        start: dict[str, bool | None] = {}
        for entity_id, up, _ts in self.db.execute(
            "SELECT entity_id, up, MAX(ts) FROM changes WHERE ts < ? GROUP BY entity_id", (since,)
        ):
            start[entity_id] = None if up is None else bool(up)
        after: dict[str, list[tuple[float, bool | None]]] = {}
        for entity_id, ts, up in self.db.execute(
            "SELECT entity_id, ts, up FROM changes WHERE ts >= ? ORDER BY entity_id, ts", (since,)
        ):
            after.setdefault(entity_id, []).append((ts, None if up is None else bool(up)))
        return start, after

    def first_seen(self) -> dict[str, float]:
        return {
            e: ts for e, ts in self.db.execute("SELECT entity_id, MIN(ts) FROM changes WHERE up = 1 GROUP BY entity_id")
        }

    def last_seen(self) -> dict[str, float]:
        """The last time each tracker went off the network (or on, when it still is)."""
        return {
            e: ts
            for e, ts in self.db.execute(
                "SELECT entity_id, MAX(ts) FROM changes WHERE up IS NOT NULL GROUP BY entity_id"
            )
        }


# ----------------------------------------------------------------------
# Statistics: pure functions over intervals, so the tests can check them
# ----------------------------------------------------------------------
def up_intervals(
    start_up: bool | None, changes: list[tuple[float, bool | None]], since: float, until: float
) -> tuple[list[tuple[float, float]], float]:
    """The times a tracker was up between since and until, and how long its state was known."""
    out: list[tuple[float, float]] = []
    known = 0.0
    state, at = start_up, since
    for ts, up in changes:
        ts = min(max(ts, since), until)
        if state is not None:
            known += ts - at
        if state and ts > at:
            out.append((at, ts))
        state, at = up, ts
    if state is not None:
        known += until - at
    if state and until > at:
        out.append((at, until))
    return merge(out), known


def merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Overlapping or touching intervals as one."""
    out: list[list[float]] = []
    for a, b in sorted(intervals):
        if out and a <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def overlap(intervals: list[tuple[float, float]], a: float, b: float) -> float:
    return sum(max(0.0, min(y, b) - max(x, a)) for x, y in intervals)


def host_stats(
    intervals: list[tuple[float, float]], known: float, since: float, until: float, tz_offset: float = 0
) -> dict:
    """Availability, drops, sessions and the daily pattern of one host."""
    up = sum(b - a for a, b in intervals)
    # A drop is an end of being up before the end of the window (still up: it ends exactly at `until`)
    drops = [b for _a, b in intervals if b < until]
    downs = [(intervals[i][1], intervals[i + 1][0]) for i in range(len(intervals) - 1)]
    hours = [0.0] * 24
    week = [[0.0] * 24 for _ in range(7)]
    for a, b in intervals:
        t = a
        while t < b:
            local = t + tz_offset
            hour_end = (local // 3600 + 1) * 3600 - tz_offset
            step = min(b, hour_end) - t
            when = datetime.fromtimestamp(local, UTC)
            hours[when.hour] += step
            week[when.weekday()][when.hour] += step
            t += step
    days = max(1.0, (until - since) / DAY)
    return {
        "availability": up / known if known else None,
        "up_seconds": up,
        "known_seconds": known,
        "drops": len(drops),
        "drops_per_day": len(drops) / days,
        "sessions": len(intervals),
        "longest_down": max((b - a for a, b in downs), default=0),
        "median_session": sorted(b - a for a, b in intervals)[len(intervals) // 2] if intervals else 0,
        "hours": [h / (days * 3600) for h in hours],
        "week": [[min(1.0, v / (days / 7 * 3600)) for v in row] for row in week],
    }


def online_series(per_host: dict[str, list[tuple[float, float]]], since: float, until: float, step: float) -> dict:
    xs, ys = [], []
    t = since
    while t <= until:
        xs.append(t)
        ys.append(sum(1 for iv in per_host.values() if any(a <= t < b or (b >= until and t >= a) for a, b in iv)))
        t += step
    return {"x": xs, "y": ys}


def host_events(
    per_host: dict[str, list[tuple[float, float]]], since: float, until: float, limit: int = 200
) -> list[dict]:
    """Every host going on and off the network, newest first."""
    events = []
    for key, iv in per_host.items():
        for a, b in iv:
            if a > since:
                events.append({"ts": a, "key": key, "up": True})
            if b < until:
                events.append({"ts": b, "key": key, "up": False})
    events.sort(key=lambda e: -e["ts"])
    return events[:limit]
