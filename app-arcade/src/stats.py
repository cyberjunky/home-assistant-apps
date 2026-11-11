"""Play statistics: how long, how often and when each game is played.

The player opens a session when MAME has started a game and reports the seconds it was
actually played (the game running in a visible tab) every half minute and when the page
closes. Everything is stored in a small SQLite database in /data.
"""

import contextlib
import sqlite3
import time
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# A session shorter than this (opened and left again) does not count
MIN_SESSION = 5
# Reported play time can't run ahead of the clock by more than this
CLOCK_SLACK = 15
DAYS = 30
WEEKS = 12

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    game TEXT NOT NULL,
    started REAL NOT NULL,
    ended REAL NOT NULL,
    seconds REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS sessions_game ON sessions (game);
"""


def _zone(name: str, offset_minutes: int) -> tzinfo:
    """The browser's time zone by name, or else its current UTC offset."""
    with contextlib.suppress(ZoneInfoNotFoundError, ValueError, OSError):
        if name:
            return ZoneInfo(name)
    return timezone(timedelta(minutes=-offset_minutes))


def _streaks(days: set) -> tuple[int, int]:
    """(current, longest) number of days in a row with play; current counts up to today or yesterday."""
    longest, run, prev = 0, 0, None
    for d in sorted(days):
        run = run + 1 if prev is not None and (d - prev).days == 1 else 1
        longest = max(longest, run)
        prev = d
    return run, longest


class Stats:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.executescript(SCHEMA)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def start(self, game: str) -> int:
        now = time.time()
        cur = self.db.execute(
            "INSERT INTO sessions (game, started, ended, seconds) VALUES (?, ?, ?, 0)", (game, now, now)
        )
        self.db.commit()
        return int(cur.lastrowid)

    def beat(self, session: int, seconds: float) -> bool:
        """Record the play time of a session so far (it only grows, and never beyond the clock)."""
        row = self.db.execute("SELECT started, seconds FROM sessions WHERE id = ?", (session,)).fetchone()
        if not row:
            return False
        now = time.time()
        started, known = row
        seconds = max(known, min(float(seconds), now - started + CLOCK_SLACK))
        self.db.execute("UPDATE sessions SET seconds = ?, ended = ? WHERE id = ?", (seconds, now, session))
        self.db.commit()
        return True

    def played(self) -> dict[str, dict]:
        """Per game: seconds played and when it was last played."""
        rows = self.db.execute(
            "SELECT game, SUM(seconds), MAX(started) FROM sessions WHERE seconds >= ? GROUP BY game", (MIN_SESSION,)
        )
        return {g: {"seconds": round(s), "last": last} for g, s, last in rows}

    def summary(self, tz_name: str = "", tz_offset: int = 0) -> dict:
        tz = _zone(tz_name, tz_offset)
        rows = self.db.execute(
            "SELECT game, started, seconds FROM sessions WHERE seconds >= ? ORDER BY started", (MIN_SESSION,)
        ).fetchall()
        today = datetime.now(tz).date()
        first_day = today - timedelta(days=DAYS - 1)
        week0 = today - timedelta(days=today.weekday()) - timedelta(weeks=WEEKS - 1)
        per_day = {first_day + timedelta(days=i): 0.0 for i in range(DAYS)}
        per_week = {week0 + timedelta(weeks=i): 0.0 for i in range(WEEKS)}
        hours = [0.0] * 24
        games: dict[str, dict] = {}
        played_days = set()
        longest = None
        for game, started, seconds in rows:
            local = datetime.fromtimestamp(started, UTC).astimezone(tz)
            day = local.date()
            played_days.add(day)
            if day in per_day:
                per_day[day] += seconds
            week = day - timedelta(days=day.weekday())
            if week in per_week:
                per_week[week] += seconds
            hours[local.hour] += seconds
            g = games.setdefault(
                game, {"name": game, "seconds": 0.0, "sessions": 0, "first": started, "last": started, "longest": 0.0}
            )
            g["seconds"] += seconds
            g["sessions"] += 1
            g["last"] = started
            g["longest"] = max(g["longest"], seconds)
            if longest is None or seconds > longest["seconds"]:
                longest = {"game": game, "seconds": round(seconds), "at": started}
        current, best = _streaks(played_days)
        if not played_days or (today - max(played_days)).days > 1:
            current = 0
        for g in games.values():
            g["seconds"] = round(g["seconds"])
            g["longest"] = round(g["longest"])
        return {
            "total_seconds": round(sum(g["seconds"] for g in games.values())),
            "sessions": len(rows),
            "games": sorted(games.values(), key=lambda g: -g["seconds"]),
            "days": [{"day": d.isoformat(), "seconds": round(s)} for d, s in per_day.items()],
            "weeks": [{"week": d.isoformat(), "seconds": round(s)} for d, s in per_week.items()],
            "hours": [round(s) for s in hours],
            "longest": longest,
            "days_played": len(played_days),
            "streak": {"current": current, "longest": best},
            "min_session": MIN_SESSION,
        }
