"""A scan of one device: open ports, exposed services and nuclei's findings become issues; a later scan
resolves what is gone."""

import pytest

from src.config import Config
from src.database import Database
from src.engine import Engine, NotScannable
from src.scanner import HostResult


class Scanner:
    """Stands in for naabu and nuclei: these ports are open, nuclei reports these findings."""

    def __init__(self, ports=(), findings=()):
        self.ports = list(ports)
        self.findings = list(findings)
        self.targets = None

    def require_templates(self):
        pass

    def templates_updated_at(self):
        return None

    def template_count(self):
        return 100

    async def update_templates(self):
        return True

    async def discover_ports(self, hosts):
        return [HostResult(hosts[0], self.ports)] if self.ports else []

    async def scan_targets(self, targets, on_finding):
        self.targets = targets
        for f in self.findings:
            on_finding(dict(f))
        return len(self.findings)


def finding(template_id, severity, port=80):
    return {"host": "192.168.1.20", "port": port, "kind": "vulnerability", "template_id": template_id, "name": template_id,
            "severity": severity, "cve": None, "cvss": None, "matched_at": f"http://192.168.1.20:{port}/x",
            "proof": "", "description": "", "remediation": "", "reference": ""}  # fmt: skip


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    d.sync_trackers([
        {"entity_id": "device_tracker.nas", "name": "NAS", "ip": "192.168.1.20", "mac": None, "online": True},
        {"entity_id": "device_tracker.vps", "name": "VPS", "ip": "8.8.8.8", "mac": None, "online": True},
    ])  # fmt: skip
    yield d
    d.close()


def device(db, name):
    return next(d for d in db.devices() if d["name"] == name)


async def test_a_scan_turns_findings_into_issues(db):
    scanner = Scanner(ports=[23, 80], findings=[finding("cve-x", "critical"), finding("noise", "info")])
    engine = Engine(db, Config(update_templates=False, min_severity="low"), scanner)
    nas = device(db, "NAS")
    assert await engine.scan_device(nas, "new device") == 2
    issues = {i["template_id"]: i for i in db.issues("open")}
    # Telnet open is an issue by itself; the info finding is below the minimum severity
    assert set(issues) == {"open-port-23", "cve-x"} and issues["open-port-23"]["severity"] == "high"
    assert scanner.targets == ["192.168.1.20:23", "192.168.1.20:80"]
    nas = db.device(nas["id"])
    assert nas["open_ports"] == [23, 80] and nas["last_scan_status"] == "done"
    scan = db.scans()[0]
    assert scan["status"] == "done" and scan["open_ports"] == 2 and scan["new_issues"] == 2

    # Telnet closed and the CVE fixed: the next scan resolves both
    engine.scanner = Scanner(ports=[80])
    assert await engine.scan_device(db.device(nas["id"]), "rescan") == 0
    assert db.issues("open") == [] and len(db.issues("resolved")) == 2
    assert db.scans()[0]["resolved_issues"] == 2


async def test_no_open_ports_means_no_nuclei_run(db):
    scanner = Scanner()
    await Engine(db, Config(update_templates=False), scanner).scan_device(device(db, "NAS"), "new device")
    assert scanner.targets is None and db.scans()[0]["status"] == "done"


async def test_only_your_own_network(db):
    engine = Engine(db, Config(update_templates=False), Scanner(ports=[80]))
    with pytest.raises(NotScannable, match="not an address on your own network"):
        await engine.scan_device(device(db, "VPS"), "requested")
    with pytest.raises(NotScannable):
        engine.check(None)
    assert db.scans() == []
