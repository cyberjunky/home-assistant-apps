"""Learned consumption, charger power, trim names and the stream's frame checks."""

import asyncio
import json

import pytest

from src import streaming, vehicle_specs
from src.monitor import charge_power_kw, normalize
from src.streaming import VehicleStream, parse_update

from .conftest import vehicle_data

T0 = 1_700_000_000


def _learning_charge(database, car_id, start):
    """Range rises 200 -> 300 km; the energy counter stays at zero for the first 10 km of it."""
    charge_id = database.open_charge(car_id, {"ts": start, "range_km": 200}, "AC", None)
    points = [(200, 0.0), (205, 0.0), (210, 0.0)] + [(210 + 9 * i, round(15.0 * i / 10, 2)) for i in range(1, 11)]
    for i, (km, kwh) in enumerate(points):
        database.add_charge_sample(charge_id, {"ts": start + i * 60, "range_km": km, "energy_added_kwh": kwh})
    database.close_charge(charge_id, 0.0)


def test_learned_consumption_ignores_the_counter_lag(database):
    car_id = database.upsert_car(1, 2, "VIN1", "car")
    for n in range(3):
        _learning_charge(database, car_id, T0 + n * 86400)
    # 1.5 -> 15 kWh over 219 -> 300 km is 166.7 Wh/km; the whole session (15 kWh for 100 km) said 150
    assert database.learn_efficiency(car_id) == pytest.approx(166.7, abs=0.1)


def test_unknown_charger_power_is_none():
    assert charge_power_kw({}) is None
    assert charge_power_kw({"charger_power": 0}) == 0.0
    assert charge_power_kw({"charger_voltage": 230, "charger_actual_current": 16, "charger_phases": 3}) == 11.04
    snapshot = normalize(vehicle_data(T0, charger_power=None), T0)
    assert snapshot["charger_power_kw"] is None


def test_model3_rwd_trim_from_2022():
    # 10th VIN character: M = 2021, N = 2022
    assert vehicle_specs.trim_name("model3", "50", "5YJ3E7EA0MF000001") == "Standard Range"
    assert vehicle_specs.trim_name("model3", "50", "5YJ3E7EA0NF000001") == "RWD"
    assert vehicle_specs.trim_name("model3", "50", "LRW3E7EA0RC000001") == "RWD"
    assert vehicle_specs.trim_name("model3", "74d", "5YJ3E7EA0NF000001") == "Long Range AWD"
    assert vehicle_specs.trim_name("modely", "50", "7SAYGDEE0NF000001") == "Standard Range"


def _line(shift="D", lat="52.1", lon="5.1"):
    return f"1700000000000,30,6213.8,80,12,90,{lat},{lon},10,{shift},155,150,90"


def test_parse_update_drops_garbled_frames():
    assert parse_update(_line())["shift"] == "D"
    assert parse_update(_line(shift=""))["shift"] is None
    assert parse_update(_line(shift="X")) is None
    assert parse_update(_line(lat="95.0")) is None
    assert parse_update(_line(lon="-181")) is None
    assert parse_update(_line(lat="0", lon="0")) is None
    assert parse_update(_line(lat="", lon="5.1")) is None
    assert parse_update(_line(lat="", lon=""))["lat"] is None


def test_stream_reconnects_after_bad_frames():
    received = []
    stream = VehicleStream(None, 1, received.append)

    async def scenario():
        results = []
        bad = json.dumps({"msg_type": "data:update", "value": _line(shift="?")})
        good = json.dumps({"msg_type": "data:update", "value": _line()})
        results.append(await stream._handle(None, good))
        for _ in range(streaming.MAX_BAD_FRAMES - 1):
            results.append(await stream._handle(None, bad))
        results.append(await stream._handle(None, good))
        for _ in range(streaming.MAX_BAD_FRAMES):
            results.append(await stream._handle(None, bad))
        return results

    results = asyncio.run(scenario())
    assert len(received) == 2
    assert all(results[:-1]) and results[-1] is False


def test_silence_timeout_is_short_while_driving():
    driving = [False]
    stream = VehicleStream(None, 1, lambda sample: None, driving=lambda: driving[0])
    assert stream.silence_timeout == streaming.SILENCE_TIMEOUT
    driving[0] = True
    assert stream.silence_timeout == streaming.SILENCE_TIMEOUT_DRIVING
