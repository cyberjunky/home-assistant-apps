"""Autotune: finding the middle of a signal in an averaged spectrum."""

import numpy as np

from src.dsp import Spectrum, find_signal

RATE = 2_400_000


def averaged(iq, size=2048):
    spectrum = Spectrum(size)
    blocks = [spectrum(iq[i : i + RATE // 20]) for i in range(0, len(iq), RATE // 20)]
    return np.mean([10 ** (b / 10) for b in blocks], axis=0)


def noise(n, level, seed=1):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * level


def test_finds_a_carrier_off_the_tuned_frequency():
    n = RATE // 2
    t = np.arange(n) / RATE
    iq = 0.05 * np.exp(2j * np.pi * 303_000 * t) + noise(n, 0.002)
    found = find_signal(averaged(iq), RATE, 296_000, 12_500, 12_500)
    assert found and abs(found[0] - 303_000) < 1_200 and found[1] > 20


def test_centres_a_wide_fm_station_on_its_width():
    """A broadcast station 200 kHz below centre, tuned 50 kHz off it: to the middle of its width."""
    n = RATE // 2
    rng = np.random.default_rng(2)
    audio = np.convolve(rng.standard_normal(n), np.ones(40) / 40, mode="same") * 4
    phase = 2 * np.pi * np.cumsum(-200_000 + 60_000 * np.clip(audio, -1, 1)) / RATE
    iq = 0.1 * np.exp(1j * phase) + noise(n, 0.002)
    found = find_signal(averaged(iq), RATE, -150_000, 180_000, 180_000)
    assert found and abs(found[0] - -200_000) < 10_000


def test_nothing_in_noise():
    assert find_signal(averaged(noise(RATE // 2, 0.01)), RATE, 0, 12_500, 12_500) is None
