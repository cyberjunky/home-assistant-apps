"""The JSON API of every page against a small Chronicle history."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.database import Database, bucket_seconds
from src.webui import WebUI, entity_csv


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
    # Without the aggregates the long ranges are averaged from the rows
    data = await (await client.get("/api/entity?entity_id=sensor.temperature&range=30d")).json()
    assert data["source"] == "buckets"
    assert data["bucket"] == 3600


async def test_state_entity_and_csv(client):
    data = await (await client.get("/api/entity?entity_id=light.desk&range=7d")).json()
    assert data["kind"] == "state"
    assert {p["state"] for p in data["points"]} == {"on", "off"}
    assert data["latest"]["attributes"] == {"friendly_name": "Desk"}
    response = await client.get("/api/entity.csv?entity_id=light.desk&range=7d")
    text = await response.text()
    assert text.splitlines()[0] == "time,entity_id,state"


async def test_bad_requests(client):
    assert (await client.get("/api/entity?entity_id=nope&range=24h")).status == 400
    assert (await client.get("/api/entity?entity_id=sensor.missing&range=24h")).status == 404
    assert (await client.get("/api/entity?entity_id=sensor.temperature&range=5y")).status == 400


async def test_storage(client):
    data = await (await client.get("/api/storage")).json()
    assert data["chunks"]
    assert {t["name"] for t in data["tables"]} >= {"chronicle.states", "chronicle.entities", "chronicle.attributes"}


async def test_query_endpoint(client):
    ok = await client.post("/api/query", json={"sql": "SELECT entity_id FROM chronicle.entities ORDER BY 1;"})
    assert ok.status == 200
    assert (await ok.json())["rows"] == [["light.desk"], ["sensor.temperature"]]
    refused = await client.post("/api/query", json={"sql": "DELETE FROM chronicle.states"})
    assert refused.status == 400
    failed = await client.post("/api/query", json={"sql": "SELECT * FROM nope"})
    body = await failed.json()
    assert failed.status == 400
    assert body["sqlstate"] == "42P01"
    assert "nope" in body["error"]


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


def test_csv_of_buckets():
    data = {
        "entity": {"entity_id": "sensor.x"},
        "kind": "numeric",
        "source": "hourly",
        "points": [{"t": 0, "mean": 1.5, "min": 1, "max": 2, "last": 2, "n": 4}],
    }
    assert entity_csv(data).splitlines() == [
        "bucket_start,entity_id,mean,min,max,last,samples",
        "1970-01-01T00:00:00+00:00,sensor.x,1.5,1,2,2,4",
    ]
