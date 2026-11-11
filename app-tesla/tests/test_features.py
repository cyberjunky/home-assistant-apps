"""0.8.0: battery health by capacity, consumption per period, estimated charges, auto climate, capacity option."""

import asyncio

import pytest

from src import stats
from src.billing import ChargingCosts
from src.config import Config
from src.database import Database, session_figures
from src.monitor import normalize
from src.mqtt import state_payload

from .conftest import LAT, LON, as_layout, feed, make_monitor, vehicle_data

T0 = 1_700_000_000
DAY = 86400


def _charge(database, car_id, start, soc_from, soc_to, capacity_kwh, wh_km, lag_points=2):
    """A recorded session: battery soc_from -> soc_to on a battery of capacity_kwh, the car's rated
    consumption wh_km. The energy counter stays at zero for the first lag_points percent."""
    charge_id = database.open_charge(car_id, {"ts": start, "lat": LAT, "lon": LON}, "AC", None)
    kwh_per_point = capacity_kwh / 100.0
    km_per_point = kwh_per_point * 1000.0 / wh_km
    for i, soc in enumerate(range(soc_from, soc_to + 1)):
        kwh = max(0, soc - soc_from - lag_points) * kwh_per_point
        database.add_charge_sample(
            charge_id,
            {
                "ts": start + i * 60,
                "soc": soc,
                "range_km": soc * km_per_point,
                "energy_added_kwh": round(kwh, 3),
                "power_kw": 11.0,
            },
        )
    return database.close_charge(charge_id, 0.0)


@pytest.fixture
def car(database):
    return database.upsert_car(1, 2, "5YJ3E7EA0NF000001", "car")


def test_session_figures_with_counter_lag():
    samples = [
        {"soc": soc, "range_km": soc * 5.0, "energy_added_kwh": max(0, soc - 32) * 0.75} for soc in range(30, 71)
    ]
    capacity, rated = session_figures(samples)
    assert capacity == pytest.approx(75.0, abs=0.01)
    assert rated == pytest.approx(150.0, abs=0.1)
    # 10 points of battery level is too little to measure the capacity by; the range still tells the consumption
    assert session_figures(samples[:10]) == (None, pytest.approx(150.0, abs=0.1))
    assert session_figures(samples[:4]) == (None, None)


def test_wheel_change_is_not_battery_wear(database, car):
    # Ten charges on 150 Wh/km, then new wheels: the car rates 165 Wh/km, the battery is the same 75 kWh
    for n in range(10):
        _charge(database, car, T0 + n * DAY, 30, 80, 75.0, 150.0)
    for n in range(10, 20):
        _charge(database, car, T0 + n * DAY, 30, 80, 75.0, 165.0)
    health = stats.battery(database, car, T0 + 10 * DAY, T0 + 30 * DAY, 165.0)
    assert health["basis"] == "capacity"
    assert health["capacity_kwh"] == pytest.approx(75.0, abs=0.1)
    assert health["loss_pct"] == pytest.approx(0.0, abs=0.2)
    # The range at 100% did drop by 9%: that is what used to show as wear
    assert health["now_km"] / health["new_km"] == pytest.approx(150 / 165, abs=0.01)


def test_capacity_when_new_option(database, car):
    for n in range(5):
        _charge(database, car, T0 + n * DAY, 30, 80, 75.0, 150.0)
    health = stats.battery(database, car, T0, T0 + 10 * DAY, 150.0, capacity_new_kwh=80.0)
    assert health["new_kwh"] == 80.0
    assert health["new_source"] == "option"
    assert health["loss_pct"] == pytest.approx(6.25, abs=0.2)


def test_capacity_option_is_read(tmp_path):
    options = tmp_path / "options.json"
    options.write_text('{"battery_capacity_kwh": 78.1}')
    assert Config.from_options_file(options).battery_capacity_kwh == 78.1
    options.write_text('{"battery_capacity_kwh": 900}')
    assert Config.from_options_file(options).battery_capacity_kwh == 0.0


def _drive(database, car_id, start, km=20.0):
    drive_id = database.open_drive(
        car_id, {"ts": start, "lat": LAT, "lon": LON, "odometer_km": 1000.0, "range_km": 300}
    )
    for i in range(3):
        database.add_position(
            car_id,
            {
                "ts": start + i * 300,
                "lat": LAT,
                "lon": LON,
                "odometer_km": 1000.0 + km * i / 2,
                "range_km": 300 - 10 * i,
            },
            drive_id,
        )
    return drive_id


def test_drives_keep_the_consumption_of_their_time(database, car):
    for n in range(5):
        _charge(database, car, T0 + n * DAY, 30, 80, 75.0, 150.0)
    early = _drive(database, car, T0 + 6 * DAY)
    database.close_drive(early, 150.0)
    for n in range(7, 40):
        _charge(database, car, T0 + n * DAY, 30, 80, 75.0, 170.0)
    late = _drive(database, car, T0 + 41 * DAY)
    database.close_drive(late, 170.0)
    learned = database.learn_efficiency(car)
    assert learned == pytest.approx(170.0, abs=0.1)
    database.apply_learned_efficiency(car, learned, learned)
    rows = {r["id"]: r for r in database.query("SELECT id, energy_kwh, efficiency_wh_km FROM drives")}
    # 20 km of rated range: 3.0 kWh at 150 Wh/km before the change, 3.4 kWh at 170 after
    assert rows[early]["efficiency_wh_km"] == pytest.approx(150.0, abs=0.1)
    assert rows[early]["energy_kwh"] == pytest.approx(3.0, abs=0.01)
    assert rows[late]["efficiency_wh_km"] == pytest.approx(170.0, abs=0.1)
    # More charges later don't rewrite earlier drives
    for n in range(42, 60):
        _charge(database, car, T0 + n * DAY, 30, 80, 75.0, 190.0)
    database.apply_learned_efficiency(car, database.learn_efficiency(car), 190.0)
    again = {r["id"]: r for r in database.query("SELECT id, energy_kwh, efficiency_wh_km FROM drives")}
    assert again[early] == rows[early]
    assert again[late] == rows[late]


def test_hand_set_consumption_still_applies_to_all(database, car):
    drive = _drive(database, car, T0)
    database.close_drive(drive, 150.0)
    database.apply_efficiency(car, 200.0)
    row = database.query_one("SELECT energy_kwh, efficiency_wh_km FROM drives WHERE id = ?", (drive,))
    assert row == {"energy_kwh": pytest.approx(4.0), "efficiency_wh_km": 200.0}


def test_upgrade_fills_charge_figures_and_drive_consumption(tmp_path):
    path = tmp_path / "tesla.db"
    database = Database(path)
    car = database.upsert_car(1, 2, "VIN1", "car")
    database.update_car(car, efficiency_wh_km=155.0)
    charge = _charge(database, car, T0, 30, 80, 75.0, 150.0)
    drive = _drive(database, car, T0 + DAY)
    database.close_drive(drive, 155.0)
    database.close()
    as_layout(path, 6)
    database = Database(path)
    row = database.query_one("SELECT capacity_kwh, rated_wh_km, estimated FROM charges WHERE id = ?", (charge["id"],))
    assert row["capacity_kwh"] == pytest.approx(75.0, abs=0.1)
    assert row["rated_wh_km"] == pytest.approx(150.0, abs=0.1)
    assert row["estimated"] == 0
    assert database.query_one("SELECT efficiency_wh_km FROM drives")["efficiency_wh_km"] == 155.0
    database.close()


def test_missed_charge_is_estimated(database):
    monitor = make_monitor(database, config=Config(streaming=False, battery_capacity_kwh=75.0))
    feed(monitor, T0, soc=50, range_km=250)
    # The app wasn't running for a night; the car is back at 80%, unplugged again
    feed(monitor, T0 + 10 * 3600, soc=80, range_km=400)
    charges = database.query("SELECT * FROM charges")
    assert len(charges) == 1
    charge = charges[0]
    assert charge["estimated"] == 1
    assert charge["energy_added_kwh"] == pytest.approx(22.5)
    assert (charge["start_soc"], charge["end_soc"]) == (50, 80)
    assert (charge["start_ts"], charge["end_ts"]) == (T0, T0 + 10 * 3600)
    # Not driven in between: it charged where it is parked
    assert charge["lat"] == LAT
    # It doesn't count for battery health
    assert stats.battery(database, monitor.car_id, 0, 2**40, 150.0)["points"] == []


def test_missed_charge_uses_the_measured_capacity(database):
    monitor = make_monitor(database)
    for n in range(3):
        _charge(database, monitor.car_id, T0 - (n + 2) * DAY, 30, 80, 60.0, 150.0)
    feed(monitor, T0, soc=50)
    feed(monitor, T0 + 3600, soc=60, odometer_km=10100.0)
    charge = database.query_one("SELECT * FROM charges WHERE estimated = 1")
    assert charge["energy_added_kwh"] == pytest.approx(6.0)
    # Driven in between: it charged somewhere along the way
    assert charge["lat"] is None


def test_no_estimate_when_a_charge_is_recorded_or_the_gain_is_small(database):
    monitor = make_monitor(database, config=Config(streaming=False, battery_capacity_kwh=75.0))
    feed(monitor, T0, soc=50)
    feed(monitor, T0 + 3600, soc=53)
    database.import_charge(monitor.car_id, {"start_ts": T0 + 4000, "end_ts": T0 + 5000, "energy_added_kwh": 20})
    feed(monitor, T0 + 7200, soc=80)
    assert database.query("SELECT id FROM charges WHERE estimated = 1") == []


def test_supercharger_history_replaces_the_estimate(database):
    monitor = make_monitor(database, config=Config(streaming=False, battery_capacity_kwh=75.0))
    feed(monitor, T0, soc=20)
    feed(monitor, T0 + 4 * 3600, soc=70, odometer_km=10200.0)
    estimate = database.query_one("SELECT id FROM charges WHERE estimated = 1")["id"]

    class HistoryApi:
        async def charging_history(self, vin):
            return {
                "data": [
                    {
                        "vin": vin,
                        "chargeStartDateTime": "2023-11-14T23:13:20+00:00",  # T0 + 1 h
                        "chargeStopDateTime": "2023-11-14T23:43:20+00:00",
                        "siteLocationName": "Utrecht Supercharger",
                        "chargerType": "DC",
                        "fees": [
                            {
                                "feeType": "CHARGING",
                                "uom": "kwh",
                                "usageBase": 36.5,
                                "totalDue": 14.6,
                                "currencyCode": "EUR",
                            }
                        ],
                    }
                ]
            }

    costs = ChargingCosts(Config(), database, None, HistoryApi())
    assert asyncio.run(costs.import_history()) == 0
    charges = database.query("SELECT * FROM charges")
    assert len(charges) == 1
    charge = charges[0]
    assert charge["id"] == estimate
    assert charge["estimated"] == 0
    assert charge["kind"] == "DC"
    assert charge["energy_added_kwh"] == 36.5
    assert charge["billed_amount"] == 14.6
    assert (charge["start_soc"], charge["end_soc"]) == (20, 70)


def test_auto_climate():
    snapshot = normalize(vehicle_data(T0, climate={"is_auto_conditioning_on": True}), T0)
    assert snapshot["auto_conditioning"] is True
    assert state_payload(snapshot)["auto_conditioning"] is True
    unknown = normalize(vehicle_data(T0), T0)
    assert unknown["auto_conditioning"] is None
    assert state_payload(unknown)["auto_conditioning"] is None
