"""The Ships mode: AIS decoded from GMSK on both channels, and the messages read."""

import time

import numpy as np

from src.decoders import ships
from src.decoders.ships import Ships, parse, to_nmea
from tests import ais_signal

RATE = 2_400_000
BLOCK = RATE // 20
# Where the radio puts the tuned frequency: 240 kHz below the receiver's centre
OFFSET = -240_000


def dearmour(text: str, fill: int) -> bytes:
    bits = ""
    for c in text:
        v = ord(c) - 48
        bits += format(v - 8 if v > 40 else v, "06b")
    bits = bits[: len(bits) - fill] if fill else bits
    bits += "0" * (-len(bits) % 8)
    return bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8))


def test_reads_a_real_message():
    """A position report from the wild, as published decoded (pyais' example)."""
    data = dearmour("177KQJ5000G?tO`K>RA1wUbN0TKH", 0)
    msg = parse(data)
    assert msg["mmsi"] == 477553000 and msg["status"] == "moored" and msg["speed"] == 0.0
    assert msg["lat"] == 47.58283 and msg["lon"] == -122.34583 and msg["course"] == 51.0 and msg["heading"] == 181
    assert to_nmea(data, "B") == "!AIVDM,1,1,,B,177KQJ5000G?tO`K>RA1wUbN0TKH,0*5C"


def test_crc_is_x25():
    assert ships.crc16(b"123456789") == 0x906E


def test_names_and_types():
    bits = ais_signal.static(244123456, "TEST SHIP", "PD1234", 70, "ROTTERDAM")
    bits += "0" * (-len(bits) % 8)
    msg = parse(bytes(int(bits[i : i + 8], 2) for i in range(0, len(bits), 8)))
    assert msg["name"] == "TEST SHIP" and msg["callsign"] == "PD1234" and msg["destination"] == "ROTTERDAM"
    assert ships.ship_type_name(msg["ship_type"]) == "cargo" and msg["length"] == 100


def test_decodes_both_channels_from_the_receiver():
    a = ais_signal.gmsk_iq(ais_signal.position(244123456, 51.9, 4.45, 12.3, 271.5, 270), RATE, OFFSET - 25_000 + 300)
    b = ais_signal.gmsk_iq(ais_signal.static(244123456, "TEST SHIP", "PD1234", 70, "ROTTERDAM"), RATE,
                           OFFSET + 25_000 - 200)  # fmt: skip
    c = ais_signal.gmsk_iq(ais_signal.class_b(244987654, 51.95, 4.40, 6.0, 90), RATE, OFFSET + 25_000, level=0.02)
    quiet = np.zeros(50_000, np.complex64)
    x = np.concatenate([quiet, a, quiet, b, c, quiet, quiet, quiet])
    rng = np.random.default_rng(3)
    x = x + ((rng.standard_normal(len(x)) + 1j * rng.standard_normal(len(x))) * 0.004).astype(np.complex64)
    stored, live = [], []
    decoder = Ships({}, stored.append, live.append)
    decoder.location = (52.0, 4.5)
    for i in range(0, len(x), BLOCK):
        decoder.feed_iq(x[i : i + BLOCK], RATE, OFFSET)
    found = {s["mmsi"]: s for s in decoder.live()["ships"]}
    assert decoder.messages == 3  # each once, though the five phases find them
    ship = found[244123456]
    assert ship["name"] == "TEST SHIP" and ship["kind"] == "cargo" and ship["destination"] == "ROTTERDAM"
    assert ship["lat"] == 51.9 and ship["lon"] == 4.45 and ship["speed"] == 12.3 and ship["distance"] > 10
    assert found[244987654]["class"] == "B" and found[244987654]["speed"] == 6.0
    decoder.feed_iq(np.zeros(BLOCK, np.complex64), RATE, OFFSET)
    time.sleep(1.05)
    decoder.feed_iq(np.zeros(BLOCK, np.complex64), RATE, OFFSET)
    assert live and {s["mmsi"] for s in live[-1]["ships"]} <= {244123456, 244987654}
    decoder.stop()
    assert {s["key"] for s in stored} == {"244123456", "244987654"}


def test_noise_finds_nothing():
    rng = np.random.default_rng(4)
    decoder = Ships({}, print, None)
    for _ in range(40):
        x = ((rng.standard_normal(BLOCK) + 1j * rng.standard_normal(BLOCK)) * 0.01).astype(np.complex64)
        decoder.feed_iq(x, RATE, OFFSET)
    assert decoder.messages == 0
