"""SQLite storage: the devices Home Assistant tracks, their scans, the issues found and a daily history.

All of it lives in one file in the app's /data folder, so Home Assistant backs it up, and the dashboard
can download a copy or wipe it (the danger zone). Every call comes from the event loop and is short.
"""

import sqlite3
import time
from pathlib import Path

from . import score
from .config import DATA_DIR, SEVERITIES
from .devices import services
from .trackers import scannable

DB_FILE = DATA_DIR / "scanner.db"

# An issue is open until acknowledged; it is resolved when a later scan of its device no longer finds it
ISSUE_STATES = ("open", "acknowledged", "resolved")
UNRESOLVED = ("open", "acknowledged")
# A failed scan is tried again after this long, not after the whole rescan period
RETRY_FAILED_AFTER = 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
    id INTEGER PRIMARY KEY,
    entity_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    ip TEXT,
    mac TEXT,
    online INTEGER NOT NULL DEFAULT 0,
    in_ha INTEGER NOT NULL DEFAULT 1,
    excluded INTEGER NOT NULL DEFAULT 0,
    scan_requested INTEGER NOT NULL DEFAULT 0,
    open_ports TEXT NOT NULL DEFAULT '',
    first_seen INTEGER NOT NULL,
    last_online INTEGER,
    last_scan_at INTEGER,
    last_attempt_at INTEGER,
    last_scan_status TEXT
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY,
    device_id INTEGER REFERENCES devices(id) ON DELETE CASCADE,
    ip TEXT NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    phase TEXT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    open_ports INTEGER DEFAULT 0,
    findings INTEGER DEFAULT 0,
    new_issues INTEGER DEFAULT 0,
    resolved_issues INTEGER DEFAULT 0,
    error TEXT
);
-- fingerprint identifies "the same issue on the same device" across scans, so a repeat finding updates
-- its issue instead of making a duplicate, also after the device's address changed.
CREATE TABLE IF NOT EXISTS issues (
    id INTEGER PRIMARY KEY,
    fingerprint TEXT NOT NULL UNIQUE,
    device_id INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    -- What found it: "network" for this app's own scans; room for agents that report from inside a device
    source TEXT NOT NULL DEFAULT 'network',
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
    state TEXT NOT NULL DEFAULT 'open',
    first_seen INTEGER NOT NULL,
    last_seen INTEGER NOT NULL,
    acknowledged_at INTEGER,
    resolved_at INTEGER
);
-- One row per day: the score and the unresolved issues at the end of it, for the trend
CREATE TABLE IF NOT EXISTS daily (
    day TEXT PRIMARY KEY,
    score INTEGER NOT NULL,
    critical INTEGER NOT NULL,
    high INTEGER NOT NULL,
    medium INTEGER NOT NULL,
    low INTEGER NOT NULL,
    devices INTEGER NOT NULL,
    scans INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS issues_state ON issues (state);
CREATE INDEX IF NOT EXISTS issues_device ON issues (device_id);
CREATE INDEX IF NOT EXISTS scans_started ON scans (started_at);
"""

SEVERITY_ORDER = (
    "CASE i.severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 WHEN 'low' THEN 3 ELSE 4 END"
)


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
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("devices", "scans", "issues")}
        wal = Path(f"{source}-wal")
        return {
            "size": source.stat().st_size if source.exists() else 0,
            "wal_size": wal.stat().st_size if wal.exists() else 0,
            "counts": counts,
        }
    finally:
        db.close()


def _empty_counts() -> dict:
    return {s: 0 for s in SEVERITIES if s != "info"}


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
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    def _rows(self, sql: str, params: tuple = ()) -> list[dict]:
        return [dict(r) for r in self._db.execute(sql, params).fetchall()]

    def _row(self, sql: str, params: tuple = ()) -> dict | None:
        r = self._db.execute(sql, params).fetchone()
        return dict(r) if r else None

    # -- Devices ------------------------------------------------------------
    def sync_trackers(self, trackers: list[dict]) -> None:
        """Bring the devices in line with Home Assistant's trackers. A tracker that is gone stays with its
        history, marked as no longer in Home Assistant."""
        now = int(time.time())
        seen = []
        for t in trackers:
            seen.append(t["entity_id"])
            self._db.execute(
                "INSERT INTO devices (entity_id, name, ip, mac, online, first_seen, last_online)"
                " VALUES (:entity_id, :name, :ip, :mac, :online, :now, CASE WHEN :online THEN :now END)"
                " ON CONFLICT (entity_id) DO UPDATE SET name = :name, in_ha = 1, online = :online,"
                " ip = COALESCE(:ip, ip), mac = COALESCE(:mac, mac),"
                " last_online = CASE WHEN :online THEN :now ELSE last_online END",
                {**t, "online": int(t["online"]), "now": now},
            )
        marks = ",".join("?" * len(seen))
        self._db.execute(
            f"UPDATE devices SET in_ha = 0, online = 0 WHERE entity_id NOT IN ({marks})"
            if seen
            else "UPDATE devices SET in_ha = 0, online = 0",
            tuple(seen),
        )
        self._db.commit()

    @staticmethod
    def _device_row(row: dict) -> dict:
        row["open_ports"] = [int(p) for p in row["open_ports"].split(",") if p]
        row["services"] = services(row["open_ports"])
        row["scannable"] = scannable(row["ip"])
        return row

    def devices(self) -> list[dict]:
        rows = self._rows(
            "SELECT d.*,"
            " SUM(i.severity = 'critical') AS critical, SUM(i.severity = 'high') AS high,"
            " SUM(i.severity = 'medium') AS medium, SUM(i.severity = 'low') AS low,"
            " SUM(i.state = 'open') AS needs_attention"
            " FROM devices d LEFT JOIN issues i ON i.device_id = d.id AND i.state IN ('open','acknowledged')"
            " GROUP BY d.id ORDER BY d.in_ha DESC, d.online DESC, d.name COLLATE NOCASE"
        )
        for r in rows:
            counts = {s: int(r.pop(s) or 0) for s in ("critical", "high", "medium", "low")}
            r["issues"] = counts
            r["needs_attention"] = int(r["needs_attention"] or 0)
            r["score"] = score.score(counts) if r["last_scan_at"] else None
            self._device_row(r)
        return rows

    def device(self, device_id: int) -> dict | None:
        row = self._row("SELECT * FROM devices WHERE id = ?", (device_id,))
        return self._device_row(row) if row else None

    def set_device(self, device_id: int, fields: dict) -> None:
        allowed = {k: int(bool(v)) for k, v in fields.items() if k in ("excluded", "scan_requested")}
        if allowed:
            columns = ", ".join(f"{k} = ?" for k in allowed)
            self._db.execute(f"UPDATE devices SET {columns} WHERE id = ?", (*allowed.values(), device_id))
            self._db.commit()

    def due_devices(self, rescan_days: int, now: float | None = None) -> list[dict]:
        """Devices to scan now: online, on your own network, not excluded, and new, asked for by hand, or
        last scanned longer ago than the rescan period. Asked-for and new ones go first."""
        now = time.time() if now is None else now
        period = rescan_days * 86400
        out = []
        for d in self._rows("SELECT * FROM devices WHERE in_ha = 1 AND online = 1 AND excluded = 0"):
            if not scannable(d["ip"]):
                continue
            failed_recently = (
                d["last_scan_status"] == "failed" and now - (d["last_attempt_at"] or 0) < RETRY_FAILED_AFTER
            )
            if failed_recently and not d["scan_requested"]:
                continue
            if d["scan_requested"]:
                d["reason"], rank = "requested", 0
            elif d["last_scan_at"] is None:
                d["reason"], rank = "new device", 1
            elif now - d["last_scan_at"] >= period:
                d["reason"], rank = "rescan", 2
            else:
                continue
            out.append((rank, d["last_scan_at"] or 0, d))
        return [d for _, _, d in sorted(out, key=lambda x: (x[0], x[1]))]

    # -- Scans --------------------------------------------------------------
    def start_scan(self, device: dict, reason: str) -> int:
        now = int(time.time())
        cur = self._db.execute(
            "INSERT INTO scans (device_id, ip, reason, status, phase, started_at) VALUES (?, ?, ?, 'running', 'ports', ?)",
            (device["id"], device["ip"], reason, now),
        )
        self._db.execute("UPDATE devices SET last_attempt_at = ?, scan_requested = 0 WHERE id = ?", (now, device["id"]))
        self._db.commit()
        return cur.lastrowid

    def update_scan(self, scan_id: int, fields: dict) -> None:
        if fields:
            columns = ", ".join(f"{k} = ?" for k in fields)
            self._db.execute(f"UPDATE scans SET {columns} WHERE id = ?", (*fields.values(), scan_id))
            self._db.commit()

    def finish_scan(self, scan_id: int, status: str, error: str | None = None) -> None:
        now = int(time.time())
        self._db.execute(
            "UPDATE scans SET status = ?, phase = NULL, finished_at = ?, error = ? WHERE id = ?",
            (status, now, error, scan_id),
        )
        scan = self._row("SELECT device_id FROM scans WHERE id = ?", (scan_id,))
        if scan and scan["device_id"]:
            if status == "done":
                self._db.execute(
                    "UPDATE devices SET last_scan_at = ?, last_scan_status = 'done' WHERE id = ?",
                    (now, scan["device_id"]),
                )
            else:
                self._db.execute("UPDATE devices SET last_scan_status = ? WHERE id = ?", (status, scan["device_id"]))
        self._db.commit()
        self.snapshot_day()

    def set_ports(self, device_id: int, ports: list[int]) -> None:
        self._db.execute(
            "UPDATE devices SET open_ports = ? WHERE id = ?", (",".join(str(p) for p in sorted(set(ports))), device_id)
        )
        self._db.commit()

    def scans(self, limit: int = 50) -> list[dict]:
        return self._rows(
            "SELECT s.*, d.name AS device_name FROM scans s LEFT JOIN devices d ON d.id = s.device_id"
            " ORDER BY s.started_at DESC LIMIT ?",
            (limit,),
        )

    def running_scan(self) -> dict | None:
        return self._row(
            "SELECT s.*, d.name AS device_name FROM scans s LEFT JOIN devices d ON d.id = s.device_id"
            " WHERE s.status = 'running' ORDER BY s.started_at DESC LIMIT 1"
        )

    def end_interrupted_scans(self) -> None:
        """Scans still marked running when the app starts were cut off by a restart."""
        self._db.execute(
            "UPDATE scans SET status = 'failed', phase = NULL, finished_at = ?, error = 'the app restarted'"
            " WHERE status = 'running'",
            (int(time.time()),),
        )
        self._db.commit()

    # -- Issues -------------------------------------------------------------
    def record_issue(self, finding: dict) -> bool:
        """Insert a finding as an issue, or refresh the one with the same fingerprint. Returns whether it is
        new or came back: a resolved issue seen again opens again; an acknowledged one stays acknowledged."""
        now = int(time.time())
        existing = self._row("SELECT id, state FROM issues WHERE fingerprint = ?", (finding["fingerprint"],))
        if existing:
            back = existing["state"] == "resolved"
            self._db.execute(
                "UPDATE issues SET last_seen = ?, proof = ?, cvss = COALESCE(?, cvss), matched_at = ?,"
                " state = CASE WHEN state = 'resolved' THEN 'open' ELSE state END, resolved_at = NULL WHERE id = ?",
                (now, finding.get("proof"), finding.get("cvss"), finding.get("matched_at"), existing["id"]),
            )
            self._db.commit()
            return back
        fields = (
            "fingerprint", "device_id", "port", "kind", "template_id", "name", "severity", "cve", "cvss",
            "matched_at", "proof", "description", "remediation", "reference",
        )  # fmt: skip
        self._db.execute(
            f"INSERT INTO issues ({', '.join(fields)}, first_seen, last_seen)"
            f" VALUES ({', '.join(':' + f for f in fields)}, :now, :now)",
            {"now": now, **{k: finding.get(k) for k in fields}},
        )
        self._db.commit()
        return True

    def resolve_missing(self, device_id: int, seen: set[str]) -> int:
        """After a complete scan of a device, its issues that scan did not find are resolved."""
        now = int(time.time())
        rows = self._rows(
            "SELECT id, fingerprint FROM issues WHERE device_id = ? AND state IN ('open','acknowledged')",
            (device_id,),
        )
        gone = [r["id"] for r in rows if r["fingerprint"] not in seen]
        for issue_id in gone:
            self._db.execute("UPDATE issues SET state = 'resolved', resolved_at = ? WHERE id = ?", (now, issue_id))
        self._db.commit()
        return len(gone)

    def set_issue_state(self, issue_id: int, state: str) -> dict | None:
        """Acknowledge an issue, or open it again. Resolving is the scanner's job, not a button."""
        if state not in ("open", "acknowledged"):
            return None
        self._db.execute(
            "UPDATE issues SET state = ?, acknowledged_at = ? WHERE id = ? AND state != 'resolved'",
            (state, int(time.time()) if state == "acknowledged" else None, issue_id),
        )
        self._db.commit()
        return self.issue(issue_id)

    def acknowledge_all(self, severity: str | None = None) -> int:
        sql = "UPDATE issues SET state = 'acknowledged', acknowledged_at = ? WHERE state = 'open'"
        params: tuple = (int(time.time()),)
        if severity:
            sql += " AND severity = ?"
            params += (severity,)
        n = self._db.execute(sql, params).rowcount
        self._db.commit()
        return n

    def issues(self, state: str | None = None, device_id: int | None = None, limit: int = 1000) -> list[dict]:
        where, params = [], []
        if state == "unresolved":
            where.append("i.state IN ('open','acknowledged')")
        elif state:
            where.append("i.state = ?")
            params.append(state)
        if device_id:
            where.append("i.device_id = ?")
            params.append(device_id)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        return self._rows(
            "SELECT i.*, d.name AS device_name, d.ip AS device_ip FROM issues i JOIN devices d ON d.id = i.device_id"
            f"{clause} ORDER BY {SEVERITY_ORDER}, i.first_seen DESC LIMIT ?",
            (*params, limit),
        )

    def issue(self, issue_id: int) -> dict | None:
        return self._row(
            "SELECT i.*, d.name AS device_name, d.ip AS device_ip FROM issues i JOIN devices d ON d.id = i.device_id"
            " WHERE i.id = ?",
            (issue_id,),
        )

    # -- Overview -----------------------------------------------------------
    def counts(self, states: tuple = UNRESOLVED) -> dict:
        marks = ",".join("?" * len(states))
        counts = _empty_counts()
        for r in self._rows(
            f"SELECT severity, COUNT(*) AS n FROM issues WHERE state IN ({marks}) GROUP BY severity", states
        ):
            if r["severity"] in counts:
                counts[r["severity"]] = r["n"]
        return counts

    def summary(self, rescan_days: int) -> dict:
        now = time.time()
        unresolved = self.counts()
        current = score.score(unresolved)
        devices = self._rows("SELECT ip, online, in_ha, excluded, last_scan_at FROM devices WHERE in_ha = 1")
        eligible = [d for d in devices if scannable(d["ip"]) and not d["excluded"]]
        covered = [d for d in eligible if d["last_scan_at"] and now - d["last_scan_at"] < rescan_days * 86400]
        week_ago = int(now) - 7 * 86400
        return {
            "score": current,
            "grade": score.grade(current),
            "unresolved": unresolved,
            "needs_attention": self.counts(("open",)),
            "acknowledged": sum(self.counts(("acknowledged",)).values()),
            "new_this_week": self._row("SELECT COUNT(*) AS n FROM issues WHERE first_seen >= ?", (week_ago,))["n"],
            "resolved_this_week": self._row(
                "SELECT COUNT(*) AS n FROM issues WHERE state = 'resolved' AND resolved_at >= ?", (week_ago,)
            )["n"],
            "devices": {
                "tracked": len(devices),
                "online": sum(1 for d in devices if d["online"]),
                "scannable": len(eligible),
                "covered": len(covered),
                "never_scanned": sum(1 for d in eligible if not d["last_scan_at"]),
                "excluded": sum(1 for d in devices if d["excluded"]),
            },
            "scans_this_week": self._row(
                "SELECT COUNT(*) AS n FROM scans WHERE started_at >= ? AND status = 'done'", (week_ago,)
            )["n"],
            "last_scan": self._row("SELECT MAX(finished_at) AS t FROM scans WHERE status = 'done'")["t"],
            "top_findings": self._rows(
                "SELECT name, severity, COUNT(*) AS devices FROM issues WHERE state IN ('open','acknowledged')"
                " GROUP BY template_id ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'high' THEN 1"
                " WHEN 'medium' THEN 2 ELSE 3 END, devices DESC LIMIT 5"
            ),
        }

    def snapshot_day(self, day: str | None = None) -> None:
        """Write today's row of the trend: the score and the unresolved issues as they are now."""
        day = day or time.strftime("%Y-%m-%d")
        counts = self.counts()
        start = int(time.mktime(time.strptime(day, "%Y-%m-%d")))
        self._db.execute(
            "INSERT OR REPLACE INTO daily (day, score, critical, high, medium, low, devices, scans)"
            " VALUES (?, ?, ?, ?, ?, ?, (SELECT COUNT(*) FROM devices WHERE in_ha = 1 AND last_scan_at IS NOT NULL),"
            " (SELECT COUNT(*) FROM scans WHERE started_at >= ? AND started_at < ? AND status = 'done'))",
            (day, score.score(counts), counts["critical"], counts["high"], counts["medium"], counts["low"],
             start, start + 86400),
        )  # fmt: skip
        self._db.commit()

    def history(self, days: int = 30) -> list[dict]:
        return self._rows("SELECT * FROM (SELECT * FROM daily ORDER BY day DESC LIMIT ?) ORDER BY day", (days,))

    def prune(self, retention_days: int) -> None:
        """Drop resolved issues, finished scans and days of history older than the retention."""
        cutoff = int(time.time()) - retention_days * 86400
        self._db.execute("DELETE FROM issues WHERE state = 'resolved' AND resolved_at < ?", (cutoff,))
        self._db.execute("DELETE FROM scans WHERE status != 'running' AND started_at < ?", (cutoff,))
        self._db.execute("DELETE FROM daily WHERE day < ?", (time.strftime("%Y-%m-%d", time.localtime(cutoff)),))
        self._db.commit()

    def wipe(self) -> None:
        """Empty every table: the Settings danger zone. The file stays, so WAL and backups still work."""
        for table in ("issues", "scans", "daily", "devices"):
            self._db.execute(f"DELETE FROM {table}")
        self._db.commit()
        self._db.execute("VACUUM")

    def size_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0
