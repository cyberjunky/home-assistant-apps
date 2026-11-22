"""The app's own history, in SQLite.

samples: each miner's readings once a minute, kept for SAMPLE_DAYS, for the charts.
bests:   every new best share of a session, kept for good: the lottery tickets that came closest.
days:    per miner per day the work done (hashes), energy, best share, shares and time mining.
events:  what happened (paused, restarted, a new best, a pool lost), the newest MAX_EVENTS.
state:   the counters a miner keeps across restarts of the app (work done, energy, all-time best).

Calls are short; the web server runs them in a thread.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

SAMPLE_DAYS = 31
MAX_EVENTS = 5000
SAMPLE_COLUMNS = ("hashrate", "power", "temp", "vr_temp", "fan", "mining")

SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    ts INTEGER NOT NULL, miner TEXT NOT NULL,
    hashrate REAL, power REAL, temp REAL, vr_temp REAL, fan REAL, mining INTEGER
);
CREATE INDEX IF NOT EXISTS samples_ts ON samples (ts);
CREATE TABLE IF NOT EXISTS bests (ts INTEGER NOT NULL, miner TEXT NOT NULL, diff REAL NOT NULL);
CREATE INDEX IF NOT EXISTS bests_ts ON bests (ts);
CREATE TABLE IF NOT EXISTS days (
    day TEXT NOT NULL, miner TEXT NOT NULL,
    hashes REAL NOT NULL DEFAULT 0, kwh REAL NOT NULL DEFAULT 0, best REAL,
    shares INTEGER NOT NULL DEFAULT 0, mining_s REAL NOT NULL DEFAULT 0, network REAL,
    PRIMARY KEY (day, miner)
);
CREATE TABLE IF NOT EXISTS events (
    ts REAL NOT NULL, miner TEXT NOT NULL, kind TEXT NOT NULL, level TEXT NOT NULL, text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE TABLE IF NOT EXISTS state (miner TEXT PRIMARY KEY, data TEXT NOT NULL);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def _write(self, sql: str, params: tuple = ()) -> None:
        with self._lock:
            self._db.execute(sql, params)
            self._db.commit()

    def _read(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._lock:
            return [dict(row) for row in self._db.execute(sql, params).fetchall()]

    # --- writes -------------------------------------------------------
    def add_sample(self, ts: int, miner: str, values: dict) -> None:
        self._write(
            "INSERT INTO samples (ts, miner, hashrate, power, temp, vr_temp, fan, mining) VALUES (?,?,?,?,?,?,?,?)",
            (ts, miner, *(values.get(column) for column in SAMPLE_COLUMNS)),
        )

    def add_best(self, ts: int, miner: str, diff: float) -> None:
        self._write("INSERT INTO bests (ts, miner, diff) VALUES (?,?,?)", (ts, miner, diff))

    def add_event(self, ts: float, miner: str, kind: str, level: str, text: str) -> None:
        self._write(
            "INSERT INTO events (ts, miner, kind, level, text) VALUES (?,?,?,?,?)", (ts, miner, kind, level, text)
        )

    def add_day(
        self,
        day: str,
        miner: str,
        *,
        hashes: float = 0,
        kwh: float = 0,
        best: float | None = None,
        shares: int = 0,
        mining_s: float = 0,
        network: float | None = None,
    ) -> None:
        """Add to a miner's totals of the day; the best share and network difficulty keep the highest."""
        self._write(
            """INSERT INTO days (day, miner, hashes, kwh, best, shares, mining_s, network) VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT (day, miner) DO UPDATE SET
                 hashes = hashes + excluded.hashes, kwh = kwh + excluded.kwh,
                 best = MAX(COALESCE(best, 0), COALESCE(excluded.best, 0)),
                 shares = shares + excluded.shares, mining_s = mining_s + excluded.mining_s,
                 network = COALESCE(excluded.network, network)""",
            (day, miner, hashes, kwh, best, shares, mining_s, network),
        )

    def save_state(self, miner: str, data: dict) -> None:
        self._write(
            "INSERT INTO state (miner, data) VALUES (?,?) ON CONFLICT (miner) DO UPDATE SET data = excluded.data",
            (miner, json.dumps(data)),
        )

    def load_state(self, miner: str) -> dict:
        rows = self._read("SELECT data FROM state WHERE miner = ?", (miner,))
        try:
            data = json.loads(rows[0]["data"]) if rows else {}
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def prune(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            self._db.execute("DELETE FROM samples WHERE ts < ?", (int(now - SAMPLE_DAYS * 86400),))
            self._db.execute(
                "DELETE FROM events WHERE rowid NOT IN (SELECT rowid FROM events ORDER BY ts DESC LIMIT ?)",
                (MAX_EVENTS,),
            )
            self._db.commit()

    # --- reads --------------------------------------------------------
    def history(self, start: int, end: int, miner: str | None = None, points: int = 360) -> dict:
        """Averages per time bucket, at most about `points` per miner, oldest first."""
        bucket = max(60, (end - start) // max(1, points) // 60 * 60)
        where, params = "ts >= ? AND ts <= ?", [start, end]
        if miner:
            where += " AND miner = ?"
            params.append(miner)
        columns = ", ".join(f"AVG({column}) AS {column}" for column in SAMPLE_COLUMNS)
        rows = self._read(
            f"SELECT miner, (ts / {bucket}) * {bucket} AS ts, {columns} FROM samples WHERE {where}"
            f" GROUP BY miner, ts / {bucket} ORDER BY ts",
            tuple(params),
        )
        return {"bucket": bucket, "start": start, "end": end, "rows": rows}

    def bests(self, limit: int = 50, miner: str | None = None) -> list[dict]:
        """The highest best shares, highest first."""
        if miner:
            return self._read("SELECT * FROM bests WHERE miner = ? ORDER BY diff DESC LIMIT ?", (miner, limit))
        return self._read("SELECT * FROM bests ORDER BY diff DESC LIMIT ?", (limit,))

    def days(self, since: str, miner: str | None = None) -> list[dict]:
        if miner:
            return self._read("SELECT * FROM days WHERE day >= ? AND miner = ? ORDER BY day", (since, miner))
        return self._read("SELECT * FROM days WHERE day >= ? ORDER BY day", (since,))

    def events(self, limit: int = 100, miner: str | None = None, before: float | None = None) -> list[dict]:
        where, params = "1 = 1", []
        if miner:
            where += " AND miner = ?"
            params.append(miner)
        if before is not None:
            where += " AND ts < ?"
            params.append(before)
        return self._read(f"SELECT * FROM events WHERE {where} ORDER BY ts DESC LIMIT ?", (*params, limit))

    def counts(self) -> dict:
        out = {}
        for table in ("samples", "bests", "days", "events"):
            out[table] = self._read(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"]
        first = self._read("SELECT MIN(day) AS day FROM days")[0]["day"]
        out["since"] = first
        return out

    def wipe(self) -> dict:
        """Delete all history, and the counters. Returns what was removed."""
        removed = self.counts()
        removed.pop("since", None)
        with self._lock:
            for table in ("samples", "bests", "days", "events", "state"):
                self._db.execute(f"DELETE FROM {table}")
            self._db.commit()
            self._db.execute("VACUUM")
        return removed

    def backup_to(self, path: Path) -> None:
        with self._lock:
            target = sqlite3.connect(path)
            try:
                self._db.backup(target)
            finally:
                target.close()
