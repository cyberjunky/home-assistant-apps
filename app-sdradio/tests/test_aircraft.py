"""The Aircraft mode: dump1090's aircraft list, the aircraft database, and the receiver centred."""

import json
import shutil
import time

import pytest

from src.decoders import aircraft
from src.decoders.aircraft import Aircraft, distance_km, lookup
from tests import adsb_signal

RATE = 2_400_000
BLOCK = RATE // 20


def test_lookup_follows_the_database_files():
    files = {
        "4": {"children": ["48"], "0001": {"r": "X"}},
        "48": {"4506": {"r": "PH-BXA", "t": "B738"}, "children": []},
    }
    load = lambda prefix: files.get(prefix, {})  # noqa: E731
    assert lookup("484506", load) == {"r": "PH-BXA", "t": "B738"}
    assert lookup("40001", load) == {"r": "X"}
    assert lookup("3c6444", load) == {}
    assert lookup("489999", load) == {}


def test_distance():
    assert abs(distance_km((52.0, 5.0), (52.0, 6.0)) - 68.5) < 0.5


def test_aircraft_list_live_and_stored_when_gone(monkeypatch):
    monkeypatch.setattr(aircraft, "lookup", lambda icao: {"r": "PH-BXA", "t": "B738"} if icao == "484506" else {})
    stored, live = [], []
    decoder = Aircraft({}, stored.append, live.append)
    decoder.location = (52.0, 5.0)
    now = time.time()
    decoder._update({"now": now, "messages": 10, "aircraft": [
        {"hex": "484506", "flight": "KLM1234 ", "alt_baro": 3000, "lat": 52.3, "lon": 4.77, "gs": 230, "seen": 0.5,
         "messages": 5},
        {"hex": "~2a1234", "seen": 1},
    ]})  # fmt: skip
    assert live[-1]["aircraft"][0]["flight"] == "KLM1234" and live[-1]["aircraft"][0]["registration"] == "PH-BXA"
    assert live[-1]["aircraft"][0]["point"] == [52.3, 4.77, 3000] and live[-1]["aircraft"][0]["distance"] > 30
    decoder._update({"now": now, "messages": 12, "aircraft": [
        {"hex": "484506", "alt_baro": 3500, "lat": 52.31, "lon": 4.80, "seen": 0.1, "messages": 7},
    ]})  # fmt: skip
    assert decoder.live()["aircraft"][0]["trail"] == [[52.3, 4.77, 3000], [52.31, 4.8, 3500]]
    assert decoder.live()["aircraft"][0]["flight"] == "KLM1234"  # kept from before
    # Not heard for over a minute: gone, and stored once
    decoder.planes["484506"]["seen"] = now - 120
    decoder._update({"now": now, "messages": 12, "aircraft": []})
    assert live[-1]["gone"] == ["484506"]
    assert len(stored) == 1 and stored[0]["key"] == "484506" and stored[0]["alt_min"] == 3000
    assert stored[0]["alt_max"] == 3500 and stored[0]["positions"] == 2 and stored[0]["registration"] == "PH-BXA"
    decoder.stop()


@pytest.mark.skipif(not shutil.which("dump1090"), reason="needs dump1090")
def test_dump1090_follows_two_aircraft():
    events = adsb_signal.flight(0x484506, "KLM1234", 52.30, 4.76, 3000, 90, 120, 6)
    events += adsb_signal.flight(0x3C6444, "DLH4AB", 52.0, 5.5, 36000, 270, 230, 6, start=0.123)
    iq = adsb_signal.timed_iq(events, 6.5)
    stored, live = [], []
    decoder = Aircraft({}, stored.append, live.append)
    for i in range(0, len(iq), BLOCK):
        decoder.feed_iq(iq[i : i + BLOCK], RATE, 0.0)
        time.sleep(0.02)
    end = time.monotonic() + 6
    while time.monotonic() < end and len([a for a in decoder.live()["aircraft"] if a.get("lat")]) < 2:
        time.sleep(0.2)
    planes = {a["icao"]: a for a in decoder.live()["aircraft"]}
    decoder.stop()
    assert set(planes) == {"484506", "3c6444"}, decoder.error
    assert planes["484506"]["flight"] == "KLM1234" and planes["484506"]["altitude"] == 3000
    assert abs(planes["3c6444"]["lat"] - 52.0) < 0.01 and planes["3c6444"]["lon"] < 5.5
    assert len(stored) == 2  # stopping stores what was in view
    json.dumps(stored)
