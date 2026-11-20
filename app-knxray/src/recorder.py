"""Round-the-clock recording: every telegram the app sees goes to a SQLite file in the app's data, so what
happened on the bus at three in the morning can be looked up later.

The live monitor keeps a short list in memory; this is the long-term store behind the Archive, the charts
and the statistics. Writes are batched (a burst of telegrams is one transaction a second), reads run in a
worker thread. Retention is by age and by row count. Beside the telegrams the recorder keeps a small log
of events (app started and stopped, bus connected and lost) and a heartbeat, so a gap in the history can be
explained: a quiet bus, a lost link, or the app not running.
"""

import asyncio
import contextlib
import csv
import io
import logging
import sqlite3
import struct
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FLUSH_SECONDS = 1.0
PRUNE_EVERY = 300
HEARTBEAT_EVERY = 60
# A pause on the bus longer than this is listed in the availability report
QUIET_GAP = 1800
MAX_LIMIT = 1000

SCHEMA = """
CREATE TABLE IF NOT EXISTS telegrams (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    dir TEXT NOT NULL,
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    ga INTEGER,
    type TEXT NOT NULL,
    raw TEXT NOT NULL,
    value TEXT,
    num REAL,
    unit TEXT,
    dpt TEXT
);
CREATE INDEX IF NOT EXISTS telegrams_ts ON telegrams (ts);
CREATE INDEX IF NOT EXISTS telegrams_ga_ts ON telegrams (ga, ts);
CREATE INDEX IF NOT EXISTS telegrams_src ON telegrams (src);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    kind TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
COLUMNS = "id, ts, dir, src, dst, ga, type, raw, value, num, unit, dpt"
EVENT_KINDS = ("start", "stop", "connected", "disconnected")


def parse_ga(text: str) -> int | None:
    """``1/2/3``, ``1/2`` or a plain number as a raw group address; None when it is not one."""
    try:
        parts = [int(part) for part in str(text).strip().split("/")]
    except ValueError:
        return None
    if len(parts) == 3 and 0 <= parts[0] <= 31 and 0 <= parts[1] <= 7 and 0 <= parts[2] <= 255:
        return (parts[0] << 11) | (parts[1] << 8) | parts[2]
    if len(parts) == 2 and 0 <= parts[0] <= 31 and 0 <= parts[1] <= 2047:
        return (parts[0] << 11) | parts[1]
    if len(parts) == 1 and 0 <= parts[0] <= 65535:
        return parts[0]
    return None


def guess_numeric(raw: str) -> float | None:
    """What a payload probably means when its address has no datapoint type: the length leaves one likely
    reading. Lets such an address still be charted; the chart says it is a guess."""
    if not raw or len(raw) % 2:
        return None
    try:
        data = bytes.fromhex(raw)
    except ValueError:
        return None
    if len(data) == 1:
        return float(data[0])
    if len(data) == 2:  # DPT 9: 0.01 * M * 2^E, M an 11-bit two's complement mantissa
        word = (data[0] << 8) | data[1]
        mantissa = word & 0x07FF
        if word & 0x8000:
            mantissa -= 0x800
        return 0.01 * mantissa * (2 ** ((word & 0x7800) >> 11))
    if len(data) == 4:
        return struct.unpack(">f", data)[0]
    return None


def _row(r: tuple) -> dict[str, Any]:
    return {
        "id": r[0],
        "ts": r[1],
        "dir": r[2],
        "src": r[3],
        "dst": r[4],
        "type": r[6],
        "raw": r[7],
        "text": r[8],
        "num": r[9],
        "unit": r[10],
        "dpt": r[11],
    }


class Recorder:
    def __init__(self, path: Path, retain_days: int = 90, retain_rows: int = 2_000_000):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.executescript(SCHEMA)
        self._lock = threading.Lock()
        self._buffer: list[tuple] = []
        self._task: asyncio.Task | None = None
        self.retain_days = retain_days
        self.retain_rows = retain_rows
        self._last_prune = 0.0
        self._last_beat = 0.0

    # ---- lifecycle ----

    def start(self) -> None:
        """Open the run: close a previous run that never said stop, note the start, start the writer."""
        now = time.time()
        with self._lock:
            last = self._db.execute("SELECT kind, ts FROM events ORDER BY id DESC LIMIT 1").fetchone()
            if last is not None and last[0] != "stop":
                alive = self._db.execute("SELECT value FROM meta WHERE key='alive'").fetchone()
                died = max(float(alive[0]) if alive else 0.0, float(last[1]))
                self._db.execute("INSERT INTO events (ts, kind) VALUES (?, 'stop')", (min(died, now),))
            self._db.execute("INSERT INTO events (ts, kind) VALUES (?, 'start')", (now,))
            self._beat(now)
        self._last_prune = now
        self._task = asyncio.get_running_loop().create_task(self._writer())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self.flush()
        with self._lock:
            self._db.execute("INSERT INTO events (ts, kind) VALUES (?, 'stop')", (time.time(),))
            self._beat(time.time())
            self._db.close()

    def event(self, kind: str) -> None:
        """The link to the bus came up or was lost."""
        if kind not in EVENT_KINDS:
            raise ValueError(kind)
        with self._lock:
            self._db.execute("INSERT INTO events (ts, kind) VALUES (?, ?)", (time.time(), kind))

    # ---- writing ----

    def add(self, t: dict[str, Any]) -> None:
        """Queue one telegram (the dict the live monitor shows). Cheap: called on the event loop."""
        self._buffer.append(
            (
                t["ts"],
                t["dir"],
                t["src"],
                t["dst"],
                parse_ga(t["dst"]),
                t["type"],
                t.get("raw") or "",
                t.get("text"),
                t.get("num"),
                t.get("unit"),
                t.get("dpt"),
            )
        )

    def flush(self) -> int:
        rows, self._buffer = self._buffer, []
        if not rows:
            return 0
        with self._lock:
            self._db.execute("BEGIN")
            self._db.executemany(
                "INSERT INTO telegrams (ts, dir, src, dst, ga, type, raw, value, num, unit, dpt)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            self._db.execute("COMMIT")
        return len(rows)

    async def _writer(self) -> None:
        while True:
            await asyncio.sleep(FLUSH_SECONDS)
            try:
                await asyncio.to_thread(self._tick)
            except Exception:
                # Recording must never take the app down
                logger.exception("The telegram recorder failed")

    def _tick(self) -> None:
        self.flush()
        now = time.time()
        if now - self._last_beat >= HEARTBEAT_EVERY:
            with self._lock:
                self._beat(now)
        if now - self._last_prune >= PRUNE_EVERY:
            self._last_prune = now
            self.prune()

    def _beat(self, now: float) -> None:
        self._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('alive', ?)", (str(now),))
        self._last_beat = now

    def prune(self) -> int:
        """Apply the retention limits; returns how many rows went."""
        removed = 0
        with self._lock:
            if self.retain_days > 0:
                edge = time.time() - self.retain_days * 86400
                removed += self._db.execute("DELETE FROM telegrams WHERE ts < ?", (edge,)).rowcount
                self._db.execute("DELETE FROM events WHERE ts < ?", (edge,))
            if self.retain_rows > 0:
                row = self._db.execute(
                    "SELECT id FROM telegrams ORDER BY id DESC LIMIT 1 OFFSET ?", (self.retain_rows,)
                ).fetchone()
                if row is not None:
                    removed += self._db.execute("DELETE FROM telegrams WHERE id <= ?", (row[0],)).rowcount
        if removed:
            logger.info("The recorder dropped %d old telegrams", removed)
        return removed

    def clear(self) -> None:
        self._buffer.clear()
        with self._lock:
            self._db.execute("DELETE FROM telegrams")
            self._db.execute("VACUUM")

    # ---- reading ----

    def summary(self) -> dict[str, Any]:
        with self._lock:
            row = self._db.execute("SELECT count(*), min(ts), max(ts) FROM telegrams").fetchone()
        size = 0
        for path in (self.path, self.path.with_name(self.path.name + "-wal")):
            with contextlib.suppress(OSError):
                size += path.stat().st_size
        return {
            "rows": int(row[0] or 0) + len(self._buffer),
            "oldest": row[1],
            "newest": row[2],
            "bytes": size,
            "retain_days": self.retain_days,
            "retain_rows": self.retain_rows,
        }

    def newest(self, limit: int) -> list[dict[str, Any]]:
        """The last telegrams recorded, oldest first: what the live monitor starts with after a restart."""
        with self._lock:
            rows = self._db.execute(f"SELECT {COLUMNS} FROM telegrams ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_row(r) for r in reversed(rows)]

    @staticmethod
    def _where(f: dict[str, Any]) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if f.get("since") is not None:
            clauses.append("ts >= ?")
            args.append(f["since"])
        if f.get("until") is not None:
            clauses.append("ts <= ?")
            args.append(f["until"])
        if f.get("ga") is not None:
            clauses.append("ga = ?")
            args.append(f["ga"])
        if f.get("ga_prefix"):
            clauses.append("dst LIKE ?")
            args.append(str(f["ga_prefix"]).replace("%", "") + "%")
        if f.get("src"):
            clauses.append("src = ?")
            args.append(f["src"])
        if f.get("type"):
            clauses.append("type = ?")
            args.append(f["type"])
        if f.get("dpt_main"):
            clauses.append("(dpt = ? OR dpt LIKE ?)")
            args += [str(f["dpt_main"]), f"{f['dpt_main']}.%"]
        if f.get("q"):
            like = f"%{f['q']}%"
            text = "dst LIKE ? OR src LIKE ? OR value LIKE ? OR raw LIKE ?"
            extra: list[Any] = [like] * 4
            # Addresses whose name (of the group address or of a sending device) matched the text
            for column, key in (("ga", "q_gas"), ("src", "q_srcs")):
                found = list(f.get(key) or [])[:500]
                if found:
                    text += f" OR {column} IN ({','.join('?' * len(found))})"
                    extra += found
            clauses.append(f"({text})")
            args += extra
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", args

    def archive(self, filters: dict[str, Any], cursor: int | None = None, limit: int = 200) -> dict[str, Any]:
        """Newest first, paged on id: pass the returned ``next`` to continue."""
        limit = max(1, min(int(limit), MAX_LIMIT))
        self.flush()
        where, args = self._where(filters)
        page_where = where + (" AND " if where else " WHERE ") + "id < ?" if cursor else where
        page_args = [*args, cursor] if cursor else args
        with self._lock:
            total = self._db.execute(f"SELECT count(*) FROM telegrams{where}", args).fetchone()[0]
            rows = self._db.execute(
                f"SELECT {COLUMNS} FROM telegrams{page_where} ORDER BY id DESC LIMIT ?", [*page_args, limit]
            ).fetchall()
        items = [_row(r) for r in rows]
        return {"items": items, "total": int(total), "next": items[-1]["id"] if len(items) == limit else None}

    def csv_rows(self, filters: dict[str, Any], names: dict[str, str]) -> Iterator[str]:
        """The filtered archive as CSV, oldest first, a block of lines at a time."""
        self.flush()
        where, args = self._where(filters)

        def block(rows: list[list]) -> str:
            out = io.StringIO()
            csv.writer(out, lineterminator="\n").writerows(rows)
            return out.getvalue()

        yield block(
            [["time", "direction", "source", "source name", "destination", "name", "type", "value", "unit", "raw"]]
        )
        last_id = 0
        while True:
            with self._lock:
                rows = self._db.execute(
                    f"SELECT {COLUMNS} FROM telegrams{where}{' AND ' if where else ' WHERE '}id > ? ORDER BY id LIMIT 2000",
                    [*args, last_id],
                ).fetchall()
            if not rows:
                return
            last_id = rows[-1][0]
            yield block(
                [
                    [
                        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r[1])) + f".{int(r[1] % 1 * 1000):03d}",
                        r[2],
                        r[3],
                        names.get(r[3], ""),
                        r[4],
                        names.get(r[4], ""),
                        r[6],
                        "" if r[8] is None else r[8],
                        r[10] or "",
                        r[7],
                    ]
                    for r in rows
                ]
            )

    def addresses(self, since: float, until: float, limit: int = 2000) -> list[dict[str, Any]]:
        """Group addresses in the recording, busiest first, with how many of their telegrams carry a number."""
        self.flush()
        with self._lock:
            rows = self._db.execute(
                "SELECT dst, count(*), sum(num IS NOT NULL), max(unit), max(ts) FROM telegrams"
                " WHERE ga IS NOT NULL AND ts BETWEEN ? AND ? GROUP BY ga ORDER BY count(*) DESC LIMIT ?",
                (since, until, limit),
            ).fetchall()
        return [{"ga": r[0], "count": r[1], "numeric": int(r[2] or 0), "unit": r[3], "last": r[4]} for r in rows]

    def series(self, ga: int, since: float, until: float, points: int = 600) -> dict[str, Any]:
        """The numbers one group address carried over a range: as they are when they fit in ``points``,
        otherwise the average per stretch of time with its lowest and highest. An address without a datapoint
        type has no decoded values; its payloads are then read by their length, and the answer says so."""
        self.flush()
        points = max(10, min(int(points), 5000))
        empty = {"count": 0, "bucket": 0, "guessed": False, "unit": None, "points": []}
        with self._lock:
            n, unit = self._db.execute(
                "SELECT count(*), max(unit) FROM telegrams WHERE ga = ? AND num IS NOT NULL AND ts BETWEEN ? AND ?",
                (ga, since, until),
            ).fetchone()
            if not n:
                # Only when nothing was decoded at all: values that came out as text have a type, and reading
                # their payloads as numbers would be wrong
                decoded = self._db.execute(
                    "SELECT count(*) FROM telegrams WHERE ga = ? AND value IS NOT NULL AND ts BETWEEN ? AND ?",
                    (ga, since, until),
                ).fetchone()[0]
                if decoded:
                    return empty
                rows = self._db.execute(
                    "SELECT ts, raw FROM telegrams WHERE ga = ? AND raw != '' AND type != 'read' AND ts BETWEEN ? AND ?"
                    " ORDER BY ts DESC LIMIT ?",
                    (ga, since, until, points),
                ).fetchall()
                values = [[ts, v, v, v] for ts, raw in reversed(rows) if (v := guess_numeric(raw)) is not None]
                return {**empty, "count": len(values), "guessed": bool(values), "points": values}
            if n <= points:
                rows = self._db.execute(
                    "SELECT ts, num FROM telegrams WHERE ga = ? AND num IS NOT NULL AND ts BETWEEN ? AND ? ORDER BY ts",
                    (ga, since, until),
                ).fetchall()
                return {**empty, "count": n, "unit": unit, "points": [[r[0], r[1], r[1], r[1]] for r in rows]}
            bucket = max(1.0, (until - since) / points)
            rows = self._db.execute(
                "SELECT CAST((ts - ?) / ? AS INTEGER) AS b, avg(num), min(num), max(num) FROM telegrams"
                " WHERE ga = ? AND num IS NOT NULL AND ts BETWEEN ? AND ? GROUP BY b ORDER BY b",
                (since, bucket, ga, since, until),
            ).fetchall()
        return {
            **empty,
            "count": n,
            "unit": unit,
            "bucket": bucket,
            "points": [[since + r[0] * bucket + bucket / 2, r[1], r[2], r[3]] for r in rows],
        }

    def stats(self, since: float, until: float, filters: dict[str, Any] | None = None, top: int = 15) -> dict[str, Any]:
        """Totals, telegrams over time, the weekday by hour heatmap and the busiest addresses and devices."""
        self.flush()
        where, args = self._where({**(filters or {}), "since": since, "until": until})
        buckets = 60
        width = max(1.0, (until - since) / buckets)
        with self._lock:
            total = self._db.execute(f"SELECT count(*) FROM telegrams{where}", args).fetchone()[0]
            hist = self._db.execute(
                f"SELECT CAST((ts - ?) / ? AS INTEGER) AS b, count(*) FROM telegrams{where} GROUP BY b",
                [since, width, *args],
            ).fetchall()
            heat = self._db.execute(
                "SELECT CAST(strftime('%w', ts, 'unixepoch', 'localtime') AS INTEGER),"
                " CAST(strftime('%H', ts, 'unixepoch', 'localtime') AS INTEGER), count(*)"
                f" FROM telegrams{where} GROUP BY 1, 2",
                args,
            ).fetchall()
            top_ga = self._db.execute(
                f"SELECT dst, count(*) AS n, count(DISTINCT src), max(ts) FROM telegrams{where} AND ga IS NOT NULL"
                " GROUP BY ga ORDER BY n DESC LIMIT ?",
                [*args, top],
            ).fetchall()
            top_src = self._db.execute(
                f"SELECT src, count(*) AS n, count(DISTINCT ga), max(ts) FROM telegrams{where}"
                " GROUP BY src ORDER BY n DESC LIMIT ?",
                [*args, top],
            ).fetchall()
            kinds = self._db.execute(f"SELECT type, count(*) FROM telegrams{where} GROUP BY type", args).fetchall()
            distinct = self._db.execute(
                f"SELECT count(DISTINCT ga), count(DISTINCT src) FROM telegrams{where}", args
            ).fetchone()
            # The busiest minute: the peak load, which an average over the range hides
            peak = self._db.execute(
                f"SELECT CAST(ts / 60 AS INTEGER) AS m, count(*) AS n FROM telegrams{where} GROUP BY m ORDER BY n DESC LIMIT 1",
                args,
            ).fetchone()
        heatmap = [[0] * 24 for _ in range(7)]
        for day, hour, n in heat:
            if 0 <= day < 7 and 0 <= hour < 24:
                heatmap[day][hour] = n
        counts = {int(b): n for b, n in hist}
        return {
            "since": since,
            "until": until,
            "total": int(total),
            "per_minute": total / max(1.0, until - since) * 60,
            "peak_minute": {"ts": peak[0] * 60, "count": peak[1]} if peak else None,
            "addresses": int(distinct[0] or 0),
            "sources": int(distinct[1] or 0),
            "histogram": {"width": width, "counts": [counts.get(i, 0) for i in range(buckets)]},
            "heatmap": heatmap,
            "top_addresses": [{"ga": r[0], "count": r[1], "sources": r[2], "last": r[3]} for r in top_ga],
            "top_sources": [{"src": r[0], "count": r[1], "addresses": r[2], "last": r[3]} for r in top_src],
            "by_type": {r[0]: r[1] for r in kinds},
        }

    def availability(self, since: float, until: float) -> dict[str, Any]:
        """What the recorder was doing across a range (recording, link down, not running) and the longest
        pauses on the bus while it was recording."""
        self.flush()
        with self._lock:
            events = self._db.execute("SELECT ts, kind FROM events WHERE ts <= ? ORDER BY ts, id", (until,)).fetchall()
            gaps = self._db.execute(
                "SELECT prev, ts FROM (SELECT ts, LAG(ts) OVER (ORDER BY ts) AS prev FROM telegrams"
                " WHERE ts BETWEEN ? AND ?) WHERE ts - prev > ? ORDER BY ts - prev DESC LIMIT 20",
                (since, until, QUIET_GAP),
            ).fetchall()
            last = self._db.execute("SELECT max(ts) FROM telegrams WHERE ts <= ?", (until,)).fetchone()[0]
        running = connected = False
        segments: list[dict[str, Any]] = []
        cursor = since

        def close(at: float) -> None:
            nonlocal cursor
            at = min(max(at, since), until)
            if at > cursor:
                state = "not_running" if not running else "recording" if connected else "link_down"
                if segments and segments[-1]["state"] == state:
                    segments[-1]["to"] = at
                else:
                    segments.append({"state": state, "from": cursor, "to": at})
            cursor = max(cursor, at)

        for ts, kind in events:
            if ts > since:
                close(ts)
            if kind == "start":
                running, connected = True, False
            elif kind == "stop":
                running, connected = False, False
            else:
                connected = kind == "connected"
        close(until)
        coverage = {"recording": 0.0, "link_down": 0.0, "not_running": 0.0}
        for segment in segments:
            coverage[segment["state"]] += segment["to"] - segment["from"]
        quiet = [{"from": a, "to": b} for a, b in gaps]
        if last is not None and segments and segments[-1]["state"] == "recording" and until - last > QUIET_GAP:
            quiet.insert(0, {"from": max(last, since), "to": until})
        return {"since": since, "until": until, "segments": segments, "coverage": coverage, "quiet": quiet}

    def backfill(self, decode, limit: int = 200_000) -> int:
        """Fill in the values of telegrams recorded before their datapoint type was known (no project yet, or
        the address typed later). ``decode(dst, raw)`` gives (text, num, unit, dpt) or None. Only rows
        without a value are touched, so it can run any number of times."""
        self.flush()
        done = 0
        with self._lock:
            pending = self._db.execute(
                "SELECT DISTINCT dst FROM telegrams WHERE value IS NULL AND ga IS NOT NULL AND raw != '' AND type != 'read'"
            ).fetchall()
        for (dst,) in pending:
            if decode(dst, "") is None:
                continue
            with self._lock:
                rows = self._db.execute(
                    "SELECT id, raw FROM telegrams WHERE dst = ? AND value IS NULL AND raw != '' AND type != 'read' LIMIT ?",
                    (dst, limit - done),
                ).fetchall()
            updates = [
                (*decoded, row_id) for row_id, raw in rows if (decoded := decode(dst, raw)) and decoded[0] is not None
            ]
            if updates:
                with self._lock:
                    self._db.execute("BEGIN")
                    self._db.executemany(
                        "UPDATE telegrams SET value = ?, num = ?, unit = ?, dpt = ? WHERE id = ?", updates
                    )
                    self._db.execute("COMMIT")
                done += len(updates)
            if done >= limit:
                break
        if done:
            logger.info("Decoded %d recorded telegrams with the project's datapoint types", done)
        return done
