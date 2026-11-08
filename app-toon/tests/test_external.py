"""Sensors from Home Assistant, the database backup and report."""

import asyncio
import sqlite3
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.config import Config
from src.database import Database
from src.external import OUTSIDE_KEY, STALE_SECONDS, ExternalSensors, sensor_key
from src.monitor import ToonMonitor
from src.webui import WebUI


class FakeClient:
    available = True

    def __init__(self, states):
        self.states = states

    async def get_state(self, entity):
        state = self.states.get(entity)
        if isinstance(state, Exception):
            raise state
        return state


class FakeMqtt:
    connected = False
    broker = None


def config(**extra):
    return Config.from_options({"ssh": {"host": "toon", "password": "x"}, **extra})


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "toon.db")
    yield db
    db.close()


def reading(state, unit="°C"):
    return {"state": state, "attributes": {"unit_of_measurement": unit}}


def test_options_are_read():
    c = config(
        outside_temperature=" sensor.out ",
        gas_kwh_per_m3=9.5,
        external_sensors=[{"name": "Pipe", "entity": "sensor.pipe"}, {"name": "", "entity": "sensor.no_name"}],
    )
    assert c.outside_temperature == "sensor.out"
    assert c.gas_kwh_per_m3 == 9.5
    assert c.external_sensors == [{"name": "Pipe", "entity": "sensor.pipe"}]
    assert config().external_sensors == []
    assert config().gas_kwh_per_m3 == 9.77


def test_keys_stay_the_same_and_unique():
    assert sensor_key("Radiators Supply!", set()) == "ext_radiators_supply"
    assert sensor_key("Pipe", {"ext_pipe"}) == "ext_pipe_2"
    assert sensor_key("???", set()) == "ext_sensor"


async def test_readings_are_stored_and_bad_ones_reported(database):
    c = config(
        outside_temperature="sensor.out",
        external_sensors=[
            {"name": "Pipe", "entity": "sensor.pipe"},
            {"name": "Off", "entity": "sensor.off"},
            {"name": "Gone", "entity": "sensor.gone"},
            {"name": "Broken", "entity": "sensor.broken"},
        ],
    )
    client = FakeClient(
        {
            "sensor.out": reading("4.5"),
            "sensor.pipe": reading("41.25"),
            "sensor.off": reading("unavailable"),
            "sensor.broken": OSError("boom"),
        }
    )
    sensors = ExternalSensors(c, database, client)
    now = time.time()
    await sensors.poll(now)
    status = {s["key"]: s for s in sensors.status(now)}
    assert status[OUTSIDE_KEY]["value"] == 4.5
    assert status["ext_pipe"]["value"] == 41.25
    assert status["ext_pipe"]["unit"] == "°C"
    assert "unavailable" in status["ext_off"]["error"]
    assert "not found" in status["ext_gone"]["error"]
    assert "boom" in status["ext_broken"]["error"]
    history = database.history(["ext_pipe", OUTSIDE_KEY], int(now) - 60, int(now) + 60)
    assert history["ext_pipe"][0][1] == 41.25
    assert history[OUTSIDE_KEY][0][1] == 4.5
    # An old reading stays stored but no longer counts as the current value
    assert sensors.status(now + STALE_SECONDS + 1)[0]["value"] is None


async def test_no_sensors_or_no_home_assistant_ends_quietly(database):
    await ExternalSensors(config(), database, FakeClient({})).run()
    client = FakeClient({})
    client.available = False
    sensors = ExternalSensors(config(outside_temperature="sensor.out"), database, client)
    await sensors.run()
    assert "no connection" in sensors.status()[0]["error"]


@pytest.fixture
async def api(database):
    c = config(external_sensors=[{"name": "Pipe", "entity": "sensor.pipe"}])
    sensors = ExternalSensors(c, database, FakeClient({"sensor.pipe": reading("40")}))
    await sensors.poll()
    ui = WebUI(0, database, ToonMonitor(c, database, None), FakeMqtt(), sensors)
    async with TestClient(TestServer(ui.app)) as client:
        yield client


async def test_api_shows_the_sensors_and_their_history(api):
    status = await (await api.get("/api/status")).json()
    assert status["external"][0]["name"] == "Pipe"
    history = await (await api.get("/api/history?metrics=ext_pipe,ext_other")).json()
    assert list(history) == ["ext_pipe"]
    assert history["ext_pipe"][0][1] == 40
    export = await (await api.get("/api/export/values.csv")).text()
    assert "ext_pipe" in export


async def test_backup_is_a_working_copy_and_leaves_nothing_behind(api, database, tmp_path):
    response = await api.get("/api/backup")
    assert response.status == 200
    assert 'filename="toon-' in response.headers["Content-Disposition"]
    copy = tmp_path / "copy.db"
    copy.write_bytes(await response.read())
    db = sqlite3.connect(copy)
    assert db.execute("SELECT COUNT(*) FROM samples WHERE metric = 'ext_pipe'").fetchone()[0] == 1
    db.close()
    # The temporary copy goes once the handler has finished, just after the client got the last byte
    for _ in range(50):
        if not list(database.path.parent.glob("backup-*")):
            break
        await asyncio.sleep(0.02)
    assert not list(database.path.parent.glob("backup-*"))


async def test_database_report_names_the_values(api):
    report = await (await api.get("/api/database")).json()
    pipe = next(m for m in report["metrics"] if m["metric"] == "ext_pipe")
    assert pipe["name"] == "Pipe"
    assert pipe["samples"] == 1
    assert report["size"] > 0
    assert report["outages"] == 0
