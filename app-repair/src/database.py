"""What the app keeps itself: the score of each scan for the trend, what you chose to ignore, and
what it changed. Nothing of Home Assistant's own data is stored here.
"""

import json
import sqlite3
import threading
import time
from pathlib import Path

SCAN_DAYS = 365
ACTION_DAYS = 365

SCHEMA = """
CREATE TABLE IF NOT EXISTS scans (
    ts INTEGER PRIMARY KEY,
    score INTEGER NOT NULL,
    total INTEGER NOT NULL,
    high INTEGER NOT NULL,
    medium INTEGER NOT NULL,
    low INTEGER NOT NULL,
    categories TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ignored (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    ts INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    ts REAL NOT NULL,
    fix TEXT NOT NULL,
    subject TEXT NOT NULL,
    ok INTEGER NOT NULL,
    steps TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS actions_ts ON actions (ts);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ------------------------------------------------------------------ scans
    def add_scan(self, ts: float, score: int, counts: dict) -> None:
        severity = counts["severity"]
        row = (
            int(ts),
            score,
            counts["total"],
            severity["high"],
            severity["medium"],
            severity["low"],
            json.dumps(counts["category"]),
        )
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO scans VALUES (?, ?, ?, ?, ?, ?, ?)", row)
            self._db.execute("DELETE FROM scans WHERE ts < ?", (ts - SCAN_DAYS * 86400,))
            self._db.execute("DELETE FROM actions WHERE ts < ?", (ts - ACTION_DAYS * 86400,))
            self._db.commit()

    def trend(self, start: float, points: int = 200) -> dict:
        """The score and the number of findings since start, the last scan of each step."""
        step = max(60, int((time.time() - start) // max(1, points)))
        with self._lock:
            rows = self._db.execute(
                "SELECT MAX(ts), score, total, high, medium, low FROM scans WHERE ts >= ? GROUP BY ts / ? ORDER BY ts",
                (int(start), step),
            ).fetchall()
        names = ("ts", "score", "total", "high", "medium", "low")
        return {name: [row[i] for row in rows] for i, name in enumerate(names)}

    # ------------------------------------------------------------------ ignored
    def ignored(self) -> dict[str, dict]:
        with self._lock:
            rows = self._db.execute("SELECT id, title, ts FROM ignored").fetchall()
        return {row[0]: {"title": row[1], "ts": row[2]} for row in rows}

    def set_ignored(self, finding_id: str, title: str, ignored: bool) -> None:
        with self._lock:
            if ignored:
                self._db.execute(
                    "INSERT OR REPLACE INTO ignored VALUES (?, ?, ?)", (finding_id, title, int(time.time()))
                )
            else:
                self._db.execute("DELETE FROM ignored WHERE id = ?", (finding_id,))
            self._db.commit()

    # ------------------------------------------------------------------ actions
    def add_action(self, fix: str, subject: str, ok: bool, steps: list[dict]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO actions VALUES (?, ?, ?, ?, ?)", (time.time(), fix, subject, int(ok), json.dumps(steps))
            )
            self._db.commit()

    def actions(self, limit: int = 200) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT ts, fix, subject, ok, steps FROM actions ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()
        return [{"ts": r[0], "fix": r[1], "subject": r[2], "ok": bool(r[3]), "steps": json.loads(r[4])} for r in rows]

    # ------------------------------------------------------------------ the whole
    def counts(self) -> dict:
        with self._lock:
            scans, oldest = self._db.execute("SELECT COUNT(*), MIN(ts) FROM scans").fetchone()
            (actions,) = self._db.execute("SELECT COUNT(*) FROM actions").fetchone()
            (ignored,) = self._db.execute("SELECT COUNT(*) FROM ignored").fetchone()
        # The write-ahead log counts too: right after many writes most of the data is still in it
        size = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
        return {"scans": scans, "actions": actions, "ignored": ignored, "oldest": oldest, "size": size}

    def wipe(self) -> dict:
        """Delete the trend and the list of what the app did. What you ignored stays."""
        with self._lock:
            removed = {
                "scans": self._db.execute("DELETE FROM scans").rowcount,
                "actions": self._db.execute("DELETE FROM actions").rowcount,
            }
            self._db.commit()
            self._db.execute("VACUUM")
        return removed

    def backup_to(self, target: Path) -> None:
        """A consistent copy of the database, while it is in use."""
        with self._lock:
            destination = sqlite3.connect(target)
            try:
                self._db.backup(destination)
            finally:
                destination.close()
