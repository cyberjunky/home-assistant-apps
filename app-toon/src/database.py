"""SQLite storage: samples, hourly values, connection history and import progress.

Samples hold every change for a limited time. Hourly values are kept for good and also receive the
hourly and daily history read from the Toon.
"""

import logging
import sqlite3
import time
from pathlib import Path

from .config import DATA_DIR

logger = logging.getLogger(__name__)

DB_FILE = DATA_DIR / "toon.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS samples (
    metric TEXT NOT NULL, ts INTEGER NOT NULL, value REAL NOT NULL, PRIMARY KEY (metric, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS hourly (
    metric TEXT NOT NULL, ts INTEGER NOT NULL, avg REAL NOT NULL, min REAL NOT NULL, max REAL NOT NULL,
    last REAL NOT NULL, PRIMARY KEY (metric, ts)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS outages (
    id INTEGER PRIMARY KEY, start_ts INTEGER NOT NULL, end_ts INTEGER, kind TEXT, reason TEXT,
    toon_uptime INTEGER, memory_free REAL, memory_gui REAL
);
CREATE TABLE IF NOT EXISTS import_progress (
    logger TEXT NOT NULL, rra TEXT NOT NULL, next_ts INTEGER NOT NULL, done INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (logger, rra)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS samples_ts ON samples (ts);
"""
HOUR = 3600
# Above this range a chart reads the hourly values instead of the samples
SAMPLES_RANGE_LIMIT = 3 * 86400
# Values where a reading of 0 isn't a measurement
NO_ZERO_READINGS = ("boiler_pressure",)


def backup_copy(source: Path, target: Path) -> None:
    """A consistent copy of the database while the app keeps writing. Runs on its own connection."""
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as from_db, sqlite3.connect(target) as to_db:
        from_db.backup(to_db)
    to_db.close()
    from_db.close()


def database_report(source: Path) -> dict:
    """What's in the database, for the Settings page. Runs on its own connection."""
    db = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    try:
        metrics: dict[str, dict] = {}
        for table in ("samples", "hourly"):
            for metric, rows, first, last in db.execute(
                f"SELECT metric, COUNT(*), MIN(ts), MAX(ts) FROM {table} GROUP BY metric"
            ):
                entry = metrics.setdefault(
                    metric, {"metric": metric, "samples": 0, "hourly": 0, "first": first, "last": last}
                )
                entry[table] = rows
                entry["first"] = min(entry["first"], first)
                entry["last"] = max(entry["last"], last)
        wal = Path(f"{source}-wal")
        return {
            "size": source.stat().st_size,
            "wal_size": wal.stat().st_size if wal.exists() else 0,
            "outages": db.execute("SELECT COUNT(*) FROM outages").fetchone()[0],
            "metrics": sorted(metrics.values(), key=lambda m: m["samples"] + m["hourly"], reverse=True),
        }
    finally:
        db.close()


class Database:
    """The app's database. All calls come from the event loop and are short."""

    def __init__(self, path: Path = DB_FILE):
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._db.commit()
        self._pending: list[tuple[str, int, float]] = []

    def close(self) -> None:
        self.flush()
        self._db.close()

    # Samples

    def record(self, metric: str, ts: float, value: float) -> None:
        """Queue a sample; flush() writes the queue in one transaction."""
        self._pending.append((metric, int(ts), value))

    def flush(self) -> None:
        if self._pending:
            pending, self._pending = self._pending, []
            self._db.executemany("INSERT OR REPLACE INTO samples VALUES (?, ?, ?)", pending)
            self._db.commit()

    def sample_times(self, metric: str, start: int, end: int) -> list[int]:
        """When a metric has samples, strictly between start and end."""
        self.flush()
        rows = self._db.execute(
            "SELECT ts FROM samples WHERE metric = ? AND ts > ? AND ts < ? ORDER BY ts", (metric, start, end)
        ).fetchall()
        return [row[0] for row in rows]

    def value_before(self, metric: str, ts: int) -> float | None:
        """A metric's last sample at or before a moment."""
        self.flush()
        row = self._db.execute(
            "SELECT value FROM samples WHERE metric = ? AND ts <= ? ORDER BY ts DESC LIMIT 1", (metric, ts)
        ).fetchone()
        return row[0] if row else None

    def add_samples(self, rows: list[tuple[str, int, float]]) -> None:
        """Samples from the Toon's history; what was recorded live stays as it is."""
        self._db.executemany("INSERT OR IGNORE INTO samples VALUES (?, ?, ?)", rows)
        self._db.commit()

    def add_hourly(self, rows: list[tuple[str, int, float]]) -> None:
        """Hourly or daily values from the Toon's history; existing hours stay as they are."""
        self._db.executemany(
            "INSERT OR IGNORE INTO hourly VALUES (?, ?, ?, ?, ?, ?)",
            [(metric, ts, value, value, value, value) for metric, ts, value in rows],
        )
        self._db.commit()

    def roll_up(self, since: int) -> None:
        """Compute the hourly values of the completed hours since a moment from the samples."""
        self.flush()
        end = int(time.time()) // HOUR * HOUR
        self._db.execute(
            """
            INSERT OR REPLACE INTO hourly
            SELECT metric, ts / 3600 * 3600 AS hour, AVG(value), MIN(value), MAX(value),
                   (SELECT s2.value FROM samples s2 WHERE s2.metric = s.metric AND s2.ts / 3600 = s.ts / 3600
                    ORDER BY s2.ts DESC LIMIT 1)
            FROM samples s WHERE ts >= ? AND ts < ? GROUP BY metric, hour
            """,
            (since // HOUR * HOUR, end),
        )
        self._db.commit()

    def prune(self, retention_days: int) -> None:
        """Remove samples past the retention; their hours stay in the hourly values."""
        cutoff = int(time.time()) - retention_days * 86400
        self.roll_up(cutoff - 2 * HOUR)
        self._db.execute("DELETE FROM samples WHERE ts < ?", (cutoff,))
        self._db.commit()

    def history(self, metrics: list[str], start: int, end: int, points: int = 600) -> dict[str, list]:
        """Each metric's values in a time range, averaged down to about this many points."""
        self.flush()
        bucket = max(1, (end - start) // points)
        table = "samples" if end - start <= SAMPLES_RANGE_LIMIT else "hourly"
        column = "value" if table == "samples" else "avg"
        result: dict[str, list] = {}
        for metric in metrics:
            floor = f" AND {column} > 0" if metric in NO_ZERO_READINGS else ""
            rows = self._db.execute(
                f"SELECT ts / ? * ? AS b, AVG({column}) FROM {table}"
                f" WHERE metric = ? AND ts >= ? AND ts <= ?{floor} GROUP BY b ORDER BY b",
                (bucket, bucket, metric, start, end),
            ).fetchall()
            result[metric] = [[ts, round(value, 3)] for ts, value in rows]
        return result

    def wipe(self) -> dict[str, int]:
        """Empty every table: the Settings danger zone. Returns what was there. The file stays and the space
        goes back to the disk. Samples not written out yet go too; the import positions are cleared, so the
        Toon's own history can be fetched again."""
        self._pending = []
        removed = {
            "samples": self._db.execute("SELECT COUNT(*) FROM samples").fetchone()[0],
            "hourly": self._db.execute("SELECT COUNT(*) FROM hourly").fetchone()[0],
            "outages": self._db.execute("SELECT COUNT(*) FROM outages").fetchone()[0],
        }
        for table in ("samples", "hourly", "outages", "import_progress"):
            self._db.execute(f"DELETE FROM {table}")
        self._db.commit()
        self._db.execute("VACUUM")
        # In WAL mode the freed space only returns once the log is merged and cut down
        self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return removed

    def counts(self) -> dict[str, int]:
        samples = self._db.execute("SELECT COUNT(*) FROM samples").fetchone()[0]
        hourly = self._db.execute("SELECT COUNT(*) FROM hourly").fetchone()[0]
        oldest = self._db.execute("SELECT MIN(ts) FROM hourly").fetchone()[0]
        return {"samples": samples, "hourly": hourly, "oldest": oldest, "size": self.path.stat().st_size}

    # Connection history

    def open_outage(self, start_ts: float, reason: str, memory_free: float | None, memory_gui: float | None) -> None:
        if self.current_outage() is None:
            self._db.execute(
                "INSERT INTO outages (start_ts, reason, memory_free, memory_gui) VALUES (?, ?, ?, ?)",
                (int(start_ts), reason[:200], memory_free, memory_gui),
            )
            self._db.commit()

    def current_outage(self) -> dict | None:
        row = self._db.execute("SELECT id, start_ts FROM outages WHERE end_ts IS NULL ORDER BY id DESC").fetchone()
        return {"id": row[0], "start_ts": row[1]} if row else None

    def close_outage(self, end_ts: float, kind: str, toon_uptime: float | None = None) -> None:
        self._db.execute(
            "UPDATE outages SET end_ts = ?, kind = ?, toon_uptime = ? WHERE end_ts IS NULL",
            (int(end_ts), kind, None if toon_uptime is None else int(toon_uptime)),
        )
        self._db.commit()

    def outages(self, limit: int = 100) -> list[dict]:
        rows = self._db.execute(
            "SELECT start_ts, end_ts, kind, reason, toon_uptime, memory_free, memory_gui FROM outages"
            " ORDER BY start_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        names = ("start_ts", "end_ts", "kind", "reason", "toon_uptime", "memory_free", "memory_gui")
        return [dict(zip(names, row, strict=True)) for row in rows]

    # History import

    def import_position(self, logger_name: str, rra: str) -> tuple[int | None, bool]:
        row = self._db.execute(
            "SELECT next_ts, done FROM import_progress WHERE logger = ? AND rra = ?", (logger_name, rra)
        ).fetchone()
        return (row[0], bool(row[1])) if row else (None, False)

    def set_import_position(self, logger_name: str, rra: str, next_ts: int, done: bool) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO import_progress VALUES (?, ?, ?, ?)", (logger_name, rra, next_ts, int(done))
        )
        self._db.commit()
