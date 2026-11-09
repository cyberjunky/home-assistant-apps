import asyncio
import json
import sqlite3

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import custom_templates
from src.config import Config
from src.database import Database
from src.engine import Engine
from src.scanner import HostResult
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

    async def discover_ports(self, hosts):
        return [HostResult(hosts[0], [23])]

    async def scan_targets(self, targets, on_finding):
        return 0


@pytest.fixture
async def client(tmp_path, monkeypatch):
    monkeypatch.setattr(custom_templates, "CUSTOM_DIR", tmp_path / "custom")
    db = Database(tmp_path / "n.db")
    ui = WebUI(0, db, Engine(db, Config(update_templates=False), Scan()))

    async def ok(path):
        return True, "ok"

    ui.templates._validate = ok
    async with TestClient(TestServer(ui.app)) as c:
        c.db = db
        yield c
    db.close()


async def post(c, path, body, method="post"):
    r = await getattr(c, method)(f"/api/{path}", data=json.dumps(body), headers={"Content-Type": "application/json"})
    return r.status, await r.json()


async def test_page_and_status(client):
    page = await (await client.get("/")).text()
    assert "__VERSION__" not in page and "Nuclei" in page
    status = await (await client.get("/api/status")).json()
    assert status["summary"]["open_total"] == 0 and "scanners" in status


async def test_object_flow_and_gate(client):
    status, preview = await post(client, "objects/preview", {"target": "10.0.0.0/30"})
    assert status == 200 and preview["private"] and preview["host_count"] == 2
    assert (await post(client, "objects/preview", {"target": "nonsense!"}))[0] == 400
    assert (await post(client, "objects/preview", {"target": "10.0.0.0/8"}))[0] == 400  # too large

    # An object added without confirming authorisation can't be scanned
    status, obj = await post(client, "objects", {"target": "10.0.0.5", "authorised": False})
    assert status == 200 and not obj["authorised"]
    assert (await post(client, "objects", {"target": "10.0.0.5"}))[0] == 409  # no duplicates
    status, refused = await post(client, f"objects/{obj['id']}/scan", {})
    assert status == 403 and "authorised" in refused["error"]

    status, patched = await post(client, f"objects/{obj['id']}", {"authorised": True}, method="patch")
    assert patched["authorised"] == 1
    assert (await post(client, f"objects/{obj['id']}/scan", {}))[0] == 200


async def test_scan_creates_device_and_tickets_that_can_be_worked(client):
    _, obj = await post(client, "objects", {"target": "10.0.0.5", "authorised": True})
    await post(client, f"objects/{obj['id']}/scan", {})
    for _ in range(50):
        if (await (await client.get("/api/tickets")).json())["tickets"]:
            break
        await asyncio.sleep(0.02)
    tickets = (await (await client.get("/api/tickets")).json())["tickets"]
    assert tickets and tickets[0]["name"].startswith("TELNET")
    tid = tickets[0]["id"]
    _status, updated = await post(client, f"tickets/{tid}", {"status": "false_positive", "note": "lab"}, method="patch")
    assert updated["status"] == "false_positive" and updated["note"] == "lab"
    assert (await post(client, f"tickets/{tid}", {"status": "bogus"}, method="patch"))[0] == 400
    assert not (await (await client.get("/api/tickets?status=open")).json())["tickets"]

    devices = (await (await client.get("/api/devices")).json())["devices"]
    assert devices[0]["services"][0]["service"] == "telnet"
    _status, device = await post(
        client, f"devices/{devices[0]['id']}", {"name": "Old switch", "tags": ["switch"]}, method="patch"
    )
    assert device["name"] == "Old switch" and device["tags"] == ["switch"]


async def test_templates_roundtrip(client):
    assert (await post(client, "templates/my-check", {"body": "id: my-check"}, method="put"))[0] == 200
    payload = await (await client.get("/api/templates")).json()
    assert payload["templates"][0]["name"] == "my-check"
    assert payload["community_count"] == 4321 and payload["community_updated_at"] == 1700000000
    assert (await post(client, "templates/Bad Name", {"body": "x"}, method="put"))[0] == 400
    assert (await client.delete("/api/templates/my-check")).status == 200


async def test_backup_is_a_database_and_wipe_needs_confirmation(client, tmp_path):
    await post(client, "objects", {"target": "10.0.0.5", "authorised": True})
    response = await client.get("/api/backup")
    assert response.status == 200 and "nuclei-" in response.headers["Content-Disposition"]
    copy = tmp_path / "copy.db"
    copy.write_bytes(await response.read())
    assert sqlite3.connect(copy).execute("SELECT COUNT(*) FROM scan_objects").fetchone()[0] == 1
    # The temporary copy goes once the handler finishes, just after the client got the last byte
    for _ in range(50):
        if not list(client.db.path.parent.glob("backup-*")):
            break
        await asyncio.sleep(0.02)
    assert not list(client.db.path.parent.glob("backup-*"))

    assert (await post(client, "wipe", {}))[0] == 400
    assert (await post(client, "wipe", {"confirm": "no"}))[0] == 400
    assert client.db.summary()["objects"] == 1
    assert (await post(client, "wipe", {"confirm": "delete"}))[0] == 200
    assert client.db.summary()["objects"] == 0
    report = await (await client.get("/api/database")).json()
    assert report["counts"]["scan_objects"] == 0


async def test_under_home_assistant_only_ingress_may_change(client, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    status, _ = await post(client, "objects", {"target": "10.0.0.5", "authorised": True})
    assert status == 403
    assert (await client.get("/api/status")).status == 200
    assert (await client.get("/api/backup")).status == 200


async def test_json_only(client):
    r = await client.post("/api/objects", data="x=1", headers={"Content-Type": "text/plain"})
    assert r.status == 415
