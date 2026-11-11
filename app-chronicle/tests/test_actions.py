"""Exclude and Delete history: the app calls the Chronicle integration's actions in Home Assistant.

A small fake of Home Assistant's core API stands in for the Supervisor; no database needed.
"""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from src.config import Config
from src.database import Database
from src.webui import WebUI


@pytest.fixture
async def core_api():
    """Records the actions called; exclude_* fails like an integration older than 0.1.5 when asked."""
    calls = []
    state = {"old_integration": False}

    async def service(request: web.Request) -> web.Response:
        assert request.headers["Authorization"] == "Bearer token"
        assert "return_response" in request.query
        name = request.match_info["service"]
        data = await request.json()
        calls.append((name, data))
        if name.startswith("exclude") and state["old_integration"]:
            return web.Response(status=400, text="Service chronicle.exclude_entities not found.")
        return web.json_response({"changed_states": [], "service_response": {"deleted_rows": 7}})

    app = web.Application()
    app.router.add_post("/services/chronicle/{service}", service)
    async with TestServer(app) as server:
        yield server, calls, state


@pytest.fixture
async def client(core_api, monkeypatch, tmp_path):
    server, _, _ = core_api
    monkeypatch.setattr("src.webui.DATA_DIR", tmp_path)
    monkeypatch.setattr("src.webui.CORE_API_URL", str(server.make_url("")).rstrip("/"))
    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")
    config = Config()
    webui = WebUI(0, config, Database(config))
    webui.ingress_only = False
    async with TestClient(TestServer(webui.app)) as c:
        yield c


async def test_exclude_entity(client, core_api):
    _, calls, _ = core_api
    response = await client.post("/api/exclude", json={"entity_id": "sensor.noisy", "purge": True})
    assert response.status == 200
    assert (await response.json()) == {"deleted_rows": 7}
    assert calls == [("exclude_entities", {"entity_id": ["sensor.noisy"], "purge": True})]


async def test_exclude_domain(client, core_api):
    _, calls, _ = core_api
    response = await client.post("/api/exclude", json={"domain": "automation"})
    assert response.status == 200
    assert calls == [("exclude_domains", {"domain": ["automation"], "purge": False})]


async def test_purge(client, core_api):
    _, calls, _ = core_api
    response = await client.post("/api/purge", json={"entity_id": "sensor.gone"})
    assert response.status == 200
    assert calls == [("purge_entities", {"entity_id": ["sensor.gone"]})]


async def test_invalid_input(client, core_api):
    _, calls, _ = core_api
    assert (await client.post("/api/exclude", json={"entity_id": "not an entity"})).status == 400
    assert (await client.post("/api/exclude", json={"domain": "Bad Domain"})).status == 400
    assert (await client.post("/api/exclude", json={})).status == 400
    assert (await client.post("/api/purge", json={"entity_id": "x"})).status == 400
    assert calls == []


async def test_old_integration_says_so(client, core_api):
    _, _, state = core_api
    state["old_integration"] = True
    response = await client.post("/api/exclude", json={"entity_id": "sensor.a"})
    assert response.status == 502
    assert "0.1.5" in (await response.json())["error"]


async def test_outside_home_assistant(client, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN")
    response = await client.post("/api/purge", json={"entity_id": "sensor.a"})
    assert response.status == 503
    assert "Home Assistant app" in (await response.json())["error"]
