"""The broker's history in SQLite, for the Stats page.

Every minute: the messages and bytes in and out (from Mosquitto's $SYS counters), the clients,
subscriptions and retained messages, and the connects, disconnects and refused logins (from the
log). Every hour: the messages and bytes per topic group (the first level of the topic) and the
connects per client, to find the clients that keep reconnecting.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MINUTE = 60
HOUR = 3600
DAY = 86400

SYS_COUNTERS = {
    "received": "$SYS/broker/messages/received",
    "sent": "$SYS/broker/messages/sent",
    "bytes_received": "$SYS/broker/bytes/received",
    "bytes_sent": "$SYS/broker/bytes/sent",
}
SYS_GAUGES = {
    "clients": "$SYS/broker/clients/connected",
    "subscriptions": "$SYS/broker/subscriptions/count",
    "retained": "$SYS/broker/retained messages/count",
}
COLUMNS = (
    "received",
    "sent",
    "bytes_received",
    "bytes_sent",
    "clients",
    "subscriptions",
    "retained",
    "connects",
    "disconnects",
    "refused",
)
# The bucket of each period on the Stats page
BUCKETS = {1: 5 * MINUTE, 7: HOUR, 30: 4 * HOUR, 90: 12 * HOUR, 365: DAY}


def _number(text: str | None) -> float | None:
    try:
        return float(str(text).split()[0])
    except (TypeError, ValueError, IndexError):
        return None


class History:
    def __init__(self, path: Path, keep_days: int):
        self.path = path
        self.keep_days = keep_days
        self.lock = threading.Lock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS minutes (ts INTEGER PRIMARY KEY, "
            + ", ".join(f"{c} REAL" for c in COLUMNS)
            + ")"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS groups (hour INTEGER, grp TEXT, messages INTEGER, bytes INTEGER, "
            "PRIMARY KEY (hour, grp))"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS client_hours (hour INTEGER, client TEXT, connects INTEGER, "
            "PRIMARY KEY (hour, client))"
        )
        self.db.commit()
        # The $SYS counters at the last sample, to take the difference
        self._last: dict[str, float] = {}

    def close(self) -> None:
        with self.lock:
            self.db.close()

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------
    def counter_deltas(self, sys: dict[str, str]) -> dict[str, float | None]:
        """How much each $SYS counter went up since the last sample (the whole value after a broker restart)."""
        out: dict[str, float | None] = {}
        for key, topic in SYS_COUNTERS.items():
            value = _number(sys.get(topic))
            if value is None:
                out[key] = None
                continue
            last = self._last.get(key)
            out[key] = None if last is None else (value - last if value >= last else value)
            self._last[key] = value
        return out

    def record(
        self,
        ts: float,
        sys: dict[str, str],
        counts: tuple[int, int, int, dict[str, int]],
        groups: dict[str, list[int]],
    ) -> None:
        minute = int(ts // MINUTE * MINUTE)
        hour = int(ts // HOUR * HOUR)
        row = self.counter_deltas(sys)
        for key, topic in SYS_GAUGES.items():
            row[key] = _number(sys.get(topic))
        connects, disconnects, refused, by_client = counts
        row.update(connects=connects, disconnects=disconnects, refused=refused)
        with self.lock:
            self.db.execute(
                f"INSERT OR REPLACE INTO minutes (ts, {', '.join(COLUMNS)}) VALUES (?{', ?' * len(COLUMNS)})",
                (minute, *(row.get(c) for c in COLUMNS)),
            )
            self.db.executemany(
                "INSERT INTO groups (hour, grp, messages, bytes) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (hour, grp) DO UPDATE SET messages = messages + excluded.messages, bytes = bytes + excluded.bytes",
                [(hour, g, v[0], v[1]) for g, v in groups.items()],
            )
            self.db.executemany(
                "INSERT INTO client_hours (hour, client, connects) VALUES (?, ?, ?) "
                "ON CONFLICT (hour, client) DO UPDATE SET connects = connects + excluded.connects",
                [(hour, c, n) for c, n in by_client.items()],
            )
            self.db.commit()

    def prune(self, now: float | None = None) -> None:
        cutoff = (now or time.time()) - self.keep_days * DAY
        with self.lock:
            self.db.execute("DELETE FROM minutes WHERE ts < ?", (cutoff,))
            self.db.execute("DELETE FROM groups WHERE hour < ?", (cutoff,))
            self.db.execute("DELETE FROM client_hours WHERE hour < ?", (cutoff,))
            self.db.commit()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def stats(self, days: int, now: float | None = None) -> dict:
        now = now or time.time()
        bucket = BUCKETS.get(days, HOUR)
        until = int(now // bucket * bucket) + bucket
        since = until - days * DAY
        with self.lock:
            first = self.db.execute("SELECT MIN(ts) FROM minutes").fetchone()[0]
            # A history younger than the period: the charts start at its first sample, not empty
            if first is not None and first > since:
                since = int(first // bucket * bucket)
            rows = self.db.execute(
                f"SELECT ts / {bucket} * {bucket} AS b, COUNT(*), "
                "SUM(received), SUM(sent), SUM(bytes_received), SUM(bytes_sent), "
                "AVG(clients), MAX(clients), AVG(subscriptions), AVG(retained), "
                "SUM(connects), SUM(disconnects), SUM(refused) "
                "FROM minutes WHERE ts >= ? GROUP BY b ORDER BY b",
                (since,),
            ).fetchall()
            groups = self.db.execute(
                "SELECT grp, SUM(messages), SUM(bytes) FROM groups WHERE hour >= ? GROUP BY grp ORDER BY 2 DESC LIMIT 25",
                (since,),
            ).fetchall()
            group_hours = self.db.execute(
                f"SELECT hour / {max(bucket, HOUR)} * {max(bucket, HOUR)} AS b, grp, SUM(messages) FROM groups "
                "WHERE hour >= ? GROUP BY b, grp ORDER BY b",
                (since,),
            ).fetchall()
            flapping = self.db.execute(
                "SELECT client, SUM(connects) FROM client_hours WHERE hour >= ? GROUP BY client ORDER BY 2 DESC LIMIT 20",
                (since,),
            ).fetchall()
        by_bucket = {r[0]: r for r in rows}
        xs = list(range(since, until, bucket))

        def series(index: int, per_second: bool = False) -> list[float | None]:
            out = []
            for x in xs:
                r = by_bucket.get(x)
                if r is None or r[index] is None:
                    out.append(None)
                else:
                    # A rate over the minutes that have a sample
                    out.append(r[index] / (r[1] * MINUTE) if per_second else r[index])
            return out

        top = [g for g, _, _ in groups[:6]]
        group_series: dict[str, dict[int, float]] = {g: {} for g in top}
        group_bucket = max(bucket, HOUR)
        for b, g, n in group_hours:
            if g in group_series:
                group_series[g][b] = group_series[g].get(b, 0) + n
        gx = list(range(since // group_bucket * group_bucket, until, group_bucket))
        return {
            "days": days,
            "since": since,
            "until": until,
            "bucket": bucket,
            "first": first,
            "x": xs,
            "received": series(2, True),
            "sent": series(3, True),
            "bytes_received": series(4, True),
            "bytes_sent": series(5, True),
            "clients": series(6),
            "clients_max": series(7),
            "subscriptions": series(8),
            "retained": series(9),
            "connects": series(10),
            "disconnects": series(11),
            "refused": series(12),
            "totals": {
                "received": sum(r[2] or 0 for r in rows),
                "sent": sum(r[3] or 0 for r in rows),
                "bytes_received": sum(r[4] or 0 for r in rows),
                "bytes_sent": sum(r[5] or 0 for r in rows),
                "connects": sum(r[10] or 0 for r in rows),
                "disconnects": sum(r[11] or 0 for r in rows),
                "refused": sum(r[12] or 0 for r in rows),
                "clients_max": max((r[7] or 0 for r in rows), default=None) if rows else None,
            },
            "groups": [{"group": g, "messages": m, "bytes": b} for g, m, b in groups],
            "group_series": {
                "bucket": group_bucket,
                "x": gx,
                "groups": {g: [s.get(x, 0) / group_bucket for x in gx] for g, s in group_series.items()},
            },
            "flapping": [{"client": c, "connects": n} for c, n in flapping if n > 1],
        }
