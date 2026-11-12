"""A simulated receiver for the tests and for running the page without an SDR (tests/devserver.py).

Not part of the app image. It makes noise plus a few transmissions that come and go:
NFM voices on 2 m, APRS bursts, an AM airband carrier, an FM broadcast station with RDS, pager
bursts, and aircraft flying round Utrecht on 1090 MHz.
"""

import math
import time

import numpy as np

from tests import adsb_signal, pocsag_signal, rds_signal

# frequency, mode, level (amplitude), on seconds, off seconds, tone Hz
SIGNALS = [
    (145_500_000, "nfm", 0.05, 6, 9, 800),
    (144_800_000, "nfm", 0.08, 1, 6, 1200),
    (145_612_500, "nfm", 0.03, 4, 11, 600),
    (145_287_500, "nfm", 0.02, 5, 6, 1000),
    (121_500_000, "am", 0.04, 5, 5, 700),
    (98_900_000, "wfm", 0.2, 1000, 0, 440),
    (156_800_000, "nfm", 0.04, 3, 8, 900),
    # A POCSAG pager burst every 8 seconds (the Pager mode's default frequency)
    (169_650_000, "pocsag", 0.08, 0, 8, 0),
    # Aircraft sending ADS-B (the Aircraft mode)
    (1_090_000_000, "adsb", 0.3, 0, 0, 0),
]
# ICAO address, callsign, start latitude and longitude, feet, heading, knots, climb ft/min
AIRCRAFT = [
    (0x484506, "KLM1234", 52.31, 4.76, 3000, 100, 210, 1500),
    (0x3C6444, "DLH4AB", 52.40, 6.20, 36000, 255, 450, 0),
    (0x4CA7B3, "RYR8QK", 51.70, 5.10, 24000, 10, 380, -1200),
    (0x400F1E, "EZY45TJ", 52.00, 3.90, 33000, 75, 430, 0),
    (0x484C51, "PHLAB", 52.12, 5.35, 1500, 300, 95, 0),
]
ADSB_HOME = (52.09, 5.12)
PAGER_TEXTS = ["SDRadio simulated page", "Test alarm 112 Teststraat", "Coffee is ready"]


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
        self.pager_burst: np.ndarray | None = None
        self.pager_count = 0

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
            if mode == "pocsag":
                x += self._pager(start, rate, offset, level, off, count)
                continue
            if mode == "adsb":
                x += self._aircraft(start, rate, offset, level, count)
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

    def _pager(self, start: float, rate: float, offset: float, level: float, every: float, count: int) -> np.ndarray:
        """A POCSAG 1200 message at the start of every `every` seconds."""
        into = int((start % every) * rate)
        if into == 0 or self.pager_burst is None:
            text = PAGER_TEXTS[self.pager_count % len(PAGER_TEXTS)]
            self.pager_count += 1
            data = pocsag_signal.bits(1_234_560 + self.pager_count % 3, 3, text)
            self.pager_burst = pocsag_signal.fm_iq(data, 1200, rate, 0, lead=0.05) * (level / 0.1)
        burst = self.pager_burst
        if into >= len(burst):
            return np.zeros(count, np.complex64)
        part = burst[into : into + count]
        part = np.concatenate([part, np.zeros(count - len(part), np.complex64)])
        t = (np.arange(count) + self.sample_index) / rate
        return (part * np.exp(2j * math.pi * offset * t)).astype(np.complex64)

    def _aircraft(self, start: float, rate: float, offset: float, level: float, count: int) -> np.ndarray:
        """The aircraft's ADS-B messages in this block: positions four times a second, callsign and
        velocity every second. They fly straight for ten minutes, then start over."""
        end = start + count / rate
        envelope = np.zeros(count, dtype=np.float32)
        for i, (icao, callsign, lat, lon, feet, heading, knots, climb) in enumerate(AIRCRAFT):
            phase = 0.037 * (i + 1)
            k = math.ceil((start - phase) / 0.25)
            while phase + k * 0.25 < end:
                t = phase + k * 0.25
                flown = t % 600
                metres = knots * 0.5144 * flown
                la = lat + metres * math.cos(math.radians(heading)) / 111_320
                lo = lon + metres * math.sin(math.radians(heading)) / (111_320 * math.cos(math.radians(lat)))
                altitude = max(0, round((feet + climb * flown / 60) / 25) * 25)
                if k % 4 == 1:
                    text = adsb_signal.identity(icao, callsign)
                elif k % 4 == 3:
                    east = knots * math.sin(math.radians(heading))
                    north = knots * math.cos(math.radians(heading))
                    text = adsb_signal.velocity(icao, east, north, climb)
                else:
                    text = adsb_signal.position(icao, la, lo, altitude, (k // 2) % 2)
                p = adsb_signal.pulses(text, rate)
                at = int((t - start) * rate)
                if at >= 0 and at + len(p) <= count:
                    envelope[at : at + len(p)] += p
                k += 1
        t = (np.arange(count) + self.sample_index) / rate
        return (level * envelope * np.exp(2j * math.pi * offset * t)).astype(np.complex64)

    def close(self) -> None:
        self.closed = True
