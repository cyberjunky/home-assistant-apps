"""A simulated receiver for the tests and for running the page without an SDR (tests/devserver.py).

Not part of the app image. It makes noise plus a few transmissions that come and go:
NFM voices on 2 m, APRS bursts, an AM airband carrier, an FM broadcast station with RDS, pager
bursts, and aircraft flying round Utrecht on 1090 MHz.
"""

import math
import os
import time

import numpy as np

from tests import (
    acars_signal,
    adsb_signal,
    ais_signal,
    aprs_signal,
    pocsag_signal,
    rds_signal,
    sstv_signal,
    video_signal,
)

# frequency, mode, level (amplitude), on seconds, off seconds, tone Hz
SIGNALS = [
    (145_500_000, "nfm", 0.05, 6, 9, 800),
    # APRS packets from a few stations (the APRS mode)
    (144_800_000, "aprs", 0.08, 0, 0, 0),
    (145_612_500, "nfm", 0.03, 4, 11, 600),
    (145_287_500, "nfm", 0.02, 5, 6, 1000),
    (121_500_000, "am", 0.04, 5, 5, 700),
    (98_900_000, "wfm", 0.2, 1000, 0, 440),
    (156_800_000, "nfm", 0.04, 3, 8, 900),
    # A POCSAG pager burst every 8 seconds (the Pager mode's default frequency)
    (169_650_000, "pocsag", 0.08, 0, 8, 0),
    # Aircraft sending ADS-B (the Aircraft mode)
    (1_090_000_000, "adsb", 0.3, 0, 0, 0),
    # An SSTV picture (Robot 36) every minute on the ISS's frequency (the SSTV mode)
    (145_800_000, "sstv", 0.05, 0, 0, 0),
    # ACARS on the three European channels (the ACARS mode, tuned to the middle one)
    (131_725_000, "acars", 0.05, 0, 0, 0),
    # Analog FPV video on 5.8 GHz (the Video mode, with the receiver set to a HackRF)
    (5_800_000_000, "video", 0.3, 0, 0, 0),
    # Ships sending AIS on both channels (the Ships mode, tuned between them)
    (162_000_000, "ais", 0.05, 0, 0, 0),
]
# Channel offset from 131.725 MHz, mode, registration, label, block, text
ACARS = [
    (-200_000, "2", ".PH-BXA", "H1", "3", "M01AKL1234REQUEST GATE INFO AMS"),
    (0, "2", ".PH-EZM", "_d", "A", ""),
    (100_000, "2", ".D-AIKB", "15", "5", "M12ALH0987POS N52123E004567,FL350,1210"),
    (0, "2", ".G-EZTH", "QA", "7", "M33AU25432OUT 1155 FOB 0123"),
    (-200_000, "2", ".EI-EFX", "SQ", "S", "02XSAMSEHAM15201N00446EV136975/"),
]
# Call sign, path, info (a moving car sends its position, which the simulator moves along)
APRS_STATIONS = [
    ("PI1UTR-10", ["WIDE2-2"], "=5206.10N/00508.50E#PHG5130 Utrecht digipeater"),
    ("PD0ABC-9", ["WIDE1-1", "WIDE2-1"], "car"),
    ("PA3WX", ["WIDE2-1"], "@121200z5203.20N/00512.40E_090/005g010t054r000p000P000h85b10132 weather"),
    ("PE1XYZ-7", ["WIDE1-1"], ">On my way to the radio club"),
]
# MMSI, name, call sign, ship type, destination, start latitude and longitude, heading, knots, class
SHIPS = [
    (244660001, "RHINE STAR", "PE2345", 80, "BASEL", 52.20, 5.03, 165, 7, "A"),
    (244660002, "VERA", "PD7788", 79, "AMSTERDAM", 52.00, 5.08, 345, 8, "A"),
    (211234567, "MAINZ", "DA4567", 70, "ROTTERDAM", 51.985, 5.25, 262, 9, "A"),
    (244999001, "WATERRAT", "", 37, "", 52.06, 5.06, 10, 4, "B"),
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


# A DAB+ recording to play (8-bit IQ at 2.048 MS/s, as DAB-Radio's raw IQ data): SDRADIO_SIM_DAB=<file>,
# on SDRADIO_SIM_DAB_MHZ (222.064, block 11D, for the Bavarian one); heard in the DAB+ mode
DAB_FILE = os.environ.get("SDRADIO_SIM_DAB")
DAB_FREQUENCY = float(os.environ.get("SDRADIO_SIM_DAB_MHZ", "222.064")) * 1e6
if DAB_FILE:
    SIGNALS = [*SIGNALS, (round(DAB_FREQUENCY), "dab", 1.0, 0, 0, 0)]


class SimulatedDevice:
    def __init__(self, config, realtime: bool = True, signals=SIGNALS):
        self.config = config
        self.realtime = realtime
        self.signals = signals
        self.closed = False
        self.center = 0.0
        self.gain: float | None = None
        self.gain_range = (0.0, 49.6, 0.0)
        # As an RTL-SDR, or as a HackRF (1 MHz to 6 GHz) when the receiver is set to one
        hackrf = getattr(config, "type", "") == "hackrf"
        self.frequency_range = (1_000_000.0, 6_000_000_000.0) if hackrf else (24_000_000.0, 1_766_000_000.0)
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
        return float(getattr(self, "rate_override", None) or self.config.sample_rate)

    def set_sample_rate(self, rate: float) -> float:
        self.rate_override = float(rate)
        return self.sample_rate

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
            if mode == "ais":
                x += self._ships(start, rate, offset, level, count)
                continue
            if mode == "aprs":
                x += self._aprs(start, rate, offset, level, count)
                continue
            if mode == "acars":
                x += self._acars(start, rate, offset, level, count)
                continue
            if mode == "sstv":
                x += self._sstv(start, rate, offset, level, count)
                continue
            if mode == "video":
                x += self._video(rate, offset, level, count)
                continue
            if mode == "dab":
                x += self._dab(rate, offset, count)
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

    def _aprs(self, start: float, rate: float, offset: float, level: float, count: int) -> np.ndarray:
        """A packet every 6 seconds from the stations in turn; the car drives east along the A12."""
        end = start + count / rate
        k = math.ceil((start - 0.3) / 6.0)
        while 0.3 + k * 6.0 < end:
            t = 0.3 + k * 6.0
            call, path, info = APRS_STATIONS[k % len(APRS_STATIONS)]
            if info == "car":
                lon = 4.95 + (t % 1800) * 0.00025
                info = f"!5204.80N/{int(lon):03d}{(lon % 1) * 60:05.2f}E>090/050Mobile in the simulator"
            audio = aprs_signal.afsk(aprs_signal.frame(call, "APSDR1", path, info))
            self._queue_burst("aprs", int(t * rate), aprs_signal.fm_iq(audio, rate, offset, level=level))
            k += 1
        return self._play_bursts("aprs", count)

    def _acars(self, start: float, rate: float, offset: float, level: float, count: int) -> np.ndarray:
        """A message every 4 seconds, on the three channels in turn."""
        end = start + count / rate
        k = math.ceil((start - 0.7) / 4.0)
        while 0.7 + k * 4.0 < end:
            t = 0.7 + k * 4.0
            channel, mode, registration, label, block, text = ACARS[k % len(ACARS)]
            audio = acars_signal.audio(acars_signal.frame(mode, registration, label, block, text))
            self._queue_burst(
                "acars", int(t * rate), acars_signal.am_iq(audio, rate, offset + channel, level, lead=0.002)
            )
            k += 1
        return self._play_bursts("acars", count)

    def _sstv(self, start: float, rate: float, offset: float, level: float, count: int) -> np.ndarray:
        """Robot 36 (38 s) at the start of every minute, as FM: the audio made once, sent block by block."""
        if not hasattr(self, "sstv_audio"):
            k = sstv_signal.kind("Robot 36")
            self.sstv_audio = sstv_signal.encode("Robot 36", sstv_signal.test_picture(k.width, k.height), lead_ms=100)
            self.sstv_phase = 0.0
        into = start % 60.0
        first = int(into * 48_000)
        n_audio = int(np.ceil(count * 48_000 / rate)) + 1
        audio = self.sstv_audio[first : first + n_audio]
        if not len(audio):
            return np.zeros(count, np.complex64)
        audio = np.concatenate([audio, np.zeros(max(0, n_audio - len(audio)), np.float32)])
        a = audio[(np.arange(count) * 48_000 / rate).astype(int)].astype(np.float64)
        phase = self.sstv_phase + 2 * math.pi * np.cumsum(offset + 3_000 * 2 * a) / rate
        self.sstv_phase = float(phase[-1] % (2 * math.pi))
        return (level * np.exp(1j * phase)).astype(np.complex64)

    def _video(self, rate: float, offset: float, level: float, count: int) -> np.ndarray:
        """Two fields of FM video made once (for this rate), played round and round."""
        if getattr(self, "video_rate", None) != rate:
            image = video_signal.test_picture()
            self.video_iq = video_signal.fm_iq(video_signal.composite(image, rate, "PAL", fields=2), rate) * (
                level / 0.5
            )
            self.video_rate = rate
        n = len(self.video_iq)
        first = self.sample_index % n
        idx = (first + np.arange(count)) % n
        x = self.video_iq[idx]
        if offset:
            t = (np.arange(count) + self.sample_index) / rate
            x = x * np.exp(2j * math.pi * offset * t).astype(np.complex64)
        return x.astype(np.complex64)

    def _dab(self, rate: float, offset: float, count: int) -> np.ndarray:
        """The DAB+ recording, round and round; only at its own rate, centred (the DAB+ mode)."""
        if rate != 2_048_000 or abs(offset) > 1000:
            return np.zeros(count, np.complex64)
        if not hasattr(self, "dab_raw"):
            self.dab_raw = np.memmap(DAB_FILE, dtype=np.uint8, mode="r")
            self.dab_pos = 0
        n = len(self.dab_raw) // 2
        idx = (self.dab_pos + np.arange(count)) % n
        self.dab_pos = (self.dab_pos + count) % n
        raw = self.dab_raw
        x = ((raw[2 * idx].astype(np.float32) - 127.5) + 1j * (raw[2 * idx + 1].astype(np.float32) - 127.5)) / 128
        return x.astype(np.complex64)

    def _queue_burst(self, name: str, at: int, samples: np.ndarray) -> None:
        self.__dict__.setdefault(f"{name}_bursts", []).append([at, samples])

    def _play_bursts(self, name: str, count: int) -> np.ndarray:
        """The queued bursts' samples that fall in this block; the ones not finished stay queued."""
        out = np.zeros(count, np.complex64)
        first = self.sample_index
        keep = []
        for burst in self.__dict__.get(f"{name}_bursts", []):
            at, samples = burst
            lo, hi = max(at, first), min(at + len(samples), first + count)
            if lo < hi:
                out[lo - first : hi - first] += samples[lo - at : hi - at]
            if at + len(samples) > first + count:
                keep.append(burst)
        self.__dict__[f"{name}_bursts"] = keep
        return out

    def _ships(self, start: float, rate: float, offset: float, level: float, count: int) -> np.ndarray:
        """The ships' AIS bursts: a position every 3 seconds (on channel A and B in turn) and the
        name and destination every fourth time. They sail straight for half an hour, then start over."""
        end = start + count / rate
        for i, (mmsi, name, callsign, kind, destination, lat, lon, heading, knots, cls) in enumerate(SHIPS):
            phase = 0.41 * (i + 1)
            k = math.ceil((start - phase) / 3.0)
            while phase + k * 3.0 < end:
                t = phase + k * 3.0
                sailed = knots * 0.5144 * (t % 1800)
                la = lat + sailed * math.cos(math.radians(heading)) / 111_320
                lo = lon + sailed * math.sin(math.radians(heading)) / (111_320 * math.cos(math.radians(lat)))
                if k % 4 == 0 and cls == "A":
                    bits = ais_signal.static(mmsi, name, callsign, kind, destination)
                elif k % 4 == 0:
                    bits = ais_signal.name_b(mmsi, name)
                elif cls == "B":
                    bits = ais_signal.class_b(mmsi, la, lo, knots, heading)
                else:
                    bits = ais_signal.position(mmsi, la, lo, knots, heading, heading)
                channel = -25_000 if k % 2 else 25_000
                samples = ais_signal.gmsk_iq(bits, rate, offset + channel, level, lead=0.0)
                self._queue_burst("ais", int(t * rate), samples)
                k += 1
        return self._play_bursts("ais", count)

    def close(self) -> None:
        self.closed = True
