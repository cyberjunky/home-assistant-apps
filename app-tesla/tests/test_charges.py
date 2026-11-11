"""A paused and resumed charge books its energy once (TeslaMate #5491), and the counter's quirks."""

import sqlite3

import pytest

from src.database import Database, counter_energy

from .conftest import LAT, feed, make_monitor

T0 = 1_700_000_000


def _charge(monitor, start, kwh_from, kwh_to, minutes, soc_from=40):
    """Charging polls once a minute, the counter rising evenly from kwh_from to kwh_to."""
    for i in range(minutes + 1):
        kwh = kwh_from + (kwh_to - kwh_from) * i / minutes
        feed(
            monitor,
            start + i * 60,
            charging_state="Charging",
            energy_kwh=round(kwh, 2),
            charger_power=11,
            soc=soc_from + i // 4,
        )


def _pause(monitor, start, kwh, minutes, state="Stopped"):
    for i in range(minutes):
        feed(monitor, start + i * 60, charging_state=state, energy_kwh=kwh)


def _charges(database):
    return database.query("SELECT * FROM charges ORDER BY start_ts")


def test_paused_charge_counts_once(monitor, database):
    _charge(monitor, T0, 0.0, 5.2, 30)
    _pause(monitor, T0 + 31 * 60, 5.2, 20)
    _charge(monitor, T0 + 51 * 60, 5.2, 9.0, 20)
    feed(monitor, T0 + 72 * 60, charging_state="Complete", energy_kwh=9.0)
    charges = _charges(database)
    assert len(charges) == 1
    assert charges[0]["energy_added_kwh"] == pytest.approx(9.0)
    assert charges[0]["end_ts"] is not None


def test_paused_charge_counts_once_across_a_restart(database):
    first = make_monitor(database)
    _charge(first, T0, 0.0, 5.2, 30)
    _pause(first, T0 + 31 * 60, 5.2, 20)
    # The app restarts during the pause: the new monitor didn't see the car stay plugged in
    second = make_monitor(database)
    _charge(second, T0 + 51 * 60, 5.2, 9.0, 20)
    feed(second, T0 + 72 * 60, charging_state="Complete", energy_kwh=9.0)
    assert sum(c["energy_added_kwh"] for c in _charges(database)) == pytest.approx(9.0)


def test_long_pause_keeps_two_sessions_without_double_counting(monitor, database):
    _charge(monitor, T0, 0.0, 5.2, 30)
    _pause(monitor, T0 + 31 * 60, 5.2, 2)
    # Scheduled charging picks up again the next morning, the car still plugged in
    _charge(monitor, T0 + 12 * 3600, 5.2, 9.0, 20)
    feed(monitor, T0 + 13 * 3600, charging_state="Complete", energy_kwh=9.0)
    charges = _charges(database)
    assert [round(c["energy_added_kwh"], 2) for c in charges] == [5.2, 3.8]


def test_unplugged_in_between_is_a_new_session(monitor, database):
    _charge(monitor, T0, 0.0, 5.2, 30)
    feed(monitor, T0 + 31 * 60, charging_state="Disconnected", energy_kwh=5.2)
    # Plugged in again: the counter starts from zero (its first reading can still show the old total)
    feed(monitor, T0 + 40 * 60, charging_state="Charging", energy_kwh=5.2, charger_power=11)
    _charge(monitor, T0 + 41 * 60, 0.0, 3.8, 20)
    feed(monitor, T0 + 62 * 60, charging_state="Complete", energy_kwh=3.8)
    charges = _charges(database)
    assert [round(c["energy_added_kwh"], 2) for c in charges] == [5.2, 3.8]


def test_counter_energy():
    assert counter_energy([0.0, 2.0, 5.2, 5.2, 7.0, 9.0]) == pytest.approx(9.0)
    # A first reading still showing the previous plug-in's total
    assert counter_energy([5.2, 5.2, 0.0, 1.0, 3.8]) == pytest.approx(3.8)
    # Wrongly continued over a new plug-in: both stretches count, from zero
    assert counter_energy([0.0, 5.2, 5.2, 0.1, 3.8]) == pytest.approx(9.0)
    # A later part of a plug-in: only the rise
    assert counter_energy([5.2, 7.0, 9.0], fragment=True) == pytest.approx(3.8)
    assert counter_energy([None, 1.0, None, 2.0]) == pytest.approx(2.0)
    assert counter_energy([]) == 0.0


def _old_split_session(database: Database, car_id: int) -> tuple[int, int]:
    """Two sessions as 0.7.0 recorded a paused charge: the second booked the counter from zero."""
    ids = []
    for start, kwh_from, kwh_to, booked in ((T0, 0.0, 5.2, 5.2), (T0 + 3600, 5.2, 9.0, 9.0)):
        charge_id = database.execute(
            "INSERT INTO charges (car_id, start_ts, end_ts, lat, lon, kind, energy_added_kwh, energy_used_kwh, cost)"
            " VALUES (?, ?, ?, ?, ?, 'AC', ?, ?, ?)",
            (car_id, start, start + 1800, LAT, 5.1214, booked, booked * 1.1, round(booked * 1.1 * 0.3, 2)),
        )
        for i in range(31):
            database.add_charge_sample(
                charge_id,
                {"ts": start + i * 60, "power_kw": 11.0, "energy_added_kwh": kwh_from + (kwh_to - kwh_from) * i / 30},
            )
        ids.append(charge_id)
    return ids[0], ids[1]


def test_repair_of_stored_split_sessions(database):
    car_id = database.upsert_car(1, 2, "VIN1", "car")
    first, second = _old_split_session(database, car_id)
    assert database.repair_resumed_charges() == 1
    rows = {c["id"]: c for c in _charges(database)}
    assert rows[first]["energy_added_kwh"] == pytest.approx(5.2)
    assert rows[second]["energy_added_kwh"] == pytest.approx(3.8)
    # Power over time (11 kW for 30 min) is what the cost goes by; it was raised to the double count before
    assert rows[second]["energy_used_kwh"] == pytest.approx(5.5)
    assert rows[second]["cost"] == pytest.approx(round(2.97 * 5.5 / 9.9, 2))
    # Once is enough: again changes nothing
    assert database.repair_resumed_charges() == 0


def test_repair_runs_once_on_upgrade(tmp_path):
    path = tmp_path / "tesla.db"
    database = Database(path)
    car_id = database.upsert_car(1, 2, "VIN1", "car")
    _, second = _old_split_session(database, car_id)
    database.close()
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version=5")
    database = Database(path)
    assert database.query_one("PRAGMA user_version")["user_version"] == 6
    assert database.query_one("SELECT energy_added_kwh FROM charges WHERE id = ?", (second,))[
        "energy_added_kwh"
    ] == pytest.approx(3.8)
    database.close()


def test_repair_leaves_sessions_after_a_drive_alone(database):
    car_id = database.upsert_car(1, 2, "VIN1", "car")
    _old_split_session(database, car_id)
    database.execute(
        "INSERT INTO drives (car_id, start_ts, end_ts, distance_km) VALUES (?, ?, ?, 3)", (car_id, T0 + 2000, T0 + 2500)
    )
    assert database.repair_resumed_charges() == 0
