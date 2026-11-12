"""An SSTV transmitter: a picture as audio tones, with its VIS code, for testing the SSTV decoder."""

import math

import numpy as np

from src.decoders.sstv import KINDS


def kind(name: str):
    return next(k for k in KINDS.values() if k.name == name)


def test_picture(width: int, height: int) -> np.ndarray:
    """Colour bars over a grey ramp, with a white diagonal: easy to compare."""
    img = np.zeros((height, width, 3), dtype=np.uint8)
    bars = [(255, 255, 255), (255, 255, 0), (0, 255, 255), (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255),
            (0, 0, 0)]  # fmt: skip
    for i, colour in enumerate(bars):
        img[: height // 2, i * width // 8 : (i + 1) * width // 8] = colour
    img[height // 2 :] = np.linspace(0, 255, width, dtype=np.uint8)[None, :, None]
    for r in range(height):
        c = int(r * width / height)
        img[r, max(0, c - 2) : c + 2] = 255
    return img


def rgb_to_ycc(rgb: np.ndarray):
    r, g, b = (rgb[..., i].astype(np.float32) for i in range(3))
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128 + (b - y) / 1.772
    cr = 128 + (r - y) / 1.402
    return y, cr, cb


class Tones:
    """Phase-continuous tones, with sample-exact (fractional) timing."""

    def __init__(self, rate: int):
        self.rate = rate
        self.freqs: list[np.ndarray] = []
        self.t = 0.0  # ms
        self.n = 0  # samples so far

    def add(self, freq, ms: float) -> None:
        self.t += ms
        end = round(self.t * self.rate / 1000)
        count = end - self.n
        if count <= 0:
            return
        if np.isscalar(freq):
            self.freqs.append(np.full(count, float(freq)))
        else:
            values = np.asarray(freq, dtype=np.float64)
            idx = (np.arange(count) * len(values) / count).astype(int)
            self.freqs.append(values[idx])
        self.n = end

    def pixels(self, values: np.ndarray, ms: float) -> None:
        self.add(1500 + np.asarray(values, dtype=np.float64) / 255 * 800, ms)

    def audio(self) -> np.ndarray:
        f = np.concatenate(self.freqs)
        return (0.5 * np.sin(2 * math.pi * np.cumsum(f) / self.rate)).astype(np.float32)


def encode(name: str, image: np.ndarray, rate: int = 48_000, lead_ms: float = 500, clock: float = 1.0) -> np.ndarray:
    """The picture as SSTV audio; `clock` stretches the timing (a transmitter's clock that is off)."""
    k = kind(name)
    t = Tones(rate)
    s = clock
    t.add(0, lead_ms)
    t.add(1900, 300 * s)
    t.add(1200, 10 * s)
    t.add(1900, 300 * s)
    t.add(1200, 30 * s)
    bits = [(k.vis >> i) & 1 for i in range(7)]
    for b in [*bits, sum(bits) % 2]:
        t.add(1100 if b else 1300, 30 * s)
    t.add(1200, 30 * s)
    if k.family == "pd":
        for row in range(0, k.height, 2):
            y0, cr0, cb0 = rgb_to_ycc(image[row])
            y1, cr1, cb1 = rgb_to_ycc(image[row + 1])
            t.add(1200, k.sync * s)
            t.add(1500, k.porch * s)
            t.pixels(y0, k.component * s)
            t.pixels((cr0 + cr1) / 2, k.component * s)
            t.pixels((cb0 + cb1) / 2, k.component * s)
            t.pixels(y1, k.component * s)
    elif k.family == "martin":
        for row in range(k.height):
            t.add(1200, 4.862 * s)
            t.add(1500, 0.572 * s)
            for ch in (1, 2, 0):  # G, B, R
                t.pixels(image[row, :, ch], k.component * s)
                t.add(1500, 0.572 * s)
    else:
        for row in range(k.height):
            y, cr, cb = rgb_to_ycc(image[row])
            t.add(1200, 9 * s)
            t.add(1500, 3 * s)
            t.pixels(y, 88 * s)
            even = row % 2 == 0
            t.add(1500 if even else 2300, 4.5 * s)
            t.add(1900, 1.5 * s)
            if even:
                _, cr2, cb2 = rgb_to_ycc(image[min(row + 1, k.height - 1)])
                t.pixels((cr + cr2) / 2, 44 * s)
            else:
                _, cr2, cb2 = rgb_to_ycc(image[row - 1])
                t.pixels((cb + cb2) / 2, 44 * s)
    t.add(0, 300)
    return t.audio()
