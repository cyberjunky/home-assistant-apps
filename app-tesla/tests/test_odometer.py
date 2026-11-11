"""The odometer never steps back (TeslaMate #5863/#5864): Home Assistant treats it as a meter."""

import math

from src.config import KM_PER_MILE
from src.mqtt import state_payload

from .conftest import feed, make_monitor


def _stream(monitor, ts, odometer_km, lat=52.1):
    monitor._on_stream_sample({"ts": ts, "lat": lat, "lon": 5.1, "odometer_km": odometer_km, "shift": "D"})


def test_rounded_stream_value_does_not_lower_the_odometer(monitor, database):
    feed(monitor, 1000, shift="D", odometer_km=10000.27)
    # The stream sends whole tenths of a mile: 6213.8 mi is 10000.2 km, below the polled value
    _stream(monitor, 1001, math.floor(10000.27 / KM_PER_MILE * 10) / 10 * KM_PER_MILE)
    assert monitor.snapshot["odometer_km"] == 10000.27
    assert state_payload(monitor.view())["odometer_km"] == 10000.27
    stored = [r["odometer_km"] for r in database.query("SELECT odometer_km FROM positions ORDER BY id")]
    assert stored == sorted(stored)


def test_older_poll_does_not_undo_newer_stream_values(monitor):
    feed(monitor, 1000, shift="D", odometer_km=10000.0)
    _stream(monitor, 1010, 10000.5, lat=52.2)
    # A poll that arrives now but was taken before the last frame
    feed(monitor, 1005, shift="D", odometer_km=10000.3, lat=52.15)
    assert monitor.snapshot["lat"] == 52.2
    assert monitor.snapshot["odometer_km"] == 10000.5
    assert monitor.snapshot["ts"] == 1010


def test_odometer_is_kept_across_a_restart(database):
    first = make_monitor(database)
    feed(first, 1000, shift="D", odometer_km=10000.5)
    feed(first, 1003, shift="P", odometer_km=10000.5)
    again = make_monitor(database)
    feed(again, 2000, shift=None, odometer_km=10000.4)
    assert again.snapshot["odometer_km"] == 10000.5


def test_odometer_still_goes_up(monitor):
    feed(monitor, 1000, shift="D", odometer_km=10000.0)
    _stream(monitor, 1001, 10000.2)
    feed(monitor, 1005, shift="D", odometer_km=10000.6)
    assert monitor.snapshot["odometer_km"] == 10000.6
