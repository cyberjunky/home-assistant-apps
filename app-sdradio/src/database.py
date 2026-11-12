"""The SQLite database: stored channels, scan hits, ignored frequencies, bands and settings.

One connection, shared by the web server and the radio thread behind a lock. The data is
small (channels in the hundreds, hits in the tens of thousands), so plain SQL is fast enough.
"""

import csv
import io
import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

from .bands import DEFAULT_BANDS, MODES, band_for, default_bandwidth

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY,
    number INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL DEFAULT '',
    frequency INTEGER NOT NULL,
    mode TEXT NOT NULL,
    bandwidth INTEGER NOT NULL,
    step INTEGER,
    squelch REAL,
    gain REAL,
    band TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    favourite INTEGER NOT NULL DEFAULT 0,
    scan INTEGER NOT NULL DEFAULT 1,
    priority INTEGER NOT NULL DEFAULT 0,
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS channels_frequency ON channels (frequency);
CREATE TABLE IF NOT EXISTS hits (
    id INTEGER PRIMARY KEY,
    ts REAL NOT NULL,
    duration REAL NOT NULL DEFAULT 0,
    frequency INTEGER NOT NULL,
    mode TEXT NOT NULL,
    channel_id INTEGER REFERENCES channels (id) ON DELETE SET NULL,
    peak REAL NOT NULL,
    scanning INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS hits_ts ON hits (ts);
CREATE INDEX IF NOT EXISTS hits_frequency ON hits (frequency);
CREATE INDEX IF NOT EXISTS hits_channel ON hits (channel_id);
CREATE TABLE IF NOT EXISTS ignored (frequency INTEGER PRIMARY KEY, ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS bands (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    start INTEGER NOT NULL,
    stop INTEGER NOT NULL,
    mode TEXT NOT NULL,
    step INTEGER NOT NULL,
    bandwidth INTEGER NOT NULL,
    button INTEGER NOT NULL DEFAULT 1,
    position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# Settings the Settings page edits, with their defaults and allowed values
SETTINGS: dict[str, dict] = {
    "scan_speed": {"default": 10, "min": 1, "max": 20},  # channels a second
    "scan_resume": {"default": 2.0, "min": 0.0, "max": 30.0},  # seconds of quiet before moving on
    "scan_range": {"default": {"start": 144_000_000, "stop": 146_000_000, "step": 12_500, "mode": "nfm"}},
    "priority_interval": {"default": 2.0, "min": 0.5, "max": 30.0},
    "find_min_hits": {"default": 3, "min": 1, "max": 100},
    "fft_size": {"default": 2048, "options": [1024, 2048, 4096, 8192]},
    "spectrum_rate": {"default": 20, "options": [10, 20]},
    "waterfall_palette": {"default": "classic", "options": ["classic", "grey", "turbo"]},
    "audio_buffer_ms": {"default": 250, "options": [100, 250, 500]},
    "hits_keep_days": {"default": 90, "options": [7, 30, 90, 365, 0]},
    "backup_time": {"default": "03:00"},
    "backups_keep": {"default": 14, "min": 1, "max": 100},
    "ha_sensors": {"default": False},
    "ha_events": {"default": False},
    "lcd_colour": {"default": "lcd", "options": ["lcd", "amber", "green", "blue", "white"]},
}

CHANNEL_FIELDS = (
    "number",
    "name",
    "frequency",
    "mode",
    "bandwidth",
    "step",
    "squelch",
    "gain",
    "band",
    "tags",
    "notes",
    "favourite",
    "scan",
    "priority",
)

CSV_COLUMNS = [
    "number",
    "name",
    "frequency_mhz",
    "mode",
    "bandwidth_khz",
    "step_khz",
    "squelch_db",
    "band",
    "tags",
    "notes",
    "favourite",
    "scan",
    "priority",
]
# Other names for the CSV columns, so lists from other programs (CHIRP, SDR#) import too
CSV_ALIASES = {
    "location": "number",
    "channel": "number",
    "memory": "number",
    "#": "number",
    "label": "name",
    "description": "name",
    "frequency": "frequency_mhz",
    "freq": "frequency_mhz",
    "frequency (mhz)": "frequency_mhz",
    "bandwidth": "bandwidth_khz",
    "filter": "bandwidth_khz",
    "step": "step_khz",
    "tstep": "step_khz",
    "squelch": "squelch_db",
    "comment": "notes",
    "group": "tags",
    "favorite": "favourite",
}
MODE_ALIASES = {
    "fm": "nfm",
    "nfm": "nfm",
    "wfm": "wfm",
    "am": "am",
    "usb": "usb",
    "lsb": "lsb",
    "cw": "cw",
    "dsb": "dsb",
}


class ValidationError(ValueError):
    """Bad input from the page; the message is shown to the user."""


def _bool(value) -> int:
    if isinstance(value, str):
        return 1 if value.strip().lower() in ("1", "true", "yes", "y", "x", "★") else 0
    return 1 if value else 0


def _text(value, limit: int = 500) -> str:
    return str(value if value is not None else "").strip()[:limit]


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.conn = self._connect()
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _setup(self) -> None:
        with self.lock:
            self.conn.executescript(SCHEMA)
            self.conn.execute("INSERT OR IGNORE INTO meta VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),))
            if not self.conn.execute("SELECT 1 FROM bands LIMIT 1").fetchone():
                self.reset_bands()

    def close(self) -> None:
        with self.lock:
            self.conn.close()

    def _rows(self, sql: str, args=()) -> list[dict]:
        with self.lock:
            return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def _one(self, sql: str, args=()) -> dict | None:
        with self.lock:
            row = self.conn.execute(sql, args).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------- settings

    def settings(self) -> dict:
        stored = {r["key"]: json.loads(r["value"]) for r in self._rows("SELECT key, value FROM settings")}
        return {key: stored.get(key, spec["default"]) for key, spec in SETTINGS.items()}

    def update_settings(self, changes: dict) -> dict:
        clean = {}
        for key, value in changes.items():
            spec = SETTINGS.get(key)
            if spec is None:
                raise ValidationError(f"Unknown setting {key}")
            clean[key] = _check_setting(key, spec, value)
        with self.lock:
            for key, value in clean.items():
                self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(value)))
        return self.settings()

    def state(self) -> dict:
        """What the radio was last tuned to, kept for a restart."""
        row = self._one("SELECT value FROM settings WHERE key = 'radio_state'")
        return json.loads(row["value"]) if row else {}

    def save_state(self, state: dict) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO settings VALUES ('radio_state', ?)", (json.dumps(state),))

    def add_listening(self, seconds: float, day: str) -> None:
        key = f"listen:{day}"
        with self.lock:
            row = self.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            total = (json.loads(row["value"]) if row else 0) + seconds
            self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, json.dumps(round(total, 1))))

    def listening(self, days: list[str]) -> dict:
        keys = [f"listen:{d}" for d in days]
        marks = ",".join("?" * len(keys))
        rows = self._rows(f"SELECT key, value FROM settings WHERE key IN ({marks})", keys)
        return {r["key"].split(":", 1)[1]: json.loads(r["value"]) for r in rows}

    # ------------------------------------------------------------- bands

    def bands(self) -> list[dict]:
        return self._rows("SELECT * FROM bands ORDER BY position, start")

    def reset_bands(self) -> list[dict]:
        with self.lock:
            self.conn.execute("DELETE FROM bands")
            for i, b in enumerate(DEFAULT_BANDS):
                self.conn.execute(
                    "INSERT INTO bands (name, start, stop, mode, step, bandwidth, button, position)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        b["name"],
                        b["start"],
                        b["stop"],
                        b["mode"],
                        b["step"],
                        b["bandwidth"],
                        int(b.get("button", True)),
                        i,
                    ),
                )
        return self.bands()

    def save_band(self, data: dict, band_id: int | None = None) -> dict:
        name = _text(data.get("name"), 40)
        if not name:
            raise ValidationError("Give the band a name")
        start, stop = _hz(data.get("start"), "From"), _hz(data.get("stop"), "To")
        if stop <= start:
            raise ValidationError("To must be above From")
        mode = _mode(data.get("mode"))
        step = _hz(data.get("step") or 12_500, "Step")
        bandwidth = _hz(data.get("bandwidth") or default_bandwidth(mode), "Bandwidth")
        button = _bool(data.get("button", 1))
        with self.lock:
            if band_id is None:
                position = (self.conn.execute("SELECT MAX(position) FROM bands").fetchone()[0] or 0) + 1
                cur = self.conn.execute(
                    "INSERT INTO bands (name, start, stop, mode, step, bandwidth, button, position)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (name, start, stop, mode, step, bandwidth, button, position),
                )
                band_id = cur.lastrowid
            else:
                cur = self.conn.execute(
                    "UPDATE bands SET name=?, start=?, stop=?, mode=?, step=?, bandwidth=?, button=? WHERE id=?",
                    (name, start, stop, mode, step, bandwidth, button, band_id),
                )
                if cur.rowcount == 0:
                    raise KeyError(band_id)
        return self._one("SELECT * FROM bands WHERE id = ?", (band_id,))

    def delete_band(self, band_id: int) -> None:
        with self.lock:
            if self.conn.execute("DELETE FROM bands WHERE id = ?", (band_id,)).rowcount == 0:
                raise KeyError(band_id)

    # ------------------------------------------------------------- channels

    def channels(self) -> list[dict]:
        rows = self._rows(
            """SELECT c.*, h.last_heard, COALESCE(h.hits, 0) AS hits
               FROM channels c LEFT JOIN (
                   SELECT channel_id, MAX(ts) AS last_heard, COUNT(*) AS hits FROM hits
                   WHERE channel_id IS NOT NULL GROUP BY channel_id) h ON h.channel_id = c.id
               ORDER BY c.number"""
        )
        return rows

    def channel(self, channel_id: int) -> dict | None:
        return self._one("SELECT * FROM channels WHERE id = ?", (channel_id,))

    def channel_at(self, frequency: int, tolerance: int = 500) -> dict | None:
        """The stored channel on this frequency, if any (the closest within the tolerance)."""
        return self._one(
            "SELECT * FROM channels WHERE ABS(frequency - ?) <= ? ORDER BY ABS(frequency - ?), number LIMIT 1",
            (frequency, tolerance, frequency),
        )

    def next_number(self) -> int:
        with self.lock:
            numbers = [r[0] for r in self.conn.execute("SELECT number FROM channels ORDER BY number")]
        expected = 1
        for n in numbers:
            if n > expected:
                break
            expected = n + 1
        return expected

    def _clean_channel(self, data: dict, current: dict | None = None) -> dict:
        merged = dict(current or {})
        merged.update({k: v for k, v in data.items() if k in CHANNEL_FIELDS})
        frequency = _hz(merged.get("frequency"), "Frequency")
        if not 10_000 <= frequency <= 6_000_000_000:
            raise ValidationError("Frequency must be between 0.01 and 6000 MHz")
        mode = _mode(merged.get("mode") or "nfm")
        number = merged.get("number")
        if number in (None, ""):
            number = self.next_number()
        try:
            number = int(number)
        except (TypeError, ValueError):
            raise ValidationError("The channel number must be a whole number") from None
        if not 0 <= number <= 99_999:
            raise ValidationError("The channel number must be 0 to 99999")
        bandwidth = merged.get("bandwidth") or default_bandwidth(mode)
        band = _text(merged.get("band"), 40)
        if not band:
            found = band_for(self.bands(), frequency)
            band = found["name"] if found else ""
        return {
            "number": number,
            "name": _text(merged.get("name"), 80),
            "frequency": frequency,
            "mode": mode,
            "bandwidth": _hz(bandwidth, "Bandwidth"),
            "step": _hz(merged["step"], "Step") if merged.get("step") not in (None, "") else None,
            "squelch": _optional_float(merged.get("squelch"), "Squelch", -140, 0),
            "gain": _optional_float(merged.get("gain"), "Gain", 0, 110),
            "band": band,
            "tags": _text(merged.get("tags"), 200),
            "notes": _text(merged.get("notes"), 2000),
            "favourite": _bool(merged.get("favourite", 0)),
            "scan": _bool(merged.get("scan", 1)),
            "priority": _bool(merged.get("priority", 0)),
        }

    def save_channel(self, data: dict, channel_id: int | None = None) -> dict:
        now = time.time()
        with self.lock:
            current = self.channel(channel_id) if channel_id is not None else None
            if channel_id is not None and current is None:
                raise KeyError(channel_id)
            clean = self._clean_channel(data, current)
            clash = self.conn.execute(
                "SELECT id FROM channels WHERE number = ? AND id IS NOT ?", (clean["number"], channel_id)
            ).fetchone()
            if clash:
                raise ValidationError(f"Channel number {clean['number']} is already in use")
            columns = list(clean)
            if channel_id is None:
                cur = self.conn.execute(
                    f"INSERT INTO channels ({', '.join(columns)}, created, updated)"
                    f" VALUES ({', '.join('?' * len(columns))}, ?, ?)",
                    [*clean.values(), now, now],
                )
                channel_id = cur.lastrowid
                # Earlier hits on this frequency now belong to the channel
                self.conn.execute(
                    "UPDATE hits SET channel_id = ? WHERE channel_id IS NULL AND ABS(frequency - ?) <= ?",
                    (channel_id, clean["frequency"], max(clean["bandwidth"] // 4, 500)),
                )
                self.conn.execute("DELETE FROM ignored WHERE frequency = ?", (clean["frequency"],))
            else:
                self.conn.execute(
                    f"UPDATE channels SET {', '.join(c + ' = ?' for c in columns)}, updated = ? WHERE id = ?",
                    [*clean.values(), now, channel_id],
                )
        return self.channel(channel_id)

    def delete_channel(self, channel_id: int) -> None:
        with self.lock:
            if self.conn.execute("DELETE FROM channels WHERE id = ?", (channel_id,)).rowcount == 0:
                raise KeyError(channel_id)

    def scan_channels(self, band: str | None = None) -> list[dict]:
        if band:
            return self._rows("SELECT * FROM channels WHERE scan = 1 AND band = ? ORDER BY number", (band,))
        return self._rows("SELECT * FROM channels WHERE scan = 1 ORDER BY number")

    def priority_channels(self) -> list[dict]:
        return self._rows("SELECT * FROM channels WHERE priority = 1 ORDER BY number")

    # ------------------------------------------------------------- CSV

    def export_csv(self) -> str:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        for c in self._rows("SELECT * FROM channels ORDER BY number"):
            writer.writerow(
                [
                    c["number"],
                    c["name"],
                    f"{c['frequency'] / 1e6:.6f}",
                    c["mode"],
                    f"{c['bandwidth'] / 1e3:g}",
                    f"{c['step'] / 1e3:g}" if c["step"] else "",
                    f"{c['squelch']:g}" if c["squelch"] is not None else "",
                    c["band"],
                    c["tags"],
                    c["notes"],
                    c["favourite"],
                    c["scan"],
                    c["priority"],
                ]
            )
        return out.getvalue()

    def import_csv(self, text: str, replace: bool = False) -> dict:
        """Adds or updates channels from CSV. A row with a known number or frequency updates that channel."""
        if text.startswith("﻿"):
            text = text[1:]
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        try:
            header = [h.strip().lower() for h in next(reader)]
        except StopIteration:
            raise ValidationError("The file is empty") from None
        columns = [CSV_ALIASES.get(h, h) for h in header]
        if "frequency_mhz" not in columns:
            raise ValidationError("No frequency column: the first row must name the columns (see Export CSV)")

        rows, errors = [], []
        for line, values in enumerate(reader, start=2):
            if not any(v.strip() for v in values):
                continue
            record = dict(zip(columns, (v.strip() for v in values), strict=False))
            try:
                rows.append(_csv_row(record))
            except ValidationError as e:
                errors.append(f"line {line}: {e}")
        if errors:
            raise ValidationError("Nothing imported. " + "; ".join(errors[:10]) + (" …" if len(errors) > 10 else ""))

        added = updated = 0
        with self.lock:
            self.conn.execute("BEGIN")
            try:
                if replace:
                    self.conn.execute("DELETE FROM channels")
                for row in rows:
                    existing = None
                    if row.get("number") not in (None, ""):
                        existing = self._one("SELECT id FROM channels WHERE number = ?", (int(row["number"]),))
                    if existing is None:
                        existing = self._one(
                            "SELECT id FROM channels WHERE frequency = ? AND mode = ?", (row["frequency"], row["mode"])
                        )
                        if existing:
                            row.pop("number", None)
                    if existing:
                        self.save_channel(row, existing["id"])
                        updated += 1
                    else:
                        if row.get("number") in (None, ""):
                            row.pop("number", None)
                        self.save_channel(row)
                        added += 1
                self.conn.execute("COMMIT")
            except Exception:
                self.conn.execute("ROLLBACK")
                raise
        return {"added": added, "updated": updated}

    # ------------------------------------------------------------- hits

    def add_hit(self, ts: float, duration: float, frequency: int, mode: str, peak: float, scanning: bool) -> int:
        channel = self.channel_at(frequency)
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO hits (ts, duration, frequency, mode, channel_id, peak, scanning) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    round(duration, 1),
                    frequency,
                    mode,
                    channel["id"] if channel else None,
                    round(peak, 1),
                    int(scanning),
                ),
            )
            return cur.lastrowid

    def recent_hits(self, limit: int = 20) -> list[dict]:
        return self._rows(
            """SELECT h.*, c.name, c.number, c.band FROM hits h LEFT JOIN channels c ON c.id = h.channel_id
               ORDER BY h.ts DESC LIMIT ?""",
            (limit,),
        )

    def finds(self, min_hits: int = 1, limit: int = 200) -> list[dict]:
        """Frequencies with signals that are not stored as a channel and not ignored."""
        return self._rows(
            """SELECT frequency, MIN(mode) AS mode, COUNT(*) AS hits, MAX(peak) AS peak, MIN(ts) AS first, MAX(ts) AS last
               FROM hits WHERE channel_id IS NULL AND frequency NOT IN (SELECT frequency FROM ignored)
               GROUP BY frequency HAVING COUNT(*) >= ? ORDER BY last DESC LIMIT ?""",
            (min_hits, limit),
        )

    def ignore(self, frequency: int) -> None:
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO ignored VALUES (?, ?)", (frequency, time.time()))

    def unignore(self, frequency: int) -> None:
        with self.lock:
            self.conn.execute("DELETE FROM ignored WHERE frequency = ?", (frequency,))

    def ignored(self) -> list[dict]:
        return self._rows("SELECT * FROM ignored ORDER BY frequency")

    def ignored_set(self) -> set[int]:
        return {r["frequency"] for r in self._rows("SELECT frequency FROM ignored")}

    def prune_hits(self, keep_days: int) -> int:
        if keep_days <= 0:
            return 0
        with self.lock:
            return self.conn.execute("DELETE FROM hits WHERE ts < ?", (time.time() - keep_days * 86400,)).rowcount

    # ------------------------------------------------------------- stats

    def stats(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        local = time.localtime(now)
        midnight = time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))
        day_ago = now - 86400
        totals = self._one(
            """SELECT COUNT(*) AS channels, COALESCE(SUM(favourite), 0) AS favourites,
                      COUNT(DISTINCT NULLIF(band, '')) AS bands FROM channels"""
        )
        heard = self._one(
            "SELECT COUNT(DISTINCT channel_id) AS n FROM hits WHERE channel_id IS NOT NULL AND ts >= ?", (midnight,)
        )
        hits_24h = self._one("SELECT COUNT(*) AS n FROM hits WHERE ts >= ?", (day_ago,))
        # Hits per hour for the last 24 hours, oldest first
        start_hour = int(now // 3600) * 3600 - 23 * 3600
        per_hour = {
            r["hour"]: r["n"]
            for r in self._rows(
                "SELECT CAST(ts / 3600 AS INTEGER) * 3600 AS hour, COUNT(*) AS n FROM hits WHERE ts >= ? GROUP BY hour",
                (start_hour,),
            )
        }
        hours = [{"ts": start_hour + i * 3600, "hits": per_hour.get(start_hour + i * 3600, 0)} for i in range(24)]
        bands = self._rows(
            """SELECT c.band AS name, COUNT(*) AS channels,
                      SUM(CASE WHEN EXISTS (SELECT 1 FROM hits h WHERE h.channel_id = c.id AND h.ts >= ?) THEN 1 ELSE 0 END) AS heard
               FROM channels c GROUP BY c.band ORDER BY channels DESC""",
            (midnight,),
        )
        top = self._rows(
            """SELECT c.id, c.number, c.name, c.frequency, c.mode, c.band, COUNT(*) AS hits, MAX(h.ts) AS last
               FROM hits h JOIN channels c ON c.id = h.channel_id WHERE h.ts >= ?
               GROUP BY c.id ORDER BY hits DESC LIMIT 10""",
            (now - 7 * 86400,),
        )
        return {
            "channels": totals["channels"],
            "favourites": totals["favourites"],
            "bands": totals["bands"],
            "heard_today": heard["n"],
            "hits_24h": hits_24h["n"],
            "hours": hours,
            "per_band": bands,
            "top": top,
            "hits_total": self._one("SELECT COUNT(*) AS n FROM hits")["n"],
        }

    def counts(self) -> dict:
        return {
            "channels": self._one("SELECT COUNT(*) AS n FROM channels")["n"],
            "hits": self._one("SELECT COUNT(*) AS n FROM hits")["n"],
        }

    # ------------------------------------------------------------- backups

    def backup_to(self, target: Path) -> None:
        target = Path(target)
        tmp = target.with_name(target.name + ".tmp")
        dest = sqlite3.connect(tmp)
        try:
            with self.lock:
                self.conn.backup(dest)
        finally:
            dest.close()
        tmp.replace(target)

    def restore_from(self, source: Path) -> None:
        check_backup(source)
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            with self.lock:
                src.backup(self.conn)
                self.conn.executescript(SCHEMA)
        finally:
            src.close()


def check_backup(path: Path) -> None:
    """Raises ValidationError unless the file is an SDRadio database."""
    path = Path(path)
    try:
        with path.open("rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                raise ValidationError("This is not an SDRadio backup (not an SQLite database)")
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        finally:
            conn.close()
    except (OSError, sqlite3.Error) as e:
        raise ValidationError(f"Cannot read the backup: {e}") from e
    if not {"channels", "hits", "settings"} <= tables:
        raise ValidationError("This is not an SDRadio backup (no channels table)")


def _hz(value, label: str) -> int:
    try:
        number = round(float(value))
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be a number") from None
    if number <= 0:
        raise ValidationError(f"{label} must be above 0")
    return number


def _optional_float(value, label: str, low: float, high: float) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValidationError(f"{label} must be a number") from None
    if not low <= number <= high:
        raise ValidationError(f"{label} must be between {low:g} and {high:g}")
    return number


def _mode(value) -> str:
    mode = str(value or "").strip().lower()
    mode = MODE_ALIASES.get(mode, mode)
    if mode not in MODES:
        raise ValidationError(f"Unknown mode {value!r}: use one of {', '.join(m.upper() for m in MODES)}")
    return mode


def _csv_row(record: dict) -> dict:
    raw = (record.get("frequency_mhz") or "").replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        raise ValidationError(f"frequency {raw!r} is not a number") from None
    # Values this large are in Hz already
    frequency = round(value if value >= 100_000 else value * 1e6)
    mode = _mode(record.get("mode") or "nfm")

    def khz(key: str) -> int | None:
        text = (record.get(key) or "").replace(",", ".").lower().removesuffix("khz").strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            raise ValidationError(f"{key} {text!r} is not a number") from None
        return round(number if number >= 1000 else number * 1000)

    row = {
        "frequency": frequency,
        "mode": mode,
        "name": record.get("name") or "",
        "bandwidth": khz("bandwidth_khz") or default_bandwidth(mode),
        "step": khz("step_khz"),
        "squelch": record.get("squelch_db") or None,
        "band": record.get("band") or "",
        "tags": record.get("tags") or "",
        "notes": record.get("notes") or "",
        "favourite": _bool(record.get("favourite", "")),
        "scan": _bool(record.get("scan", "1") or "1"),
        "priority": _bool(record.get("priority", "")),
    }
    if record.get("number"):
        try:
            row["number"] = int(float(record["number"]))
        except ValueError:
            raise ValidationError(f"number {record['number']!r} is not a whole number") from None
    return row


def _check_setting(key: str, spec: dict, value):
    default = spec["default"]
    if "options" in spec:
        if value not in spec["options"]:
            raise ValidationError(f"{key} must be one of {', '.join(map(str, spec['options']))}")
        return value
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int | float):
        try:
            number = type(default)(value)
        except (TypeError, ValueError):
            raise ValidationError(f"{key} must be a number") from None
        if not spec["min"] <= number <= spec["max"]:
            raise ValidationError(f"{key} must be between {spec['min']} and {spec['max']}")
        return number
    if key == "backup_time":
        text = str(value).strip()
        parts = text.split(":")
        if len(parts) != 2 or not all(p.isdigit() for p in parts) or int(parts[0]) > 23 or int(parts[1]) > 59:
            raise ValidationError("The backup time must look like 03:00")
        return f"{int(parts[0]):02d}:{int(parts[1]):02d}"
    if key == "scan_range":
        if not isinstance(value, dict):
            raise ValidationError("scan_range must have start, stop, step and mode")
        start, stop = _hz(value.get("start"), "Start"), _hz(value.get("stop"), "Stop")
        if stop <= start:
            raise ValidationError("The range must end above its start")
        step = _hz(value.get("step"), "Step")
        if (stop - start) / step > 200_000:
            raise ValidationError("That range has too many steps; use a bigger step")
        return {"start": start, "stop": stop, "step": step, "mode": _mode(value.get("mode"))}
    return value
