"""The danger zone: delete everything recorded, and make the rest of the app follow."""

import asyncio
import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src.config import Config
from src.database import Database
from src.monitor import ToonMonitor
from src.webui import WebUI


class FakeMqtt:
    connected = False
    broker = None


def config(**extra):
    return Config.from_options({"ssh": {"host": "toon", "password": "x"}, **extra})


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "toon.db")
    now = int(time.time())
    db.add_samples([("room_temperature", now - i * 60, 20.0 + i / 100) for i in range(300)])
    db.add_hourly([("gas_total", now // 3600 * 3600 - i * 3600, 1.0) for i in range(50)])
    db.open_outage(now - 500, "test", None, None)
    db.set_import_position("gas_quantity", "10yrdays", now, True)
    db.record("setpoint", now, 21.0)  # still waiting in memory
    yield db
    db.close()


@pytest.fixture
async def client(database):
    monitor = ToonMonitor(config(), database, None)
    ui = WebUI(0, database, monitor, FakeMqtt())
    async with TestClient(TestServer(ui.app)) as c:
        c.monitor, c.ui = monitor, ui
        yield c


async def delete(client, body, content_type="application/json"):
    r = await client.post("/api/data/delete", data=json.dumps(body), headers={"Content-Type": content_type})
    return r.status, await r.json()


def test_wipe_empties_everything_and_reports_what_was_there(database):
    removed = database.wipe()
    assert removed == {"samples": 300, "hourly": 50, "outages": 1}
    counts = database.counts()
    assert counts["samples"] == 0 and counts["hourly"] == 0
    assert database.outages() == [] and database.import_position("gas_quantity", "10yrdays") == (None, False)
    # The sample waiting in memory is gone too, and recording works again
    database.flush()
    assert database.counts()["samples"] == 0
    database.record("setpoint", int(time.time()), 20.0)
    database.flush()
    assert database.counts()["samples"] == 1


def test_the_file_shrinks(tmp_path):
    db = Database(tmp_path / "big.db")
    now = int(time.time())
    db.add_samples([("room_temperature", now - i, float(i)) for i in range(120_000)])
    big = db.path.stat().st_size + (
        db.path.with_name("big.db-wal").stat().st_size if db.path.with_name("big.db-wal").exists() else 0
    )
    assert big > 1_000_000
    db.wipe()
    after = db.path.stat().st_size + (
        db.path.with_name("big.db-wal").stat().st_size if db.path.with_name("big.db-wal").exists() else 0
    )
    assert after < big / 10
    db.close()


async def test_the_word_delete_is_needed(client, database):
    for body in ({}, {"confirm": "delete"}, {"confirm": True}, {"confirm": "yes"}, []):
        status, _ = await delete(client, body)
        assert status == 400
    assert database.counts()["samples"] == 300
    assert (await delete(client, {"confirm": "DELETE"}, "text/plain"))[0] == 415
    status, answer = await delete(client, {"confirm": "DELETE"})
    # 300 written plus the one still waiting in memory, which the endpoint writes out first
    assert status == 200 and answer["removed"]["samples"] == 301
    assert database.counts()["samples"] == 0


async def test_it_waits_for_a_running_history_import(client, database):
    client.monitor.importer.running = True
    status, answer = await delete(client, {"confirm": "DELETE"})
    assert status == 409 and "import is running" in answer["error"]
    assert database.counts()["samples"] == 300
    client.monitor.importer.running = False
    assert (await delete(client, {"confirm": "DELETE"}))[0] == 200


async def test_under_home_assistant_only_ingress_may_delete(client, database, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    assert (await delete(client, {"confirm": "DELETE"}))[0] == 403
    assert database.counts()["samples"] == 300


async def test_statistics_do_not_show_numbers_that_are_gone(client, database):
    first = await (await client.get("/api/today")).json()
    assert client.ui.stats._today is not None
    await delete(client, {"confirm": "DELETE"})
    assert client.ui.stats._today is None and not client.ui.stats._deltas
    assert first is not None


async def test_after_a_wipe_values_are_stored_again_and_history_is_fetched_again(client, database):
    monitor = client.monitor
    monitor.set_value("setpoint", "2100")
    assert monitor._stored
    started = []

    async def reimport(bus):
        started.append(bus)

    monitor._reimport = reimport
    monitor.connected, monitor._bus = True, object()
    await delete(client, {"confirm": "DELETE"})
    await asyncio.sleep(0.01)
    assert not monitor._stored and len(started) == 1
    # With the history import turned off, nothing is fetched
    started.clear()
    monitor.config.import_history = False
    await delete(client, {"confirm": "DELETE"})
    await asyncio.sleep(0.01)
    assert not started
