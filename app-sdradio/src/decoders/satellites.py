"""Satellites: passes over Home Assistant's home, followed with the Doppler shift taken out.

The orbits come from Celestrak (the public two-line elements, fetched once a day and kept), and
ephem works out where each satellite is and when it passes. During a pass the radio is tuned to
the satellite's frequency as it is heard here: higher while it comes closer, lower while it goes
away (by up to 3.5 kHz on 2 m, 10 kHz on 70 cm). What a pass brings depends on the satellite:

- the ISS on 145.800 MHz: its voice and, during SSTV events, pictures (the SSTV decoder);
- the ISS APRS digipeater on 145.825 MHz: packets (multimon-ng, as the APRS mode);
- SO-50, an FM repeater on 436.795 MHz: to listen to;
- Meteor-M2-3 and M2-4 (137.1 / 137.9 MHz, their frequencies change now and then): the pass is
  recorded as IQ samples (8-bit, 200 kHz) to decode into weather pictures with SatDump.

Every pass is kept, with what it brought.
"""

import contextlib
import logging
import math
import threading
import time
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ..dsp import Decimator, Mixer
from . import Decoder, ValidationError
from .aprs import Aprs
from .sstv import Picture, SstvReader, save

logger = logging.getLogger(__name__)

C = 299_792_458.0
TLE_URL = "https://celestrak.org/NORAD/elements/gp.php?CATNR={}&FORMAT=TLE"
TLE_MAX_AGE = 24 * 3600
RECORD_RATE = 200_000
# How often the position (and the Doppler correction) is worked out, and the page told
STEP_SECONDS = 1.0

# id, name, NORAD number, frequency, demodulation and filter, what the pass brings
SATELLITES = {
    "iss": {"name": "ISS", "norad": 25544, "frequency": 145_800_000, "demod": ("nfm", 16_000), "brings": "sstv"},
    "iss-aprs": {"name": "ISS APRS", "norad": 25544, "frequency": 145_825_000, "demod": ("nfm", 16_000),
                 "brings": "aprs"},
    "so50": {"name": "SO-50", "norad": 27607, "frequency": 436_795_000, "demod": ("nfm", 16_000), "brings": "voice"},
    "meteor-m2-3": {"name": "Meteor-M2-3", "norad": 57166, "frequency": 137_100_000, "demod": ("wfm", 120_000),
                    "brings": "recording"},
    "meteor-m2-4": {"name": "Meteor-M2-4", "norad": 59051, "frequency": 137_900_000, "demod": ("wfm", 120_000),
                    "brings": "recording"},
}  # fmt: skip
DEFAULT_ON = ["iss", "so50"]


def doppler(frequency: float, range_rate: float) -> float:
    """The frequency heard: range_rate in m/s, positive while the satellite goes away."""
    return frequency * (1 - range_rate / C)


class Orbits:
    """The two-line elements, from Celestrak, kept in the mode's folder."""

    def __init__(self, folder: Path | None, fetch=None):
        self.folder = folder
        self.fetch = fetch or self._download
        self.tles: dict[int, tuple[str, str, str]] = {}
        self.error: str | None = None

    @staticmethod
    def _download(norad: int) -> str:
        request = urllib.request.Request(TLE_URL.format(norad), headers={"User-Agent": "SDRadio"})
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.read().decode("ascii", "replace")

    def load(self, norads: list[int]) -> None:
        for norad in sorted(set(norads)):
            path = self.folder / f"{norad}.tle" if self.folder else None
            text = None
            if path and path.is_file() and time.time() - path.stat().st_mtime < TLE_MAX_AGE:
                text = path.read_text()
            if text is None:
                try:
                    text = self.fetch(norad)
                    lines = [line for line in text.splitlines() if line.strip()]
                    if len(lines) < 3 or not lines[1].startswith("1 ") or not lines[2].startswith("2 "):
                        raise ValueError(f"no orbit for {norad}")
                    if path:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text(text)
                    self.error = None
                except Exception as e:
                    # Older ones do for a while (days): better than none
                    self.error = f"Could not fetch the orbits from Celestrak: {e}"
                    text = path.read_text() if path and path.is_file() else None
            if text:
                lines = [line.strip() for line in text.splitlines() if line.strip()]
                if len(lines) >= 3:
                    self.tles[norad] = (lines[0], lines[1], lines[2])


class Tracker:
    """Where the satellites are, and when they pass, for one place on earth."""

    def __init__(self, location: tuple[float, float], tles: dict[int, tuple[str, str, str]], min_elevation: float):
        import ephem

        self.ephem = ephem
        self.location = location
        self.tles = tles
        self.min_elevation = min_elevation

    def observer(self, when: float):
        obs = self.ephem.Observer()
        obs.lat, obs.lon = str(self.location[0]), str(self.location[1])
        obs.elevation = 0
        obs.pressure = 0  # no refraction: the geometric horizon
        obs.date = self.ephem.Date(datetime.fromtimestamp(when, UTC).replace(tzinfo=None))
        return obs

    def body(self, norad: int):
        name, l1, l2 = self.tles[norad]
        return self.ephem.readtle(name, l1, l2)

    def where(self, norad: int, when: float) -> dict:
        sat = self.body(norad)
        sat.compute(self.observer(when))
        return {
            "elevation": round(math.degrees(sat.alt), 1),
            "azimuth": round(math.degrees(sat.az), 1),
            "range_rate": float(sat.range_velocity),
            "range_km": round(sat.range / 1000, 1),
            "lat": round(math.degrees(sat.sublat), 3),
            "lon": round(math.degrees(sat.sublong), 3),
            "height_km": round(sat.elevation / 1000, 1),
        }

    def track(self, norad: int, start: float, minutes: int = 95, step: float = 60) -> list[list[float]]:
        """The ground track from `start`: [lat, lon] every `step` seconds."""
        sat = self.body(norad)
        out = []
        for i in range(int(minutes * 60 / step) + 1):
            sat.compute(self.ephem.Date(datetime.fromtimestamp(start + i * step, UTC).replace(tzinfo=None)))
            out.append([round(math.degrees(sat.sublat), 2), round(math.degrees(sat.sublong), 2)])
        return out

    def passes(self, norad: int, start: float, hours: float = 24) -> list[dict]:
        """The passes from `start` that rise above the minimum elevation."""
        sat = self.body(norad)
        obs = self.observer(start)
        end = start + hours * 3600
        out = []
        for _ in range(40):
            try:
                rise, rise_az, top, top_alt, set_, set_az = obs.next_pass(sat)
            except (ValueError, TypeError):
                break
            if rise is None or set_ is None:
                break
            aos = self._ts(rise)
            los = self._ts(set_)
            if aos > end:
                break
            if math.degrees(top_alt) >= self.min_elevation:
                out.append({"aos": aos, "los": los, "top": self._ts(top), "max_elevation": round(math.degrees(top_alt), 1),
                            "aos_azimuth": round(math.degrees(rise_az)), "los_azimuth": round(math.degrees(set_az))})  # fmt: skip
            obs.date = set_ + self.ephem.minute
        # A pass already going on at `start`
        here = self.where(norad, start)
        if here["elevation"] > 0 and not (out and out[0]["aos"] <= start):
            obs = self.observer(start - 20 * 60)
            with contextlib.suppress(ValueError, TypeError):
                rise, rise_az, top, top_alt, set_, set_az = obs.next_pass(sat)
                if self._ts(rise) <= start <= self._ts(set_) and math.degrees(top_alt) >= self.min_elevation:
                    out.insert(0, {"aos": self._ts(rise), "los": self._ts(set_), "top": self._ts(top),
                                   "max_elevation": round(math.degrees(top_alt), 1), "aos_azimuth": round(math.degrees(rise_az)),
                                   "los_azimuth": round(math.degrees(set_az))})  # fmt: skip
        return out

    def _ts(self, d) -> float:
        return self.ephem.Date(d).datetime().replace(tzinfo=UTC).timestamp()


class Recorder:
    """A pass recorded as 8-bit IQ at 200 kHz round the satellite (the .cs8 SatDump reads)."""

    def __init__(self, path: Path, rate: float):
        self.path = path
        self.mixer = Mixer(rate)
        self.decimate = Decimator(max(1, round(rate / RECORD_RATE)), 100_000 / (rate / 2), k=6)
        self.file = path.open("wb")
        self.bytes = 0

    def write(self, iq: np.ndarray, offset: float) -> None:
        self.mixer.offset = offset
        x = self.decimate(self.mixer(iq))
        out = np.empty(len(x) * 2, dtype=np.int8)
        out[0::2] = np.clip(x.real * 127, -127, 127)
        out[1::2] = np.clip(x.imag * 127, -127, 127)
        self.file.write(out.tobytes())
        self.bytes += len(out)

    def close(self) -> None:
        self.file.close()


class Satellites(Decoder):
    key = "satellites"
    name = "Satellites"
    signal = "Doppler"
    input = "both"
    defaults = {
        "frequency": 145_800_000,
        "follow": DEFAULT_ON,
        "min_elevation": 10,
        "frequencies": {},  # a satellite's own frequency instead of the default (Hz)
        "record": False,  # Meteor passes as IQ recordings (about 300 MB a pass)
        "keep_recordings": 3,
    }
    demod = ("nfm", 16_000)

    @classmethod
    def validate(cls, value: dict) -> dict:
        clean = super().validate(value)
        follow = value.get("follow", DEFAULT_ON)
        if not isinstance(follow, list) or any(f not in SATELLITES for f in follow):
            raise ValidationError(f"Satellites: follow some of {', '.join(SATELLITES)}")
        clean["follow"] = [s for s in SATELLITES if s in follow]
        try:
            clean["min_elevation"] = max(0, min(60, int(value.get("min_elevation", 10))))
            clean["keep_recordings"] = max(1, min(50, int(value.get("keep_recordings", 3))))
            freqs = value.get("frequencies") or {}
            clean["frequencies"] = {k: round(float(v)) for k, v in freqs.items() if k in SATELLITES and v}
        except (TypeError, ValueError):
            raise ValidationError("Satellites: elevation, recordings and frequencies must be numbers") from None
        if any(not 30_000_000 <= f <= 6_000_000_000 for f in clean["frequencies"].values()):
            raise ValidationError("Satellites: a frequency must be between 30 and 6000 MHz")
        clean["record"] = bool(value.get("record", False))
        return clean

    @classmethod
    def available(cls, frequency_range) -> bool:
        return True  # each satellite is checked on its own (the RTL-SDR hears them all)

    def __init__(self, settings, publish, update=None, fetch=None, clock=time.time):
        super().__init__(settings, publish, update)
        self.clock = clock
        self.fetch = fetch
        self.orbits: Orbits | None = None
        self.tracker: Tracker | None = None
        self.passes: list[dict] = []
        self.lock = threading.Lock()
        self.current: dict | None = None  # the pass being followed
        self.retune = None  # set by the radio: (frequency, mode, bandwidth)
        self._next_step = 0.0
        self._next_plan = 0.0
        self._loading = False
        self.sstv: SstvReader | None = None
        self.aprs: Aprs | None = None
        self.recorder: Recorder | None = None
        self.recording_path: Path | None = None
        self.positions: list[dict] = []

    # ---------------------------------------------------------------- set up

    def satellite(self, sid: str) -> dict:
        sat = dict(SATELLITES[sid], id=sid)
        sat["frequency"] = int(self.settings.get("frequencies", {}).get(sid, sat["frequency"]))
        return sat

    def following(self) -> list[dict]:
        return [self.satellite(s) for s in self.settings.get("follow", DEFAULT_ON) if s in SATELLITES]

    def start(self) -> None:
        if not self.location:
            self.error = "Satellites need Home Assistant's home location (Settings → System → General)"
            return
        threading.Thread(target=self._load, name="satellites", daemon=True).start()

    def _load(self) -> None:
        self._loading = True
        try:
            self.orbits = Orbits(self.folder, self.fetch)
            self.orbits.load([s["norad"] for s in self.following()])
            self.error = self.orbits.error
            if self.orbits.tles:
                self.tracker = Tracker(self.location, self.orbits.tles, float(self.settings.get("min_elevation", 10)))
                self._plan()
        except Exception as e:
            logger.exception("Satellites: could not work out the passes")
            self.error = f"Could not work out the passes: {e}"
        finally:
            self._loading = False

    def _plan(self) -> None:
        """The passes of the next day, every satellite followed, in time order."""
        now = self.clock()
        passes = []
        for sat in self.following():
            if sat["norad"] not in self.tracker.tles:
                continue
            for p in self.tracker.passes(sat["norad"], now):
                passes.append({**p, "id": sat["id"], "name": sat["name"], "frequency": sat["frequency"],
                               "brings": sat["brings"]})  # fmt: skip
        passes.sort(key=lambda p: p["aos"])
        with self.lock:
            self.passes = passes
        self._next_plan = now + 3600

    # ---------------------------------------------------------------- following a pass

    def _step(self) -> None:
        now = self.clock()
        if self.tracker is None:
            return
        if now >= self._next_plan and not self._loading:
            threading.Thread(target=self._plan, daemon=True).start()
            self._next_plan = now + 3600
        with self.lock:
            passes = list(self.passes)
        # The pass going on now; of two at once, the higher one
        going = [p for p in passes if p["aos"] - 30 <= now <= p["los"] + 10]
        going.sort(key=lambda p: -p["max_elevation"])
        chosen = going[0] if going else None
        if self.current and (
            chosen is None or chosen["id"] != self.current["id"] or chosen["aos"] != self.current["aos"]
        ):
            self._end_pass(now)
        if chosen and self.current is None:
            self._begin_pass(chosen, now)
        positions = []
        for sat in self.following():
            if sat["norad"] in self.tracker.tles:
                positions.append({"id": sat["id"], "name": sat["name"], **self.tracker.where(sat["norad"], now)})
        self.positions = positions
        if self.current:
            sat = self.satellite(self.current["id"])
            here = next((p for p in positions if p["id"] == sat["id"]), None) or self.tracker.where(sat["norad"], now)
            heard = doppler(sat["frequency"], here["range_rate"])
            self.current.update({"elevation": here["elevation"], "azimuth": here["azimuth"],
                                 "doppler": round(heard - sat["frequency"]), "heard": round(heard)})  # fmt: skip
            self.current["max_seen"] = max(self.current.get("max_seen", -90), here["elevation"])
            if self.retune:
                self.retune(round(heard / 10) * 10, *sat["demod"])
        if self.update:
            with self.lock:
                upcoming = [p for p in self.passes if p["los"] >= now][:12]
            self.update({"satellites": positions, "pass": self.current, "passes": upcoming, "now": now})

    def _begin_pass(self, p: dict, now: float) -> None:
        sat = self.satellite(p["id"])
        self.current = {**p, "started": now, "pictures": [], "packets": 0, "recording": None}
        logger.info(f"Satellites: {sat['name']} rises, up to {p['max_elevation']}°")
        if sat["brings"] == "sstv":
            self.sstv = SstvReader(self._picture)
        elif sat["brings"] == "aprs":
            self.aprs = Aprs({}, self._packet)
        elif sat["brings"] == "recording" and self.settings.get("record") and self.folder:
            self.folder.mkdir(parents=True, exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M", time.localtime(now)) + f"-{sat['id']}-{RECORD_RATE // 1000}k.cs8"
            # Opened with the first samples, at the receiver's rate
            self.recording_path = self.folder / name
            self.current["recording"] = name
            self._prune_recordings()

    def _end_pass(self, now: float) -> None:
        p, self.current = self.current, None
        if self.sstv:
            self.sstv.cancel()
            self.sstv = None
        if self.aprs:
            self.aprs.stop()
            self.aprs = None
        size = 0
        self.recording_path = None
        if self.recorder:
            self.recorder.close()
            size = self.recorder.bytes
            self.recorder = None
        self.count += 1
        item = {k: p.get(k) for k in ("id", "name", "aos", "los", "max_elevation", "frequency", "brings", "pictures",
                                      "packets", "recording")}  # fmt: skip
        item["max_seen"] = p.get("max_seen")
        item["recording_mb"] = round(size / 1e6, 1) if size else None
        item["ended"] = now
        self.publish({"key": p["name"], "type": "pass", **item})

    def _picture(self, picture: Picture) -> None:
        if not self.folder or not self.current:
            return
        name = (
            time.strftime("%Y%m%d-%H%M%S", time.localtime(picture.started))
            + f"-{picture.kind.name.replace(' ', '')}.png"
        )
        save(picture, self.folder, name)
        self.current["pictures"].append(name)
        self.publish({"key": self.current["name"], "type": "picture", "file": name, "kind": picture.kind.name,
                      "rows": picture.rows, "height": picture.kind.height})  # fmt: skip

    def _packet(self, packet: dict) -> None:
        if self.current:
            self.current["packets"] += 1
        self.publish({**packet, "type": "packet", "via": "ISS"})

    def _prune_recordings(self) -> None:
        keep = int(self.settings.get("keep_recordings", 3))
        recordings = sorted(self.folder.glob("*.cs8"), key=lambda p: p.stat().st_mtime)
        for old in recordings[:-keep] if len(recordings) > keep else []:
            old.unlink(missing_ok=True)

    # ---------------------------------------------------------------- samples in

    def feed_audio(self, audio: np.ndarray, rate: float) -> None:
        now = self.clock()
        if now >= self._next_step:
            self._next_step = now + STEP_SECONDS
            self._step()
        if self.sstv:
            self.sstv.feed(audio, rate)
        if self.aprs:
            self.aprs.feed_audio(audio, rate)
            if self.aprs.error:
                self.error = self.aprs.error

    def feed_iq(self, iq: np.ndarray, rate: float, offset: float) -> None:
        if self.recording_path is not None and self.recorder is None:
            self.recorder = Recorder(self.recording_path, rate)
        if self.recorder:
            self.recorder.write(iq, offset)

    def live(self) -> dict:
        with self.lock:
            now = self.clock()
            upcoming = [p for p in self.passes if p["los"] >= now][:12]
        tracks = {}
        if self.tracker:
            for sat in self.following():
                if sat["norad"] in self.tracker.tles:
                    tracks[sat["id"]] = self.tracker.track(sat["norad"], now)
        return {"satellites": self.positions, "pass": self.current, "passes": upcoming, "tracks": tracks,
                "location": self.location, "now": now}  # fmt: skip

    def status(self) -> dict:
        return {**super().status(), "now": 1 if self.current else 0}

    def stop(self) -> None:
        if self.current:
            self._end_pass(self.clock())
