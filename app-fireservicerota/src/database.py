"""SQLite storage: incidents (with their updates), responses and duty changes.

Everything is keyed so that storing the same Home Assistant state twice is a no-op:
incidents on FireServiceRota's incident id, incident updates, responses and duty changes
on the state's last_updated timestamp. A history import can run any number of times.
"""

import contextlib
import json
import logging
import os
import sqlite3
import threading
from typing import Any

from .timeutil import parse_ts

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

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
"""

TABLES = ("incidents", "incident_updates", "responses", "duty", "meta")
CSV_TABLES = ("incidents", "incident_updates", "responses", "duty")


def _json(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> int | None:
    return None if value is None else int(bool(value))


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
        self.conn.execute(
            "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
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
            self.conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema_version', ?)", (str(SCHEMA_VERSION),)
            )
            self.conn.commit()
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
