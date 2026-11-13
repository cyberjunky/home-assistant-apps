"""Weather fax: weather maps by radio, as ships have received them for decades.

Stations such as the DWD's in Pinneberg (3855, 7880 and 13882.5 kHz) send surface analyses,
forecasts and sea-state maps on a schedule, day and night, on shortwave: so with the HackRF.
The picture is a tone on upper sideband, from 1500 Hz for black to 2300 Hz for white, two lines
a second (120 lines a minute) of 1810 pixels each (IOC 576). A transmission opens with a 300 Hz
start tone, then half a minute of phasing lines (black, with a white pulse where a line begins),
then the picture, and closes with a 450 Hz stop tone. The radio is tuned 1.9 kHz below the
station's frequency, so the tones come out at 1500 to 2300 Hz.

Written for SDRadio from the format's description (ITU-R and WMO); no other decoder's code.
"""

import time
from pathlib import Path

import numpy as np
from scipy import signal

from .. import png
from . import Decoder

RATE = 12_000
CENTRE = 1_900.0  # Hz, the middle between black and white
SHIFT = 400.0  # Hz, from the middle to black or white
LPM = 120
LINE = RATE * 60 // LPM  # samples a line
WIDTH = 1810  # IOC 576: 576 x pi
MAX_LINES = 1600  # 13 minutes: longer than any chart
WINDOW = RATE // 2  # tone detection, every half second
START, STOP = 300.0, 450.0


def tone(values: np.ndarray, frequency: float) -> float:
    """How much of the brightness swing is a square wave at this frequency (0 to 1)."""
    x = values - values.mean()
    power = float(np.sum(x * x))
    if power < len(x) * 0.01:  # no swing: a picture or nothing, not a tone
        return 0.0
    t = np.arange(len(x)) / RATE
    c = abs(np.sum(x * np.exp(-2j * np.pi * frequency * t)))
    # A square wave carries 8/π² (0.81) of its power in the fundamental
    return min(1.0, 2 * c * c / len(x) / power)


class Demodulator:
    """USB audio to brightness, 0 (black) to 1 (white), at RATE; it keeps its state between blocks."""

    def __init__(self):
        self.rate = 0.0
        self.phase = 0.0
        self.b, self.a = signal.butter(4, 1_100, fs=RATE)
        self.zi = None
        self.last = 1 + 0j

    def __call__(self, audio: np.ndarray, rate: float) -> np.ndarray:
        if rate != self.rate:
            self.rate = rate
            self.zi = None
        x = signal.resample_poly(audio.astype(np.float64), RATE, int(rate)) if rate != RATE else audio
        n = np.arange(len(x))
        mixed = x * np.exp(-1j * (self.phase + 2 * np.pi * CENTRE / RATE * n))
        self.phase = (self.phase + 2 * np.pi * CENTRE / RATE * len(x)) % (2 * np.pi)
        if self.zi is None:
            self.zi = signal.lfilter_zi(self.b, self.a) * 0
        z, self.zi = signal.lfilter(self.b, self.a, mixed, zi=self.zi)
        previous = np.concatenate([[self.last], z[:-1]])
        self.last = z[-1] if len(z) else self.last
        hz = np.angle(z * np.conj(previous)) * RATE / (2 * np.pi)
        return np.clip((hz + SHIFT) / (2 * SHIFT), 0.0, 1.0)


class FaxReader:
    """Brightness in, pictures out: waits for the start tone, lines up on the phasing, reads lines."""

    def __init__(self, on_picture, on_progress=None, clock=time.time):
        self.on_picture = on_picture
        self.on_progress = on_progress
        self.clock = clock
        self.demod = Demodulator()
        self.pending = np.zeros(0)
        self.window = np.zeros(0)
        self.heard = {START: 0, STOP: 0}
        self.state = "idle"
        self.phasing: list[np.ndarray] = []
        self.rows: list[np.ndarray] = []
        self.started = 0.0

    def feed(self, audio: np.ndarray, rate: float) -> None:
        values = self.demod(audio, rate)
        self._tones(values)
        if self.state != "idle":
            self.pending = np.concatenate([self.pending, values])
            while len(self.pending) >= LINE and self.state != "idle":
                line, self.pending = self.pending[:LINE], self.pending[LINE:]
                self._line(line)

    def _tones(self, values: np.ndarray) -> None:
        self.window = np.concatenate([self.window, values])
        while len(self.window) >= WINDOW:
            w, self.window = self.window[:WINDOW], self.window[WINDOW:]
            for frequency in (START, STOP):
                self.heard[frequency] = self.heard[frequency] + 1 if tone(w, frequency) > 0.5 else 0
            # Three half seconds of a tone: it is one
            if self.heard[START] == 3 and self.state != "phasing":
                self.cancel()
                self.state, self.phasing, self.pending, self.started = "phasing", [], np.zeros(0), self.clock()
            elif self.heard[STOP] == 3 and self.state == "picture":
                self.cancel()

    def _line(self, line: np.ndarray) -> None:
        if self.state == "phasing":
            # Still the start tone: not a line yet
            if self.heard[START]:
                return
            if self._is_phasing(line):
                self.phasing.append(line)
                return
            # The first line that isn't phasing: the picture starts, lined up on the pulses (if any)
            shift = self._line_start() if len(self.phasing) >= 3 else 0
            self.state, self.phasing = "picture", []
            self.pending = np.concatenate([line, self.pending])[shift:]
            return
        row = np.interp(np.linspace(0, LINE - 1, WIDTH), np.arange(LINE), line)
        self.rows.append((row * 255).astype(np.uint8))
        if self.on_progress and len(self.rows) % 20 == 0:
            self.on_progress(self)
        if len(self.rows) >= MAX_LINES:
            self.cancel()

    @staticmethod
    def _is_phasing(line: np.ndarray) -> bool:
        # Mostly black, with a short white pulse: under a tenth of the line bright
        bright = np.mean(line > 0.6)
        return 0.01 < bright < 0.12 and np.mean(line < 0.4) > 0.8

    def _line_start(self) -> int:
        """Where a line begins in the samples: the middle of the phasing lines' white pulse."""
        mean = np.mean(self.phasing, axis=0)
        width = LINE // 20
        smooth = np.convolve(np.concatenate([mean, mean[:width]]), np.ones(width) / width, "valid")[:LINE]
        return (int(np.argmax(smooth)) + width // 2) % LINE

    @property
    def image(self) -> np.ndarray:
        return np.array(self.rows, dtype=np.uint8) if self.rows else np.zeros((1, WIDTH), np.uint8)

    def cancel(self) -> None:
        """The picture so far, if there is one worth keeping; back to waiting for a start tone."""
        if self.state == "picture" and len(self.rows) >= 50:
            self.on_picture(self)
        self.state, self.rows, self.phasing, self.pending = "idle", [], [], np.zeros(0)


class Wefax(Decoder):
    key = "wefax"
    name = "Weather fax"
    signal = "FM on USB"
    input = "audio"
    # DWD Pinneberg on 7880 kHz: the radio 1.9 kHz below it, on upper sideband
    defaults = {"frequency": 7_878_100}
    demod = ("usb", 3_000)
    receivers = ("hackrf",)
    receivers_why = "weather fax is on shortwave, below the RTL-SDR's range"

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.reader = FaxReader(self._done, self._progress)
        self.folder: Path | None = None

    def feed_audio(self, audio: np.ndarray, rate: float) -> None:
        self.reader.feed(audio, rate)

    def _save(self, name: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / name).write_bytes(png.encode(self.reader.image))

    def _progress(self, reader: FaxReader) -> None:
        if self.folder is None or not self.update:
            return
        self._save("live.png")
        self.update({"receiving": {"rows": len(reader.rows), "file": "live.png", "t": time.time()}})

    def _done(self, reader: FaxReader) -> None:
        self.count += 1
        name = time.strftime("%Y%m%d-%H%M%S", time.localtime(reader.started)) + "-fax.png"
        if self.folder is not None:
            self._save(name)
        self.publish({"key": f"{self.frequency + CENTRE:.0f}", "file": name, "width": WIDTH,
                      "rows": len(reader.rows), "frequency": round(self.frequency + CENTRE)})  # fmt: skip
        if self.update:
            self.update({"receiving": None})

    def live(self) -> dict:
        r = self.reader
        return {"receiving": {"rows": len(r.rows), "file": "live.png", "t": time.time()} if r.rows else None}

    def stop(self) -> None:
        self.reader.cancel()
