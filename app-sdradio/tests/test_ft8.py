"""FT8: decode_ft8's lines read, grid squares placed, and the audio cut on the UTC slots."""

import subprocess
import wave

import numpy as np
import pytest

from src.decoders import ft8
from src.decoders.ft8 import Ft8, grid_position, parse
from src.errors import ValidationError


def test_lines():
    m = parse("000000 +13.0 +1.60 1291 ~  CQ R7IW LN35")
    assert m["snr"] == 13.0 and m["offset"] == 1291 and m["call"] == "R7IW" and m["grid"] == "LN35" and m["to"] == "CQ"
    m = parse("000000 -12.0 +0.30  640 ~  PA3EPP SP8NFO KN09")
    assert m["call"] == "SP8NFO" and m["to"] == "PA3EPP" and m["grid"] == "KN09"
    m = parse("000000  -3.0 +0.10 1500 ~  CQ DX R6WA LN32")
    assert m["call"] == "R6WA" and m["grid"] == "LN32"
    assert parse("000000 -8.0 +0.1 900 ~  PA3EPP SP8NFO RR73")["grid"] is None
    assert parse("Decoded 4 messages, callsign hashtable size 5") is None


def test_grid():
    lat, lon = grid_position("JO22")  # the west of the Netherlands
    assert (lat, lon) == (52.5, 5.0)
    assert grid_position("AA00") == (-89.5, -179.0)


def test_protocol_setting():
    assert Ft8.validate({"protocol": "FT4"})["protocol"] == "FT4"
    with pytest.raises(ValidationError):
        Ft8.validate({"protocol": "JT65"})


@pytest.mark.parametrize("protocol, slot", [("FT8", 15.0), ("FT4", 7.5)])
def test_cut_on_the_slots(monkeypatch, protocol, slot):
    runs = []

    def fake(command, **kwargs):
        with wave.open(command[-1]) as w:
            runs.append((command[1:-1], w.getframerate(), w.getnframes()))
        return subprocess.CompletedProcess(command, 0, "000000 +5.0 +0.2 1200 ~  CQ PA3EPP JO22\n", "")

    monkeypatch.setattr(ft8.subprocess, "run", fake)
    monkeypatch.setattr(ft8.threading, "Thread", _Inline)
    n = [0]
    t0 = 1_700_000_100.0 - 1_700_000_100.0 % 15 + 4.0  # halfway into a slot
    out, updates = [], []
    mode = Ft8({"protocol": protocol}, out.append, updates.append, clock=lambda: t0 + n[0] / 48_000)
    mode.location = (52.0, 4.5)
    block = np.zeros(2400, np.float32)
    for _ in range(int(48_000 * 40 / 2400)):
        n[0] += 2400
        mode.feed_audio(block + 0.01, 48_000)
    # The slot that was half in is skipped; every whole slot after it is decoded
    whole = int((t0 + 40 - (t0 - t0 % slot + slot)) // slot)
    assert len(runs) == whole and all(r[1] == 12_000 and r[2] == int(slot * 12_000) for r in runs)
    assert all(r[0] == (["-ft4"] if protocol == "FT4" else []) for r in runs)
    assert out[0]["call"] == "PA3EPP" and out[0]["lat"] == 52.5 and out[0]["distance"] == 65
    assert mode.live()["stations"][0]["messages"] == whole


class _Inline:
    def __init__(self, target, args, daemon):
        self.target, self.args = target, args

    def start(self):
        self.target(*self.args)


def test_a_late_radio_thread_loses_no_slot(monkeypatch):
    """Blocks handed over late and then in a burst (the radio thread catching up) still make whole slots."""
    runs = []

    def fake(command, **kwargs):
        with wave.open(command[-1]) as w:
            runs.append(w.getnframes())
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(ft8.subprocess, "run", fake)
    monkeypatch.setattr(ft8.threading, "Thread", _Inline)
    t0 = 1_700_000_100.0 - 1_700_000_100.0 % 15
    wall = [t0]
    mode = Ft8({}, lambda m: None, clock=lambda: wall[0])
    block = np.full(2400, 0.01, np.float32)
    for i in range(20 * 50):
        # Every 40th block the thread stalls 0.8 s, and the next 16 come at once
        sample_time = t0 + (i + 1) * 0.05
        late = 0.8 if i % 40 < 16 else 0.0
        wall[0] = sample_time + late * (1 - (i % 40) / 16)
        mode.feed_audio(block, 48_000)
    assert len(runs) >= 2 and all(n == 15 * 12_000 for n in runs)
