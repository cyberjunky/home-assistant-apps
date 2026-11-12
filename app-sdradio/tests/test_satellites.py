"""Satellites: passes over Utrecht from a real ISS orbit, Doppler, and following a pass."""

from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("ephem")

from src.decoders.satellites import Orbits, Satellites, Tracker, doppler
from tests import sstv_signal

HOME = (52.09, 5.12)
TLE = (Path(__file__).parent / "data" / "25544.tle").read_text()
# The orbit's epoch (day 316 of 2026), a time it is good for
EPOCH = 1794441600.0 + 0.1746 * 86400


def tracker():
    orbits = Orbits(None, fetch=lambda norad: TLE)
    orbits.load([25544])
    return Tracker(HOME, orbits.tles, 10)


def test_doppler_sign():
    assert doppler(145_800_000, -7000) > 145_800_000 > doppler(145_800_000, 7000)


def test_passes_over_utrecht():
    t = tracker()
    passes = t.passes(25544, EPOCH, hours=24)
    assert passes, "the ISS passes over Utrecht several times a day"
    for p in passes:
        assert p["aos"] < p["top"] < p["los"] and 2 * 60 < p["los"] - p["aos"] < 12 * 60
        assert p["max_elevation"] >= 10
        top = t.where(25544, p["top"])
        assert abs(top["elevation"] - p["max_elevation"]) < 1.0
        # Coming closer at the start (heard higher), going away at the end (lower), within 3.5 kHz on 2 m
        rise = doppler(145_800_000, t.where(25544, p["aos"] + 30)["range_rate"]) - 145_800_000
        fall = doppler(145_800_000, t.where(25544, p["los"] - 30)["range_rate"]) - 145_800_000
        assert 1_000 < rise < 3_600 and -3_600 < fall < -1_000
    track = t.track(25544, EPOCH, minutes=92, step=60)
    assert all(-52 <= lat <= 52 for lat, _ in track)  # the ISS's inclination is 51.6°


def test_following_a_pass(tmp_path):
    first = tracker().passes(25544, EPOCH, hours=24)[0]
    now = [first["aos"] - 120]
    stored, live, tuned = [], [], []
    sat = Satellites({"follow": ["iss"]}, stored.append, live.append, fetch=lambda n: TLE, clock=lambda: now[0])
    sat.location = HOME
    sat.folder = tmp_path
    sat.retune = lambda f, mode, bw: tuned.append((f, mode))
    sat._load()
    assert sat.passes and abs(sat.passes[0]["aos"] - first["aos"]) < 1
    quiet = np.zeros(2400, np.float32)
    # Before the pass: nothing followed
    sat.feed_audio(quiet, 48_000)
    assert sat.current is None and not tuned
    # During it: tuned with the Doppler shift, an SSTV picture comes in
    now[0] = first["aos"] + 10
    sat._next_step = 0
    sat.feed_audio(quiet, 48_000)
    assert sat.current and sat.current["name"] == "ISS" and tuned[-1][0] > 145_800_000
    k = sstv_signal.kind("Robot 36")
    audio = sstv_signal.encode("Robot 36", sstv_signal.test_picture(k.width, k.height))
    for i in range(0, len(audio), 2400):
        sat.feed_audio(audio[i : i + 2400], 48_000)
    now[0] = first["los"] - 20
    sat._next_step = 0
    sat.feed_audio(quiet, 48_000)
    assert tuned[-1][0] < 145_800_000
    # After it: the pass stored, with its picture
    now[0] = first["los"] + 60
    sat._next_step = 0
    sat.feed_audio(quiet, 48_000)
    assert sat.current is None
    passes = [s for s in stored if s["type"] == "pass"]
    pictures = [s for s in stored if s["type"] == "picture"]
    assert len(passes) == 1 and passes[0]["pictures"] == [pictures[0]["file"]]
    assert (tmp_path / pictures[0]["file"]).is_file()
    assert live[-1]["passes"] and "satellites" in live[-1]


def test_settings():
    clean = Satellites.validate({"follow": ["meteor-m2-4", "iss"], "frequencies": {"meteor-m2-4": 137.1e6}})
    assert clean["follow"] == ["iss", "meteor-m2-4"] and clean["frequencies"] == {"meteor-m2-4": 137_100_000}
    with pytest.raises(Exception, match="follow"):
        Satellites.validate({"follow": ["noaa-19"]})
