"""DAB+: digital radio, a whole ensemble of stations in one 1.5 MHz block, decoded by welle-cli.

In this mode the receiver runs at 2.048 MS/s, DAB's own rate, centred on the block, and its
samples go to welle-cli (as complex floats, through a link named so welle-cli reads that format
from its stdin). welle-cli runs as a small web server inside the app: SDRadio asks it every few
seconds for the ensemble (the stations, their programme type and now-playing text), and the page
plays a station through it: welle-cli encodes the station it is asked for as MP3, which the page
gets through the app. Slides (album art, station logos) come the same way.
"""

import contextlib
import json
import logging
import shutil
import socket
import tempfile
import threading
import urllib.request
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy import signal

from ..dsp import Mixer
from . import Decoder, ValidationError
from .program import Program

logger = logging.getLogger(__name__)

RATE = 2_048_000
POLL_SECONDS = 2.0

# Band III: the DAB blocks and their centre frequencies (MHz)
BLOCKS = {
    "5A": 174.928, "5B": 176.640, "5C": 178.352, "5D": 180.064, "6A": 181.936, "6B": 183.648, "6C": 185.360,
    "6D": 187.072, "7A": 188.928, "7B": 190.640, "7C": 192.352, "7D": 194.064, "8A": 195.936, "8B": 197.648,
    "8C": 199.360, "8D": 201.072, "9A": 202.928, "9B": 204.640, "9C": 206.352, "9D": 208.064, "10A": 209.936,
    "10B": 211.648, "10C": 213.360, "10D": 215.072, "11A": 216.928, "11B": 218.640, "11C": 220.352,
    "11D": 222.064, "12A": 223.936, "12B": 225.648, "12C": 227.360, "12D": 229.072, "13A": 230.784,
    "13B": 232.496, "13C": 234.208, "13D": 235.776, "13E": 237.488, "13F": 239.200,
}  # fmt: skip


def block_of(frequency: float) -> str | None:
    """The DAB block a frequency is the centre of (within 50 kHz), or None."""
    name, mhz = min(BLOCKS.items(), key=lambda kv: abs(kv[1] * 1e6 - frequency))
    return name if abs(mhz * 1e6 - frequency) < 50_000 else None


def _label(value) -> str:
    if isinstance(value, dict):
        return str(value.get("label") or value.get("shortlabel") or "").strip()
    return str(value or "").strip()


def summary(mux: dict) -> dict:
    """What the page shows of welle-cli's mux.json: the ensemble and its stations."""
    services = []
    for s in mux.get("services") or []:
        components = s.get("components") or [{}]
        primary = next((c for c in components if c.get("primary")), components[0])
        sub = primary.get("subchannel") or {}
        if primary.get("transportmode") not in (None, "audio"):
            continue  # data services (EPG, traffic) have nothing to hear
        if not _label(s.get("label")):
            continue  # its name hasn't come in yet: listed once it has
        services.append({
            "sid": s.get("sid"),
            "label": _label(s.get("label")),
            "pty": s.get("ptystring") or "",
            "dls": _label(s.get("dls")),
            "kind": primary.get("ascty") or "",
            "bitrate": sub.get("bitrate"),
            "slide": (s.get("mot") or {}).get("time") or 0,
        })  # fmt: skip
    services.sort(key=lambda s: s["label"].lower())
    demod = mux.get("demodulator") or {}
    return {
        "ensemble": _label((mux.get("ensemble") or {}).get("label")),
        "snr": demod.get("snr"),
        "services": services,
    }


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Dab(Decoder):
    key = "dab"
    name = "DAB+"
    signal = "OFDM"
    decoded_by = "welle-cli"
    input = "iq"
    # DAB's own rate, centred on the block (its 1.536 MHz fills most of the 2.048)
    sample_rate = RATE
    centred = True
    # The page plays a station through welle-cli, not through the radio's audio
    listen = False
    # 12C: the NPO's DAB+ in the Netherlands
    defaults = {"frequency": round(BLOCKS["12C"] * 1e6)}
    demod = ("wfm", 200_000)

    @classmethod
    def validate(cls, value: dict) -> dict:
        clean = super().validate(value)
        if block_of(clean["frequency"]) is None:
            raise ValidationError("DAB+: the frequency must be the centre of a DAB block (5A to 13F)")
        return clean

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.program: Program | None = None
        self.port = 0
        self.workdir: Path | None = None
        self.rate = 0.0
        self.mixer: Mixer | None = None
        self.resample: tuple[int, int] | None = None
        self.mux: dict | None = None
        self.lock = threading.Lock()
        self._stopping = threading.Event()
        self._poller: threading.Thread | None = None
        self._seen: set[str] = set()

    @property
    def block(self) -> str | None:
        return block_of(self.frequency)

    def _start_program(self, rate: float) -> None:
        if self.program:
            self.program.stop()
        self.rate = rate
        self.mixer = Mixer(rate)
        ratio = Fraction(RATE / rate).limit_denominator(200)
        self.resample = None if ratio == 1 else (ratio.numerator, ratio.denominator)
        if self.workdir is None:
            self.workdir = Path(tempfile.mkdtemp(prefix="sdradio-dab-"))
            # welle-cli reads a file, its format from its name: this one is its stdin, complex floats
            (self.workdir / "in.cf32.iq").symlink_to("/dev/stdin")
        self.port = _free_port()
        command = ["welle-cli", "-f", str(self.workdir / "in.cf32.iq"), "-w", str(self.port), "-T"]
        self.program = Program("welle-cli", command, lambda line: None, backlog=20)
        if self._poller is None:
            self._poller = threading.Thread(target=self._poll, name="dab", daemon=True)
            self._poller.start()

    def feed_iq(self, iq: np.ndarray, rate: float, offset: float) -> None:
        if self.program is None or rate != self.rate:
            self._start_program(rate)
        x = iq
        if offset:
            self.mixer.offset = offset
            x = self.mixer(x)
        if self.resample:
            x = signal.resample_poly(x, *self.resample)
        self.program.write(np.asarray(x, dtype=np.complex64).tobytes())
        self.error = self.program.error

    def _poll(self) -> None:
        while not self._stopping.wait(POLL_SECONDS):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/mux.json", timeout=3) as r:
                    mux = summary(json.loads(r.read()))
            except Exception:
                continue  # still starting, or between restarts
            mux["block"] = self.block
            with self.lock:
                self.mux = mux
            self.count = len(mux["services"])
            # Each station once in the history, when it is first found
            for s in mux["services"]:
                if s["sid"] and s["label"] and s["sid"] not in self._seen:
                    self._seen.add(s["sid"])
                    self.publish({"key": s["label"], "sid": s["sid"], "ensemble": mux["ensemble"],
                                  "block": self.block, "pty": s["pty"], "kind": s["kind"],
                                  "bitrate": s["bitrate"]})  # fmt: skip
            if self.update:
                self.update({"dab": mux})

    def url(self, path: str) -> str | None:
        """welle-cli's own address for a station's audio or slide, for the app to pass on."""
        return f"http://127.0.0.1:{self.port}{path}" if self.port and self.program else None

    def live(self) -> dict:
        with self.lock:
            return {"dab": self.mux, "block": self.block}

    def status(self) -> dict:
        out = super().status()
        with self.lock:
            if self.mux:
                out["ensemble"] = self.mux["ensemble"]
        return out

    def stop(self) -> None:
        self._stopping.set()
        if self.program:
            self.program.stop()
            self.program = None
        if self.workdir:
            shutil.rmtree(self.workdir, ignore_errors=True)
            self.workdir = None
        with contextlib.suppress(Exception):
            if self._poller:
                self._poller.join(timeout=3)
