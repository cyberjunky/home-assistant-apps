"""An analog video transmitter (as FPV drones and wireless cameras): black-and-white PAL or NTSC
composite video, FM on a carrier, as IQ samples. For testing the Video mode."""

import math

import numpy as np

SYNC, BLACK, WHITE = -0.3, 0.0, 0.7


def composite(image: np.ndarray, rate: float, standard: str = "PAL", fields: int = 2) -> np.ndarray:
    """The picture (rows x columns, 0..255) as composite video at the rate, `fields` fields long."""
    pal = standard == "PAL"
    line_us = 64.0 if pal else 63.556
    lines_per_field = 312.5 if pal else 262.5
    first_active = 23 if pal else 21
    active_lines = 288 if pal else 240
    per_us = rate / 1e6
    out = []
    t = 0.0  # in µs, to place every sample exactly

    def add(level: float, us: float) -> None:
        nonlocal t
        start = round(t * per_us)
        t += us
        out.append(np.full(round(t * per_us) - start, level, dtype=np.float32))

    def add_pixels(values: np.ndarray, us: float) -> None:
        nonlocal t
        start = round(t * per_us)
        t += us
        n = round(t * per_us) - start
        idx = (np.arange(n) * len(values) / n).astype(int)
        out.append((BLACK + (WHITE - BLACK) * values[idx] / 255).astype(np.float32))

    rows = np.asarray(image, dtype=np.float32)
    for _field in range(fields):
        # Vertical sync: five equalising pulses, five broad ones, five equalising (half lines)
        for _ in range(5):
            add(SYNC, 2.35)
            add(BLACK, line_us / 2 - 2.35)
        for _ in range(5):
            add(SYNC, 27.3 if pal else 27.1)
            add(BLACK, line_us / 2 - (27.3 if pal else 27.1))
        for _ in range(5):
            add(SYNC, 2.35)
            add(BLACK, line_us / 2 - 2.35)
        # Line 1 starts with the broad pulses: the lines with a sync pulse are 6 onwards
        for line in range(6, 6 + round(lines_per_field - 7.5)):
            add(SYNC, 4.7)
            add(BLACK, 5.7)
            row = line - first_active
            if 0 <= row < active_lines:
                add_pixels(rows[min(len(rows) - 1, row * len(rows) // active_lines)], 52.0)
            else:
                add(BLACK, 52.0)
            add(BLACK, line_us - 4.7 - 5.7 - 52.0)
    return np.concatenate(out)


def fm_iq(video: np.ndarray, rate: float, deviation: float = 3e6, offset: float = 0.0, invert: bool = False,
          noise: float = 0.0, seed: int = 1) -> np.ndarray:  # fmt: skip
    """The video on an FM carrier: `deviation` Hz from sync to white."""
    v = -video if invert else video
    freq = offset + deviation * (v - 0.2)
    phase = 2 * math.pi * np.cumsum(freq.astype(np.float64)) / rate
    x = np.exp(1j * phase).astype(np.complex64) * 0.5
    if noise:
        rng = np.random.default_rng(seed)
        x += ((rng.standard_normal(len(x)) + 1j * rng.standard_normal(len(x))) * noise).astype(np.complex64)
    return x


def test_picture(width: int = 384, height: int = 288) -> np.ndarray:
    img = np.zeros((height, width), dtype=np.uint8)
    img[:, :] = np.linspace(0, 255, width, dtype=np.uint8)[None, :]
    img[height // 4 : height // 2, width // 4 : width // 2] = 255
    img[height // 2 : 3 * height // 4, width // 2 : 3 * width // 4] = 0
    for r in range(height):
        c = int(r * width / height)
        img[r, max(0, c - 2) : c + 2] = 255
    return img
