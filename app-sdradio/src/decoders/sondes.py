"""Radiosondes: weather balloons, followed from the launch to where they come down.

Weather services send up a radiosonde twice a day (in the Netherlands the KNMI from De Bilt, a
Vaisala RS41), on 400 to 406 MHz. It reports its GPS position, altitude, temperature, humidity
and pressure every second, up to some 30 km where the balloon bursts, and on the way down.

The radio's FM audio goes to the decoders of rs1729's RS project (as radiosonde_auto_rx uses
them), one for each kind: RS41, DFM, M10 and M20, side by side; the one that recognises the
signal writes a JSON line a frame. Each sonde is on the map with its track, and goes into the
history once it is gone, with how high it got (where the balloon burst) and where it came down.
"""

import json
import threading
import time

import numpy as np

from . import Decoder
from .aircraft import distance_km
from .program import Program

# The decoders: rs1729's demodulators for the sondes in use in Europe
DECODERS = {"RS41": ["rs41mod", "--ptu", "--json"], "DFM": ["dfm09mod", "--ptu", "--json"],
            "M10": ["m10mod", "--ptu", "--json"], "M20": ["mXXmod", "--ptu", "--json"]}  # fmt: skip
# A sonde not heard for this long is gone (and stored): long enough to wait out a short fade
LOST_AFTER = 10 * 60.0
TRAIL = 3000


def parse(line: str) -> dict | None:
    """A decoder's JSON line as a sonde's frame, or None."""
    if not line.startswith("{"):
        return None
    try:
        frame = json.loads(line)
    except ValueError:
        return None
    if not isinstance(frame, dict) or not frame.get("id") or "lat" not in frame or "lon" not in frame:
        return None
    lat, lon = float(frame["lat"]), float(frame["lon"])
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or (lat == 0 and lon == 0):
        return None
    out = {
        "id": str(frame["id"]),
        "type": frame.get("subtype") or frame.get("type") or "",
        "lat": round(lat, 5),
        "lon": round(lon, 5),
        "altitude": round(float(frame.get("alt", 0))),
        "climb": round(float(frame.get("vel_v", 0)), 1),
        "speed": round(float(frame.get("vel_h", 0)) * 3.6, 1),  # m/s to km/h
        "heading": round(float(frame.get("heading", 0))),
        "frame": frame.get("frame"),
        "time": frame.get("datetime"),
    }
    for field, name in (("temp", "temperature"), ("humidity", "humidity"), ("pressure", "pressure"),
                        ("batt", "battery"), ("sats", "satellites")):  # fmt: skip
        value = frame.get(field)
        # The decoders write -273 (or nothing) when a reading is missing
        if isinstance(value, int | float) and value > -270:
            out[name] = round(float(value), 1)
    return out


class Sondes(Decoder):
    key = "sondes"
    name = "Radiosondes"
    signal = "GFSK"
    decoded_by = "rs1729's RS"
    input = "audio"
    # Data, not speech: decoded from the audio, but not sent to the page
    audible = False
    # KNMI's sondes from De Bilt are on 400-406 MHz; the frequency changes from launch to launch
    defaults = {"frequency": 403_000_000}
    demod = ("nfm", 16_000)

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.programs: dict[str, Program] = {}
        self.rate = 0.0
        self.sondes: dict[str, dict] = {}
        self.lock = threading.Lock()

    def _command(self, decoder: list[str], rate: float) -> list[str]:
        # The audio as WAV (the decoders read its header for the rate) on their stdin
        sox = f"sox -q -t raw -e signed-integer -b 16 -c 1 -r {int(rate)} - -t wav -"
        return ["sh", "-c", f"{sox} | {' '.join(decoder)}"]

    def feed_audio(self, audio: np.ndarray, rate: float) -> None:
        if not self.programs or rate != self.rate:
            self.stop_programs()
            self.rate = rate
            for kind, decoder in DECODERS.items():
                self.programs[kind] = Program(decoder[0], self._command(decoder, rate), self._line, backlog=20)
        pcm = (np.clip(audio, -1.0, 1.0) * 32000).astype("<i2").tobytes()
        errors = []
        for program in self.programs.values():
            program.write(pcm)
            if program.error:
                errors.append(program.error)
        self.error = errors[0] if errors else None
        self._expire()

    def _line(self, line: str) -> None:
        frame = parse(line)
        if frame is None:
            return
        now = time.time()
        with self.lock:
            sonde = self.sondes.get(frame["id"])
            if sonde is None:
                sonde = self.sondes[frame["id"]] = {"id": frame["id"], "first": now, "trail": [], "frames": 0,
                                                    "max_altitude": frame["altitude"]}  # fmt: skip
                self.count += 1
            sonde.update(frame)
            sonde["seen"] = now
            sonde["frames"] += 1
            sonde["max_altitude"] = max(sonde["max_altitude"], frame["altitude"])
            point = [frame["lat"], frame["lon"], frame["altitude"]]
            if not sonde["trail"] or sonde["trail"][-1][:2] != point[:2]:
                sonde["trail"].append(point)
                del sonde["trail"][:-TRAIL]
            if self.location:
                sonde["distance"] = round(distance_km(self.location, (frame["lat"], frame["lon"])), 1)
            public = self._public(sonde, trail=False)
        if self.update:
            self.update({"sondes": [public], "gone": []})

    def _expire(self) -> None:
        now = time.time()
        with self.lock:
            gone = [i for i, s in self.sondes.items() if now - s["seen"] > LOST_AFTER]
            lost = [self.sondes.pop(i) for i in gone]
        for sonde in lost:
            self._store(sonde)
        if gone and self.update:
            self.update({"sondes": [], "gone": gone})

    @staticmethod
    def _public(sonde: dict, trail: bool) -> dict:
        out = {k: v for k, v in sonde.items() if k != "trail"}
        if trail:
            out["trail"] = list(sonde["trail"])
        elif sonde["trail"]:
            out["point"] = sonde["trail"][-1]
        return out

    def _store(self, sonde: dict) -> None:
        """A sonde gone: one line in the history, with how high it got and where it was last."""
        item = {k: sonde.get(k) for k in ("type", "first", "seen", "frames", "max_altitude", "altitude")}
        item["last_position"] = sonde["trail"][-1][:2] if sonde["trail"] else None
        item["launch_position"] = sonde["trail"][0][:2] if sonde["trail"] else None
        self.publish({"key": sonde["id"], **item})

    def live(self) -> dict:
        with self.lock:
            return {"sondes": [self._public(s, trail=True) for s in self.sondes.values()], "location": self.location}

    def status(self) -> dict:
        with self.lock:
            return {**super().status(), "now": len(self.sondes)}

    def stop_programs(self) -> None:
        for program in self.programs.values():
            program.stop()
        self.programs = {}

    def stop(self) -> None:
        self.stop_programs()
        with self.lock:
            lost = list(self.sondes.values())
            self.sondes.clear()
        for sonde in lost:
            self._store(sonde)
