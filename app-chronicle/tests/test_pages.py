"""The JSON API of every page against a small Chronicle history."""

from datetime import timedelta

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.database import Database, bucket_seconds, resolution
from src.webui import WebUI

# The page's own scripts send this header with every POST
SAME_PAGE = {"X-Chronicle": "1"}


@pytest.fixture
async def client(config, tmp_path, monkeypatch):
    monkeypatch.setattr("src.webui.DATA_DIR", tmp_path)
    db = Database(config)
    async with TestClient(TestServer(WebUI(0, config, db).app)) as c:
        yield c
    await db.close()


async def test_status(client):
    data = await (await client.get("/api/status")).json()
    assert data["connected"] is True
    assert data["schema"] is True
    assert data["aggregates"] is False
    assert "password" not in data["connection"]


async def test_overview(client):
    data = await (await client.get("/api/overview?hours=24")).json()
    assert data["totals"]["entities"] == 2
    assert data["totals"]["rows_24h"] > 0
    assert {d["domain"] for d in data["domains"]} == {"sensor", "light"}
    assert data["top"][0]["entity_id"] == "sensor.temperature"


async def test_entities(client):
    data = await (await client.get("/api/entities")).json()
    by_id = {e["entity_id"]: e for e in data["entities"]}
    assert by_id["sensor.temperature"]["value"] is not None
    assert by_id["sensor.temperature"]["state"] is None
    assert by_id["light.desk"]["state"] in ("on", "off")


async def test_numeric_entity(client):
    data = await (await client.get("/api/entity?entity_id=sensor.temperature&range=24h")).json()
    assert data["kind"] == "numeric"
    assert data["source"] == "raw"
    assert len(data["points"]) > 100
    # The history is two days old: 30 days shows those two days, still every value
    data = await (await client.get("/api/entity?entity_id=sensor.temperature&range=30d")).json()
    assert data["from"] == data["entity"]["first"]
    assert data["source"] == "raw"


async def test_state_entity(client):
    data = await (await client.get("/api/entity?entity_id=light.desk&range=7d")).json()
    assert data["kind"] == "state"
    assert {p["state"] for p in data["points"]} == {"on", "off"}
    assert data["latest"]["attributes"] == {"friendly_name": "Desk"}


async def test_zoom_window(client):
    entities = await (await client.get("/api/entity?entity_id=sensor.temperature&range=24h")).json()
    to = entities["to"]
    data = await (await client.get(f"/api/entity?entity_id=sensor.temperature&from={to - 7200}&to={to}")).json()
    assert data["zoomed"] is True
    assert data["source"] == "raw"
    # A value every 10 minutes for two hours, plus the value carried to the window's start
    assert 12 <= len([p for p in data["points"] if not p.get("carried")]) <= 13
    assert data["points"][0]["t"] == data["from"]


async def test_bad_requests(client):
    assert (await client.get("/api/entity?entity_id=nope&range=24h")).status == 400
    assert (await client.get("/api/entity?entity_id=sensor.missing&range=24h")).status == 404
    assert (await client.get("/api/entity?entity_id=sensor.temperature&range=5y")).status == 400
    assert (await client.get("/api/entity?entity_id=sensor.temperature&from=200&to=100")).status == 400
    assert (await client.get("/api/entity?entity_id=sensor.temperature&from=100")).status == 400
    assert (await client.get("/api/entity?entity_id=sensor.temperature&from=x&to=y")).status == 400


async def test_storage(client):
    data = await (await client.get("/api/storage")).json()
    assert data["chunks"]
    assert {t["name"] for t in data["tables"]} >= {"chronicle.states", "chronicle.entities", "chronicle.attributes"}


async def test_sql_endpoint(client):
    """The SQL page through the web server: a session, a confirmation, PostgreSQL's errors."""
    ok = await client.post(
        "/api/sql/run", headers=SAME_PAGE, json={"sql": "SELECT entity_id FROM chronicle.entities ORDER BY 1;"}
    )
    assert ok.status == 200
    body = await ok.json()
    assert body["results"][0]["rows"] == [["light.desk"], ["sensor.temperature"]]
    assert body["session"]
    asked = await client.post(
        "/api/sql/run", headers=SAME_PAGE, json={"sql": "DELETE FROM chronicle.states", "session": body["session"]}
    )
    assert (await asked.json())["confirm"] == [{"kind": "DELETE", "count": 1}]
    failed = await client.post(
        "/api/sql/run", headers=SAME_PAGE, json={"sql": "SELECT * FROM nope", "session": body["session"]}
    )
    error = (await failed.json())["error"]
    assert error["sqlstate"] == "42P01" and "nope" in error["message"]
    closed = await client.post("/api/sql/close", headers=SAME_PAGE, json={"session": body["session"]})
    assert closed.status == 200


async def test_without_chronicle_tables(empty_config, tmp_path, monkeypatch):
    monkeypatch.setattr("src.webui.DATA_DIR", tmp_path)
    db = Database(empty_config)
    async with TestClient(TestServer(WebUI(0, empty_config, db).app)) as c:
        status = await (await c.get("/api/status")).json()
        assert status["connected"] is True
        assert status["schema"] is False
        assert (await c.get("/api/overview")).status == 409
    await db.close()


def test_bucket_seconds():
    assert bucket_seconds(86400) == 120
    assert bucket_seconds(7 * 86400) == 900
    assert bucket_seconds(10) == 60


def test_resolution_follows_the_span():
    assert resolution(timedelta(hours=1)) == "raw"
    assert resolution(timedelta(days=2)) == "raw"
    assert resolution(timedelta(days=7)) == "minutes"
    assert resolution(timedelta(days=30)) == "hourly"
    assert resolution(timedelta(days=365)) == "daily"


async def test_fresh_history_fills_every_range(fresh_config, tmp_path, monkeypatch):
    """An hour after installing: every range shows that hour with every value (Ron saw empty
    charts in 0.1.0: the ranges ran from 24 hours or 30 days ago, with an hour at the edge)."""
    monkeypatch.setattr("src.webui.DATA_DIR", tmp_path)
    db = Database(fresh_config)
    async with TestClient(TestServer(WebUI(0, fresh_config, db).app)) as c:
        for range_key in ("24h", "7d", "30d", "1y", "all"):
            data = await (await c.get(f"/api/entity?entity_id=sensor.power&range={range_key}")).json()
            assert data["source"] == "raw", range_key
            assert data["from"] == data["entity"]["first"], range_key
            assert data["to"] - data["from"] < 2 * 3600, range_key
            values = [p for p in data["points"] if not p.get("carried")]
            assert len(values) == 120, range_key
            # The unavailable stretch is a gap, not left out
            assert sum(1 for p in values if p["v"] is None) == 5
        tracker = await (await c.get("/api/entity?entity_id=device_tracker.phone&range=24h")).json()
    await db.close()
    # Twelve updates, of which ten only changed attributes: two changes of state
    assert [p["state"] for p in tracker["points"]] == ["home", "not_home"]
    assert tracker["updates"] == 12


async def test_post_needs_the_page_header(aiohttp_client, config, tmp_path, monkeypatch):
    """A link, a form or another site can't send the header: such a POST is refused."""
    monkeypatch.setattr("src.webui.DATA_DIR", tmp_path)
    client = await aiohttp_client(WebUI(0, config, Database(config)).app)
    response = await client.post("/api/sql/run", json={"sql": "DROP TABLE chronicle.states"})
    assert response.status == 403
    # The donate card's shared script is the one exception, and only records the card's state
    assert (await client.post("/api/donate", json={"action": "later"})).status != 403


def test_infinite_times_are_none():
    """TimescaleDB's -infinity ("never") for a job that hasn't run yet, e.g. right after a restore."""
    from datetime import UTC, datetime

    from src.database import ts

    assert ts(datetime.min.replace(tzinfo=UTC)) is None
    assert ts(datetime.max.replace(tzinfo=UTC)) is None
    assert ts(datetime(2026, 1, 1, tzinfo=UTC)) == 1767225600.0
