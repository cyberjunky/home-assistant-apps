"""A simulated receiver for the tests and for running the page without an SDR (tests/devserver.py).

Not part of the app image. It makes noise plus a few transmissions that come and go:
NFM voices on 2 m, APRS bursts, an AM airband carrier and an FM broadcast station with RDS.
"""

import math
import time

import numpy as np

from tests import rds_signal

# frequency, mode, level (amplitude), on seconds, off seconds, tone Hz
SIGNALS = [
    (145_500_000, "nfm", 0.05, 6, 9, 800),
    (144_800_000, "nfm", 0.08, 1, 6, 1200),
    (145_612_500, "nfm", 0.03, 4, 11, 600),
    (145_287_500, "nfm", 0.02, 5, 6, 1000),
    (121_500_000, "am", 0.04, 5, 5, 700),
    (98_900_000, "wfm", 0.2, 1000, 0, 440),
    (156_800_000, "nfm", 0.04, 3, 8, 900),
]


class SimulatedDevice:
    def __init__(self, config, realtime: bool = True, signals=SIGNALS):
        self.config = config
        self.realtime = realtime
        self.signals = signals
        self.closed = False
        self.center = 0.0
        self.gain: float | None = None
        self.gain_range = (0.0, 49.6, 0.0)
        self.frequency_range = (24_000_000.0, 1_766_000_000.0)
        self.info: dict = {}
        self.sample_index = 0
        self.rng = np.random.default_rng(1)
        self._next_time = 0.0
        self.tunes: list[float] = []
        # The FM station's RDS: station name, radio text with RadioText+ artist and title
        rt = "Now playing: Simulated Artist - Simulated Title"
        self.rds_bits = rds_signal.bits(rds_signal.groups(0x8204, "SIM FM", rt, "Simulated Artist", "Simulated Title"))
        self.rds_phase = 0.0

    def open(self, center: float) -> dict:
        self.center = float(center)
        self.info = {
            "driver": "simulator",
            "hardware": "Simulated receiver",
            "details": {},
            "sample_rate": float(self.config.sample_rate),
            "gain_range": list(self.gain_range),
            "frequency_range": list(self.frequency_range),
        }
        self._next_time = time.monotonic()
        return self.info

    @property
    def sample_rate(self) -> float:
        return float(self.config.sample_rate)

    def set_center(self, frequency: float) -> None:
        self.center = float(frequency)
        self.tunes.append(self.center)

    def set_gain(self, gain: float | None) -> None:
        self.gain = gain

    def read(self, count: int) -> np.ndarray:
        rate = self.sample_rate
        n = np.arange(count) + self.sample_index
        t = n / rate
        x = ((self.rng.standard_normal(count) + 1j * self.rng.standard_normal(count)) * 0.002).astype(np.complex64)
        start = self.sample_index / rate
        for frequency, mode, level, on, off, tone in self.signals:
            offset = frequency - self.center
            if abs(offset) > rate * 0.48:
                continue
            period = on + off
            if off and (start % period) > on:
                continue
            m = np.sin(2 * math.pi * tone * t)
            if mode == "am":
                s = level * (1 + 0.6 * m) * np.exp(2j * math.pi * offset * t)
            else:
                deviation = 75_000 if mode == "wfm" else 2_500
                phase = 2 * math.pi * offset * t - deviation / tone * np.cos(2 * math.pi * tone * t)
                if mode == "wfm":
                    # RDS at 3 % of the deviation
                    rds = rds_signal.subcarrier(self.rds_bits, rate, start, count) * (
                        0.03 * 2 * math.pi * 75_000 / rate
                    )
                    integral = np.cumsum(rds, dtype=np.float64) + self.rds_phase
                    self.rds_phase = float(integral[-1])
                    phase = phase + integral
                s = level * np.exp(1j * phase)
            x += s.astype(np.complex64)
        self.sample_index += count
        if self.realtime:
            self._next_time += count / rate
            delay = self._next_time - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                self._next_time = time.monotonic()
        return x

    def close(self) -> None:
        self.closed = True
