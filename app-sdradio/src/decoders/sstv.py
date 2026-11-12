"""SSTV: slow-scan television pictures, sent as audio tones (1500 Hz black to 2300 Hz white).

Decoded here from the radio's audio. The audio's frequency is followed sample by sample (mixed
down round 1900 Hz, 12 kHz, the phase step between samples). A picture starts with a VIS code
that says its kind: two 300 ms leaders of 1900 Hz, a start bit of 1200 Hz, seven data bits
(1100 Hz a one, 1300 Hz a zero, least significant first), a parity bit and a stop bit. Then line
after line, each starting with a 1200 Hz sync pulse; every line's sync is looked for near where
it should be, so a sound card or transmitter clock that is slightly off doesn't slant the picture.

The ISS sends PD120 and PD180; PD50 to PD290, Martin M1 and M2, and Robot 36 are read as well.
"""

import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .. import png
from ..dsp import Decimator, Mixer
from . import Decoder

RATE = 12_000
MS = RATE / 1000


@dataclass(frozen=True)
class Kind:
    name: str
    vis: int
    width: int
    height: int
    family: str  # pd, martin, robot36
    component: float  # ms per colour component (Y, R-Y, B-Y for PD; G, B, R for Martin)
    sync: float = 0.0
    porch: float = 0.0

    @property
    def line_ms(self) -> float:
        """One sync to the next (a pair of lines for PD)."""
        if self.family == "pd":
            return self.sync + self.porch + 4 * self.component
        if self.family == "martin":
            return 4.862 + 0.572 + 3 * self.component + 3 * 0.572
        return 150.0  # Robot 36

    @property
    def rows_per_sync(self) -> int:
        return 2 if self.family == "pd" else 1


KINDS = {
    k.vis: k
    for k in (
        Kind("PD50", 93, 320, 256, "pd", 91.52, 20.0, 2.08),
        Kind("PD90", 99, 320, 256, "pd", 170.24, 20.0, 2.08),
        Kind("PD120", 95, 640, 496, "pd", 121.6, 20.0, 2.08),
        Kind("PD160", 98, 512, 400, "pd", 195.584, 20.0, 2.08),
        Kind("PD180", 96, 640, 496, "pd", 183.04, 20.0, 2.08),
        Kind("PD240", 97, 640, 496, "pd", 244.48, 20.0, 2.08),
        Kind("PD290", 94, 800, 616, "pd", 228.8, 20.0, 2.08),
        Kind("Martin M1", 44, 320, 256, "martin", 146.432, 4.862, 0.572),
        Kind("Martin M2", 40, 320, 256, "martin", 73.216, 4.862, 0.572),
        Kind("Robot 36", 8, 320, 240, "robot36", 88.0, 9.0, 3.0),
    )
}
# Lines in a row without a sync before a picture is given up
LOST_SYNCS = 12


def ycc_to_rgb(y: np.ndarray, cr: np.ndarray, cb: np.ndarray) -> np.ndarray:
    y, cr, cb = (np.asarray(v, dtype=np.float32) for v in (y, cr, cb))
    r = y + 1.402 * (cr - 128)
    g = y - 0.344136 * (cb - 128) - 0.714136 * (cr - 128)
    b = y + 1.772 * (cb - 128)
    return np.clip(np.stack([r, g, b], axis=-1), 0, 255).astype(np.uint8)


class FrequencyTracker:
    """Audio in, its frequency (Hz) out at 12 kHz."""

    def __init__(self, rate: float):
        self.mixer = Mixer(rate)
        self.mixer.offset = 1900
        # 900 to 2900 Hz: the tones and a little room, not the noise beyond
        self.decimate = Decimator(max(1, round(rate / RATE)), 1000 / (rate / 2), k=12)
        self.last = np.complex64(1)

    def __call__(self, audio: np.ndarray) -> np.ndarray:
        z = self.decimate(self.mixer(audio.astype(np.complex64)))
        if not len(z):
            return np.zeros(0, dtype=np.float32)
        prev = np.concatenate([[self.last], z[:-1]])
        self.last = z[-1]
        return (1900 + np.angle(z * np.conj(prev)) * RATE / (2 * np.pi)).astype(np.float32)


class Picture:
    """A picture being received: its kind, rows, and where its next sync should be."""

    def __init__(self, kind: Kind, start: float):
        self.kind = kind
        self.image = np.zeros((kind.height, kind.width, 3), dtype=np.uint8)
        self.rows = 0
        self.next_sync = start  # in samples, absolute
        self.missed = 0
        # The line period as measured from the syncs found (a clock that is off stretches it)
        self.period = kind.line_ms * MS
        self.last_found: tuple[float, int] | None = None  # where, at which line
        # Robot 36 sends R-Y with the even lines and B-Y with the odd ones: the even line waits
        self.robot_cr: np.ndarray | None = None
        self.robot_y: np.ndarray | None = None
        self.started = time.time()


class SstvReader:
    """The part that reads pictures from the frequency stream; used by the SSTV and Satellites modes."""

    def __init__(self, on_picture, on_progress=None):
        self.on_picture = on_picture  # (Picture) when done
        self.on_progress = on_progress  # (Picture) now and then while receiving
        self.tracker: FrequencyTracker | None = None
        self.rate = 0.0
        self.f = np.zeros(0, dtype=np.float32)
        self.start = 0  # absolute index of f[0]
        self.picture: Picture | None = None
        self._progress_rows = 0

    def feed(self, audio: np.ndarray, rate: float) -> None:
        if rate != self.rate:
            self.rate = rate
            self.tracker = FrequencyTracker(rate)
        self.f = np.concatenate([self.f, self.tracker(audio)])
        if self.picture is None:
            self._find_vis()
        while self.picture is not None and self._line():
            pass
        # Keep what may still be needed: a VIS is 1 s long; a picture keeps from its next sync
        keep_from = len(self.f) - int(1.2 * RATE)
        if self.picture is not None:
            keep_from = min(keep_from, int(self.picture.next_sync) - self.start - int(50 * MS))
        if keep_from > 0:
            self.f = self.f[keep_from:]
            self.start += keep_from

    # ---------------------------------------------------------------- VIS

    def _find_vis(self) -> None:
        f = self.f
        need = int(910 * MS)  # leader 300, break 10, leader 300, start bit and 9 bits of 30
        if len(f) < need:
            return
        smooth = np.convolve(f, np.ones(int(6 * MS)) / int(6 * MS), mode="same")
        at1200 = np.abs(smooth - 1200) < 110
        at1900 = np.abs(smooth - 1900) < 110
        c1200 = np.concatenate([[0], np.cumsum(at1200)])
        c1900 = np.concatenate([[0], np.cumsum(at1900)])
        bit = int(30 * MS)
        leader = int(250 * MS)
        # A start bit: 30 ms of 1200 Hz after (most of) 250 ms of 1900 Hz
        starts = np.flatnonzero(np.diff(at1200.astype(np.int8)) == 1) + 1
        for s in starts:
            if s < leader or s + 10 * bit > len(f):
                continue
            if c1200[s + bit - 3] - c1200[s + 3] < 0.7 * (bit - 6):
                continue
            if c1900[s] - c1900[s - leader] < 0.7 * leader:
                continue
            bits = []
            for i in range(8):
                centre = f[s + bit * (i + 1) + bit // 5 : s + bit * (i + 2) - bit // 5]
                value = float(np.median(centre))
                # 1100 Hz a one, 1300 Hz a zero: whichever it is nearer to, if it is either
                if not 1000 <= value <= 1400 or abs(value - 1200) < 25:
                    break
                bits.append(1 if value < 1200 else 0)
            if len(bits) != 8 or sum(bits) % 2:
                continue
            code = sum(b << i for i, b in enumerate(bits[:7]))
            kind = KINDS.get(code)
            if kind is None:
                continue
            # The picture's first sync follows the stop bit
            self.picture = Picture(kind, self.start + s + 10 * bit)
            self._progress_rows = 0
            return

    # ---------------------------------------------------------------- lines

    def _find_sync(self, expected: float, length_ms: float) -> float | None:
        """The start of the sync pulse nearest to where it should be, or None if there is none."""
        f = self.f
        n = int(length_ms * MS)
        lo = int(expected - self.start - 6 * MS)
        hi = int(expected - self.start + 6 * MS)
        if lo < 0 or hi + n >= len(f):
            return None
        # Sync is the only tone below the picture's 1500 Hz: count the (lightly smoothed) samples under 1350
        seg = np.convolve(f[lo : hi + n], np.ones(6) / 6, mode="same")
        is_sync = (seg < 1350).astype(np.float32)
        score = np.convolve(is_sync, np.ones(n), mode="valid")[: hi - lo + 1] / n
        best = int(np.argmax(score))
        if score[best] < 0.5:
            return None
        return float(self.start + lo + best)

    def _values(self, at: float, ms: float, width: int, per_ms: float = MS) -> np.ndarray:
        """A colour component: `width` pixels over `ms` milliseconds from `at` (absolute samples)."""
        start = at - self.start
        edges = start + np.arange(width + 1) * (ms * per_ms / width)
        idx = np.clip(edges.astype(int), 0, len(self.f) - 1)
        csum = np.concatenate([[0], np.cumsum(self.f, dtype=np.float64)])
        counts = np.maximum(idx[1:] - idx[:-1], 1)
        means = (csum[idx[:-1] + counts] - csum[idx[:-1]]) / counts
        return np.clip((means - 1500) / 800 * 255, 0, 255)

    def _line(self) -> bool:
        p = self.picture
        k = p.kind
        line = k.line_ms * MS
        if p.next_sync + line + 8 * MS - self.start > len(self.f):
            return False  # not all here yet
        found = self._find_sync(p.next_sync, k.sync if k.family != "martin" else 4.862)
        line_index = p.rows // k.rows_per_sync
        if found is None:
            p.missed += 1
            at = p.next_sync
        else:
            p.missed = 0
            if p.last_found is not None and line_index > p.last_found[1]:
                # The period between syncs found, averaged: follows a clock that is off
                measured = (found - p.last_found[0]) / (line_index - p.last_found[1])
                if abs(measured - k.line_ms * MS) < 0.01 * k.line_ms * MS:
                    p.period += 0.2 * (measured - p.period)
            p.last_found = (found, line_index)
            # Follow the sync, but not too eagerly (noise moves it about)
            at = p.next_sync + 0.3 * (found - p.next_sync) if p.rows else found
        w = k.width
        # Within the line, too, the timing as measured (a clock that is off)
        ms = MS * p.period / (k.line_ms * MS)
        if k.family == "pd":
            t = at + (k.sync + k.porch) * ms
            c = k.component
            y0 = self._values(t, c, w, ms)
            cr = self._values(t + c * ms, c, w, ms)
            cb = self._values(t + 2 * c * ms, c, w, ms)
            y1 = self._values(t + 3 * c * ms, c, w, ms)
            if p.rows < k.height:
                p.image[p.rows] = ycc_to_rgb(y0, cr, cb)
            if p.rows + 1 < k.height:
                p.image[p.rows + 1] = ycc_to_rgb(y1, cr, cb)
        elif k.family == "martin":
            t = at + (4.862 + 0.572) * ms
            c, gap = k.component, 0.572
            g = self._values(t, c, w, ms)
            b = self._values(t + (c + gap) * ms, c, w, ms)
            r = self._values(t + 2 * (c + gap) * ms, c, w, ms)
            p.image[p.rows] = np.stack([r, g, b], axis=-1).astype(np.uint8)
        else:  # Robot 36: Y, then R-Y on even lines and B-Y on odd ones
            t = at + (9.0 + 3.0) * ms
            y = self._values(t, 88.0, w, ms)
            chroma = self._values(t + (88.0 + 4.5 + 1.5) * ms, 44.0, w, ms)
            if p.rows % 2 == 0:
                p.robot_cr, p.robot_y = chroma, y
                p.image[p.rows] = ycc_to_rgb(y, chroma, np.full(w, 128.0))
            else:
                cr = p.robot_cr if p.robot_cr is not None else np.full(w, 128.0)
                p.image[p.rows] = ycc_to_rgb(y, cr, chroma)
                if p.robot_y is not None:
                    p.image[p.rows - 1] = ycc_to_rgb(p.robot_y, cr, chroma)
        p.rows += k.rows_per_sync
        p.next_sync = at + p.period
        done = p.rows >= k.height
        lost = p.missed >= LOST_SYNCS
        if self.on_progress and (p.rows - self._progress_rows >= 16 or done):
            self._progress_rows = p.rows
            self.on_progress(p)
        if done or lost:
            self.picture = None
            if p.rows >= k.height // 8:
                p.rows = min(p.rows, k.height)
                self.on_picture(p)
        return True

    def cancel(self) -> None:
        p, self.picture = self.picture, None
        if p is not None and p.rows >= p.kind.height // 8:
            self.on_picture(p)


def save(picture: Picture, folder: Path, name: str) -> str:
    folder.mkdir(parents=True, exist_ok=True)
    rows = picture.image if picture.rows >= picture.kind.height else picture.image[: max(1, picture.rows)]
    (folder / name).write_bytes(png.encode(rows))
    return name


class Sstv(Decoder):
    key = "sstv"
    name = "SSTV"
    signal = "SSTV"
    input = "audio"
    # The ISS sends SSTV on 145.800 MHz during its SSTV events; 14.230 MHz on HF (HackRF)
    defaults = {"frequency": 145_800_000}

    @property
    def demod(self):
        # Below 30 MHz SSTV is sent on upper sideband, above it on FM
        return ("usb", 3_000) if self.frequency < 30_000_000 else ("nfm", 16_000)

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.reader = SstvReader(self._done, self._progress)
        self.folder: Path | None = None

    def feed_audio(self, audio: np.ndarray, rate: float) -> None:
        self.reader.feed(audio, rate)

    def _progress(self, picture: Picture) -> None:
        if self.folder is None or not self.update:
            return
        save(picture, self.folder, "live.png")
        self.update({"receiving": {"kind": picture.kind.name, "rows": picture.rows, "height": picture.kind.height,
                                   "file": "live.png", "t": time.time()}})  # fmt: skip

    def _done(self, picture: Picture) -> None:
        self.count += 1
        name = (
            time.strftime("%Y%m%d-%H%M%S", time.localtime(picture.started))
            + f"-{picture.kind.name.replace(' ', '')}.png"
        )
        if self.folder is not None:
            save(picture, self.folder, name)
        complete = picture.rows >= picture.kind.height
        self.publish({"key": picture.kind.name, "kind": picture.kind.name, "file": name, "width": picture.kind.width,
                      "height": picture.kind.height, "rows": picture.rows, "complete": complete})  # fmt: skip
        if self.update:
            self.update({"receiving": None})

    def stop(self) -> None:
        self.reader.cancel()
