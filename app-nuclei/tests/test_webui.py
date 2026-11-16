"""The dashboard's API: overview, issues, devices, scanner, templates, backup and the danger zone."""

import asyncio
import json
import sqlite3

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import custom_templates
from src.config import Config
from src.database import Database
from src.engine import Engine
from src.freshness import Freshness
from src.scanner import HostResult
from src.scheduler import Scheduler
from src.webui import WebUI


class Scan:
    def require_templates(self):
        pass

    async def update_templates(self):
        return True

    def template_count(self):
        return 4321

    def templates_updated_at(self):
        return 1700000000

    def template_stats(self):
        return {"total": 4321, "cves": 1200, "newest_cve": "CVE-2026-1234"}

    async def versions(self):
        return {"nuclei": "3.11.1", "naabu": "2.7.0", "templates": "10.3.4"}

    async def discover_ports(self, hosts):
        return [HostResult(hosts[0], [23])]

    async def scan_targets(self, targets, on_finding):
        return 0


class Trackers:
    async def fetch(self):
        return [
            {"entity_id": "device_tracker.nas", "name": "NAS", "ip": "192.168.1.20", "mac": None, "online": True},
            {"entity_id": "device_tracker.phone", "name": "Phone", "ip": "192.168.1.100", "mac": None, "online": False},
            {"entity_id": "device_tracker.vps", "name": "VPS", "ip": "8.8.8.8", "mac": None, "online": True},
        ]


async def latest(repo):
    return {"projectdiscovery/nuclei": "3.11.2"}.get(repo)


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_templates, "CUSTOM_DIR", tmp_path / "custom")
    db = Database(tmp_path / "n.db")
    engine = Engine(db, Config(update_templates=False), Scan())
    scheduler = Scheduler(engine, db, Trackers())
    await scheduler.sync()
    ui = WebUI(0, db, engine, scheduler, Freshness(engine.scanner, latest=latest))

    async def ok(path):
        return True, "ok"

    ui.templates._validate = ok
    async with TestClient(TestServer(ui.app)) as c:
        c.db, c.engine, c.scheduler = db, engine, scheduler
        yield c
    db.close()


async def call(c, path, body=None, method="post"):
    kwargs = {"data": json.dumps(body), "headers": {"Content-Type": "application/json"}} if body is not None else {}
    r = await getattr(c, method)(f"/api/{path}", **kwargs)
    return r.status, await r.json()


def device(c, name):
    return next(d for d in c.db.devices() if d["name"] == name)


async def test_page_and_overview(client):
    page = await (await client.get("/")).text()
    assert "__VERSION__" not in page and "NUCLEI" in page
    status, o = await call(client, "overview", method="get")
    assert status == 200 and o["summary"]["devices"]["tracked"] == 3
    assert [q["name"] for q in o["queue"]] == ["NAS"] and o["home_assistant"]["error"] is None
    nuclei = next(c for c in o["freshness"]["components"] if c["key"] == "nuclei")
    assert nuclei == {"key": "nuclei", "installed": "3.11.1", "latest": "3.11.2", "outdated": True}
    assert o["freshness"]["templates"]["newest_cve"] == "CVE-2026-1234"


async def test_scan_a_device_and_work_its_issues(client):
    nas = device(client, "NAS")
    await client.engine.scan_device(nas, "new device")
    status, devices = await call(client, "devices", method="get")
    assert status == 200 and devices["rescan_days"] == 7
    nas = next(d for d in devices["devices"] if d["name"] == "NAS")
    assert nas["open_ports"] == [23] and nas["issues"]["high"] == 1 and nas["next_scan_at"]
    issue = (await call(client, "issues?state=open", method="get"))[1]["issues"][0]
    assert issue["name"] == "TELNET exposed on port 23" and issue["device_name"] == "NAS"
    assert (await call(client, f"issues/{issue['id']}", {"state": "resolved"}, "patch"))[0] == 400
    assert (await call(client, f"issues/{issue['id']}", {"state": "acknowledged"}, "patch"))[1][
        "state"
    ] == "acknowledged"
    assert (await call(client, "issues?state=open", method="get"))[1]["issues"] == []
    assert (await call(client, f"issues?state=unresolved&device={nas['id']}", method="get"))[1]["issues"]


async def test_scan_now_and_exclude(client):
    nas, phone, vps = device(client, "NAS"), device(client, "Phone"), device(client, "VPS")
    assert (await call(client, f"devices/{nas['id']}/scan"))[1] == {"queued": True}
    assert client.db.device(nas["id"])["scan_requested"] == 1
    # Not home, or not on your own network: refused
    assert (await call(client, f"devices/{phone['id']}/scan"))[0] == 409
    assert (await call(client, f"devices/{vps['id']}/scan"))[0] == 403
    assert (await call(client, f"devices/{nas['id']}", {"excluded": True}, "patch"))[1]["excluded"] == 1
    await client.scheduler.sync()
    assert client.scheduler.queue == []


async def test_templates_roundtrip(client):
    assert (await call(client, "templates/my-check", {"body": "id: my-check"}, "put"))[0] == 200
    assert (await call(client, "templates", method="get"))[1]["templates"][0]["name"] == "my-check"
    assert (await call(client, "templates/Bad Name", {"body": "x"}, "put"))[0] == 400
    assert (await client.delete("/api/templates/my-check")).status == 200


async def test_backup_is_a_database_and_wipe_needs_confirmation(client, tmp_path):
    response = await client.get("/api/backup")
    assert response.status == 200 and "nuclei-" in response.headers["Content-Disposition"]
    copy = tmp_path / "copy.db"
    copy.write_bytes(await response.read())
    assert sqlite3.connect(copy).execute("SELECT COUNT(*) FROM devices").fetchone()[0] == 3
    # The temporary copy goes once the handler finishes, just after the client got the last byte
    for _ in range(50):
        if not list(client.db.path.parent.glob("backup-*")):
            break
        await asyncio.sleep(0.02)
    assert not list(client.db.path.parent.glob("backup-*"))

    assert (await call(client, "wipe", {}))[0] == 400
    assert (await call(client, "wipe", {"confirm": "no"}))[0] == 400
    assert (await call(client, "wipe", {"confirm": "delete"}))[0] == 200
    assert (await call(client, "database", method="get"))[1]["counts"]["devices"] == 0


async def test_under_home_assistant_only_ingress_may_change(client, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    nas = device(client, "NAS")
    assert (await call(client, f"devices/{nas['id']}/scan"))[0] == 403
    assert (await client.get("/api/overview")).status == 200


async def test_json_only(client):
    r = await client.post("/api/issues/acknowledge", data="x=1", headers={"Content-Type": "text/plain"})
    assert r.status == 415
