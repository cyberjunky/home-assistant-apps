"""The scheduler follows Home Assistant's trackers and queues the devices that are due."""

from src.config import Config
from src.database import Database
from src.engine import Engine
from src.scheduler import Scheduler
from src.trackers import HAError


class Trackers:
    def __init__(self, devices=None, error=None):
        self.devices = devices or []
        self.error = error

    async def fetch(self):
        if self.error:
            raise HAError(self.error)
        return self.devices


def tracker(entity, ip, online=True):
    return {"entity_id": f"device_tracker.{entity}", "name": entity, "ip": ip, "mac": None, "online": online}


async def test_sync_queues_the_devices_that_are_home(tmp_path):
    db = Database(tmp_path / "n.db")
    trackers = Trackers([tracker("nas", "192.168.1.20"), tracker("phone", "192.168.1.100", online=False)])
    scheduler = Scheduler(Engine(db, Config(update_templates=False)), db, trackers)
    await scheduler.sync()
    assert [d["name"] for d in scheduler.queue] == ["nas"] and scheduler.ha_error is None
    assert scheduler.last_sync

    # The phone comes home: it is queued on the next look
    trackers.devices[1]["online"] = True
    await scheduler.sync()
    assert sorted(d["name"] for d in scheduler.queue) == ["nas", "phone"]
    db.close()


async def test_home_assistant_unreachable_keeps_what_is_known(tmp_path):
    db = Database(tmp_path / "n.db")
    trackers = Trackers([tracker("nas", "192.168.1.20")])
    scheduler = Scheduler(Engine(db, Config(update_templates=False)), db, trackers)
    await scheduler.sync()
    trackers.error = "Home Assistant answered 403"
    await scheduler.sync()
    assert scheduler.ha_error == "Home Assistant answered 403"
    # The devices stay as they were, still queued
    assert [d["name"] for d in db.devices()] == ["nas"] and [d["name"] for d in scheduler.queue] == ["nas"]
    db.close()
