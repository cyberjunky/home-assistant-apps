"""Aircraft: ADS-B on 1090 MHz, decoded by dump1090 from the receiver's own samples.

dump1090 reads 2.4 MS/s, the rate the receiver runs at, so it gets the samples as they come
(16-bit IQ on its stdin), with the receiver centred on 1090 MHz for this mode: the pulses are
2 MHz wide. Every second it writes the aircraft it hears to a JSON file, which is read here:
the page gets them live (with the trail each one leaves on the map), and an aircraft is stored
once, when it is gone, with what was learnt about it.

The registration and type come from the aircraft database dump1090 is packaged with.
"""

import contextlib
import json
import logging
import math
import shutil
import tempfile
import threading
import time
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

import numpy as np
from scipy import signal

from ..dsp import Mixer
from . import Decoder
from .program import Program

logger = logging.getLogger(__name__)

RATE = 2_400_000
# Seconds without a message before an aircraft is gone (and stored)
LOST_AFTER = 60.0
# Points kept of an aircraft's trail
TRAIL = 400
DATABASE = Path("/usr/share/dump1090/html/db")


@lru_cache(maxsize=64)
def _db_file(prefix: str) -> dict:
    try:
        return json.loads((DATABASE / f"{prefix}.json").read_text())
    except (OSError, ValueError):
        return {}


def lookup(icao: str, load=_db_file) -> dict:
    """The registration (r) and type (t) of an aircraft by its ICAO address, or {}.

    The database is split by the first characters of the address: a file holds the addresses
    that start with its name, and lists in "children" the longer names that have their own file.
    """
    icao = icao.upper()
    for length in range(1, len(icao)):
        data = load(icao[:length])
        if not data:
            return {}
        entry = data.get(icao[length:])
        if entry:
            return {k: v for k, v in entry.items() if k in ("r", "t", "desc")}
        if icao[: length + 1] not in data.get("children", []):
            return {}
    return {}


def distance_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(min(1.0, h)))


class Aircraft(Decoder):
    key = "aircraft"
    name = "Aircraft"
    signal = "PPM"
    decoded_by = "dump1090"
    input = "iq"
    defaults = {"frequency": 1_090_000_000}
    # Heard as AM: pulses, a buzz
    demod = ("am", 15_000)
    # dump1090 needs the whole 2 MHz round 1090 MHz: the receiver is centred on it
    centred = True

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.program: Program | None = None
        # dump1090's own list, in a temporary folder of its own (not the mode's data folder)
        self.json_dir: Path | None = None
        self.rate = 0.0
        self.mixer: Mixer | None = None
        self.resample: tuple[int, int] | None = None
        self.planes: dict[str, dict] = {}
        self.lock = threading.Lock()
        self._poller: threading.Thread | None = None
        self._stopping = threading.Event()
        self.messages = 0

    # ---------------------------------------------------------------- samples in

    def _command(self) -> list[str]:
        command = ["dump1090", "--ifile", "-", "--iformat", "SC16", "--quiet", "--write-json", str(self.json_dir),
                   "--write-json-every", "1", "--json-location-accuracy", "2"]  # fmt: skip
        if self.location:
            command += ["--lat", f"{self.location[0]:.5f}", "--lon", f"{self.location[1]:.5f}"]
        return command

    def feed_iq(self, iq: np.ndarray, rate: float, offset: float) -> None:
        if self.program is None or rate != self.rate:
            self._start_program(rate)
        x = iq
        if offset:
            self.mixer.offset = offset
            x = self.mixer(x)
        if self.resample:
            x = signal.resample_poly(x, *self.resample).astype(np.complex64)
        interleaved = np.empty(len(x) * 2, dtype="<i2")
        interleaved[0::2] = np.clip(x.real * 32767, -32768, 32767)
        interleaved[1::2] = np.clip(x.imag * 32767, -32768, 32767)
        self.program.write(interleaved.tobytes())
        self.error = self.program.error

    def _start_program(self, rate: float) -> None:
        if self.program:
            self.program.stop()
        self.rate = rate
        self.mixer = Mixer(rate)
        ratio = Fraction(RATE / rate).limit_denominator(100)
        self.resample = None if ratio == 1 else (ratio.numerator, ratio.denominator)
        if self.json_dir is None:
            self.json_dir = Path(tempfile.mkdtemp(prefix="sdradio-aircraft-"))
        self.program = Program("dump1090", self._command(), lambda line: None)
        if self._poller is None:
            self._poller = threading.Thread(target=self._poll, name="aircraft", daemon=True)
            self._poller.start()

    # ---------------------------------------------------------------- aircraft out

    def _poll(self) -> None:
        while not self._stopping.wait(1.0):
            try:
                data = json.loads((self.json_dir / "aircraft.json").read_text())
            except (OSError, ValueError):
                continue
            try:
                self._update(data)
            except Exception:
                logger.exception("Aircraft: could not read dump1090's list")

    def _update(self, data: dict) -> None:
        now = time.time()
        changed = []
        with self.lock:
            self.messages = int(data.get("messages") or 0)
            for a in data.get("aircraft", []):
                icao = str(a.get("hex", "")).lower()
                if not icao or icao.startswith("~"):
                    continue  # ~: not a real ICAO address (TIS-B)
                seen = now - float(a.get("seen", 0))
                plane = self.planes.get(icao)
                if plane is None:
                    found = lookup(icao)
                    plane = self.planes[icao] = {
                        "icao": icao,
                        "registration": found.get("r"),
                        "type": found.get("t"),
                        "first": seen,
                        "trail": [],
                        "alt_min": None,
                        "alt_max": None,
                    }
                    self.count += 1
                if seen <= plane.get("seen", 0) and plane.get("messages") == a.get("messages"):
                    continue
                plane.update(
                    {
                        "seen": seen,
                        "flight": (a.get("flight") or plane.get("flight") or "").strip() or None,
                        "squawk": a.get("squawk", plane.get("squawk")),
                        "altitude": a.get("alt_baro") if isinstance(a.get("alt_baro"), int) else plane.get("altitude"),
                        "ground": a.get("alt_baro") == "ground",
                        "speed": a.get("gs", plane.get("speed")),
                        "track": a.get("track", plane.get("track")),
                        "climb": a.get("baro_rate", a.get("geom_rate", plane.get("climb"))),
                        "category": a.get("category", plane.get("category")),
                        "emergency": a.get("emergency") if a.get("emergency") not in (None, "none") else None,
                        "rssi": a.get("rssi"),
                        "messages": a.get("messages"),
                    }
                )
                altitude = plane["altitude"]
                if isinstance(altitude, int):
                    plane["alt_min"] = altitude if plane["alt_min"] is None else min(plane["alt_min"], altitude)
                    plane["alt_max"] = altitude if plane["alt_max"] is None else max(plane["alt_max"], altitude)
                if "lat" in a and "lon" in a:
                    point = [round(a["lat"], 5), round(a["lon"], 5), altitude if isinstance(altitude, int) else None]
                    if not plane["trail"] or plane["trail"][-1][:2] != point[:2]:
                        plane["trail"].append(point)
                        del plane["trail"][:-TRAIL]
                    plane["lat"], plane["lon"] = point[0], point[1]
                    if self.location:
                        plane["distance"] = round(distance_km(self.location, (point[0], point[1])), 1)
                changed.append(self._public(plane, trail=False))
            gone = [icao for icao, p in self.planes.items() if now - p.get("seen", now) > LOST_AFTER]
            lost = [self.planes.pop(icao) for icao in gone]
        for plane in lost:
            self._store(plane)
        if self.update and (changed or gone):
            self.update({"aircraft": changed, "gone": gone, "messages": self.messages})

    @staticmethod
    def _public(plane: dict, trail: bool) -> dict:
        out = {k: v for k, v in plane.items() if k != "trail"}
        if trail:
            out["trail"] = list(plane["trail"])
        elif plane["trail"]:
            out["point"] = plane["trail"][-1]
        return out

    def _store(self, plane: dict) -> None:
        """An aircraft gone from view: one line in the history."""
        item = {k: plane.get(k) for k in ("flight", "registration", "type", "squawk", "category", "alt_min",
                                          "alt_max", "first", "seen", "messages")}  # fmt: skip
        item["positions"] = len(plane["trail"])
        if plane["trail"]:
            item["first_position"] = plane["trail"][0][:2]
            item["last_position"] = plane["trail"][-1][:2]
        self.publish({"key": plane["icao"], **item})

    def live(self) -> dict:
        with self.lock:
            return {
                "aircraft": [self._public(p, trail=True) for p in self.planes.values()],
                "messages": self.messages,
                "location": self.location,
            }

    def status(self) -> dict:
        with self.lock:
            return {**super().status(), "now": len(self.planes)}

    def stop(self) -> None:
        self._stopping.set()
        if self.program:
            self.program.stop()
            self.program = None
        with self.lock:
            lost = list(self.planes.values())
            self.planes.clear()
        for plane in lost:
            self._store(plane)
        if self.json_dir:
            shutil.rmtree(self.json_dir, ignore_errors=True)
            self.json_dir = None
        with contextlib.suppress(Exception):
            if self._poller:
                self._poller.join(timeout=2)
