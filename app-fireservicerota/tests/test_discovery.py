"""Looking for the integration's entities again while some are missing."""

import pytest

from src.config import Config
from src.database import Database
from src.ha_client import HAClient
from src.tracker import Tracker


@pytest.fixture
def tracker(tmp_path):
    t = Tracker(Config(), Database(str(tmp_path / "f.db")), HAClient())
    yield t


def test_missing_sensors_are_noticed(tracker):
    assert tracker.sensors_missing()
    tracker.entities = {"incidents": "sensor.a", "duty": "binary_sensor.b", "response": "switch.c"}
    assert not tracker.sensors_missing()


async def test_rediscover_follows_what_turned_up(tracker, monkeypatch):
    processed = []

    async def discover():
        tracker.entities = {"incidents": "sensor.a", "duty": "binary_sensor.b", "response": "switch.c"}

    async def get_state(entity_id):
        return {"entity_id": entity_id, "state": "on"}

    async def backfill(*a, **k):
        processed.append("backfill")

    monkeypatch.setattr(tracker, "_discover", discover)
    monkeypatch.setattr(tracker.ha, "get_state", get_state)
    monkeypatch.setattr(tracker, "_process", lambda kind, state, **k: processed.append(kind))
    monkeypatch.setattr(tracker, "run_backfill", backfill)
    await tracker.rediscover()
    tracker.entities  # noqa: B018
    assert set(processed) >= {"incidents", "duty", "response"}
    assert not tracker.sensors_missing()


async def test_a_failing_lookup_does_not_stop_the_app(tracker, monkeypatch):
    async def boom():
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(tracker, "_discover", boom)
    await tracker.rediscover()
    assert tracker.sensors_missing()
