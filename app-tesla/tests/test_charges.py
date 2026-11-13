"""A paused and resumed charge books its energy once (TeslaMate #5491), and the counter's quirks."""

import pytest

from src.database import counter_energy

from .conftest import feed, make_monitor

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
