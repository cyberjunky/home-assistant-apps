"""SQLite storage for the Nuclei Scanner: scan objects, scans and findings as tickets.

All of it lives in one file in the app's /data folder, so Home Assistant backs it up, and the
dashboard can download a copy or wipe it (the danger zone), like the other apps here. Every call comes
from the event loop and is short.
"""

import sqlite3
import time
from pathlib import Path

from .config import DATA_DIR
from .devices import services

DB_FILE = DATA_DIR / "nuclei.db"

# Ticket life cycle. A finding starts open and is moved by hand; a repeat scan can reopen or auto-close.
TICKET_STATUSES = ("open", "in_progress", "false_positive", "wont_fix", "resolved")
OPEN_STATUSES = ("open", "in_progress")

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_objects (
    id INTEGER PRIMARY KEY,
    target TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    name TEXT,
    authorised INTEGER NOT NULL DEFAULT 0,
    authorised_at INTEGER,
    schedule TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    last_scan_at INTEGER
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    object_id INTEGER REFERENCES scan_objects(id) ON DELETE SET NULL,
    target TEXT NOT NULL,
    trigger TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    hosts_total INTEGER DEFAULT 0,
    hosts_done INTEGER DEFAULT 0,
    open_ports INTEGER DEFAULT 0,
    findings INTEGER DEFAULT 0,
    error TEXT,
    detail TEXT
);
-- A finding is a ticket. fingerprint identifies "the same issue on the same host" across scans, so a
-- repeat finding updates its ticket (last_seen, times_seen) instead of making a duplicate.
CREATE TABLE IF NOT EXISTS findings (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    object_id INTEGER REFERENCES scan_objects(id) ON DELETE SET NULL,
    host TEXT NOT NULL,
    port INTEGER,
    kind TEXT NOT NULL,
    template_id TEXT,
    name TEXT NOT NULL,
    severity TEXT NOT NULL,
    cve TEXT,
    cvss REAL,
    matched_at TEXT,
    proof TEXT,
    description TEXT,
    remediation TEXT,
    reference TEXT,
    status TEXT NOT NULL DEFAULT 'open',
    note TEXT,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    times_seen INTEGER NOT NULL DEFAULT 1,
    resolved_at INTEGER
);
-- A device is a host the scans found. The app guesses a type from open ports and detections; the person
-- renames it, describes it and tags it. Those edits are never overwritten by a later scan.
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    host TEXT NOT NULL UNIQUE,
    object_id INTEGER REFERENCES scan_objects(id) ON DELETE SET NULL,
    name TEXT,
    description TEXT,
    tags TEXT NOT NULL DEFAULT '',
    guessed_type TEXT,
    signals TEXT NOT NULL DEFAULT '',
    open_ports TEXT NOT NULL DEFAULT '',
    online INTEGER,
    last_online INTEGER,
    last_checked INTEGER,
    status_changed_at INTEGER,
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS ticket_events (
    id INTEGER PRIMARY KEY,
    finding_id INTEGER NOT NULL REFERENCES findings(id) ON DELETE CASCADE,
    ts INTEGER NOT NULL,
    field TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT
);
CREATE INDEX IF NOT EXISTS findings_status ON findings (status);
CREATE INDEX IF NOT EXISTS findings_object ON findings (object_id);
CREATE INDEX IF NOT EXISTS scans_started ON scans (started_at);
"""


# Columns added after the first release. CREATE TABLE IF NOT EXISTS leaves an existing table alone, so a
# database made by an older version gets them added when it is opened. Add new columns here, never only in
# SCHEMA, or an update breaks every existing installation.
ADDED_COLUMNS = {
    "devices": (
        ("online", "INTEGER"),
        ("last_online", "INTEGER"),
        ("last_checked", "INTEGER"),
        ("status_changed_at", "INTEGER"),
    ),
}


def backup_copy(source: Path, target: Path) -> None:
    """A consistent copy of the database while the app keeps writing, on its own connection."""
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as frm, sqlite3.connect(target) as to:
        frm.backup(to)
    to.close()
    frm.close()


def database_report(source: Path) -> dict:
    """Row counts and size, for the Settings page, on its own connection."""
    db = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30)
    try:
        counts = {}
        for table in ("scan_objects", "scans", "findings", "ticket_events", "devices"):
            counts[table] = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        wal = Path(f"{source}-wal")
        return {
            "size": source.stat().st_size if source.exists() else 0,
            "wal_size": wal.stat().st_size if wal.exists() else 0,
            "counts": counts,
        }
    finally:
        db.close()


class Database:
    """The app's database. Short calls from the event loop."""

    def __init__(self, path: Path = DB_FILE):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA foreign_keys=ON")
        self._db.executescript(SCHEMA)
        self._add_missing_columns()
        self._db.commit()

    def _add_missing_columns(self) -> None:
        for table, columns in ADDED_COLUMNS.items():
            have = {row[1] for row in self._db.execute(f"PRAGMA table_info({table})")}
            for name, kind in columns:
                if name not in have:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")

    def close(self) -> None:
        self._db.close()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def _row(self, sql: str, params: tuple = ()) -> dict | None:
        r = self._db.execute(sql, params).fetchone()
        return dict(r) if r else None

    # -- Scan objects -------------------------------------------------------
    def add_object(self, target: str, kind: str, name: str, schedule: str | None, authorised: bool) -> int:
        now = int(time.time())
        cur = self._db.execute(
            "INSERT INTO scan_objects (target, kind, name, schedule, authorised, authorised_at, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (target, kind, name, schedule, int(authorised), now if authorised else None, now),
        )
        self._db.commit()
        return cur.lastrowid

    def update_object(self, object_id: int, fields: dict) -> None:
        allowed = {"name", "schedule", "enabled", "authorised"}
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return
        if "authorised" in sets:
            sets["authorised_at"] = int(time.time()) if sets["authorised"] else None
        columns = ", ".join(f"{k} = ?" for k in sets)
        self._db.execute(f"UPDATE scan_objects SET {columns} WHERE id = ?", (*sets.values(), object_id))
        self._db.commit()

    def delete_object(self, object_id: int) -> None:
        self._db.execute("DELETE FROM scan_objects WHERE id = ?", (object_id,))
        self._db.commit()

    def objects(self) -> list[dict]:
        return self._rows("SELECT * FROM scan_objects ORDER BY created_at")

    def object(self, object_id: int) -> dict | None:
        return self._row("SELECT * FROM scan_objects WHERE id = ?", (object_id,))

    def object_by_target(self, target: str) -> dict | None:
        return self._row("SELECT * FROM scan_objects WHERE target = ?", (target,))

    # -- Scans --------------------------------------------------------------
    def start_scan(self, object_id: int | None, target: str, trigger: str) -> int:
        cur = self._db.execute(
            "INSERT INTO scans (object_id, target, trigger, status, started_at) VALUES (?, ?, ?, 'running', ?)",
            (object_id, target, trigger, int(time.time())),
        )
        self._db.commit()
        return cur.lastrowid

    def update_scan(self, scan_id: int, fields: dict) -> None:
        if not fields:
            return
        columns = ", ".join(f"{k} = ?" for k in fields)
        self._db.execute(f"UPDATE scans SET {columns} WHERE id = ?", (*fields.values(), scan_id))
        self._db.commit()

    def finish_scan(self, scan_id: int, status: str, error: str | None = None) -> None:
        self._db.execute(
            "UPDATE scans SET status = ?, finished_at = ?, error = ? WHERE id = ?",
            (status, int(time.time()), error, scan_id),
        )
        if (obj := self._row("SELECT object_id FROM scans WHERE id = ?", (scan_id,))) and obj["object_id"]:
            self._db.execute(
                "UPDATE scan_objects SET last_scan_at = ? WHERE id = ?", (int(time.time()), obj["object_id"])
            )
        self._db.commit()

    def scans(self, limit: int = 100) -> list[dict]:
        return self._rows("SELECT * FROM scans ORDER BY started_at DESC LIMIT ?", (limit,))

    def running_scans(self) -> list[dict]:
        return self._rows("SELECT * FROM scans WHERE status = 'running' ORDER BY started_at")

    # -- Devices ------------------------------------------------------------
    def record_device(
        self, host: str, object_id: int | None, guessed_type: str | None, signals: list[str], open_ports: list[int]
    ) -> int:
        """Add a host or refresh what the scans know about it. A name, description or tags the person set
        are never touched here; only the guess, signals and ports follow the latest scan."""
        now = int(time.time())
        existing = self._row("SELECT id, signals, open_ports FROM devices WHERE host = ?", (host,))
        if existing:
            merged_signals = sorted(set(filter(None, existing["signals"].split("|"))) | set(signals))
            self._db.execute(
                "UPDATE devices SET object_id = COALESCE(?, object_id), guessed_type = ?, signals = ?,"
                " open_ports = ?, last_seen = ? WHERE id = ?",
                (
                    object_id,
                    guessed_type,
                    "|".join(merged_signals),
                    ",".join(str(p) for p in sorted(open_ports)),
                    now,
                    existing["id"],
                ),
            )
            self._db.commit()
            if open_ports:
                self.set_presence(existing["id"], True)
            return existing["id"]
        cur = self._db.execute(
            "INSERT INTO devices (host, object_id, guessed_type, signals, open_ports, first_seen, last_seen)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                host,
                object_id,
                guessed_type,
                "|".join(sorted(set(signals))),
                ",".join(str(p) for p in sorted(open_ports)),
                now,
                now,
            ),
        )
        self._db.commit()
        if open_ports:
            # A host a scan just found open ports on is online right now
            self.set_presence(cur.lastrowid, True)
        return cur.lastrowid

    def set_presence(self, device_id: int, online: bool) -> None:
        """Record whether a device answered. status_changed_at only moves when the state flips, so the page can
        say how long a device has been online or offline."""
        now = int(time.time())
        current = self._row("SELECT online FROM devices WHERE id = ?", (device_id,))
        if not current:
            return
        changed = current["online"] is None or bool(current["online"]) != online
        self._db.execute(
            "UPDATE devices SET online = ?, last_checked = ?,"
            " last_online = CASE WHEN ? THEN ? ELSE last_online END,"
            " status_changed_at = CASE WHEN ? THEN ? ELSE status_changed_at END WHERE id = ?",
            (int(online), now, int(online), now, int(changed), now, device_id),
        )
        self._db.commit()

    def devices_to_check(self) -> list[dict]:
        """Devices whose object may be probed: the ownership gate applies to the online check as well."""
        rows = self._rows(
            "SELECT d.id, d.host, d.open_ports FROM devices d JOIN scan_objects o ON o.id = d.object_id"
            " WHERE o.authorised = 1 AND o.enabled = 1"
        )
        for r in rows:
            r["open_ports"] = [int(p) for p in r["open_ports"].split(",") if p]
        return rows

    def update_device(self, device_id: int, fields: dict) -> dict | None:
        sets = {}
        if "name" in fields:
            sets["name"] = str(fields["name"] or "").strip()[:120]
        if "description" in fields:
            sets["description"] = str(fields["description"] or "").strip()[:2000]
        if "tags" in fields:
            raw = fields["tags"] if isinstance(fields["tags"], list) else str(fields["tags"] or "").split(",")
            tags = []
            for tag in raw:
                tag = str(tag).strip().lower()[:32]
                if tag and tag not in tags:
                    tags.append(tag)
            sets["tags"] = ",".join(tags[:20])
        if sets:
            columns = ", ".join(f"{k} = ?" for k in sets)
            self._db.execute(f"UPDATE devices SET {columns} WHERE id = ?", (*sets.values(), device_id))
            self._db.commit()
        return self.device(device_id)

    def delete_device(self, device_id: int) -> None:
        self._db.execute("DELETE FROM devices WHERE id = ?", (device_id,))
        self._db.commit()

    @staticmethod
    def _device_row(row: dict) -> dict:
        row["tags"] = [t for t in row["tags"].split(",") if t]
        row["signals"] = [s for s in row["signals"].split("|") if s]
        row["open_ports"] = [int(p) for p in row["open_ports"].split(",") if p]
        row["services"] = services(row["open_ports"])
        return row

    def device(self, device_id: int) -> dict | None:
        row = self._row("SELECT * FROM devices WHERE id = ?", (device_id,))
        return self._device_row(row) if row else None

    def devices(self) -> list[dict]:
        rows = self._rows(
            "SELECT d.*, (SELECT COUNT(*) FROM findings f WHERE f.host = d.host"
            " AND f.status IN ('open','in_progress')) AS open_tickets FROM devices d ORDER BY d.host"
        )
        return [self._device_row(r) for r in rows]

    def device_by_host(self, host: str) -> dict | None:
        row = self._row("SELECT * FROM devices WHERE host = ?", (host,))
        return self._device_row(row) if row else None

    # -- Findings / tickets -------------------------------------------------
    def record_finding(self, finding: dict) -> tuple[int, bool]:
        """Insert a finding, or touch the existing ticket with the same fingerprint. Returns (id, is_new).
        A resolved ticket that is seen again reopens."""
        now = int(time.time())
        existing = self._row("SELECT * FROM findings WHERE fingerprint = ?", (finding["fingerprint"],))
        if existing:
            if existing["status"] == "resolved":
                self._set_status(existing["id"], "open", existing["status"], note="seen again by a later scan")
            self._db.execute(
                "UPDATE findings SET last_seen = ?, times_seen = times_seen + 1, proof = ?, cvss = ?,"
                " resolved_at = NULL WHERE id = ?",
                (now, finding.get("proof"), finding.get("cvss"), existing["id"]),
            )
            self._db.commit()
            return existing["id"], False
        cur = self._db.execute(
            "INSERT INTO findings (fingerprint, object_id, host, port, kind, template_id, name, severity,"
            " cve, cvss, matched_at, proof, description, remediation, reference, first_seen, last_seen)"
            " VALUES (:fingerprint, :object_id, :host, :port, :kind, :template_id, :name, :severity, :cve,"
            " :cvss, :matched_at, :proof, :description, :remediation, :reference, :now, :now)",
            {
                "now": now,
                **{
                    k: finding.get(k)
                    for k in (
                        "fingerprint",
                        "object_id",
                        "host",
                        "port",
                        "kind",
                        "template_id",
                        "name",
                        "severity",
                        "cve",
                        "cvss",
                        "matched_at",
                        "proof",
                        "description",
                        "remediation",
                        "reference",
                    )
                },
            },
        )
        self._db.commit()
        return cur.lastrowid, True

    def _set_status(self, finding_id: int, new: str, old: str, note: str | None = None) -> None:
        self._db.execute(
            "UPDATE findings SET status = ?, resolved_at = ? WHERE id = ?",
            (new, int(time.time()) if new == "resolved" else None, finding_id),
        )
        self._db.execute(
            "INSERT INTO ticket_events (finding_id, ts, field, old_value, new_value) VALUES (?, ?, ?, ?, ?)",
            (finding_id, int(time.time()), "status", old, new if not note else f"{new} ({note})"),
        )

    def set_ticket_status(self, finding_id: int, status: str) -> dict | None:
        current = self._row("SELECT status FROM findings WHERE id = ?", (finding_id,))
        if not current or status not in TICKET_STATUSES:
            return None
        if status != current["status"]:
            self._set_status(finding_id, status, current["status"])
            self._db.commit()
        return self.finding(finding_id)

    def set_ticket_note(self, finding_id: int, note: str) -> dict | None:
        self._db.execute("UPDATE findings SET note = ? WHERE id = ?", (note, finding_id))
        self._db.execute(
            "INSERT INTO ticket_events (finding_id, ts, field, new_value) VALUES (?, ?, 'note', ?)",
            (finding_id, int(time.time()), note[:200]),
        )
        self._db.commit()
        return self.finding(finding_id)

    def auto_resolve(self, object_id: int, seen_fingerprints: set[str]) -> int:
        """After a scan of an object, open tickets of that object not seen this time become resolved."""
        open_rows = self._rows(
            "SELECT id, fingerprint, status FROM findings WHERE object_id = ? AND status IN ('open','in_progress')",
            (object_id,),
        )
        closed = 0
        for row in open_rows:
            if row["fingerprint"] not in seen_fingerprints:
                self._set_status(row["id"], "resolved", row["status"], note="not seen by the latest scan")
                closed += 1
        self._db.commit()
        return closed

    def findings(self, status: str | None = None, object_id: int | None = None, limit: int = 1000) -> list[dict]:
        where, params = [], []
        if status == "open":
            where.append("status IN ('open','in_progress')")
        elif status:
            where.append("status = ?")
            params.append(status)
        if object_id:
            where.append("object_id = ?")
            params.append(object_id)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        order = (
            " ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2"
            " WHEN 'low' THEN 3 ELSE 4 END, last_seen DESC LIMIT ?"
        )
        return self._rows(f"SELECT * FROM findings{clause}{order}", (*params, limit))

    def finding(self, finding_id: int) -> dict | None:
        row = self._row("SELECT * FROM findings WHERE id = ?", (finding_id,))
        if row:
            row["events"] = self._rows(
                "SELECT ts, field, old_value, new_value FROM ticket_events WHERE finding_id = ? ORDER BY ts",
                (finding_id,),
            )
        return row

    def summary(self) -> dict:
        by_sev = {
            r["severity"]: r["n"]
            for r in self._rows(
                "SELECT severity, COUNT(*) AS n FROM findings WHERE status IN ('open','in_progress') GROUP BY severity"
            )
        }
        by_status = {
            r["status"]: r["n"] for r in self._rows("SELECT status, COUNT(*) AS n FROM findings GROUP BY status")
        }
        return {
            "objects": self._row("SELECT COUNT(*) AS n FROM scan_objects")["n"],
            "open_by_severity": by_sev,
            "by_status": by_status,
            "open_total": sum(by_status.get(s, 0) for s in OPEN_STATUSES),
            "last_scan": self._row("SELECT MAX(finished_at) AS t FROM scans")["t"],
            "devices_total": self._row("SELECT COUNT(*) AS n FROM devices")["n"],
            "devices_online": self._row("SELECT COUNT(*) AS n FROM devices WHERE online = 1")["n"],
        }

    def prune(self, retention_days: int) -> None:
        """Drop resolved tickets and finished scans older than the retention. Open tickets are kept."""
        cutoff = int(time.time()) - retention_days * 86400
        self._db.execute(
            "DELETE FROM findings WHERE status IN ('resolved','false_positive','wont_fix') AND last_seen < ?",
            (cutoff,),
        )
        self._db.execute("DELETE FROM scans WHERE status != 'running' AND started_at < ?", (cutoff,))
        self._db.commit()

    def wipe(self) -> None:
        """Empty every table: the Settings danger zone. The file stays, so WAL and backups still work."""
        for table in ("ticket_events", "findings", "devices", "scans", "scan_objects"):
            self._db.execute(f"DELETE FROM {table}")
        self._db.commit()
        self._db.execute("VACUUM")

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0
