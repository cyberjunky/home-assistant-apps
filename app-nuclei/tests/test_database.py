import pytest

from src.database import Database


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    yield d
    d.close()


def finding(fp="fp1", host="10.0.0.5", sev="high", template="CVE-1"):
    return {
        "fingerprint": fp,
        "object_id": None,
        "host": host,
        "port": 443,
        "kind": "vulnerability",
        "template_id": template,
        "name": "Issue",
        "severity": sev,
        "cve": "CVE-1",
        "cvss": 7.5,
        "matched_at": "u",
        "proof": "p",
        "description": "d",
        "remediation": "r",
        "reference": "ref",
    }


def test_object_lifecycle(db):
    oid = db.add_object("10.0.0.0/24", "cidr", "lan", "weekly", True)
    o = db.object(oid)
    assert o["authorised"] == 1 and o["authorised_at"]
    db.update_object(oid, {"name": "network", "enabled": 0})
    assert db.object(oid)["name"] == "network" and db.object(oid)["enabled"] == 0
    assert db.object_by_target("10.0.0.0/24")["id"] == oid
    db.delete_object(oid)
    assert db.object(oid) is None


def test_findings_dedupe_and_reopen(db):
    oid = db.add_object("10.0.0.5", "ip", "", None, True)
    f = finding()
    f["object_id"] = oid
    fid, is_new = db.record_finding(f)
    assert is_new
    fid2, is_new2 = db.record_finding(f)
    assert fid2 == fid and not is_new2
    assert db.finding(fid)["times_seen"] == 2
    # resolve, then a later scan sees it again -> reopens
    db.set_ticket_status(fid, "resolved")
    assert db.finding(fid)["status"] == "resolved"
    db.record_finding(f)
    assert db.finding(fid)["status"] == "open"


def test_status_transitions_logged(db):
    fid, _ = db.record_finding(finding())
    db.set_ticket_status(fid, "in_progress")
    db.set_ticket_status(fid, "false_positive")
    assert db.set_ticket_status(fid, "nonsense") is None
    events = db.finding(fid)["events"]
    assert [e["new_value"] for e in events if e["field"] == "status"] == ["in_progress", "false_positive"]


def test_auto_resolve(db):
    oid = db.add_object("10.0.0.5", "ip", "", None, True)
    a = finding("a")
    a["object_id"] = oid
    b = finding("b")
    b["object_id"] = oid
    db.record_finding(a)
    db.record_finding(b)
    closed = db.auto_resolve(oid, {"a"})  # b not seen this time
    assert closed == 1
    statuses = {f["fingerprint"]: f["status"] for f in db.findings(status=None, object_id=oid)}
    assert statuses["a"] in ("open", "in_progress") and statuses["b"] == "resolved"


def test_summary_and_wipe(db):
    oid = db.add_object("10.0.0.5", "ip", "", None, True)
    f = finding()
    f["object_id"] = oid
    db.record_finding(f)
    s = db.summary()
    assert s["objects"] == 1 and s["open_total"] == 1 and s["open_by_severity"]["high"] == 1
    db.wipe()
    assert db.summary()["objects"] == 0 and not db.findings(status=None)


def test_devices_keep_the_persons_edits(db):
    did = db.record_device("10.0.0.5", None, "linux", ["ssh"], [22, 80])
    db.update_device(did, {"name": "NAS", "description": "in the cupboard", "tags": ["Linux", "nas", "linux", " "]})
    d = db.device(did)
    assert d["name"] == "NAS" and d["tags"] == ["linux", "nas"]
    # A later scan refreshes the guess, signals and ports but never the person's name, description or tags
    db.record_device("10.0.0.5", None, "windows", ["rdp"], [3389])
    d = db.device(did)
    assert d["name"] == "NAS" and d["description"] == "in the cupboard" and d["tags"] == ["linux", "nas"]
    assert d["guessed_type"] == "windows" and d["open_ports"] == [3389]
    assert {"ssh", "rdp"} <= set(d["signals"])
    assert [s["service"] for s in d["services"]] == ["rdp"] and d["services"][0]["risky"]


def test_devices_count_open_tickets(db):
    db.record_device("10.0.0.5", None, None, [], [23])
    f = finding()
    f["host"] = "10.0.0.5"
    db.record_finding(f)
    assert db.devices()[0]["open_tickets"] == 1


def test_a_database_from_an_older_version_gets_the_new_columns(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(
        """
        CREATE TABLE devices (id INTEGER PRIMARY KEY, host TEXT NOT NULL UNIQUE, object_id INTEGER, name TEXT,
            description TEXT, tags TEXT NOT NULL DEFAULT '', guessed_type TEXT, signals TEXT NOT NULL DEFAULT '',
            open_ports TEXT NOT NULL DEFAULT '', first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL);
        INSERT INTO devices (host, first_seen, last_seen) VALUES ('10.0.0.5', 1, 1);
        """
    )
    old.commit()
    old.close()
    db = Database(path)
    device = db.devices()[0]
    assert device["host"] == "10.0.0.5" and device["online"] is None
    db.set_presence(device["id"], True)
    assert db.device(device["id"])["online"] == 1
    db.close()
    Database(path).close()  # opening it again must not try to add them twice
