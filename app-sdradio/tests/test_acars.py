"""The ACARS mode: MSK bursts on AM, on three channels at once, read into messages."""

import numpy as np
import pytest

from src.decoders.acars import Acars, frames, symbols
from src.errors import ValidationError
from tests import acars_signal

RATE = 2_400_000
BLOCK = RATE // 20
OFFSET = -240_000  # the tuned frequency (131.725) relative to the receiver's centre


def test_clean_audio():
    f = acars_signal.frame("2", ".PH-BXA", "H1", "3", "M01AKL1234Hello")
    env = np.concatenate([np.ones(2000), 1 + 0.6 * acars_signal.audio(f), np.ones(2000)])
    [message] = frames(symbols(env))
    assert message["registration"] == "PH-BXA" and message["flight"] == "KL1234" and message["number"] == "M01A"
    assert message["text"] == "Hello" and message["label"] == "H1" and message["crc"] and message["direction"] == "down"


@pytest.mark.parametrize("gap", [24_000, 240_000])
def test_two_channels_through_the_receiver(gap):
    long = acars_signal.frame("2", ".PH-BXA", "H1", "3", "M01AKL1234Hello from the test flight")
    short = acars_signal.frame("2", ".D-AIKB", "_d", "A", "")
    a = acars_signal.am_iq(acars_signal.audio(long), RATE, OFFSET - 200_000)  # 131.525
    b = acars_signal.am_iq(acars_signal.audio(short), RATE, OFFSET + 100_000, level=0.02)  # 131.825
    x = np.concatenate([np.zeros(48_000, np.complex64), a, np.zeros(gap, np.complex64), b,
                        np.zeros(96_000, np.complex64)])  # fmt: skip
    rng = np.random.default_rng(1)
    x = (x + (rng.standard_normal(len(x)) + 1j * rng.standard_normal(len(x))) * 0.003).astype(np.complex64)
    out = []
    decoder = Acars({}, out.append)
    for i in range(0, len(x), BLOCK):
        decoder.feed_iq(x[i : i + BLOCK], RATE, OFFSET)
    assert [(m["frequency"], m["key"]) for m in out] == [(131_525_000, "KL1234"), (131_825_000, "D-AIKB")]
    assert out[1]["label_name"] == "acknowledgement" and out[1]["text"] == ""


def test_noise_finds_nothing():
    rng = np.random.default_rng(5)
    out = []
    decoder = Acars({}, out.append)
    for _ in range(40):
        x = ((rng.standard_normal(BLOCK) + 1j * rng.standard_normal(BLOCK)) * 0.01).astype(np.complex64)
        decoder.feed_iq(x, RATE, OFFSET)
    assert out == []


def test_channels_setting():
    clean = Acars.validate({"channels": [131_825_000, 131_525_000, 131_725_000]})
    assert clean["channels"] == [131_525_000, 131_725_000, 131_825_000] and clean["frequency"] == 131_725_000
    with pytest.raises(ValidationError):
        Acars.validate({"channels": [129_125_000, 131_725_000]})
