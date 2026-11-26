"""SQLite storage.

Every answer from Garmin is kept as it came (compressed, in `raw`), so a later version of the app can
read more out of it without asking Garmin again. What the dashboard uses is read out of those answers
into plain tables: one value per metric per day, activities, their tracks, the day curves and events
(weigh-ins, ECGs, badges, ...).
"""

import json
import logging
import sqlite3
import time
import zlib
from pathlib import Path
from typing import Any

from .config import DATA_DIR
from .sports import family

logger = logging.getLogger(__name__)

DB_FILE = DATA_DIR / "garmin.db"
SCHEMA = """
CREATE TABLE IF NOT EXISTS raw (
    dataset TEXT NOT NULL, key TEXT NOT NULL, fetched_at INTEGER NOT NULL, body BLOB NOT NULL,
    PRIMARY KEY (dataset, key)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS daily (
    metric TEXT NOT NULL, day TEXT NOT NULL, value REAL NOT NULL, PRIMARY KEY (metric, day)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS daily_day ON daily (day);
CREATE TABLE IF NOT EXISTS activities (
    id INTEGER PRIMARY KEY, start_ts INTEGER NOT NULL, start_local TEXT NOT NULL, day TEXT NOT NULL,
    type TEXT, parent_type TEXT, name TEXT, distance_m REAL, duration_s REAL, moving_s REAL,
    elevation_gain_m REAL, avg_hr REAL, max_hr REAL, calories REAL, avg_speed REAL, max_speed REAL,
    steps INTEGER, training_load REAL, aerobic_te REAL, anaerobic_te REAL, vo2max REAL,
    start_lat REAL, start_lon REAL, location TEXT, has_track INTEGER NOT NULL DEFAULT 0,
    body_battery REAL, body BLOB
);
CREATE INDEX IF NOT EXISTS activities_start ON activities (start_ts);
CREATE TABLE IF NOT EXISTS tracks (
    activity_id INTEGER PRIMARY KEY, fetched_at INTEGER NOT NULL, points TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visited_cells (
    activity_id INTEGER NOT NULL, day TEXT NOT NULL, family TEXT NOT NULL, lat REAL NOT NULL, lon REAL NOT NULL,
    PRIMARY KEY (activity_id, lat, lon)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS visited_cells_day ON visited_cells (day);
CREATE INDEX IF NOT EXISTS visited_cells_cell ON visited_cells (lat, lon, day);
CREATE TABLE IF NOT EXISTS mapped_tracks (
    activity_id INTEGER PRIMARY KEY, cells INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS activity_details (
    activity_id INTEGER PRIMARY KEY, fetched_at INTEGER NOT NULL, body BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS intraday (
    kind TEXT NOT NULL, day TEXT NOT NULL, data TEXT NOT NULL, PRIMARY KEY (kind, day)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS events (
    kind TEXT NOT NULL, id TEXT NOT NULL, ts INTEGER, day TEXT, body TEXT NOT NULL, PRIMARY KEY (kind, id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS snapshots (
    name TEXT PRIMARY KEY, fetched_at INTEGER NOT NULL, body BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS coverage (
    dataset TEXT PRIMARY KEY, newest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS progress (
    dataset TEXT PRIMARY KEY, oldest TEXT, done INTEGER NOT NULL DEFAULT 0, last_fetch INTEGER,
    last_error TEXT, rows INTEGER NOT NULL DEFAULT 0
);
"""


# A cell is a position rounded to 3 decimals: about 110 m north-south, 70 m east-west here
CELL_DIGITS = 3
# Between two track points further apart than this (degrees), cells are filled in along the line
CELL_STEP = 0.0005


def track_cells(points: list) -> set[tuple[float, float]]:
    """The cells a track passes through, also between points that lie far apart."""
    cells: set[tuple[float, float]] = set()
    previous = None
    for point in points:
        lat, lon = float(point[0]), float(point[1])
        if previous is not None:
            steps = int(max(abs(lat - previous[0]), abs(lon - previous[1])) / CELL_STEP)
            # A jump of more than a few km is a gap in the recording, not a road
            if 0 < steps < 200:
                for i in range(1, steps):
                    f = i / steps
                    cells.add(
                        (
                            round(previous[0] + (lat - previous[0]) * f, CELL_DIGITS),
                            round(previous[1] + (lon - previous[1]) * f, CELL_DIGITS),
                        )
                    )
        cells.add((round(lat, CELL_DIGITS), round(lon, CELL_DIGITS)))
        previous = (lat, lon)
    return cells


def pack(value: Any) -> bytes:
    return zlib.compress(json.dumps(value, separators=(",", ":"), default=str).encode(), 6)


def unpack(blob: bytes | None) -> Any:
    return json.loads(zlib.decompress(blob)) if blob else None


def backup_copy(source: Path, target: Path) -> None:
    """A consistent copy of the database while the app keeps writing. Runs on its own connection."""
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as from_db, sqlite3.connect(target) as to_db:
        from_db.backup(to_db)
    to_db.close()
    from_db.close()


class Database:
    """The app's database. All calls come from the event loop and are short."""

    def __init__(self, path: Path = DB_FILE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._db.commit()
        # Bumped on every write, so cached statistics know when to be computed again
        self.generation = 0

    def close(self) -> None:
        self._db.close()

    def _changed(self) -> None:
        self._db.commit()
        self.generation += 1

    # Generic reads

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(row) for row in self._db.execute(sql, params).fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> dict:
        row = self._db.execute(sql, params).fetchone()
        return dict(row) if row else {}

    def scalar(self, sql: str, params: tuple = ()) -> Any:
        row = self._db.execute(sql, params).fetchone()
        return row[0] if row else None

    # Raw answers

    def put_raw(self, dataset: str, key: str, body: Any) -> None:
        self._db.execute("INSERT OR REPLACE INTO raw VALUES (?, ?, ?, ?)", (dataset, key, int(time.time()), pack(body)))

    def raw_items(self, dataset: str) -> list[tuple[str, Any]]:
        rows = self._db.execute(
            "SELECT key, body FROM raw WHERE dataset = ? ORDER BY fetched_at, key", (dataset,)
        ).fetchall()
        return [(row[0], unpack(row[1])) for row in rows]

    # Values per day

    def put_daily(self, rows: list[tuple[str, str, float]]) -> int:
        """(metric, day, value) rows; a new answer for a day replaces the old value."""
        clean = [(m, d, float(v)) for m, d, v in rows if v is not None and d and _finite(v)]
        if clean:
            self._db.executemany("INSERT OR REPLACE INTO daily VALUES (?, ?, ?)", clean)
        return len(clean)

    def series(self, metrics: list[str], start: str, end: str) -> dict[str, dict[str, float]]:
        """Each metric's values by day, both ends included."""
        result: dict[str, dict[str, float]] = {m: {} for m in metrics}
        if not metrics:
            return result
        marks = ",".join("?" * len(metrics))
        for row in self._db.execute(
            f"SELECT metric, day, value FROM daily WHERE metric IN ({marks}) AND day >= ? AND day <= ? ORDER BY day",
            (*metrics, start, end),
        ):
            result[row[0]][row[1]] = row[2]
        return result

    def latest(self, metric: str, on_or_before: str) -> tuple[str, float] | None:
        row = self._db.execute(
            "SELECT day, value FROM daily WHERE metric = ? AND day <= ? ORDER BY day DESC LIMIT 1",
            (metric, on_or_before),
        ).fetchone()
        return (row[0], row[1]) if row else None

    # Activities

    def put_activities(self, rows: list[dict]) -> int:
        for row in rows:
            body = row.pop("body", None)
            columns = ", ".join(row)
            marks = ", ".join("?" * len(row))
            updates = ", ".join(f"{c} = excluded.{c}" for c in row if c != "id")
            self._db.execute(
                f"INSERT INTO activities ({columns}, body) VALUES ({marks}, ?)"
                f" ON CONFLICT(id) DO UPDATE SET {updates}, body = excluded.body",
                (*row.values(), pack(body)),
            )
        return len(rows)

    def activities_without_track(self, limit: int) -> list[int]:
        rows = self._db.execute(
            "SELECT a.id FROM activities a LEFT JOIN tracks t ON t.activity_id = a.id"
            " WHERE a.has_track = 1 AND t.activity_id IS NULL ORDER BY a.start_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [row[0] for row in rows]

    def put_track(self, activity_id: int, points: list[list[float]]) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO tracks VALUES (?, ?, ?)",
            (activity_id, int(time.time()), json.dumps(points, separators=(",", ":"))),
        )

    # Where you've been: the ~100 m cells every track passed through, for the heat maps. Reading
    # these instead of the tracks keeps a heat map of years quick.

    def map_track_cells(self, activity_id: int) -> int:
        """Record the cells an activity's track passed through. Returns how many."""
        activity = self.query_one("SELECT day, type FROM activities WHERE id = ?", (activity_id,))
        points = self.track(activity_id) or []
        cells = track_cells(points) if activity else set()
        if cells:
            kind = family(activity["type"])
            self._db.executemany(
                "INSERT OR IGNORE INTO visited_cells VALUES (?, ?, ?, ?, ?)",
                [(activity_id, activity["day"], kind, lat, lon) for lat, lon in cells],
            )
        self._db.execute("INSERT OR REPLACE INTO mapped_tracks VALUES (?, ?)", (activity_id, len(cells)))
        return len(cells)

    def unmapped_tracks(self, limit: int = 1000000) -> list[int]:
        rows = self._db.execute(
            "SELECT t.activity_id FROM tracks t LEFT JOIN mapped_tracks m ON m.activity_id = t.activity_id"
            " WHERE m.activity_id IS NULL ORDER BY t.activity_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [row[0] for row in rows]

    def activities_without_details(self, limit: int) -> list[int]:
        rows = self._db.execute(
            "SELECT a.id FROM activities a LEFT JOIN activity_details d ON d.activity_id = a.id"
            " WHERE d.activity_id IS NULL ORDER BY a.start_ts DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [row[0] for row in rows]

    def put_activity_details(self, activity_id: int, body: Any) -> None:
        """Garmin's answers about one activity (splits, samples, weather, sets), kept whole."""
        self._db.execute(
            "INSERT OR REPLACE INTO activity_details VALUES (?, ?, ?)", (activity_id, int(time.time()), pack(body))
        )

    def activity_details(self, activity_id: int) -> Any:
        row = self._db.execute("SELECT body FROM activity_details WHERE activity_id = ?", (activity_id,)).fetchone()
        return unpack(row[0]) if row else None

    def activity_body(self, activity_id: int) -> Any:
        row = self._db.execute("SELECT body FROM activities WHERE id = ?", (activity_id,)).fetchone()
        return unpack(row[0]) if row else None

    def track(self, activity_id: int) -> list | None:
        row = self._db.execute("SELECT points FROM tracks WHERE activity_id = ?", (activity_id,)).fetchone()
        return json.loads(row[0]) if row else None

    # Day curves (heart rate, stress, body battery through the day)

    def put_intraday(self, kind: str, day: str, data: list) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO intraday VALUES (?, ?, ?)", (kind, day, json.dumps(data, separators=(",", ":")))
        )

    def intraday(self, kind: str, start: str, end: str) -> list[tuple[str, list]]:
        rows = self._db.execute(
            "SELECT day, data FROM intraday WHERE kind = ? AND day >= ? AND day <= ? ORDER BY day", (kind, start, end)
        ).fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]

    # Events: weigh-ins, blood pressure, ECGs, Health Snapshots, badges, ...

    def put_events(self, kind: str, rows: list[tuple[str, int | None, str | None, Any]]) -> int:
        """(id, ts, day, body) rows of one kind."""
        self._db.executemany(
            "INSERT OR REPLACE INTO events VALUES (?, ?, ?, ?, ?)",
            [(kind, str(i), ts, day, json.dumps(body, default=str)) for i, ts, day, body in rows],
        )
        return len(rows)

    def events(self, kind: str, start: str = "", end: str = "9999", limit: int = 5000) -> list[dict]:
        rows = self._db.execute(
            "SELECT id, ts, day, body FROM events WHERE kind = ? AND COALESCE(day, '') >= ? AND COALESCE(day, '') <= ?"
            " ORDER BY ts DESC LIMIT ?",
            (kind, start, end, limit),
        ).fetchall()
        return [{"id": r[0], "ts": r[1], "day": r[2], **json.loads(r[3])} for r in rows]

    # Snapshots: what only exists as "now" (gear, devices, records, lifetime totals)

    def put_snapshot(self, name: str, body: Any) -> None:
        self._db.execute("INSERT OR REPLACE INTO snapshots VALUES (?, ?, ?)", (name, int(time.time()), pack(body)))

    def snapshot(self, name: str) -> tuple[Any, int | None]:
        row = self._db.execute("SELECT body, fetched_at FROM snapshots WHERE name = ?", (name,)).fetchone()
        return (unpack(row[0]), row[1]) if row else (None, None)

    def snapshot_age(self, name: str) -> float:
        row = self._db.execute("SELECT fetched_at FROM snapshots WHERE name = ?", (name,)).fetchone()
        return time.time() - row[0] if row else float("inf")

    # History import progress per data set

    def progress(self, dataset: str) -> dict:
        return self.query_one("SELECT * FROM progress WHERE dataset = ?", (dataset,)) or {
            "dataset": dataset,
            "oldest": None,
            "done": 0,
            "last_fetch": None,
            "last_error": None,
            "rows": 0,
        }

    def newest(self, dataset: str) -> str | None:
        """The day up to which a data set is complete, without gaps after it."""
        row = self._db.execute("SELECT newest FROM coverage WHERE dataset = ?", (dataset,)).fetchone()
        return row[0] if row else None

    def set_newest(self, dataset: str, day: str) -> None:
        self._db.execute("INSERT OR REPLACE INTO coverage VALUES (?, ?)", (dataset, day))

    def all_progress(self) -> dict[str, dict]:
        return {
            row["dataset"]: row
            for row in self.query("SELECT p.*, c.newest FROM progress p LEFT JOIN coverage c ON c.dataset = p.dataset")
        }

    def set_progress(self, dataset: str, **fields: Any) -> None:
        current = self.progress(dataset)
        current.update(fields)
        self._db.execute(
            "INSERT OR REPLACE INTO progress VALUES (?, ?, ?, ?, ?, ?)",
            (
                dataset,
                current["oldest"],
                int(current["done"]),
                current["last_fetch"],
                current["last_error"],
                current["rows"],
            ),
        )

    def commit(self) -> None:
        self._changed()

    # Maintenance

    def counts(self) -> dict:
        return self.query_one(
            "SELECT (SELECT COUNT(DISTINCT day) FROM daily) AS days, (SELECT COUNT(*) FROM daily) AS values_,"
            " (SELECT COUNT(*) FROM activities) AS activities, (SELECT COUNT(*) FROM tracks) AS tracks,"
            " (SELECT COUNT(*) FROM activity_details) AS details,"
            " (SELECT COUNT(*) FROM intraday) AS curves, (SELECT COUNT(*) FROM events) AS events,"
            " (SELECT COUNT(*) FROM raw) AS answers, (SELECT MIN(day) FROM daily) AS first_day,"
            " (SELECT MAX(day) FROM daily) AS last_day"
        )

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    def wipe(self) -> None:
        """Empty every table (the Settings danger zone). The history import starts over."""
        for table in (
            "raw",
            "daily",
            "activities",
            "tracks",
            "activity_details",
            "visited_cells",
            "mapped_tracks",
            "intraday",
            "events",
            "snapshots",
            "progress",
            "coverage",
        ):
            self._db.execute(f"DELETE FROM {table}")
        self._changed()
        self._db.execute("VACUUM")
        self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")


def _finite(value: Any) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return number == number and number not in (float("inf"), float("-inf"))
