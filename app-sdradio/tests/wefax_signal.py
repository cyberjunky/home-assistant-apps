"""A weather fax transmission as USB audio: start tone, phasing, a picture, stop tone."""

import numpy as np

from src.decoders.wefax import CENTRE, LINE, RATE, SHIFT


def brightness_to_audio(values: np.ndarray, rate: int = RATE) -> np.ndarray:
    hz = CENTRE + (np.asarray(values, float) * 2 - 1) * SHIFT
    return (0.5 * np.cos(2 * np.pi * np.cumsum(hz) / rate)).astype(np.float32)


def transmission(picture: np.ndarray, shift: int = 0, start: float = 5.0, phasing: int = 20) -> np.ndarray:
    """picture: rows of brightness (0 to 1), each LINE samples; shift: where in the stream lines begin."""
    t = np.arange(int(start * RATE)) / RATE
    values = [np.zeros(shift), (np.sin(2 * np.pi * 300 * t) > 0).astype(float)]
    pulse = np.zeros(LINE)
    pulse[: LINE // 40] = pulse[-LINE // 40 :] = 1.0
    values += [np.roll(pulse, 0)] * phasing
    values += list(picture)
    t = np.arange(5 * RATE) / RATE
    values += [(np.sin(2 * np.pi * 450 * t) > 0).astype(float), np.zeros(RATE)]
    return brightness_to_audio(np.concatenate(values))


def chart(lines: int = 120) -> np.ndarray:
    """A test picture: a white band down the middle and a grey ramp, LINE samples a row."""
    x = np.linspace(0, 1, LINE)
    rows = []
    for i in range(lines):
        row = x.copy()
        row[int(LINE * 0.45) : int(LINE * 0.55)] = 1.0 if i % 40 < 30 else 0.0
        rows.append(row)
    return np.array(rows)
