"""The demodulators against synthetic signals: each should give back the 1 kHz tone it was sent."""

import math

import numpy as np
import pytest

from src.dsp import Decimator, Demodulator, Spectrum, quantize

RATE = 2_400_000
OFFSET = 100_000
BLOCK = RATE // 20


def _tone(audio: np.ndarray, rate: float) -> float:
    a = audio[len(audio) // 4 :]
    spectrum = np.abs(np.fft.rfft(a * np.hanning(len(a))))
    return np.argmax(spectrum) * rate / len(a)


def _run(mode: str, iq: np.ndarray, bandwidth: float) -> tuple[np.ndarray, float, float]:
    demod = Demodulator(RATE, mode, bandwidth)
    demod.set_offset(OFFSET)
    audio, level = [], -140.0
    for i in range(0, len(iq), BLOCK):
        a, level = demod.process(iq[i : i + BLOCK])
        audio.append(a)
    return np.concatenate(audio), demod.audio_rate, level


@pytest.fixture(scope="module")
def t():
    return np.arange(RATE // 2) / RATE


def _fm(t, deviation):
    m = np.sin(2 * math.pi * 1000 * t)
    return (np.exp(1j * (2 * math.pi * OFFSET * t + 2 * math.pi * deviation * np.cumsum(m) / RATE)) * 0.1).astype(
        np.complex64
    )


def test_nfm(t):
    audio, rate, level = _run("nfm", _fm(t, 3000), 12_500)
    assert rate == 48_000
    assert abs(_tone(audio, rate) - 1000) < 20
    assert -25 < level < -15


def test_wfm(t):
    audio, rate, _ = _run("wfm", _fm(t, 50_000), 180_000)
    assert rate == 48_000
    assert abs(_tone(audio, rate) - 1000) < 20


def test_am(t):
    m = np.sin(2 * math.pi * 1000 * t)
    iq = ((1 + 0.5 * m) * np.exp(1j * 2 * math.pi * OFFSET * t) * 0.05).astype(np.complex64)
    audio, rate, _ = _run("am", iq, 9_000)
    assert abs(_tone(audio, rate) - 1000) < 20


def test_usb_and_lsb(t):
    carrier = (np.exp(1j * 2 * math.pi * (OFFSET + 1000) * t) * 0.05).astype(np.complex64)
    audio, rate, _ = _run("usb", carrier, 2_700)
    assert abs(_tone(audio, rate) - 1000) < 20
    # 1 kHz above the carrier is outside LSB's passband
    lsb, _, _ = _run("lsb", carrier, 2_700)
    assert np.sqrt(np.mean(lsb[len(lsb) // 2 :] ** 2)) < 0.05 * np.sqrt(np.mean(audio[len(audio) // 2 :] ** 2))


def test_cw_tone(t):
    carrier = (np.exp(1j * 2 * math.pi * OFFSET * t) * 0.05).astype(np.complex64)
    audio, rate, _ = _run("cw", carrier, 500)
    assert abs(_tone(audio, rate) - 700) < 20


def test_decimator_blocks_match_one_pass():
    x = (np.random.default_rng(0).standard_normal(BLOCK * 3) * (1 + 1j)).astype(np.complex64)
    whole = Decimator(50, 0.02)(x)
    d = Decimator(50, 0.02)
    parts = np.concatenate([d(x[i : i + 7777]) for i in range(0, len(x), 7777)])
    n = min(len(whole), len(parts))
    assert n > 0.99 * len(x) / 50
    assert np.allclose(whole[:n], parts[:n], atol=1e-5)


def test_spectrum_peak_and_quantize(t):
    db = Spectrum(2048)(_fm(t, 0)[:BLOCK])
    assert int(np.argmax(db)) == 1024 + round(OFFSET / RATE * 2048)
    q = quantize(db)
    assert len(q) == 2048
    assert max(q) > 200
