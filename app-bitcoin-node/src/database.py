"""What the app keeps itself: the node's numbers over time, the latest blocks and what happened.

Bitcoin Core, LND and Tor only tell how things are now. The charts need how they were, so a sample
is stored every two minutes. No keys, no addresses and no payments are in here.
"""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# A sample this often: 720 rows a day
SAMPLE_SECONDS = 120
# Blocks kept, newest first: a day's worth
BLOCKS_KEPT = 144
EVENT_DAYS = 180

# The stored series, in the order of the columns. The pages ask for them by these names
SERIES = (
    "mempool_tx",
    "mempool_vsize",
    "fee_next",
    "fee_hour",
    "fee_day",
    "peers_in",
    "peers_out",
    "recv_rate",
    "sent_rate",
    "ln_local",
    "ln_remote",
    "ln_onchain",
    "tor_read_rate",
    "tor_written_rate",
    "size_on_disk",
)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER PRIMARY KEY,
    {", ".join(f"{name} REAL" for name in SERIES)}
);
CREATE TABLE IF NOT EXISTS blocks (
    height INTEGER PRIMARY KEY,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    ts REAL NOT NULL,
    source TEXT NOT NULL,
    type TEXT NOT NULL,
    level TEXT,
    text TEXT
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""
EVENT_COLUMNS = ("ts", "source", "type", "level", "text")


class Database:
    """A small SQLite database. Writes are few (a row per two minutes, a row per block), so it writes at once."""

    def __init__(self, path: Path, history_days: int = 90):
        self.path = Path(path)
        self.history_days = history_days
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._last_sample = 0.0
        self._pruned = 0.0

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ samples
    def sample(self, values: dict, now: float | None = None) -> bool:
        """Record the numbers of this moment, at most once per SAMPLE_SECONDS. Unknown ones stay empty."""
        now = time.time() if now is None else now
        if now - self._last_sample < SAMPLE_SECONDS:
            return False
        row = (int(now), *(values.get(name) for name in SERIES))
        with self._lock:
            self._db.execute(f"INSERT OR REPLACE INTO samples VALUES ({', '.join('?' * len(row))})", row)
            if now - self._pruned > 86400:
                self._db.execute("DELETE FROM samples WHERE ts < ?", (now - self.history_days * 86400,))
                self._db.execute("DELETE FROM events WHERE ts < ?", (now - EVENT_DAYS * 86400,))
                self._pruned = now
            self._db.commit()
        self._last_sample = now
        return True

    def history(self, start: int, end: int, points: int = 300) -> dict:
        """Every series between start and end, averaged into about `points` steps."""
        step = max(SAMPLE_SECONDS, (end - start) // max(1, points))
        columns = ", ".join(f"AVG({name})" for name in SERIES)
        with self._lock:
            rows = self._db.execute(
                f"SELECT (ts / ?) * ? AS t, {columns} FROM samples WHERE ts >= ? AND ts <= ? GROUP BY t ORDER BY t",
                (step, step, start, end),
            ).fetchall()
        out: dict = {"start": start, "end": end, "step": step, "ts": [row[0] for row in rows]}
        for index, name in enumerate(SERIES, 1):
            out[name] = [None if row[index] is None else round(row[index], 2) for row in rows]
        return out

    def growth(self, days: int = 30, now: float | None = None) -> dict | None:
        """How much the chain on disk grew in the last `days`, or since the first sample when that is later."""
        now = time.time() if now is None else now
        with self._lock:
            first = self._db.execute(
                "SELECT ts, size_on_disk FROM samples WHERE ts >= ? AND size_on_disk IS NOT NULL ORDER BY ts LIMIT 1",
                (now - days * 86400,),
            ).fetchone()
            last = self._db.execute(
                "SELECT ts, size_on_disk FROM samples WHERE size_on_disk IS NOT NULL ORDER BY ts DESC LIMIT 1"
            ).fetchone()
        if not first or not last or last[0] - first[0] < 3600:
            return None
        return {"bytes": int(last[1] - first[1]), "seconds": last[0] - first[0]}

    # ------------------------------------------------------------------ blocks
    def save_block(self, block: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO blocks VALUES (?, ?)", (block["height"], json.dumps(block)))
            self._db.execute("DELETE FROM blocks WHERE height <= ?", (block["height"] - BLOCKS_KEPT - 10,))
            self._db.commit()

    def blocks(self) -> list[dict]:
        """The stored blocks, newest first."""
        with self._lock:
            rows = self._db.execute(
                "SELECT data FROM blocks ORDER BY height DESC LIMIT ?", (BLOCKS_KEPT + 1,)
            ).fetchall()
        return [json.loads(row[0]) for row in rows]

    def forget_blocks_from(self, height: int) -> None:
        """After a reorganisation: the blocks from this height on are no longer the chain's."""
        with self._lock:
            self._db.execute("DELETE FROM blocks WHERE height >= ?", (height,))
            self._db.commit()

    # ------------------------------------------------------------------ events
    def add_event(self, event: dict) -> None:
        with self._lock:
            self._db.execute(
                f"INSERT INTO events VALUES ({', '.join('?' * len(EVENT_COLUMNS))})",
                tuple(event.get(column) for column in EVENT_COLUMNS),
            )
            self._db.commit()

    def events(self, limit: int = 100, before: float | None = None) -> list[dict]:
        """The latest events, newest first."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {', '.join(EVENT_COLUMNS)} FROM events WHERE ts < ? ORDER BY ts DESC LIMIT ?",
                (before or time.time() + 1, limit),
            ).fetchall()
        return [dict(zip(EVENT_COLUMNS, row, strict=True)) for row in rows]

    # ------------------------------------------------------------------ state
    def state(self, key: str):
        """A single remembered value, or None."""
        with self._lock:
            row = self._db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, key: str, value) -> None:
        """Remember a value over restarts. None forgets it."""
        with self._lock:
            if value is None:
                self._db.execute("DELETE FROM state WHERE key = ?", (key,))
            else:
                self._db.execute("INSERT OR REPLACE INTO state VALUES (?, ?)", (key, json.dumps(value)))
            self._db.commit()

    # ------------------------------------------------------------------ the whole
    def counts(self) -> dict:
        with self._lock:
            samples, oldest = self._db.execute("SELECT COUNT(*), MIN(ts) FROM samples").fetchone()
            (events,) = self._db.execute("SELECT COUNT(*) FROM events").fetchone()
            (blocks,) = self._db.execute("SELECT COUNT(*) FROM blocks").fetchone()
        # The write-ahead log counts too: right after many writes most of the data is still in it
        size = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
        return {"samples": samples, "events": events, "blocks": blocks, "oldest": oldest, "size": size}

    def wipe(self) -> dict:
        """Delete the samples and the events. The blocks stay: the node gives the same ones again anyway."""
        with self._lock:
            removed = {
                "samples": self._db.execute("DELETE FROM samples").rowcount,
                "events": self._db.execute("DELETE FROM events").rowcount,
            }
            self._db.commit()
            self._db.execute("VACUUM")
        self._last_sample = 0.0
        return removed

    def backup_to(self, target: Path) -> None:
        """A consistent copy of the database, while it is in use."""
        with self._lock:
            destination = sqlite3.connect(target)
            try:
                self._db.backup(destination)
            finally:
                destination.close()
