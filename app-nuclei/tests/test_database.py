"""The devices from Home Assistant's trackers, when they are due, and the life of an issue."""

import time

import pytest

from src.database import Database, database_report


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    yield d
    d.close()


def tracker(entity, ip="192.168.1.20", online=True, name=None, mac=None):
    return {"entity_id": f"device_tracker.{entity}", "name": name or entity, "ip": ip, "mac": mac, "online": online}


def issue(device_id, fp, severity="high", **kw):
    return {"fingerprint": fp, "device_id": device_id, "port": 80, "kind": "vulnerability", "template_id": "t-" + fp,
            "name": "n", "severity": severity, "matched_at": "u", **kw}  # fmt: skip


def scanned(db, device, status="done"):
    db.finish_scan(db.start_scan(db.device(device["id"]), "x"), status)


def test_trackers_become_devices(db):
    db.sync_trackers([tracker("nas", mac="00:11"), tracker("phone", "192.168.1.100", online=False)])
    nas = db.devices()[0]
    assert nas["name"] == "nas" and nas["online"] == 1 and nas["in_ha"] == 1 and nas["scannable"]
    # An address that Home Assistant stops reporting is kept; a new one replaces it
    db.sync_trackers([tracker("nas", ip=None, name="NAS"), tracker("phone", "192.168.1.101")])
    nas = next(d for d in db.devices() if d["entity_id"] == "device_tracker.nas")
    assert nas["ip"] == "192.168.1.20" and nas["name"] == "NAS" and nas["mac"] == "00:11"
    # A tracker that is gone stays, with its history, as no longer in Home Assistant and not home
    db.sync_trackers([tracker("phone", "192.168.1.101")])
    nas = next(d for d in db.devices() if d["entity_id"] == "device_tracker.nas")
    assert nas["in_ha"] == 0 and nas["online"] == 0


def test_which_devices_are_due(db):
    db.sync_trackers([
        tracker("nas"), tracker("phone", "192.168.1.100"), tracker("away", "192.168.1.101", online=False),
        tracker("vps", "185.199.108.25"), tracker("noip", None),
    ])  # fmt: skip
    due = db.due_devices(7)
    # Only home, on your own network: new ones first
    assert sorted(d["name"] for d in due) == ["nas", "phone"] and {d["reason"] for d in due} == {"new device"}
    nas = next(d for d in db.devices() if d["name"] == "nas")
    phone = next(d for d in db.devices() if d["name"] == "phone")
    scanned(db, nas)
    scanned(db, phone)
    assert db.due_devices(7) == []
    # Due again once the last scan is older than the period
    assert [d["reason"] for d in db.due_devices(7, time.time() + 8 * 86400)] == ["rescan", "rescan"]
    # Asked for by hand goes first; excluded never
    db.set_device(phone["id"], {"scan_requested": True})
    db.set_device(nas["id"], {"excluded": True})
    assert [(d["name"], d["reason"]) for d in db.due_devices(7, time.time() + 8 * 86400)] == [("phone", "requested")]


def test_a_failed_scan_is_tried_again_after_an_hour(db):
    db.sync_trackers([tracker("nas")])
    nas = db.devices()[0]
    scanned(db, nas, "failed")
    assert db.due_devices(7) == []
    assert db.due_devices(7, time.time() + 3700)[0]["reason"] == "new device"


def test_issue_life(db):
    db.sync_trackers([tracker("nas")])
    did = db.devices()[0]["id"]
    assert db.record_issue(issue(did, "a")) is True
    assert db.record_issue(issue(did, "a", proof="again")) is False
    db.set_issue_state(db.issues("open")[0]["id"], "acknowledged")
    # Seen again, an acknowledged issue stays acknowledged
    assert db.record_issue(issue(did, "a")) is False
    assert [i["fingerprint"] for i in db.issues("acknowledged")] == ["a"] and db.issues("open") == []
    assert db.resolve_missing(did, set()) == 1
    resolved = db.issues("resolved")[0]
    assert resolved["resolved_at"] and db.set_issue_state(resolved["id"], "open")["state"] == "resolved"
    # It comes back: needs attention again
    assert db.record_issue(issue(did, "a")) is True and db.issues("open")


def test_acknowledge_all(db):
    db.sync_trackers([tracker("nas")])
    did = db.devices()[0]["id"]
    db.record_issue(issue(did, "a", "critical"))
    db.record_issue(issue(did, "b", "low"))
    assert db.acknowledge_all("low") == 1
    assert db.acknowledge_all() == 1
    assert db.issues("open") == []


def test_summary_counts_and_score(db):
    db.sync_trackers([tracker("nas"), tracker("tv", "192.168.1.50"), tracker("vps", "8.8.8.8")])
    nas = next(d for d in db.devices() if d["name"] == "nas")
    db.record_issue(issue(nas["id"], "a", "critical"))
    db.record_issue(issue(nas["id"], "b", "low"))
    db.set_issue_state(db.issues("open")[1]["id"], "acknowledged")
    scanned(db, nas)
    s = db.summary(7)
    assert s["unresolved"] == {"low": 1, "medium": 0, "high": 0, "critical": 1}
    assert s["needs_attention"]["critical"] == 1 and s["acknowledged"] == 1
    # Acknowledged issues still count in the score: 25 + 1 points, halved at 50
    assert s["score"] == round(100 * 50 / 76) == 66 and s["grade"] == "C"
    assert s["devices"] == {"tracked": 3, "online": 3, "scannable": 2, "covered": 1, "never_scanned": 1, "excluded": 0}
    devices = {d["name"]: d for d in db.devices()}
    assert devices["nas"]["score"] == s["score"] and devices["tv"]["score"] is None


def test_history_prune_and_wipe(db, tmp_path):
    db.sync_trackers([tracker("nas")])
    did = db.devices()[0]["id"]
    db.record_issue(issue(did, "old"))
    db.resolve_missing(did, set())
    db._db.execute("UPDATE issues SET resolved_at = 1")
    db.record_issue(issue(did, "open-one"))
    db.snapshot_day()
    assert db.history()[0]["high"] == 1
    db.prune(30)
    assert [i["fingerprint"] for i in db.issues("unresolved")] == ["open-one"] and db.issues("resolved") == []
    assert database_report(db.path)["counts"]["issues"] == 1
    db.wipe()
    assert db.devices() == [] and db.history() == []
