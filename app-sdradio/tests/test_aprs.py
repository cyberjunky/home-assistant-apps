"""The APRS mode: packets read (the expected values are aprslib's), and multimon-ng hearing them."""

import shutil
import time

import numpy as np
import pytest

from src.decoders.aprs import Aprs, parse
from src.dsp import Demodulator
from tests import aprs_signal

RATE = 2_400_000
BLOCK = RATE // 20


def test_plain_position_with_course_speed_and_altitude():
    p = parse("APRS: PD0ABC-9>APRS,WIDE1-1,WIDE2-1:!5205.40N/00507.20E>088/036Test mobile /A=000123")
    assert (p["lat"], p["lon"], p["course"], p["speed"], p["altitude"]) == (52.09, 5.12, 88, 66.7, 37)
    assert p["kind"] == "car" and p["comment"] == "Test mobile" and p["path"] == ["WIDE1-1", "WIDE2-1"]


def test_compressed_object_and_mic_e():
    p = parse("APRS: PA3XYZ-7>APRS:!/5L!!<*e7>7P[")
    assert (p["lat"], p["lon"], p["course"], p["speed"]) == (49.5, -72.75, 88, 67.1)
    o = parse("APRS: PA1ABC>APRS:;LEADER   *092345z4903.50N/07201.75W>088/036")
    assert o["type"] == "object" and o["name"] == "LEADER" and (o["lat"], o["lon"]) == (49.05833, -72.02917)
    m = parse('APRS: PE1ABC-9>U3PPYU:`(_fn\x1cOj/]"4-}Mobile')
    assert (m["lat"], m["lon"], m["speed"], m["course"], m["altitude"]) == (53.01583, -112.129, 37.0, 51, 22)
    assert m["kind"] == "jeep" and m["comment"] == "Mobile" and m["status"] == "in service"


def test_weather_status_and_messages():
    w = parse("APRS: PA3WX>APSDR1:@121200z5203.20N/00512.40E_090/005g010t054r000p000P000h85b10132 weather")
    assert w["weather"]["temperature"] == 12.2 and w["weather"]["humidity"] == 85 and w["weather"]["pressure"] == 1013.2
    assert "speed" not in w and w["comment"] == "weather"
    assert parse("APRS: PE1XYZ-7>APRS:>On my way")["text"] == "On my way"
    msg = parse("APRS: PE1XYZ-7>APRS::PD0ABC-9 :Hello there{12")
    assert msg["type"] == "message" and msg["to"] == "PD0ABC-9" and msg["text"] == "Hello there{12"
    assert parse("AFSK1200: fm X") is None


def test_stations_on_the_map():
    stored, live = [], []
    decoder = Aprs({}, stored.append, live.append)
    decoder.location = (52.09, 5.12)
    decoder._line("APRS: PD0ABC-9>APRS:!5205.40N/00507.20E>088/036Mobile")
    decoder._line("APRS: PD0ABC-9>APRS:!5205.40N/00508.20E>088/036Mobile")
    decoder._line("APRS: PE1XYZ-7>APRS:>No position yet")
    station = decoder.live()["stations"][0]
    assert station["call"] == "PD0ABC-9" and len(station["trail"]) == 2 and station["packets"] == 2
    assert len(decoder.live()["stations"]) == 1  # the one without a position isn't on the map
    assert [s["key"] for s in stored] == ["PD0ABC-9", "PD0ABC-9", "PE1XYZ-7"]  # every packet is kept


@pytest.mark.skipif(not (shutil.which("multimon-ng") and shutil.which("sox")), reason="needs multimon-ng and sox")
def test_multimon_hears_packets_through_the_radio():
    offset = -240_000
    packets = [aprs_signal.frame("PD0ABC-9", "APRS", ["WIDE1-1"], "!5205.40N/00507.20E>088/036Test"),
               aprs_signal.frame("PE1ABC-9", "U3PPYU", [], '`(_fn\x1cOj/]"4-}Mobile')]  # fmt: skip
    audio = np.concatenate([np.concatenate([np.zeros(4800), aprs_signal.afsk(p)]) for p in packets] + [np.zeros(9600)])
    iq = aprs_signal.fm_iq(audio, RATE, offset)
    rng = np.random.default_rng(2)
    iq = (iq + (rng.standard_normal(len(iq)) + 1j * rng.standard_normal(len(iq))) * 0.003).astype(np.complex64)
    stored = []
    decoder = Aprs({}, stored.append, None)
    demod = Demodulator(RATE, *decoder.demod)
    demod.set_offset(offset)
    for i in range(0, len(iq), BLOCK):
        audio_block, _ = demod.process(iq[i : i + BLOCK])
        decoder.feed_audio(audio_block, demod.audio_rate)
    end = time.monotonic() + 8
    while len(stored) < 2 and time.monotonic() < end:
        time.sleep(0.1)
    decoder.stop()
    assert [s["key"] for s in stored] == ["PD0ABC-9", "PE1ABC-9"], decoder.error
    assert stored[1]["lat"] == 53.01583
