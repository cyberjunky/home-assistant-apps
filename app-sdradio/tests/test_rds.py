"""The RDS decoder against a synthetic FM multiplex: it should read back the station name and texts sent."""

import math

import numpy as np
import pytest

from src.dsp import Demodulator
from src.rds import OFFSETS, RdsDecoder, encode_block, syndrome
from tests import rds_signal

RATE = 240_000
PI = 0x8204
PS = "SIM FM"
RT = "Now: Simulated Artist - Simulated Title"
ARTIST, TITLE = "Simulated Artist", "Simulated Title"


def test_check_words():
    for name, offset in OFFSETS.items():
        for data in (0, 0x1234, 0xFFFF, PI):
            assert syndrome(encode_block(data, name)) == offset


def _multiplex(seconds: float, bit_rate=rds_signal.BIT_RATE, carrier=57_000.0, noise=0.0, seed=0):
    coded = rds_signal.bits(rds_signal.groups(PI, PS, RT, ARTIST, TITLE), repeat=1)
    n = int(seconds * RATE)
    t = np.arange(n) / RATE
    mpx = 0.5 * np.sin(2 * math.pi * 1000 * t) + 0.09 * np.sin(2 * math.pi * 19_000 * t)
    mpx += 0.03 * rds_signal.subcarrier(coded, RATE, 0.0, n, bit_rate=bit_rate, carrier=carrier)
    if noise:
        mpx += np.random.default_rng(seed).standard_normal(n) * noise
    return mpx.astype(np.float32)


def _decode(mpx: np.ndarray) -> dict:
    decoder = RdsDecoder(RATE)
    for i in range(0, len(mpx), RATE // 20):
        decoder.process(mpx[i : i + RATE // 20])
    return decoder.snapshot()


@pytest.mark.parametrize(
    "bit_rate, carrier, noise",
    [
        (rds_signal.BIT_RATE, 57_000.0, 0.0),
        # A receiver clock 50 ppm off: the subcarrier and the bits come in a little off
        (rds_signal.BIT_RATE * (1 + 50e-6), 57_000.0 * (1 + 50e-6), 0.0),
        (rds_signal.BIT_RATE * (1 - 80e-6), 57_000.0 * (1 - 80e-6), 0.05),
    ],
)
def test_decodes_station(bit_rate, carrier, noise):
    rds = _decode(_multiplex(6, bit_rate, carrier, noise))
    assert rds["pi"] == "8204"
    assert rds["ps"] == PS
    assert rds["rt"] == RT
    assert rds["pty"] == "Pop music"
    assert rds["artist"] == ARTIST
    assert rds["title"] == TITLE


def test_noise_gives_nothing():
    rng = np.random.default_rng(3)
    assert _decode((rng.standard_normal(RATE * 3) * 0.3).astype(np.float32)) is None


def test_through_the_wfm_demodulator():
    """FM-modulated at 2.4 MS/s, 100 kHz off centre, as the receiver delivers it."""
    rate, offset = 2_400_000, 100_000
    mpx = _multiplex(5)
    up = np.repeat(mpx, rate // RATE)  # hold each multiplex sample: fine for a 75 kHz deviation test
    phase = 2 * math.pi * offset * np.arange(len(up)) / rate + 2 * math.pi * 75_000 * np.cumsum(up) / rate
    iq = (np.exp(1j * phase) * 0.2).astype(np.complex64)
    demod = Demodulator(rate, "wfm", 180_000)
    demod.set_offset(offset)
    for i in range(0, len(iq), rate // 20):
        demod.process(iq[i : i + rate // 20])
    rds = demod.rds.snapshot()
    assert rds["ps"] == PS
    assert rds["title"] == TITLE
    demod.configure("nfm", 12_500)
    assert demod.rds is None
