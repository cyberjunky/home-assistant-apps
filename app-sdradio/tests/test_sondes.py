"""Radiosondes: the RS decoders' JSON frames, a sonde followed, and kept once it is gone."""

import json

from src.decoders import sondes
from src.decoders.sondes import Sondes, parse

FRAME = {"type": "RS41", "frame": 165, "id": "U4345245", "datetime": "2024-03-01T22:05:01.000Z", "lat": 48.14371,
         "lon": 17.11557, "alt": 197.32031, "vel_h": 0.45367, "heading": 353.30996, "vel_v": -0.05459, "sats": 4,
         "batt": 2.70, "temp": -273.0, "subtype": "RS41-SG"}  # fmt: skip


def test_frame():
    f = parse(json.dumps(FRAME))
    assert f["id"] == "U4345245" and f["type"] == "RS41-SG" and f["altitude"] == 197 and f["speed"] == 1.6
    assert f["battery"] == 2.7 and f["satellites"] == 4 and "temperature" not in f  # -273: no reading


def test_not_a_frame():
    assert parse("[  165] (U4345245)  Fri 2024-03-01 22:05:01.000  lat: 48.14371") is None
    assert parse(json.dumps({**FRAME, "lat": 0, "lon": 0})) is None
    assert parse(json.dumps({"id": "X"})) is None


def test_followed_then_stored(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(sondes.time, "time", lambda: now[0])
    out, updates = [], []
    mode = Sondes({}, out.append, updates.append)
    mode.location = (48.2, 17.1)
    for i, alt in enumerate([200, 5000, 31000, 12000]):
        mode._line(json.dumps({**FRAME, "frame": i, "alt": alt, "lat": 48.1 + i / 100}))
        now[0] += 1
    [live] = mode.live()["sondes"]
    assert live["frames"] == 4 and live["max_altitude"] == 31000 and len(live["trail"]) == 4 and live["distance"] > 0
    assert updates[-1]["sondes"][0]["point"] == [48.13, 17.11557, 12000]
    now[0] += sondes.LOST_AFTER + 1
    mode._expire()
    [stored] = out
    assert (
        stored["key"] == "U4345245"
        and stored["max_altitude"] == 31000
        and stored["launch_position"] == [48.1, 17.11557]
    )
    assert updates[-1] == {"sondes": [], "gone": ["U4345245"]} and mode.live()["sondes"] == []
