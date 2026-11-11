"""SQLite storage: incidents (with their updates), responses and duty changes, plus the
abbreviations and replacement rules that make the spoken text of an incident (speech.py).

Everything is keyed so that storing the same Home Assistant state twice is a no-op:
incidents on FireServiceRota's incident id, incident updates, responses and duty changes
on the state's last_updated timestamp. A history import can run any number of times.
"""

import contextlib
import csv
import io
import json
import logging
import os
import re
import sqlite3
import threading
from typing import Any

from .timeutil import parse_ts

logger = logging.getLogger(__name__)

# 2: abbreviations and tts_replacements (the spoken text). Every table is CREATE TABLE IF NOT
# EXISTS, so opening an older database adds the new tables and keeps all its data.
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    id TEXT PRIMARY KEY,
    body TEXT,
    prio TEXT,
    type TEXT,
    trigger TEXT,
    state TEXT,
    responder_mode TEXT,
    created_at TEXT,
    can_respond_until TEXT,
    latitude REAL,
    longitude REAL,
    address_type TEXT,
    formatted_address TEXT,
    message_to_speech_url TEXT,
    task_ids TEXT,
    attributes TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_incidents_first_seen ON incidents (first_seen);
CREATE TABLE IF NOT EXISTS incident_updates (
    ts TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL,
    trigger TEXT,
    state TEXT,
    body TEXT,
    attributes TEXT
);
CREATE INDEX IF NOT EXISTS idx_updates_incident ON incident_updates (incident_id, ts);
CREATE TABLE IF NOT EXISTS responses (
    ts TEXT PRIMARY KEY,
    incident_id TEXT,
    state TEXT NOT NULL,
    status TEXT,
    responded_at TEXT,
    reported_status TEXT,
    arrived_at_station TEXT,
    attributes TEXT
);
CREATE INDEX IF NOT EXISTS idx_responses_incident ON responses (incident_id, ts);
CREATE TABLE IF NOT EXISTS duty (
    ts TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    available INTEGER,
    active INTEGER,
    assigned_function TEXT,
    attributes TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
-- The spoken text: the same columns as the Meldkamer app, so their CSV files fit both
CREATE TABLE IF NOT EXISTS abbreviations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    abbreviation TEXT UNIQUE NOT NULL,
    full_text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_abbreviations_abbrev ON abbreviations(abbreviation);
CREATE TABLE IF NOT EXISTS tts_replacements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT UNIQUE NOT NULL,
    replacement TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1
);
-- Which built-in abbreviations and rules were added once: a default you deleted or
-- changed is not added again at the next start
CREATE TABLE IF NOT EXISTS speech_seeded (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    PRIMARY KEY (kind, key)
);
"""

# Reset deletes these; the speech tables (your abbreviations and rules) are kept
TABLES = ("incidents", "incident_updates", "responses", "duty", "meta")
CSV_TABLES = ("incidents", "incident_updates", "responses", "duty")
# Speech tables, exported and imported as CSV in the Meldkamer app's format
SPEECH_CSV = {
    "abbreviations": ("abbreviation", "full_text"),
    "tts_replacements": ("pattern", "replacement", "enabled"),
}


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> int | None:
    return None if value is None else int(bool(value))


def _read_csv(content: str, required: tuple[str, ...]) -> list[dict[str, str]]:
    """Rows of a CSV file as dicts by lowercase header, comma, semicolon or tab separated
    (like the Meldkamer app). Raises ValueError for unusable input."""
    if not isinstance(content, str):
        raise ValueError("CSV content must be text")
    text = content.lstrip("﻿")
    if not text.strip():
        raise ValueError("The CSV file is empty")
    header_line = text.splitlines()[0]
    for delimiter in (",", ";", "\t"):
        header = [h.strip().lower() for h in next(csv.reader([header_line], delimiter=delimiter))]
        if all(col in header for col in required):
            break
    else:
        raise ValueError(f"The CSV header must have the column(s) {', '.join(required)} (got {header_line[:100]!r})")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter, strict=True)
    try:
        rows = list(reader)
    except csv.Error as e:
        raise ValueError(f"Invalid CSV near line {reader.line_num}: {e}") from e
    return [dict(zip(header, (v.strip() for v in row), strict=False)) for row in rows[1:] if any(row)]


class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # Used from the event loop; backups run in a thread, hence the lock
        self._lock = threading.RLock()
        self._open()

    def _open(self) -> None:
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._set_schema_version()

    def _set_schema_version(self) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
        )
        self.conn.commit()

    def close(self) -> None:
        with self._lock, contextlib.suppress(sqlite3.Error):
            self.conn.close()

    # ------------------------------------------------------------------ writes

    def store_incident(self, attrs: dict, body: str | None, ts: str, source: str) -> tuple[bool, bool]:
        """Store an incident state. Returns (new incident, new update).

        The same incident id again (trigger "update", or the state restored after a
        Home Assistant restart) updates the stored incident and adds an update row
        when it differs; it never creates a second incident.
        """
        incident_id = str(attrs["id"])
        fields = {
            "body": body,
            "prio": attrs.get("prio"),
            "type": attrs.get("type"),
            "trigger": attrs.get("trigger"),
            "state": attrs.get("state"),
            "responder_mode": attrs.get("responder_mode"),
            "created_at": attrs.get("created_at"),
            "can_respond_until": attrs.get("can_respond_until"),
            "latitude": _float(attrs.get("latitude")),
            "longitude": _float(attrs.get("longitude")),
            "address_type": attrs.get("address_type"),
            "formatted_address": attrs.get("formatted_address"),
            "message_to_speech_url": attrs.get("message_to_speech_url"),
            "task_ids": _json(attrs.get("task_ids")),
            "attributes": _json(attrs),
        }
        with self._lock:
            row = self.conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
            new = row is None
            if new:
                self.conn.execute(
                    f"INSERT INTO incidents (id, {', '.join(fields)}, first_seen, last_seen, source) "
                    f"VALUES (?, {', '.join('?' for _ in fields)}, ?, ?, ?)",
                    (incident_id, *fields.values(), ts, ts, source),
                )
            else:
                # A text that Home Assistant could not store (over 255 characters) keeps the one we have
                if body is None:
                    fields["body"] = row["body"]
                # History may be imported after live states: keep the earliest first_seen, latest last_seen
                self.conn.execute(
                    f"UPDATE incidents SET {', '.join(f'{k} = ?' for k in fields)}, "
                    "first_seen = MIN(first_seen, ?), last_seen = MAX(last_seen, ?) WHERE id = ?",
                    (*fields.values(), ts, ts, incident_id),
                )
            previous = self.conn.execute(
                "SELECT * FROM incident_updates WHERE incident_id = ? AND ts < ? ORDER BY ts DESC LIMIT 1",
                (incident_id, ts),
            ).fetchone()
            changed = previous is None or (previous["body"], previous["state"], previous["trigger"]) != (
                body,
                attrs.get("state"),
                attrs.get("trigger"),
            )
            added = False
            if changed:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO incident_updates (ts, incident_id, trigger, state, body, attributes) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ts, incident_id, attrs.get("trigger"), attrs.get("state"), body, _json(attrs)),
                )
                added = cur.rowcount > 0
            self.conn.commit()
        return new, added

    def store_response(self, incident_id: str | None, state: str, attrs: dict, ts: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO responses (ts, incident_id, state, status, responded_at, reported_status, "
                "arrived_at_station, attributes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    incident_id,
                    state,
                    attrs.get("status"),
                    attrs.get("responded_at"),
                    _json(attrs.get("reported_status"))
                    if isinstance(attrs.get("reported_status"), dict | list)
                    else attrs.get("reported_status"),
                    _json(attrs.get("arrived_at_station"))
                    if isinstance(attrs.get("arrived_at_station"), dict | list)
                    else attrs.get("arrived_at_station"),
                    _json(attrs),
                ),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def store_duty(self, state: str, attrs: dict, ts: str) -> bool:
        with self._lock:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO duty (ts, state, start_time, end_time, available, active, assigned_function, "
                "attributes) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ts,
                    state,
                    attrs.get("start_time"),
                    attrs.get("end_time"),
                    _bool(attrs.get("available")),
                    _bool(attrs.get("active")),
                    attrs.get("assigned_function"),
                    _json(attrs),
                ),
            )
            self.conn.commit()
            return cur.rowcount > 0

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
            self.conn.commit()

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    # ------------------------------------------------------------------- reads

    def incident_at(self, ts: str) -> str | None:
        """The incident that was current at ts: the newest one first seen at or before it."""
        row = self.conn.execute(
            "SELECT id FROM incidents WHERE first_seen <= ? ORDER BY first_seen DESC LIMIT 1", (ts,)
        ).fetchone()
        return row["id"] if row else None

    def last_response_state(self) -> tuple[str, str] | None:
        row = self.conn.execute("SELECT state, attributes FROM responses ORDER BY ts DESC LIMIT 1").fetchone()
        return (row["state"], row["attributes"] or "") if row else None

    def last_duty_state(self) -> tuple[str, str] | None:
        row = self.conn.execute("SELECT state, attributes FROM duty ORDER BY ts DESC LIMIT 1").fetchone()
        return (row["state"], row["attributes"] or "") if row else None

    def latest_ts(self) -> str | None:
        """The newest stored state timestamp of any kind, to import a gap from."""
        row = self.conn.execute(
            "SELECT MAX(ts) AS ts FROM (SELECT MAX(last_seen) AS ts FROM incidents "
            "UNION ALL SELECT MAX(ts) FROM responses UNION ALL SELECT MAX(ts) FROM duty)"
        ).fetchone()
        return row["ts"] if row else None

    def _with_response(self, rows: list[sqlite3.Row]) -> list[dict]:
        """Incidents as dicts, each with the response that counts for it."""
        items = [dict(r) for r in rows]
        if not items:
            return items
        ids = [i["id"] for i in items]
        marks = ",".join("?" for _ in ids)
        responses: dict[str, list[dict]] = {}
        for r in self.conn.execute(f"SELECT * FROM responses WHERE incident_id IN ({marks}) ORDER BY ts", ids):
            responses.setdefault(r["incident_id"], []).append(dict(r))
        updates: dict[str, int] = {
            r["incident_id"]: r["n"]
            for r in self.conn.execute(
                f"SELECT incident_id, COUNT(*) AS n FROM incident_updates WHERE incident_id IN ({marks}) GROUP BY incident_id",
                ids,
            )
        }
        for item in items:
            item["task_ids"] = json.loads(item["task_ids"]) if item.get("task_ids") else None
            item.pop("attributes", None)
            item["response"] = response_outcome(item, responses.get(item["id"], []))
            item["updates"] = updates.get(item["id"], 0)
        return items

    def list_incidents(self, limit: int = 50, before: str | None = None, search: str = "") -> dict:
        where, args = [], []
        if before:
            where.append("first_seen < ?")
            args.append(before)
        if search:
            like = f"%{search}%"
            where.append("(body LIKE ? OR formatted_address LIKE ? OR type LIKE ? OR prio LIKE ? OR id LIKE ?)")
            args += [like] * 5
        sql = "SELECT * FROM incidents" + (f" WHERE {' AND '.join(where)}" if where else "")
        rows = self.conn.execute(sql + " ORDER BY first_seen DESC LIMIT ?", (*args, limit + 1)).fetchall()
        items = self._with_response(rows[:limit])
        return {
            "items": items,
            "has_more": len(rows) > limit,
            "next_before": items[-1]["first_seen"] if items else None,
        }

    def get_incident(self, incident_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
        if row is None:
            return None
        item = self._with_response([row])[0]
        item["attributes"] = json.loads(row["attributes"] or "{}")
        item["responses"] = [
            dict(r)
            for r in self.conn.execute("SELECT * FROM responses WHERE incident_id = ? ORDER BY ts", (incident_id,))
        ]
        for r in item["responses"]:
            r.pop("attributes", None)
        item["timeline"] = [
            {k: r[k] for k in ("ts", "trigger", "state", "body")}
            for r in self.conn.execute(
                "SELECT * FROM incident_updates WHERE incident_id = ? ORDER BY ts", (incident_id,)
            )
        ]
        return item

    def all_incidents(self, since: str | None = None) -> list[dict]:
        sql = "SELECT * FROM incidents" + (" WHERE first_seen >= ?" if since else "") + " ORDER BY first_seen"
        return self._with_response(self.conn.execute(sql, (since,) if since else ()).fetchall())

    def duty_rows(self, since: str | None = None) -> list[dict]:
        """Duty changes from (just before) since on, oldest first."""
        if since:
            before = self.conn.execute("SELECT * FROM duty WHERE ts < ? ORDER BY ts DESC LIMIT 1", (since,)).fetchall()
            after = self.conn.execute("SELECT * FROM duty WHERE ts >= ? ORDER BY ts", (since,)).fetchall()
            return [dict(r) for r in [*before, *after]]
        return [dict(r) for r in self.conn.execute("SELECT * FROM duty ORDER BY ts")]

    def counts(self) -> dict:
        counts = {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in CSV_TABLES}
        first = self.conn.execute("SELECT MIN(first_seen) FROM incidents").fetchone()[0]
        counts["first_incident"] = first
        counts["bytes"] = sum(
            os.path.getsize(self.db_path + s) for s in ("", "-wal") if os.path.exists(self.db_path + s)
        )
        return counts

    def table_rows(self, table: str) -> tuple[list[str], list[tuple]]:
        if table not in CSV_TABLES:
            raise ValueError(table)
        order = "first_seen" if table == "incidents" else "ts"
        cur = self.conn.execute(f"SELECT * FROM {table} ORDER BY {order}")
        return [d[0] for d in cur.description], [tuple(r) for r in cur.fetchall()]

    # ---------------------------------------------- abbreviations and rules (speech)

    def seed_speech(self, abbreviations: dict[str, str], rules: list[dict]) -> tuple[int, int]:
        """Add the built-in abbreviations and rules that were never added before.

        Each one is added once: one you changed keeps your text, one you deleted stays deleted.
        Returns (abbreviations added, rules added).
        """
        added_abbr = added_rules = 0
        with self._lock:
            seeded = {(r["kind"], r["key"]) for r in self.conn.execute("SELECT kind, key FROM speech_seeded")}
            for code, text in abbreviations.items():
                code = code.upper()
                if ("abbreviation", code) in seeded:
                    continue
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO abbreviations (abbreviation, full_text) VALUES (?, ?)", (code, text)
                )
                added_abbr += cur.rowcount
                self.conn.execute("INSERT OR IGNORE INTO speech_seeded (kind, key) VALUES ('abbreviation', ?)", (code,))
            for rule in rules:
                if ("rule", rule["pattern"]) in seeded:
                    continue
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO tts_replacements (pattern, replacement) VALUES (?, ?)",
                    (rule["pattern"], rule["replacement"]),
                )
                added_rules += cur.rowcount
                self.conn.execute(
                    "INSERT OR IGNORE INTO speech_seeded (kind, key) VALUES ('rule', ?)", (rule["pattern"],)
                )
            self.conn.commit()
        return added_abbr, added_rules

    def abbreviation_map(self) -> dict[str, str]:
        return {
            r["abbreviation"]: r["full_text"]
            for r in self.conn.execute("SELECT abbreviation, full_text FROM abbreviations")
        }

    def enabled_rules(self) -> list[dict]:
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT id, pattern, replacement FROM tts_replacements WHERE enabled = 1 ORDER BY id"
            )
        ]

    def list_speech(self, table: str, search: str = "", page: int = 1, per_page: int = 50) -> dict:
        """One page of abbreviations (by code) or rules (in the order they are applied)."""
        if table == "abbreviations":
            cols, order = ("abbreviation", "full_text"), "abbreviation"
        elif table == "tts_replacements":
            cols, order = ("pattern", "replacement"), "id"
        else:
            raise ValueError(table)
        where, args = "", []
        if search:
            where = f" WHERE {cols[0]} LIKE ? OR {cols[1]} LIKE ?"
            args = [f"%{search}%"] * 2
        total = self.conn.execute(f"SELECT COUNT(*) FROM {table}{where}", args).fetchone()[0]
        rows = self.conn.execute(
            f"SELECT * FROM {table}{where} ORDER BY {order} LIMIT ? OFFSET ?", (*args, per_page, (page - 1) * per_page)
        ).fetchall()
        return {"items": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}

    def save_abbreviation(self, code: str, text: str, row_id: int | None = None) -> int:
        """Add an abbreviation, or change the one with row_id. Returns its id.

        Raises ValueError for an empty code or text, or a code that another row already has.
        """
        code, text = code.strip().upper(), text.strip()
        if not code or not text:
            raise ValueError("Both the abbreviation and its meaning are needed")
        with self._lock:
            other = self.conn.execute("SELECT id FROM abbreviations WHERE abbreviation = ?", (code,)).fetchone()
            if other and other["id"] != row_id:
                if row_id is not None:
                    raise ValueError(f"{code} is already in the list")
                row_id = other["id"]
            if row_id is None:
                cur = self.conn.execute(
                    "INSERT INTO abbreviations (abbreviation, full_text) VALUES (?, ?)", (code, text)
                )
                row_id = cur.lastrowid
            else:
                cur = self.conn.execute(
                    "UPDATE abbreviations SET abbreviation = ?, full_text = ? WHERE id = ?", (code, text, row_id)
                )
                if not cur.rowcount:
                    raise ValueError("Abbreviation not found")
            self.conn.commit()
        return row_id

    def save_rule(self, pattern: str, replacement: str, enabled: bool = True, row_id: int | None = None) -> int:
        """Add a replacement rule, or change the one with row_id. Returns its id.

        Raises ValueError for an invalid pattern, an empty replacement (it would delete
        text from the spoken message) or a pattern that another rule already has.
        """
        if not pattern:
            raise ValueError("The pattern is empty")
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"Invalid pattern: {e}") from e
        if not replacement.strip():
            raise ValueError("The replacement is empty: a rule may not delete text from the spoken message")
        with self._lock:
            other = self.conn.execute("SELECT id FROM tts_replacements WHERE pattern = ?", (pattern,)).fetchone()
            if other and other["id"] != row_id:
                if row_id is not None:
                    raise ValueError("Another rule has this pattern")
                row_id = other["id"]
            if row_id is None:
                cur = self.conn.execute(
                    "INSERT INTO tts_replacements (pattern, replacement, enabled) VALUES (?, ?, ?)",
                    (pattern, replacement, int(enabled)),
                )
                row_id = cur.lastrowid
            else:
                cur = self.conn.execute(
                    "UPDATE tts_replacements SET pattern = ?, replacement = ?, enabled = ? WHERE id = ?",
                    (pattern, replacement, int(enabled), row_id),
                )
                if not cur.rowcount:
                    raise ValueError("Rule not found")
            self.conn.commit()
        return row_id

    def delete_speech(self, table: str, row_id: int) -> bool:
        if table not in SPEECH_CSV:
            raise ValueError(table)
        with self._lock:
            cur = self.conn.execute(f"DELETE FROM {table} WHERE id = ?", (row_id,))
            self.conn.commit()
            return cur.rowcount > 0

    def import_abbreviations(self, abbrevs: list[dict[str, str]], replace: bool = True) -> int:
        """Add abbreviations ({abbreviation, full_text} dicts, as abbreviation_import gives them);
        replace=True also overwrites the meaning of codes already in the list. Returns the count."""
        sql = "INSERT INTO abbreviations (abbreviation, full_text) VALUES (?, ?) ON CONFLICT(abbreviation) DO " + (
            "UPDATE SET full_text = excluded.full_text" if replace else "NOTHING"
        )
        count = 0
        with self._lock:
            for item in abbrevs:
                code, text = (item.get("abbreviation") or "").strip().upper(), (item.get("full_text") or "").strip()
                if code and text:
                    count += self.conn.execute(sql, (code, text)).rowcount
            self.conn.commit()
        return count

    def speech_csv(self, table: str) -> tuple[list[str], list[tuple]]:
        if table not in SPEECH_CSV:
            raise ValueError(table)
        cols = SPEECH_CSV[table]
        order = "abbreviation" if table == "abbreviations" else "id"
        cur = self.conn.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}")
        return list(cols), [tuple(r) for r in cur.fetchall()]

    def import_speech_csv(self, table: str, content: str, replace: bool = False) -> dict:
        """Import a CSV export (this app's or the Meldkamer app's). Checked completely first:
        on any problem nothing changes and ValueError says what is wrong.

        Rules with an empty replacement (Meldkamer has some, to leave text out) are skipped:
        here nothing may be left out of the spoken text. replace=True first empties the table.
        """
        if table not in SPEECH_CSV:
            raise ValueError("Unknown table")
        required = SPEECH_CSV[table][:2]
        rows = _read_csv(content, required)
        items, skipped = [], []
        for n, row in enumerate(rows, start=2):
            first, second = (row.get(c, "") for c in required)
            if table == "abbreviations":
                if not first or not second:
                    skipped.append(f"line {n}: empty abbreviation or meaning")
                    continue
                items.append((first.upper(), second, None))
            else:
                if not first:
                    skipped.append(f"line {n}: empty pattern")
                    continue
                try:
                    re.compile(first)
                except re.error as e:
                    raise ValueError(f"Line {n}: invalid pattern {first!r}: {e}") from e
                if not second.strip():
                    skipped.append(f"line {n}: {first!r} has an empty replacement (would delete text)")
                    continue
                enabled = (row.get("enabled") or "1").strip().lower() not in ("0", "false", "no", "nee")
                items.append((first, second, int(enabled)))
        with self._lock:
            try:
                if replace:
                    self.conn.execute(f"DELETE FROM {table}")
                for first, second, enabled in items:
                    if table == "abbreviations":
                        self.conn.execute(
                            "INSERT INTO abbreviations (abbreviation, full_text) VALUES (?, ?) "
                            "ON CONFLICT(abbreviation) DO UPDATE SET full_text = excluded.full_text",
                            (first, second),
                        )
                    else:
                        self.conn.execute(
                            "INSERT INTO tts_replacements (pattern, replacement, enabled) VALUES (?, ?, ?) "
                            "ON CONFLICT(pattern) DO UPDATE SET replacement = excluded.replacement, "
                            "enabled = excluded.enabled",
                            (first, second, enabled),
                        )
                self.conn.commit()
            except sqlite3.Error:
                self.conn.rollback()
                raise
        return {"imported": len(items), "skipped": skipped}

    def speech_counts(self) -> dict:
        return {
            "abbreviations": self.conn.execute("SELECT COUNT(*) FROM abbreviations").fetchone()[0],
            "tts_replacements": self.conn.execute("SELECT COUNT(*) FROM tts_replacements").fetchone()[0],
        }

    # -------------------------------------------------------- backup and reset

    def backup_to(self, path: str) -> None:
        """Write a consistent copy of the database to path (SQLite online backup)."""
        with self._lock:
            dest = sqlite3.connect(path)
            try:
                self.conn.backup(dest)
            finally:
                dest.close()

    @staticmethod
    def check_backup(path: str) -> str | None:
        """Why the file at path can't be restored, or None when it's a sound FireServiceRota database."""
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    return "The file is a damaged SQLite database"
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            finally:
                conn.close()
        except sqlite3.Error:
            return "The file is not an SQLite database"
        if not {"incidents", "responses", "duty"} <= tables:
            return "The file is an SQLite database, but not a FireServiceRota one"
        return None

    def replace_with(self, path: str) -> None:
        """Replace the database file with the (checked) backup at path and reopen it."""
        with self._lock:
            self.close()
            for suffix in ("", "-wal", "-shm", "-journal"):
                with contextlib.suppress(FileNotFoundError):
                    os.remove(self.db_path + suffix)
            os.replace(path, self.db_path)
            self._open()

    def reset(self) -> None:
        with self._lock:
            for table in TABLES:
                self.conn.execute(f"DROP TABLE IF EXISTS {table}")
            self.conn.commit()
            self.conn.executescript(SCHEMA)
            self._set_schema_version()
            self.conn.execute("VACUUM")


def response_outcome(incident: dict, responses: list[dict]) -> dict:
    """What you answered to an incident: acknowledged, rejected or none, from its response rows.

    The switch keeps showing the previous incident's answer until the integration has
    fetched the new one, so a response given before the incident was created doesn't count.
    """
    created = incident.get("created_at") or incident.get("first_seen") or ""
    outcome = {"status": "none", "responded_at": None, "reported_status": None, "arrived_at_station": None}
    for r in responses:
        status = r.get("status")
        if not status and r.get("state") in ("on", "off"):
            status = "acknowledged" if r["state"] == "on" else None
        responded_at = r.get("responded_at")
        if responded_at and created and _cmp_ts(responded_at) < _cmp_ts(created):
            continue
        if status in ("acknowledged", "rejected"):
            outcome = {
                "status": status,
                "responded_at": responded_at or r.get("ts"),
                "reported_status": r.get("reported_status"),
                "arrived_at_station": r.get("arrived_at_station"),
            }
        elif outcome["status"] != "none":
            # Later details (arrived at station, reported status) of the same answer
            outcome["reported_status"] = r.get("reported_status") or outcome["reported_status"]
            outcome["arrived_at_station"] = r.get("arrived_at_station") or outcome["arrived_at_station"]
    return outcome


def _cmp_ts(value: str) -> float:
    """A timestamp as epoch seconds, for comparing values with different offsets."""
    parsed = parse_ts(value)
    return parsed.timestamp() if parsed else 0.0
