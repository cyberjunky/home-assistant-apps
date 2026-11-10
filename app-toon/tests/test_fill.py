"""Filling gaps from the Toon's own history, also those the outage record doesn't know of."""

import json
import time

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import history
from src.config import Config
from src.database import Database
from src.history import BACKFILL, HistoryImporter, find_gaps
from src.monitor import ToonMonitor
from src.webui import WebUI

MINUTE = 60


def test_find_gaps():
    assert find_gaps([100, 200, 5000, 5100], 0, 6000, min_gap=1000) == [(200, 5000)]
    # The edges count: nothing stored at the start or the end of the window is a gap too
    assert find_gaps([3000], 0, 6000, min_gap=1000) == [(0, 3000), (3000, 6000)]
    assert find_gaps([], 0, 500, min_gap=1000) == []


class RrdBus:
    """Answers GetRraData with one value a minute for the room temperature; the rest has nothing."""

    def __init__(self):
        self.asked = []

    async def action(self, destination, service, name, arguments=None, timeout=5):
        self.asked.append(arguments)
        if arguments["loggerName"] != "thermstat_realTemps":
            return {"rraData": "{}"}
        from datetime import datetime
        from zoneinfo import ZoneInfo

        zone = ZoneInfo("Europe/Amsterdam")
        parse = lambda text: int(datetime.strptime(text, "%d-%m-%Y %H:%M:%S").replace(tzinfo=zone).timestamp())  # noqa: E731
        start, end = parse(arguments["from"]), parse(arguments["to"])
        data = {str(ts): 19.5 for ts in range(start - start % MINUTE, end + 1, MINUTE)}
        return {"rraData": json.dumps(data)}


@pytest.fixture
def no_pause(monkeypatch):
    monkeypatch.setattr(history, "PAUSE_BETWEEN_REQUESTS", 0)


async def test_gap_after_an_app_restart_is_filled(tmp_path, no_pause):
    """The case of 10 November: lost at 04:32, the app restarted at 12:47, the Toon back at 14:02.
    The outage record then only covered 12:47 to 14:02; the stored values show the whole gap."""
    database = Database(tmp_path / "t.db")
    now = int(time.time()) // 60 * 60
    lost, back = now - 10 * 3600, now - 600
    for ts in range(now - 30 * 86400 + 3600, lost, 600):
        database.record("room_temperature", ts, 20.0)
    for ts in range(back, now, 60):
        database.record("room_temperature", ts, 21.0)
    database.flush()
    bus = RrdBus()
    added = await HistoryImporter(database).fill_missing(bus, "toon", lambda ts: 0, now=now)
    times = database.sample_times("room_temperature", lost - 1, back)
    assert added > 0
    # Every minute of the gap is there now, and nothing outside it was asked for in vain
    assert len(times) >= (back - lost) // 60 - 2
    live = database.sample_times("room_temperature", back - 1, now)
    assert len(live) == len(range(back, now, 60))
    assert all(a["loggerName"] in {x.logger for x in BACKFILL} for a in bus.asked)
    database.close()


async def test_live_values_are_kept(tmp_path, no_pause):
    database = Database(tmp_path / "t.db")
    now = int(time.time()) // 60 * 60
    for ts in range(now - 30 * 86400, now, 300):
        database.record("room_temperature", ts, 20.0)
    database.flush()
    bus = RrdBus()
    # No gaps: the Toon isn't asked for the room temperature at all
    await HistoryImporter(database).fill_missing(bus, "toon", lambda ts: 0, now=now)
    assert not [a for a in bus.asked if a["loggerName"] == "thermstat_realTemps"]
    database.close()


async def test_fill_button(tmp_path, no_pause):
    config = Config.from_options({"ssh": {"host": "toon", "password": "x"}})
    database = Database(tmp_path / "t.db")
    monitor = ToonMonitor(config, database, None)

    class Mqtt:
        connected = False
        broker = None

    ui = WebUI(0, database, monitor, Mqtt())
    async with TestClient(TestServer(ui.app)) as client:
        response = await client.post("/api/history/fill", json={})
        assert response.status == 503
        monitor.connected = True
        monitor._bus = RrdBus()
        monitor.host_name = "toon"
        response = await client.post("/api/history/fill", json={})
        assert response.status == 200
        await ui._fill_task
        status = await (await client.get("/api/status")).json()
        assert status["fill"]["running"] is False
        assert status["fill"]["values"] > 0
    database.close()
