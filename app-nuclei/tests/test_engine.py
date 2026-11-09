import pytest

from src.config import Config
from src.database import Database
from src.engine import Engine, NotAuthorised
from src.scanner import HostResult


class FakeScanner:
    def __init__(self):
        self.updated = False

    def require_templates(self):
        pass

    async def update_templates(self):
        self.updated = True
        return True

    async def discover_ports(self, hosts):
        return [HostResult(hosts[0], [443])]

    async def scan_targets(self, targets, on_finding):
        on_finding(
            {
                "fingerprint": "f1",
                "host": targets[0].split(":")[0],
                "port": 443,
                "kind": "vulnerability",
                "template_id": "CVE-1",
                "name": "Issue",
                "severity": "high",
                "cve": "CVE-1",
                "cvss": 7.5,
                "matched_at": "u",
                "proof": "p",
                "description": "d",
                "remediation": "r",
                "reference": "",
            }
        )
        return 1


@pytest.fixture
def setup(tmp_path):
    db = Database(tmp_path / "n.db")
    engine = Engine(db, Config(update_templates=True), FakeScanner())
    yield db, engine
    db.close()


async def test_scan_needs_authorisation(setup):
    db, engine = setup
    oid = db.add_object("10.0.0.5", "ip", "", None, authorised=False)
    with pytest.raises(NotAuthorised):
        await engine.scan_object(oid)


async def test_disabled_object_is_not_scanned(setup):
    db, engine = setup
    oid = db.add_object("10.0.0.5", "ip", "", None, authorised=True)
    db.update_object(oid, {"enabled": 0})
    with pytest.raises(NotAuthorised):
        await engine.scan_object(oid)


async def test_asn_cannot_be_scanned_directly(setup):
    db, engine = setup
    oid = db.add_object("AS12345", "asn", "", None, authorised=True)
    with pytest.raises(NotAuthorised):
        await engine.scan_object(oid)


async def test_full_scan_creates_tickets(setup):
    db, engine = setup
    oid = db.add_object("10.0.0.5", "ip", "", None, authorised=True)
    count = await engine.scan_object(oid)
    assert count == 1
    assert engine.scanner.updated
    tickets = db.findings(status="open", object_id=oid)
    assert len(tickets) == 1 and tickets[0]["severity"] == "high"
    scan = db.scans()[0]
    assert scan["status"] == "done" and scan["findings"] == 1 and scan["open_ports"] == 1


async def test_url_object_skips_port_scan(setup):
    db, engine = setup
    oid = db.add_object("https://10.0.0.5", "url", "", None, authorised=True)
    assert await engine.scan_object(oid) == 1
    assert db.scans()[0]["open_ports"] == 0


async def test_risky_ports_become_tickets_and_devices_are_labelled(tmp_path):
    class Scan(FakeScanner):
        async def discover_ports(self, hosts):
            return [HostResult(hosts[0], [22, 23, 445])]

        async def scan_targets(self, targets, on_finding):
            on_finding(
                {
                    "fingerprint": "d1",
                    "host": "10.0.0.5",
                    "port": 443,
                    "kind": "vulnerability",
                    "template_id": "tech-detect",
                    "name": "Hikvision Web",
                    "severity": "info",
                    "cve": None,
                    "cvss": None,
                    "matched_at": "u",
                    "proof": "",
                    "description": "",
                    "remediation": "",
                    "reference": "",
                }
            )
            return 1

    db = Database(tmp_path / "n.db")
    engine = Engine(db, Config(), Scan())
    oid = db.add_object("10.0.0.5", "ip", "", None, authorised=True)
    await engine.scan_object(oid)
    names = {t["name"] for t in db.findings(status="open", object_id=oid)}
    assert {"TELNET exposed on port 23", "SMB exposed on port 445"} <= names
    assert not any("Hikvision" in n for n in names)  # an info detection is not a ticket
    device = db.device_by_host("10.0.0.5")
    assert "Hikvision Web" in device["signals"]
    assert {s["service"] for s in device["services"]} == {"ssh", "telnet", "smb"}

    # Fixing the issue resolves the tickets on the next scan
    class Clean(Scan):
        async def discover_ports(self, hosts):
            return [HostResult(hosts[0], [22])]

    engine.scanner = Clean()
    await engine.scan_object(oid)
    assert not db.findings(status="open", object_id=oid)
    db.close()


async def test_min_severity_decides_tickets_but_not_what_is_learned(tmp_path):
    class Scan(FakeScanner):
        async def scan_targets(self, targets, on_finding):
            base = {
                "host": "10.0.0.5",
                "port": 443,
                "kind": "vulnerability",
                "cve": None,
                "cvss": None,
                "matched_at": "u",
                "proof": "",
                "description": "",
                "remediation": "",
                "reference": "",
            }
            on_finding({**base, "fingerprint": "i", "template_id": "tech", "name": "Hikvision Web", "severity": "info"})
            on_finding({**base, "fingerprint": "l", "template_id": "low", "name": "Low issue", "severity": "low"})
            on_finding({**base, "fingerprint": "h", "template_id": "high", "name": "High issue", "severity": "high"})
            return 3

    db = Database(tmp_path / "n.db")
    engine = Engine(db, Config(min_severity="high"), Scan())
    oid = db.add_object("10.0.0.5", "ip", "", None, authorised=True)
    await engine.scan_object(oid)
    names = {t["name"] for t in db.findings(status="open", object_id=oid)}
    assert "High issue" in names and "Low issue" not in names
    assert "Hikvision Web" in db.device_by_host("10.0.0.5")["signals"]
    db.close()
