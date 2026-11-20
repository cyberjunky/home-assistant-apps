"""The app against a stand-in Home Assistant (tools/mock_ha.py): the first dashboard, what a saved one
may hold, the history's steps, and the API behind the pages."""

import asyncio
import json

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import ha_client
from src.config import Config
from src.hub import Hub, picture_mark, slim
from src.store import COLS, ROWS, Conflict, Store, clean, starter
from src.webui import SERVICES, WebUI, buckets
from tools import mock_ha

PORT = 18134


def state(value: str, **attributes) -> dict:
    return {"s": value, "a": attributes, "c": None}


# ---------------------------------------------------------------------
# The first dashboard
# ---------------------------------------------------------------------
def test_starter_from_nothing_is_a_clock():
    doc = starter({}, {})
    assert [p["name"] for p in doc["pages"]] == ["Now"]
    assert [w["type"] for w in doc["pages"][0]["widgets"]] == ["clock"]
    assert doc["chips"] == []


def test_starter_uses_what_is_there():
    states = {
        "light.desk": state("on"),
        "light.gone": state("unavailable"),
        "sensor.temp": state("20.5", device_class="temperature"),
        "sensor.power": state("300", device_class="power"),
        "sensor.energy": state("12", device_class="energy"),
        "calendar.home": state("off"),
        "camera.door": state("idle"),
        "binary_sensor.doorbell": state("off"),
        "weather.home": state("sunny"),
    }
    doc = starter(states, {"sensor.temp": "Office"})
    assert [p["name"] for p in doc["pages"]] == ["Now", "Home", "Energy"]
    now = {w["type"]: w for w in doc["pages"][0]["widgets"]}
    assert now["tiles"]["entities"] == ["light.desk"]
    assert now["agenda"]["entities"] == ["calendar.home"]
    assert doc["chips"][0] == {"entity": "sensor.temp", "label": "Office"}
    assert doc["tablet"]["doorbell"]["trigger"] == "binary_sensor.doorbell"
    assert doc["tablet"]["doorbell"]["camera"] == "camera.door"
    # Everything it places fits the grid
    for page in doc["pages"]:
        for w in page["widgets"]:
            assert w["x"] + w["w"] <= COLS and w["y"] + w["h"] <= ROWS, w


# ---------------------------------------------------------------------
# What a saved dashboard may hold
# ---------------------------------------------------------------------
def test_clean_keeps_blocks_on_the_grid():
    doc = clean({"pages": [{"name": "A", "widgets": [{"type": "value", "x": 99, "y": -3, "w": 200, "h": 0}]}]})
    w = doc["pages"][0]["widgets"][0]
    assert (w["x"], w["y"], w["w"], w["h"]) == (0, 0, COLS, 1)
    assert w["entities"] == [] and w["opts"] == {}


def test_clean_rejects_what_is_no_dashboard():
    for bad in (None, [], {"pages": "x"}, "text"):
        with pytest.raises(ValueError):
            clean(bad)
    assert len(clean({"pages": []})["pages"]) == 1


def test_clean_style_and_tablet():
    doc = clean(
        {
            "pages": [{"widgets": [{"opts": {"big": "x" * 5000}}]}],
            "style": {"text": "Inter", "evil": "url(x)", "paper": ""},
            "tablet": {
                "phases": {"morning": "07:30", "day": "25:00", "lunch": "12:00"},
                "night": {"dark": "always", "dim": 500, "page": "p1"},
                "idle": {"minutes": "5"},
                "doorbell": {"trigger": "binary_sensor.bell", "camera": "camera.door", "seconds": 1},
            },
        }
    )
    assert doc["style"] == {"text": "Inter"}
    assert doc["pages"][0]["widgets"][0]["opts"] == {}
    tablet = doc["tablet"]
    assert tablet["phases"] == {"morning": "07:30"}
    assert tablet["night"] == {"dark": "", "dim": 90, "page": "p1"}
    assert tablet["idle"]["minutes"] == 5
    assert tablet["doorbell"]["seconds"] == 5


def test_store_saves_and_notices_another_writer(tmp_path):
    store = Store(tmp_path / "dashboard.json")
    doc = store.ensure({}, {})
    assert doc["rev"] == 0
    saved = store.save({"pages": [{"name": "Mine", "widgets": []}]}, 0)
    assert saved["rev"] == 1
    with pytest.raises(Conflict):
        store.save({"pages": []}, 0)
    assert Store(tmp_path / "dashboard.json").doc["pages"][0]["name"] == "Mine"


def test_store_keeps_a_broken_file_aside(tmp_path):
    path = tmp_path / "dashboard.json"
    path.write_text("{not json")
    store = Store(path)
    assert store.doc is None
    assert (tmp_path / "dashboard.broken").read_text() == "{not json"


# ---------------------------------------------------------------------
# History in steps
# ---------------------------------------------------------------------
def at(ts: float, value) -> dict:
    return {"state": str(value), "last_changed": f"2026-01-01T00:00:{int(ts):02d}+00:00"}


BASE = 1767225600  # 2026-01-01T00:00:00Z


def test_buckets_mean_carries_the_last_value():
    history = [at(1, 10), at(2, 20), at(31, "unavailable"), at(45, 40)]
    points = buckets(history, BASE, BASE + 60, 10, "mean")
    assert [p[1] for p in points] == [15, 20, 20, 20, 40, 40]


def test_buckets_delta_counts_a_restart_from_zero():
    history = [at(1, 5), at(9, 8), at(15, 11), at(25, 2), at(29, 3)]
    points = buckets(history, BASE, BASE + 40, 10, "delta")
    # 5 -> 8, 8 -> 11, the counter starts again and reaches 3, then nothing
    assert [p[1] for p in points] == [3, 3, 3, 0]


def test_buckets_without_numbers():
    assert [p[1] for p in buckets([at(1, "on")], BASE, BASE + 20, 10, "mean")] == [None, None]


# ---------------------------------------------------------------------
# What reaches the browser
# ---------------------------------------------------------------------
def test_slim_keeps_tokens_and_bulk_out():
    out = slim(
        {
            "state": "playing",
            "last_changed": "x",
            "attributes": {
                "access_token": "secret",
                "entity_picture": "/api/media_player_proxy/media_player.a?token=secret",
                "big": list(range(2000)),
                "volume_level": 0.3,
            },
        }
    )
    assert out["s"] == "playing"
    assert out["a"] == {
        "entity_picture": picture_mark("/api/media_player_proxy/media_player.a?token=secret"),
        "volume_level": 0.3,
    }
    assert "secret" not in json.dumps(out)


def test_no_dangerous_services():
    assert "lock" not in SERVICES and "alarm_control_panel" not in SERVICES and "homeassistant" not in SERVICES
    assert "shell_command" not in SERVICES and "hassio" not in SERVICES


# ---------------------------------------------------------------------
# The API, against the stand-in
# ---------------------------------------------------------------------
@pytest.fixture
async def mock():
    server = mock_ha.MockHA(PORT)
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def app(mock, tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", mock_ha.TOKEN)
    monkeypatch.setenv("DASHBOARD_DEV", "1")
    monkeypatch.setattr(ha_client, "CORE_API_URL", f"http://127.0.0.1:{PORT}/core/api")
    monkeypatch.setattr(ha_client, "CORE_WS_URL", f"ws://127.0.0.1:{PORT}/core/websocket")
    client = ha_client.HAClient()
    loaded = asyncio.Event()
    hub: Hub

    async def connected():
        await hub.connected()
        loaded.set()

    ws = ha_client.HAWebSocket(client, connected)
    hub = Hub(ws)
    webui = WebUI(0, Config(), client, ws, hub, Store(tmp_path / "dashboard.json"))
    runner = asyncio.create_task(ws.run())
    await asyncio.wait_for(loaded.wait(), 10)
    async with TestClient(TestServer(webui.app)) as test:
        yield test, hub
    runner.cancel()
    await client.close()


async def test_boot_gives_the_dashboard_and_no_tokens(app):
    test, hub = app
    answer = await test.get("/api/boot")
    body = await answer.json()
    assert body["loaded"] and body["connected"]
    assert [p["name"] for p in body["doc"]["pages"]] == ["Now", "Home", "Energy"]
    assert body["areas"]["light.floor_lamp"] == "Living room"
    assert "secret" not in await answer.text()
    assert hub.pictures["media_player.living_room"].startswith("/api/media_player_proxy/")


async def test_page_and_static(app):
    test, _ = app
    page = await (await test.get("/")).text()
    assert "static/main.js" in page and "__VERSION__" not in page
    assert (await test.get("/static/fonts/inter.woff2")).status == 200
    assert (await test.get("/favicon.ico")).status == 200


async def test_a_tile_switches_the_light(app, mock):
    test, hub = app
    assert mock.states["light.cabinet"]["state"] == "off"
    body = {"domain": "light", "service": "toggle", "entity_id": "light.cabinet"}
    assert (await test.post("/api/service", json=body)).status == 200
    for _ in range(50):
        await asyncio.sleep(0.02)
        if hub.states["light.cabinet"]["s"] == "on":
            break
    assert hub.states["light.cabinet"]["s"] == "on"


async def test_only_what_a_dashboard_does(app):
    test, _ = app
    for body in (
        {"domain": "homeassistant", "service": "restart", "entity_id": "light.cabinet"},
        {"domain": "light", "service": "turn_on", "entity_id": "light.not_there"},
        {"domain": "cover", "service": "toggle", "entity_id": "cover.garage"},
    ):
        assert (await test.post("/api/service", json=body)).status == 403
    assert (await test.post("/api/service", json={"domain": "light"})).status == 400
    assert (await test.post("/api/service", data="x")).status == 415


async def test_changes_only_from_ingress(app, monkeypatch):
    test, _ = app
    monkeypatch.delenv("DASHBOARD_DEV")
    body = {"domain": "light", "service": "toggle", "entity_id": "light.cabinet"}
    assert (await test.post("/api/service", json=body)).status == 403
    assert (await test.post("/api/doc", json={"rev": 0, "doc": {"pages": []}})).status == 403
    # Looking is fine
    assert (await test.get("/api/boot")).status == 200


async def test_saving_the_dashboard(app):
    test, _ = app
    doc = (await (await test.get("/api/boot")).json())["doc"]
    doc["pages"][0]["name"] = "Nu"
    doc["style"] = {"text": "Fraunces"}
    answer = await test.post("/api/doc", json={"rev": doc["rev"], "doc": doc})
    assert (await answer.json())["rev"] == doc["rev"] + 1
    again = (await (await test.get("/api/boot")).json())["doc"]
    assert again["pages"][0]["name"] == "Nu" and again["style"] == {"text": "Fraunces"}
    stale = await test.post("/api/doc", json={"rev": doc["rev"], "doc": doc})
    assert stale.status == 409 and (await stale.json())["doc"]["rev"] == doc["rev"] + 1
    assert (await test.post("/api/doc", json={"rev": again["rev"], "doc": "x"})).status == 400


async def test_history(app):
    test, _ = app
    line = (await (await test.get("/api/history?entity=sensor.living_room_temperature&hours=24")).json())["points"]
    assert len(line) == 120 and all(15 < p[1] < 25 for p in line if p[1] is not None)
    days = await test.get("/api/history?entity=sensor.solar_energy_today&hours=168&agg=delta&step=day")
    days = (await days.json())["points"]
    assert len(days) == 7 and all(p[1] is None or p[1] >= 0 for p in days)
    assert (await test.get("/api/history?entity=sensor.nope")).status == 404
    assert (await test.get("/api/history?entity=sensor.power_consumption&hours=x")).status == 400


async def test_calendar_and_forecast(app):
    test, _ = app
    answer = await test.get("/api/calendar?entities=calendar.family,calendar.house,light.cabinet&days=2")
    events = (await answer.json())["events"]
    assert {e["entity"] for e in events} == {"calendar.family", "calendar.house"}
    assert [e["start"][:10] for e in events] == sorted(e["start"][:10] for e in events)
    assert any(e["all_day"] for e in events)
    daily = (await (await test.get("/api/forecast?entity=weather.home")).json())["forecast"]
    hourly = (await (await test.get("/api/forecast?entity=weather.home&type=hourly")).json())["forecast"]
    assert len(daily) == 7 and "templow" in daily[0] and len(hourly) == 48
    assert (await test.get("/api/forecast?entity=light.cabinet")).status == 404


async def test_pictures_and_cameras(app):
    test, _ = app
    cover = await test.get("/api/picture?entity=media_player.living_room")
    assert cover.status == 200 and cover.content_type == "image/png"
    assert (await test.get("/api/picture?entity=light.cabinet")).status == 404
    still = await test.get("/api/camera?entity=camera.front_door")
    assert still.status == 200 and (await still.read()).startswith(b"\x89PNG")
    assert (await test.get("/api/camera?entity=light.cabinet")).status == 404
    live = await test.get("/api/camera-stream?entity=camera.garden")
    assert live.status == 200 and live.content_type == "multipart/x-mixed-replace"
    assert b"image/png" in await live.content.readany()
    live.close()


async def test_picture_never_fetches_an_outside_address(app):
    test, hub = app
    hub.pictures["media_player.living_room"] = "http://127.0.0.1:1/internal"
    assert (await test.get("/api/picture?entity=media_player.living_room")).status == 404


async def test_the_live_stream_tells_of_changes(app, mock):
    test, _ = app
    stream = await test.get("/api/live")
    await asyncio.sleep(0.05)
    await mock.set_state("binary_sensor.doorbell", "on")
    line = await asyncio.wait_for(stream.content.readline(), 5)
    batch = json.loads(line.decode().removeprefix("data: "))
    assert batch[0]["type"] == "state" and batch[0]["id"] == "binary_sensor.doorbell" and batch[0]["state"]["s"] == "on"
    stream.close()
