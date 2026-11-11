"""Shared helpers: a monitor on a temporary database, fed with made-up vehicle_data answers."""

import sqlite3
import time

import pytest

from src.config import KM_PER_MILE, Config
from src.database import LAYOUT7_COLUMNS, Database
from src.monitor import CarMonitor, normalize

LAT, LON = 52.0907, 5.1214


def vehicle_data(
    ts: float,
    shift=None,
    lat=LAT,
    lon=LON,
    odometer_km=10000.0,
    soc=50,
    range_km=250.0,
    charging_state="Disconnected",
    energy_kwh=0.0,
    charger_power=0,
    **extra,
) -> dict:
    """A vehicle_data answer as Tesla gives it (miles, milliseconds)."""
    millis = int(ts * 1000)
    return {
        "state": "online",
        "drive_state": {
            "shift_state": shift,
            "speed": 30 if shift == "D" else None,
            "power": 10 if shift == "D" else 0,
            "latitude": lat,
            "longitude": lon,
            "heading": 90,
            "timestamp": millis,
        },
        "charge_state": {
            "battery_level": soc,
            "usable_battery_level": soc,
            "battery_range": range_km / KM_PER_MILE,
            "charging_state": charging_state,
            "charger_power": charger_power,
            "charge_energy_added": energy_kwh,
            "timestamp": millis,
            **extra.pop("charge", {}),
        },
        "climate_state": {"inside_temp": 20.0, "outside_temp": 10.0, **extra.pop("climate", {})},
        "vehicle_state": {"odometer": odometer_km / KM_PER_MILE, "locked": True, "timestamp": millis},
        "vehicle_config": {"car_type": "model3", "trim_badging": "74d"},
    }


class FakePlaces:
    async def fill_missing(self) -> None:
        return None


class FakeApi:
    """Answers with whatever `answer(now)` returns and notes every request."""

    def __init__(self, answer=None):
        self.answer = answer
        self.calls: list[tuple[str, float]] = []
        self.fail_with: list = []

    async def vehicle(self, tesla_id):
        self.calls.append(("vehicle", time.time()))
        return {"state": "online"}

    async def vehicle_data(self, tesla_id):
        self.calls.append(("vehicle_data", time.time()))
        if self.fail_with:
            raise self.fail_with.pop(0)
        return self.answer(time.time())


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "tesla.db")
    yield db
    db.close()


def make_monitor(database: Database, api=None, config: Config | None = None) -> CarMonitor:
    car_id = database.upsert_car(1001, 2002, "5YJ3E7EB0NF000001", "Testla")
    car = database.get_car(car_id)
    monitor = CarMonitor(car, config or Config(streaming=False), database, None, api or FakeApi(), FakePlaces())
    # No event loop in most tests: the background address lookup isn't needed
    monitor._background = lambda coro: coro.close()
    return monitor


@pytest.fixture
def monitor(database):
    return make_monitor(database)


def as_layout(path, version: int) -> None:
    """Turn a new database file back into one of an older layout, as an older version left it."""
    with sqlite3.connect(path) as conn:
        if version < 7:
            for table, columns in LAYOUT7_COLUMNS.items():
                for column in columns:
                    conn.execute(f"ALTER TABLE {table} DROP COLUMN {column.split()[0]}")
        conn.execute(f"PRAGMA user_version={version}")
    conn.close()


def feed(monitor: CarMonitor, ts: float, **fields) -> None:
    """One poll of the car at time ts."""
    monitor._process(normalize(vehicle_data(ts, **fields), ts), ts)
