"""CTCSS, DCS and DTMF read from NFM audio, under speech-like sound, and nothing from noise."""

import numpy as np
import pytest

from src.tones import CTCSS, DCS_RATE, DTMF_COLS, DTMF_ROWS, KEYS, MASK, ToneReader, dcs_word

RATE = 48_000
rng = np.random.default_rng(7)


def speech(seconds):
    """Band-limited noise from 300 to 3000 Hz, standing in for a voice."""
    from scipy import signal

    b, a = signal.butter(4, [300, 3000], "bandpass", fs=RATE)
    return signal.lfilter(b, a, rng.standard_normal(int(seconds * RATE))) * 0.5


def feed(audio):
    reader = ToneReader(RATE)
    for i in range(0, len(audio), 2400):
        reader.process(audio[i : i + 2400].astype(np.float32))
    return reader


def test_dcs_words():
    # From the format: 023 is 0x763813, and inverted it is a rotation of 047's word
    assert dcs_word(23) == 0x763813
    w = dcs_word(47)
    assert dcs_word(23) ^ MASK in {((w >> k) | (w << (23 - k))) & MASK for k in range(23)}


@pytest.mark.parametrize("tone", [67.0, 88.5, 150.0, 151.4, 254.1])
def test_ctcss(tone):
    t = np.arange(4 * RATE) / RATE
    reader = feed(0.12 * np.sin(2 * np.pi * tone * t) + speech(4))
    assert reader.snapshot() == {"ctcss": tone, "dcs": None, "dtmf": None}


def dcs_audio(code, seconds, inverted=False):
    w = dcs_word(code) ^ (MASK if inverted else 0)
    bits = [(w >> j) & 1 for j in range(23)] * int(seconds * DCS_RATE / 23 + 2)
    at = (np.arange(int(seconds * RATE)) * DCS_RATE / RATE).astype(int)
    levels = np.array(bits)[at] * 2.0 - 1
    from scipy import signal

    b, a = signal.butter(2, 300, fs=RATE)
    return signal.lfilter(b, a, levels) * 0.15


@pytest.mark.parametrize("code, inverted, shown", [(23, False, "D023N"), (754, False, "D754N"), (23, True, "D023I")])
def test_dcs(code, inverted, shown):
    reader = feed(dcs_audio(code, 4, inverted) + speech(4))
    snap = reader.snapshot()
    # The codes that are rotations of each other are one on air; the lowest is shown
    assert snap["ctcss"] is None and snap["dcs"] is not None
    from src.tones import WORDS

    heard = next(w for w, name in WORDS.items() if name == snap["dcs"])
    sent = dcs_word(code) ^ (MASK if inverted else 0)
    assert heard in {((sent >> k) | (sent << (23 - k))) & MASK for k in range(23)}
    if shown == "D754N" or not inverted:
        assert snap["dcs"] == shown or snap["dcs"] < shown


def test_dtmf():
    pieces = []
    for key in "112#":
        r = next(i for i, row in enumerate(KEYS) if key in row)
        c = KEYS[r].index(key)
        t = np.arange(int(0.1 * RATE)) / RATE
        pieces += [
            0.3 * (np.sin(2 * np.pi * DTMF_ROWS[r] * t) + np.sin(2 * np.pi * DTMF_COLS[c] * t)),
            np.zeros(RATE // 10),
        ]
    reader = feed(np.concatenate(pieces) + rng.standard_normal(sum(map(len, pieces))) * 0.01)
    assert reader.snapshot()["dtmf"] == "112#"


def test_nothing_in_noise_or_speech():
    assert feed(rng.standard_normal(10 * RATE) * 0.5).snapshot() is None
    assert feed(speech(10)).snapshot() is None
    assert len(CTCSS) == 51
