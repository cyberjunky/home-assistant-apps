"""Video: analog video, as FPV drones and wireless cameras send it, in black and white.

HackRF only: analog video is FM some 16 MHz wide, so the HackRF runs at 20 MS/s in this mode
(the RTL-SDR takes in 2.4 MHz). Not every frame is decoded, a few a second: one block of
samples in a few (each 50 ms, two fields and a half) is FM-demodulated into composite video at
10 MS/s, and its line syncs (4.7 µs pulses at the lowest level, every 64 µs) and the field sync
(the broad pulses) are found. The polarity (some transmitters send the video upside down), and
PAL or NTSC from the line period, are worked out from the signal itself. Each line's black level
is its back porch. Colour (the PAL or NTSC subcarrier) is left out.

Only for video that is meant to be seen: FPV drones (yours, or at a race), your own cameras.
Watching other people's camera feeds is not allowed.
"""

import shutil
import time

import numpy as np

from .. import png
from . import Decoder

RATE = 20_000_000
VIDEO_RATE = 10_000_000
US = VIDEO_RATE / 1e6  # samples per microsecond
WIDTH = 384
FPS = 4


def demodulate(iq: np.ndarray) -> np.ndarray:
    """FM: the phase step between samples, smoothed to 5 MHz and taken to 10 MS/s."""
    d = np.angle(iq[1:] * np.conj(iq[:-1])).astype(np.float32)
    n = len(d) // 2 * 2
    return (d[:n:2] + d[1:n:2]) / 2


def _runs(mask: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Starts and lengths of the runs of True."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return starts, ends - starts


def frame(video: np.ndarray) -> dict | None:
    """A field of the composite video as a picture, or None when there are no syncs."""
    # Smoothed over a microsecond for finding the syncs: FM noise comes as clicks, which would
    # break a sync pulse (4.7 µs) up
    width = int(US)
    csum = np.concatenate([[0], np.cumsum(video, dtype=np.float64)])
    # Centred: sample i is the mean round raw sample i
    video = np.concatenate([np.full(width // 2, video[0]), (csum[width:] - csum[:-width]) / width,
                            np.full(width - width // 2 - 1, video[-1])]).astype(np.float32)  # fmt: skip
    lo, hi = np.percentile(video, [0.5, 99.5])
    if hi - lo < 1e-3:
        return None
    best = None
    # Sync is the lowest level, or the highest when the video is sent upside down: try both
    for polarity in (1, -1):
        u = video * polarity
        lo_u, hi_u = (lo, hi) if polarity == 1 else (-hi, -lo)
        starts, lengths = _runs(u < lo_u + 0.2 * (hi_u - lo_u))
        hsync = starts[(lengths > 3.5 * US) & (lengths < 6.5 * US)]
        if len(hsync) < 50:
            continue
        gaps = np.diff(hsync)
        line = gaps[(gaps > 60 * US) & (gaps < 66 * US)]
        if len(line) < 40:
            continue
        if best is None or len(line) > best[3]:
            broad = starts[(lengths > 20 * US) & (lengths < 31 * US)]
            best = (polarity, u, hsync, len(line), float(np.mean(line)), broad, lo_u, hi_u)
    if best is None:
        return None
    polarity, u, hsync, _, period, broad, lo_u, hi_u = best
    # The picture from the smoothed video too: FM clicks don't average out within a pixel
    smooth = u
    pal = period > 63.8 * US
    active = 288 if pal else 240
    first = 23 if pal else 21
    # Line 1 of a field starts with its broad pulses; the picture starts at line 23 (PAL)
    start = None
    if len(broad):
        usable = broad[broad + (first + active) * period < len(u)]
        if len(usable):
            start = float(usable[0])
    locked = start is not None
    if start is None:
        start = float(hsync[0] - (first - 1) * period)
    image = np.zeros((active, WIDTH), dtype=np.uint8)
    white = hi_u
    found = 0
    # Each line from the sync found before it: the period is not a whole number of samples
    reference, reference_line = start, 0.0
    for row in range(active):
        expected = reference + (first - 1 + row - reference_line) * period
        # The sync nearest to where it should be (or where it should be, if there is none)
        i = np.searchsorted(hsync, expected - period / 2)
        candidates = [hsync[j] for j in (i, i + 1) if j < len(hsync) and abs(hsync[j] - expected) < 2 * US]
        if candidates:
            h = float(min(candidates, key=lambda c: abs(c - expected)))
            found += 1
            reference, reference_line = h, first - 1 + row
        else:
            h = expected
        a = int(h + 10.4 * US)
        b = int(a + 52 * US)
        if b + 1 >= len(u) or a < int(6 * US):
            continue
        black = float(np.mean(smooth[int(h + 6 * US) : int(h + 9.5 * US)]))
        edges = np.linspace(a, b, WIDTH + 1).astype(int)
        csum = np.concatenate([[0], np.cumsum(u[a : b + 1], dtype=np.float64)])
        idx = edges - a
        means = (csum[idx[1:]] - csum[idx[:-1]]) / np.maximum(idx[1:] - idx[:-1], 1)
        scale = max(white - black, 1e-6)
        image[row] = np.clip((means - black) / scale * 255, 0, 255).astype(np.uint8)
    return {"image": image, "standard": "PAL" if pal else "NTSC", "inverted": polarity == -1,
            "locked": locked, "lines": found, "height": active}  # fmt: skip


class Video(Decoder):
    key = "video"
    name = "Video"
    signal = "FM video"
    decoded_by = "SDRadio"
    input = "iq"
    receivers = ("hackrf",)
    receivers_why = "analog video is 16 MHz wide; the HackRF takes in 20 MHz at once, the RTL-SDR 2.4"
    sample_rate = RATE
    listen = False
    centred = True
    # Fatshark/ImmersionRC F4, a common FPV channel; Raceband and 1.2 GHz cameras under Modes
    defaults = {"frequency": 5_800_000_000, "fps": FPS}
    demod = ("wfm", 200_000)

    def __init__(self, settings, publish, update=None, clock=time.monotonic):
        super().__init__(settings, publish, update)
        self.clock = clock
        self._next = 0.0
        self.last: dict | None = None
        self.frames = 0

    def feed_iq(self, iq: np.ndarray, rate: float, offset: float) -> None:
        now = self.clock()
        if now < self._next:
            return
        self._next = now + 1 / max(1, int(self.settings.get("fps", FPS)))
        if rate < 8e6:
            self.error = "The receiver is not running fast enough for video"
            return
        x = iq
        if offset:
            x = x * np.exp(-2j * np.pi * offset * np.arange(len(x)) / rate).astype(np.complex64)
        video = demodulate(x)
        if rate != RATE:
            # The pixel timing assumes 10 MS/s after demodulation: resample to it
            n = int(len(video) * VIDEO_RATE / (rate / 2))
            video = np.interp(np.linspace(0, len(video) - 1, n), np.arange(len(video)), video).astype(np.float32)
        result = frame(video)
        if result is None:
            self.last = None
            if self.update:
                self.update({"frame": None})
            return
        self.frames += 1
        self.last = result
        if self.folder is not None:
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / "live.png").write_bytes(png.encode(result["image"]))
        if self.update:
            self.update({"frame": {"file": "live.png", "t": time.time(), "standard": result["standard"],
                                   "inverted": result["inverted"], "locked": result["locked"],
                                   "width": WIDTH, "height": result["height"]}})  # fmt: skip

    def snapshot(self) -> dict | None:
        """The picture now, kept."""
        if self.last is None or self.folder is None or not (self.folder / "live.png").is_file():
            return None
        name = time.strftime("%Y%m%d-%H%M%S") + ".png"
        shutil.copyfile(self.folder / "live.png", self.folder / name)
        self.count += 1
        item = {"key": f"{self.frequency / 1e6:g} MHz", "file": name, "standard": self.last["standard"]}
        self.publish(item)
        return item

    def status(self) -> dict:
        out = {**super().status(), "frames": self.frames}
        if self.last:
            out["standard"] = self.last["standard"]
        return out
