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

from . import default_data
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
-- Place-name codes in alarm texts (UTRECH -> Utrecht)
CREATE TABLE IF NOT EXISTS city_abbreviations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    abbreviation TEXT UNIQUE NOT NULL,
    city TEXT NOT NULL
);
-- Talk group codes (BRT-01): kind 'discipline' (B -> Brandweer) or 'region' (RT -> Rotterdam)
CREATE TABLE IF NOT EXISTS talkgroup_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    code TEXT NOT NULL,
    name TEXT NOT NULL,
    UNIQUE (kind, code)
);
-- Which built-in rows were added once: a default you deleted or changed is not added
-- again at the next start or update. value: the row's values as built in (JSON)
CREATE TABLE IF NOT EXISTS speech_seeded (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    PRIMARY KEY (kind, key)
);
"""

# Reset deletes these; the speech tables (your abbreviations and rules) are kept
TABLES = ("incidents", "incident_updates", "responses", "duty", "meta")
CSV_TABLES = ("incidents", "incident_updates", "responses", "duty")
# The speech tables: their columns (also the CSV columns, in the Meldkamer app's format),
# the columns that identify a row, the ones stored in capitals, and the list order
SPEECH_TABLES = {
    "abbreviations": {
        "cols": ("abbreviation", "full_text"),
        "key": ("abbreviation",),
        "upper": ("abbreviation",),
        "order": "abbreviation",
    },
    "tts_replacements": {
        "cols": ("pattern", "replacement", "enabled"),
        "key": ("pattern",),
        "upper": (),
        "order": "id",
    },
    "city_abbreviations": {
        "cols": ("abbreviation", "city"),
        "key": ("abbreviation",),
        "upper": ("abbreviation",),
        "order": "abbreviation",
    },
    "talkgroup_codes": {
        "cols": ("kind", "code", "name"),
        "key": ("kind", "code"),
        "upper": ("code",),
        "order": "kind, code",
    },
}
SPEECH_CSV = {table: spec["cols"] for table, spec in SPEECH_TABLES.items()}
TALKGROUP_KINDS = {"discipline": 1, "region": 2}


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
        # 0.2.3: the built-in value of each seeded row
        columns = {r["name"] for r in self.conn.execute("PRAGMA table_info(speech_seeded)")}
        if "value" not in columns:
            self.conn.execute("ALTER TABLE speech_seeded ADD COLUMN value TEXT")
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

    # ------------------------------------- speech: abbreviations, rules, places, talk groups

    def seed_speech(self) -> dict[str, int]:
        """Fill the speech tables from the factory lists in src/defaults/ (see default_data.py).

        Runs at the first start of a version only (the files' signature is remembered), so the
        files are not read again after that. A row is offered once: one you changed keeps your
        text, one you deleted stays deleted, also after updates. What each built-in row was
        is remembered with it (speech_seeded.value), to mark unchanged rows as "default".
        Returns the number added per table.
        """
        marker = default_data.signature()
        with self._lock:
            if self.conn.execute(
                "SELECT 1 FROM speech_seeded WHERE kind = 'defaults' AND key = ?", (marker,)
            ).fetchone():
                return {}
        later: dict[str, set[str]] = {}
        for row in default_data.read("added_later"):
            later.setdefault(row["table"], set()).add(row["key"])
        added: dict[str, int] = {}
        current: dict[str, dict[str, dict]] = {}
        with self._lock:
            for table, spec in SPEECH_TABLES.items():
                seeded = {
                    r["key"]: r["value"]
                    for r in self.conn.execute("SELECT key, value FROM speech_seeded WHERE kind = ?", (table,))
                }
                untracked = not seeded and self.conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone() is not None
                count = 0
                current[table] = {}
                for row in default_data.read(table):
                    values = self._clean_values(table, row)
                    key = "\x1f".join(str(values[c]) for c in spec["key"])
                    current[table][key] = values
                    value = json.dumps([values[c] for c in spec["cols"] if c not in spec["key"]], ensure_ascii=False)
                    if key in seeded:
                        if seeded[key] is None:
                            self.conn.execute(
                                "UPDATE speech_seeded SET value = ? WHERE kind = ? AND key = ?", (value, table, key)
                            )
                        continue
                    if not untracked or key in later.get(table, set()):
                        cols = [c for c in spec["cols"] if c in values]
                        count += self.conn.execute(
                            f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                            [values[c] for c in cols],
                        ).rowcount
                    self.conn.execute(
                        "INSERT OR IGNORE INTO speech_seeded (kind, key, value) VALUES (?, ?, ?)", (table, key, value)
                    )
                    seeded[key] = value
                added[table] = count
            self._apply_previous_defaults(current)
            self.conn.execute("DELETE FROM speech_seeded WHERE kind = 'defaults'")
            self.conn.execute("INSERT INTO speech_seeded (kind, key) VALUES ('defaults', ?)", (marker,))
            self.conn.commit()
        return added

    def _apply_previous_defaults(self, current: dict[str, dict[str, dict]]) -> None:
        """Rows still at a value an older version shipped (src/defaults/previous.csv) get the
        current value, or are removed when that key isn't shipped any more. Once per entry;
        an entry whose migration already ran under an older version is skipped."""
        done = {r["key"] for r in self.conn.execute("SELECT key FROM speech_seeded WHERE kind = 'migration'")}
        for row in default_data.read("previous"):
            entry = "\x1f".join((row["table"], row["key"], row["column"], row["value"]))
            if entry in done or row.get("migration") in done:
                continue
            table, column = row["table"], row["column"]
            key_column = SPEECH_TABLES[table]["key"][0]
            now = current.get(table, {}).get(row["key"])
            if now is not None:
                self.conn.execute(
                    f"UPDATE {table} SET {column} = ? WHERE {key_column} = ? AND {column} = ?",
                    (now[column], row["key"], row["value"]),
                )
            else:
                self.conn.execute(
                    f"DELETE FROM {table} WHERE {key_column} = ? AND {column} = ?", (row["key"], row["value"])
                )
            self.conn.execute("INSERT OR IGNORE INTO speech_seeded (kind, key) VALUES ('migration', ?)", (entry,))

    def default_values(self, table: str) -> dict[str, tuple]:
        """{row key: the built-in values of the row} as they were added, to mark unchanged rows."""
        spec = SPEECH_TABLES[table]
        out = {}
        for r in self.conn.execute(
            "SELECT key, value FROM speech_seeded WHERE kind = ? AND value IS NOT NULL", (table,)
        ):
            out[r["key"]] = tuple(json.loads(r["value"]))
        return {k: v for k, v in out.items() if len(v) == len([c for c in spec["cols"] if c not in spec["key"]])}

    def is_default(self, table: str, row: dict, defaults: dict[str, tuple] | None = None) -> bool:
        """Whether a row is a built-in row as it was added (not changed by you)."""
        spec = SPEECH_TABLES[table]
        defaults = self.default_values(table) if defaults is None else defaults
        key = "\x1f".join(str(row.get(c)) for c in spec["key"])
        values = tuple(row.get(c) for c in spec["cols"] if c not in spec["key"])
        stored = defaults.get(key)
        return stored is not None and tuple(str(v) for v in stored) == tuple(str(v) for v in values)

    def speech_rows(self, table: str) -> list[dict]:
        """All rows of a speech table (rules: only the enabled ones, in the order they apply)."""
        spec = SPEECH_TABLES[table]
        where = " WHERE enabled = 1" if table == "tts_replacements" else ""
        return [dict(r) for r in self.conn.execute(f"SELECT * FROM {table}{where} ORDER BY {spec['order']}")]

    def abbreviation_map(self) -> dict[str, str]:
        return {r["abbreviation"]: r["full_text"] for r in self.speech_rows("abbreviations")}

    def list_speech(self, table: str, search: str = "", page: int = 1, per_page: int = 50) -> dict:
        """One page of a speech table, searched in all its text columns."""
        if table not in SPEECH_TABLES:
            raise ValueError(table)
        spec = SPEECH_TABLES[table]
        cols = [c for c in spec["cols"] if c != "enabled"]
        where, args = "", []
        if search:
            where = " WHERE " + " OR ".join(f"{c} LIKE ?" for c in cols)
            args = [f"%{search}%"] * len(cols)
        total = self.conn.execute(f"SELECT COUNT(*) FROM {table}{where}", args).fetchone()[0]
        rows = self.conn.execute(
            f"SELECT * FROM {table}{where} ORDER BY {spec['order']} LIMIT ? OFFSET ?",
            (*args, per_page, (page - 1) * per_page),
        ).fetchall()
        return {"items": [dict(r) for r in rows], "total": total, "page": page, "per_page": per_page}

    @staticmethod
    def _clean_values(table: str, row: dict) -> dict:
        """A row's values, checked. Raises ValueError for anything that can't be stored."""
        spec = SPEECH_TABLES[table]
        values = {}
        for col in spec["cols"]:
            if col == "enabled":
                raw = row.get("enabled", True)
                if isinstance(raw, str):
                    raw = raw.strip().lower() not in ("0", "false", "no", "nee", "off")
                values[col] = int(bool(raw))
                continue
            text = str(row.get(col) or "").strip()
            if col in spec["upper"]:
                text = text.upper()
            if not text:
                if table == "tts_replacements" and col == "replacement":
                    raise ValueError("The replacement is empty: a rule may not delete text from the spoken message")
                raise ValueError(f"{col} is empty")
            values[col] = text
        if table == "tts_replacements":
            # The pattern keeps its own spaces and case
            values["pattern"] = str(row.get("pattern") or "")
            try:
                re.compile(values["pattern"])
            except re.error as e:
                raise ValueError(f"Invalid pattern {values['pattern']!r}: {e}") from e
        if table == "talkgroup_codes":
            kind = values["kind"].lower()
            if kind not in TALKGROUP_KINDS:
                raise ValueError("kind must be discipline or region")
            if len(values["code"]) != TALKGROUP_KINDS[kind] or not values["code"].isalpha():
                raise ValueError(f"A {kind} code is {TALKGROUP_KINDS[kind]} letter(s)")
            values["kind"] = kind
        return values

    def save_speech(self, table: str, row: dict, row_id: int | None = None) -> int:
        """Add a row, or change the one with row_id. Returns its id.

        Adding a row whose key (code, pattern) is already there changes that row. Raises
        ValueError for invalid input or when a change would give a row another row's key.
        """
        if table not in SPEECH_TABLES:
            raise ValueError("Unknown table")
        spec = SPEECH_TABLES[table]
        values = self._clean_values(table, row)
        cols = list(spec["cols"])
        with self._lock:
            match = " AND ".join(f"{c} = ?" for c in spec["key"])
            other = self.conn.execute(
                f"SELECT id FROM {table} WHERE {match}", [values[c] for c in spec["key"]]
            ).fetchone()
            if other and other["id"] != row_id:
                if row_id is not None:
                    raise ValueError("Another row already has this " + " and ".join(spec["key"]))
                row_id = other["id"]
            if row_id is None:
                cur = self.conn.execute(
                    f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})",
                    [values[c] for c in cols],
                )
                row_id = cur.lastrowid
            else:
                cur = self.conn.execute(
                    f"UPDATE {table} SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
                    [*(values[c] for c in cols), row_id],
                )
                if not cur.rowcount:
                    raise ValueError("Row not found")
            self.conn.commit()
        return row_id

    def save_abbreviation(self, code: str, text: str, row_id: int | None = None) -> int:
        return self.save_speech("abbreviations", {"abbreviation": code, "full_text": text}, row_id)

    def save_rule(self, pattern: str, replacement: str, enabled: bool = True, row_id: int | None = None) -> int:
        return self.save_speech(
            "tts_replacements", {"pattern": pattern, "replacement": replacement, "enabled": enabled}, row_id
        )

    def delete_speech(self, table: str, row_id: int) -> bool:
        if table not in SPEECH_TABLES:
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
        if table not in SPEECH_TABLES:
            raise ValueError(table)
        spec = SPEECH_TABLES[table]
        cur = self.conn.execute(f"SELECT {', '.join(spec['cols'])} FROM {table} ORDER BY {spec['order']}")
        return list(spec["cols"]), [tuple(r) for r in cur.fetchall()]

    def import_speech_csv(self, table: str, content: str, replace: bool = False) -> dict:
        """Import a CSV export (this app's or the Meldkamer app's). Checked completely first:
        on an invalid file or pattern nothing changes and ValueError says what is wrong.

        Rows that can't be used are skipped and listed, e.g. rules with an empty replacement
        (Meldkamer has some, to leave text out; here nothing may be left out of the spoken
        text). replace=True first empties the table. Rows with a key already there are updated.
        """
        if table not in SPEECH_TABLES:
            raise ValueError("Unknown table")
        spec = SPEECH_TABLES[table]
        required = tuple(c for c in spec["cols"] if c != "enabled")
        rows = _read_csv(content, required)
        items, skipped = [], []
        for n, row in enumerate(rows, start=2):
            try:
                items.append(self._clean_values(table, row))
            except ValueError as e:
                if table == "tts_replacements" and "Invalid pattern" in str(e):
                    raise ValueError(f"Line {n}: {e}") from e
                skipped.append(f"line {n}: {e}")
        cols = list(spec["cols"])
        keys = ", ".join(spec["key"])
        updates = ", ".join(f"{c} = excluded.{c}" for c in cols if c not in spec["key"])
        sql = (
            f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)}) "
            f"ON CONFLICT({keys}) DO UPDATE SET {updates}"
        )
        with self._lock:
            try:
                if replace:
                    self.conn.execute(f"DELETE FROM {table}")
                for values in items:
                    self.conn.execute(sql, [values[c] for c in cols])
                self.conn.commit()
            except sqlite3.Error:
                self.conn.rollback()
                raise
        return {"imported": len(items), "skipped": skipped}

    def speech_counts(self) -> dict:
        return {t: self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in SPEECH_TABLES}

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
