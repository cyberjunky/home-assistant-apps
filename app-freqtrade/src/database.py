"""What the app keeps itself: each bot's balance and profit over time, and the bots' events.

Freqtrade keeps the trades; the app only adds what no bot has: one history of all bots together,
for the overview's equity chart, and the events from the WebSocket, which a bot doesn't store.
"""

import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# A balance sample per bot this often
SNAPSHOT_SECONDS = 300
# Events are kept this long; snapshots for good (about 105,000 rows per bot per year)
EVENT_DAYS = 180

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts INTEGER NOT NULL,
    bot TEXT NOT NULL,
    currency TEXT,
    balance REAL,
    profit_closed REAL,
    profit_all REAL,
    open_trades INTEGER
);
CREATE INDEX IF NOT EXISTS snapshots_bot_ts ON snapshots (bot, ts);
CREATE TABLE IF NOT EXISTS events (
    ts REAL NOT NULL,
    bot TEXT NOT NULL,
    type TEXT NOT NULL,
    pair TEXT,
    text TEXT,
    level TEXT,
    trade_id INTEGER,
    profit_ratio REAL,
    profit_abs REAL
);
CREATE INDEX IF NOT EXISTS events_ts ON events (ts);
CREATE INDEX IF NOT EXISTS events_bot_ts ON events (bot, ts);
"""
EVENT_COLUMNS = ("ts", "bot", "type", "pair", "text", "level", "trade_id", "profit_ratio", "profit_abs")


class Database:
    """A small SQLite database. Writes are few (one row per bot per 5 minutes), so it writes at once."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self._last_snapshot: dict[str, float] = {}
        self._pruned = 0.0

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def snapshot(self, bot, now: float | None = None) -> bool:
        """Record the bot's balance and profit, at most once per SNAPSHOT_SECONDS."""
        now = time.time() if now is None else now
        if now - self._last_snapshot.get(bot.slug, 0) < SNAPSHOT_SECONDS:
            return False
        balance = bot.data.get("balance") or {}
        profit = (bot.data.get("profit_all") or {}).get("all") or {}
        count = bot.data.get("count") or {}
        row = (
            int(now),
            bot.slug,
            balance.get("stake") or (bot.data.get("config") or {}).get("stake_currency"),
            balance.get("total_bot"),
            profit.get("profit_closed_coin"),
            profit.get("profit_all_coin"),
            count.get("current"),
        )
        with self._lock:
            self._db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)", row)
            self._db.commit()
        self._last_snapshot[bot.slug] = now
        return True

    def history(self, start: int, end: int, points: int = 400) -> dict:
        """Every bot's balance and profit between start and end, averaged into about `points` steps."""
        step = max(SNAPSHOT_SECONDS, (end - start) // max(1, points))
        with self._lock:
            rows = self._db.execute(
                """SELECT bot, (ts / ?) * ? AS t, MAX(currency), AVG(balance), AVG(profit_closed), AVG(profit_all)
                   FROM snapshots WHERE ts >= ? AND ts <= ? GROUP BY bot, t ORDER BY t""",
                (step, step, start, end),
            ).fetchall()
        bots: dict[str, dict] = {}
        for bot, t, currency, balance, closed, total in rows:
            entry = bots.setdefault(bot, {"currency": currency, "points": []})
            entry["points"].append([t, balance, closed, total])
        return {"start": start, "end": end, "step": step, "bots": bots}

    def add_event(self, event: dict) -> None:
        with self._lock:
            self._db.execute(
                f"INSERT INTO events VALUES ({', '.join('?' * len(EVENT_COLUMNS))})",
                tuple(event.get(column) for column in EVENT_COLUMNS),
            )
            if event["ts"] - self._pruned > 86400:
                self._db.execute("DELETE FROM events WHERE ts < ?", (event["ts"] - EVENT_DAYS * 86400,))
                self._pruned = event["ts"]
            self._db.commit()

    def events(self, bot: str | None = None, limit: int = 100, before: float | None = None) -> list[dict]:
        """The latest events, newest first; of one bot, or of all."""
        query = f"SELECT {', '.join(EVENT_COLUMNS)} FROM events WHERE ts < ?"
        params: list = [before or time.time() + 1]
        if bot:
            query += " AND bot = ?"
            params.append(bot)
        query += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [dict(zip(EVENT_COLUMNS, row, strict=True)) for row in rows]

    def counts(self) -> dict:
        with self._lock:
            snapshots, oldest = self._db.execute("SELECT COUNT(*), MIN(ts) FROM snapshots").fetchone()
            (events,) = self._db.execute("SELECT COUNT(*) FROM events").fetchone()
        size = sum(p.stat().st_size for p in self.path.parent.glob(self.path.name + "*") if p.is_file())
        return {"snapshots": snapshots, "events": events, "oldest": oldest, "size": size}

    def wipe(self) -> dict:
        """Delete all snapshots and events."""
        with self._lock:
            removed = {
                "snapshots": self._db.execute("DELETE FROM snapshots").rowcount,
                "events": self._db.execute("DELETE FROM events").rowcount,
            }
            self._db.commit()
            self._db.execute("VACUUM")
        self._last_snapshot.clear()
        return removed

    def backup_to(self, target: Path) -> None:
        """A consistent copy of the database, while it is in use."""
        with self._lock:
            destination = sqlite3.connect(target)
            try:
                self._db.backup(destination)
            finally:
                destination.close()
