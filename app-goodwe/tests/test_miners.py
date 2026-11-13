"""The miners: the options, the switching with the surplus (one by one, with hysteresis), manual
miners, the learned draw, and the per-miner statistics."""

import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from src import miners as miners_module
from src.config import Config, _miners
from src.database import Database
from src.miners import MIN_OFF_SECONDS, Miners


class FakeHA:
    available = True

    def __init__(self):
        self.states: dict[str, dict] = {}
        self.calls: list[tuple[str, str, str]] = []

    async def get_state(self, entity_id):
        return self.states[entity_id]

    async def call_service(self, domain, service, entity_id):
        self.calls.append((domain, service, entity_id))
        self.states[entity_id] = {"state": "on" if service == "turn_on" else "off"}


class FakeEvents:
    def __init__(self):
        self.items = []

    def emit(self, kind, message, level="info", **data):
        self.items.append((kind, message))


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(miners_module.time, "monotonic", c)
    return c


def controller(**kw):
    settings = SimpleNamespace(surplus_debounce_s=120, surplus_margin_w=500)
    return SimpleNamespace(settings=settings, surplus=True, surplus_w=0, surplus_reason="", overload="", **kw)


def make(tmp_path, items, ha=None):
    config = Config(inverter_host="x", miners=_miners(items))
    ha = ha or FakeHA()
    for m in config.miners:
        if m.switch:
            ha.states[m.switch] = {"state": "off"}
        if m.sensor:
            ha.states[m.sensor] = {"state": "0", "attributes": {"unit_of_measurement": "W"}}
    link = SimpleNamespace(listeners=[])
    miners = Miners(config, ha, link, FakeEvents(), tmp_path)
    miners.controller = controller()
    return miners, ha


TWO = [
    {"name": "S19 left", "switch": "switch.left", "sensor": "sensor.left_power", "power_w": 1500},
    {"name": "S19 right", "switch": "switch.right", "sensor": "sensor.right_power", "power_w": 1500},
]


def step(miners, clock, seconds=0):
    clock.now += seconds
    asyncio.run(miners.read())
    asyncio.run(miners.control())


def test_options():
    got = _miners([{"name": "A", "switch": "switch.a"}, {"sensor": "sensor.b_power"}, {"name": "empty"}, "junk"])
    assert [(m.name, m.switch, m.sensor, m.power_w) for m in got] == [
        ("A", "switch.a", "", 0),
        ("Miner 2", "", "sensor.b_power", 0),
    ]
    assert _miners(None) == [] and _miners({"name": "old style"}) == []
    with pytest.raises(ValueError, match="entity id"):
        _miners([{"switch": "Switch A"}])
    with pytest.raises(ValueError, match="own name"):
        _miners([{"name": "A", "switch": "switch.a"}, {"name": "A", "switch": "switch.b"}])


def test_start_one_by_one_when_the_surplus_covers_the_next(tmp_path, clock):
    miners, ha = make(tmp_path, TWO)
    c = miners.controller
    c.surplus_w = 1400  # less than a miner draws
    step(miners, clock)
    assert ha.calls == [] and "needs about 1500 W" in miners.action
    c.surplus_w = 1800
    step(miners, clock)
    assert ha.calls == [("switch", "turn_on", "switch.left")]
    # The second only after the debounce, and only when the surplus covers it too
    c.surplus_w = 1600
    step(miners, clock, 60)
    assert len(ha.calls) == 1 and "starting S19 right in" in miners.action
    step(miners, clock, 61)
    assert ha.calls[-1] == ("switch", "turn_on", "switch.right")
    step(miners, clock, 500)
    assert len(ha.calls) == 2 and miners.action == "all miners on auto are running"


def test_no_start_without_the_surplus_signal(tmp_path, clock):
    miners, ha = make(tmp_path, TWO)
    miners.controller.surplus, miners.controller.surplus_w = False, 5000
    miners.controller.surplus_reason = "battery 80% not full"
    step(miners, clock)
    assert ha.calls == [] and "waiting for the solar surplus" in miners.action


def test_stop_last_started_first_while_the_house_imports(tmp_path, clock):
    miners, ha = make(tmp_path, TWO)
    c = miners.controller
    c.surplus_w = 4000
    step(miners, clock)
    step(miners, clock, 121)
    assert [call[2] for call in ha.calls] == ["switch.left", "switch.right"]
    # A dip in the surplus estimate stops nothing: only import or a draining battery does
    c.surplus, c.surplus_w = False, 0
    step(miners, clock, 300)
    assert len(ha.calls) == 2
    c.overload = "importing 400 W"
    step(miners, clock, 10)
    assert len(ha.calls) == 2 and "stopping S19 right in" in miners.action
    step(miners, clock, 120)
    assert ha.calls[-1] == ("switch", "turn_off", "switch.right")
    # The next one only if the import lasts another full step
    step(miners, clock, 60)
    assert len(ha.calls) == 3
    c.overload = ""
    step(miners, clock, 200)
    assert len(ha.calls) == 3, "the import ended: the other miner keeps running"


def test_a_stopped_miner_stays_off_a_while(tmp_path, clock):
    miners, ha = make(tmp_path, TWO[:1])
    c = miners.controller
    c.surplus_w = 2000
    step(miners, clock)
    c.overload = "importing 300 W"
    step(miners, clock, 1)
    step(miners, clock, 121)
    assert ha.calls[-1][1] == "turn_off"
    c.overload = ""
    step(miners, clock, 130)
    assert ha.calls[-1][1] == "turn_off" and "starting S19 left in" in miners.action
    step(miners, clock, MIN_OFF_SECONDS)
    assert ha.calls[-1][1] == "turn_on"


def test_manual_miner_is_left_alone(tmp_path, clock):
    miners, ha = make(tmp_path, TWO)
    miners.set_auto("S19 left", False)
    miners.controller.surplus_w = 4000
    step(miners, clock)
    assert ha.calls == [("switch", "turn_on", "switch.right")]
    # Saved: a restart keeps it manual
    assert json.loads((tmp_path / "miners.json").read_text())["S19 left"]["auto"] is False
    # By hand only when manual
    asyncio.run(miners.switch("S19 left", True))
    assert ha.calls[-1] == ("switch", "turn_on", "switch.left")
    with pytest.raises(ValueError, match="on auto"):
        asyncio.run(miners.switch("S19 right", False))


def test_learned_draw_without_a_rated_power(tmp_path, clock):
    miners, ha = make(tmp_path, [{"name": "Box", "switch": "switch.box", "sensor": "sensor.box_power"}])
    unit = miners.units[0]
    c = miners.controller
    # Nothing known yet: the surplus margin is enough to try
    c.surplus_w = 600
    step(miners, clock)
    assert ha.calls[-1][1] == "turn_on"
    ha.states["sensor.box_power"] = {"state": "1.2", "attributes": {"unit_of_measurement": "kW"}}
    asyncio.run(miners.read())
    miners.on_data({"surplus": 1})
    assert round(unit.learned_w) == 1200 and unit.on is True
    # Stopped: the next start needs the learned 1200 W
    ha.states["switch.box"] = {"state": "off"}
    unit.stopped_mono = -1e9
    c.surplus_w = 900
    step(miners, clock, 200)
    assert ha.calls[-1][1] == "turn_on" and len(ha.calls) == 1 and "needs about 1200 W" in miners.action


def test_stamps_and_daily_statistics(tmp_path, clock):
    miners, ha = make(tmp_path, TWO)
    ha.states["switch.left"] = {"state": "on"}
    ha.states["sensor.left_power"] = {"state": "1400", "attributes": {"unit_of_measurement": "W"}}
    asyncio.run(miners.read())
    data = {"surplus": 1}
    miners.on_data(data)
    assert data["miner1_on"] == 1 and data["miner1_w"] == 1400 and data["miner2_on"] == 0
    assert data["miners_on"] == 1 and data["miners_w"] == 1400
    state = miners.state()
    assert state["running"] == 1 and state["watts"] == 1400 and state["miners"][1]["auto"] is True

    db = Database(tmp_path / "goodwe.db")
    db.miner_names = ["S19 left", "S19 right"]
    start = int(time.mktime(time.strptime("2026-11-10 12:00", "%Y-%m-%d %H:%M")))
    for k in range(61):  # an hour, a snapshot a minute
        db.con.execute("INSERT INTO snapshots VALUES (?, ?)", (start + 60 * k, json.dumps(data)))
    db.con.commit()
    db.update_derived("2026-11-10")
    got = {r["name"]: r for r in db.miner_totals("2026-11-10", "2026-11-11")}
    assert got["S19 left"]["hours"] == 1.0 and got["S19 left"]["kwh"] == 1.4
    assert "S19 right" not in got  # never ran


def test_switched_off_is_not_running_whatever_the_sensor_says(tmp_path, clock):
    miners, ha = make(tmp_path, TWO[:1])
    ha.states["switch.left"] = {"state": "on"}
    ha.states["sensor.left_power"] = {"state": "111", "attributes": {"unit_of_measurement": "W"}}
    asyncio.run(miners.read())
    miners.on_data({"surplus": 1})
    unit = miners.units[0]
    assert unit.on is True
    learned = unit.learned_w
    # Mining switched off; the sensor still reports the old power (lagging, or the idle draw)
    ha.states["switch.left"] = {"state": "off"}
    asyncio.run(miners.read())
    data = {"surplus": 1}
    miners.on_data(data)
    assert unit.on is False and data["miner1_on"] == 0 and data["miner1_w"] == 111
    assert unit.learned_w == learned, "nothing learned while switched off"
    assert miners.events.items[-1][0] == "miners_stopped"
