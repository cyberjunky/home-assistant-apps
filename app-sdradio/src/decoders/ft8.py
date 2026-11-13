"""FT8 and FT4: the radio amateurs' weak-signal digital modes, the busiest on HF and 6 m.

Stations send in slots of 15 seconds (FT8) or 7.5 seconds (FT4) on the UTC clock: a callsign, a
grid square or a signal report, readable far below the noise. The radio's upper-sideband audio is
cut into those slots and each goes to ft8_lib's decoder (decode_ft8) as a 12 kHz WAV, in a thread
of its own. Every decode is kept; the stations that give their grid square (in a CQ or a reply)
are on the map, where they are within some 100 km.
"""

import re
import subprocess
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
from scipy import signal

from . import Decoder, ValidationError
from .aircraft import distance_km

RATE = 12_000
SLOTS = {"FT8": 15.0, "FT4": 7.5}
# A station not heard for this long leaves the map
LOST_AFTER = 30 * 60.0
_LINE = re.compile(r"^\d{6}\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)\s+(\d+)\s+~\s+(.+)$")
_CALL = re.compile(r"^(?:[A-Z0-9]{1,3}/)?[A-Z0-9]{1,3}\d[A-Z0-9]{0,4}(?:/[A-Z0-9]{1,4})?$")
_GRID = re.compile(r"^[A-R]{2}\d{2}$")


def grid_position(grid: str) -> tuple[float, float]:
    """A Maidenhead grid square (JO22) as the latitude and longitude of its middle."""
    lon = (ord(grid[0]) - 65) * 20 - 180 + int(grid[2]) * 2 + 1
    lat = (ord(grid[1]) - 65) * 10 - 90 + int(grid[3]) + 0.5
    return lat, lon


def parse(line: str) -> dict | None:
    """A decode_ft8 line (000000 +13.0 +1.60 1291 ~  CQ R7IW LN35) as a message, or None."""
    m = _LINE.match(line.strip())
    if not m:
        return None
    snr, dt, freq, text = m.groups()
    text = " ".join(text.split())
    words = text.split()
    message = {"snr": float(snr), "dt": float(dt), "offset": int(freq), "text": text, "call": None, "grid": None}
    if not words:
        return message
    if words[0] == "CQ":
        rest = words[1:]
        # CQ DX / CQ POTA / CQ EU …: the call is the first word that looks like one
        calls = [w for w in rest if _CALL.match(w)]
        if calls:
            message["call"] = calls[0]
            message["to"] = "CQ"
    elif len(words) >= 2 and _CALL.match(words[1].strip("<>")):
        message["call"] = words[1].strip("<>")
        message["to"] = words[0].strip("<>")
    last = words[-1]
    if _GRID.match(last) and last != "RR73":
        message["grid"] = last
    return message


class Ft8(Decoder):
    key = "ft8"
    name = "FT8"
    signal = "8-FSK"
    decoded_by = "ft8_lib"
    input = "audio"
    # Data, not speech: decoded from the audio, but not sent to the page
    audible = False
    # 10 m FT8: open by day in a solar maximum, and within the RTL-SDR's range
    defaults = {"frequency": 28_074_000, "protocol": "FT8"}
    demod = ("usb", 3_000)

    @classmethod
    def validate(cls, value: dict) -> dict:
        clean = super().validate(value)
        protocol = value.get("protocol", "FT8")
        if protocol not in SLOTS:
            raise ValidationError("FT8: the protocol is FT8 or FT4")
        clean["protocol"] = protocol
        return clean

    def __init__(self, settings, publish, update=None, clock=time.time):
        super().__init__(settings, publish, update)
        self.clock = clock
        self.slot = SLOTS[self.settings.get("protocol", "FT8")]
        self.rate = 0.0
        self.buffer = np.zeros(0, dtype=np.float32)
        self.buffer_start = 0.0  # UTC seconds of the buffer's first sample
        self.stations: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.workdir = Path(tempfile.mkdtemp(prefix="sdradio-ft8-"))
        self.decoding = 0

    def feed_audio(self, audio: np.ndarray, rate: float) -> None:
        now = self.clock()
        if rate != self.rate:
            self.rate = rate
            self.buffer = np.zeros(0, dtype=np.float32)
        x = signal.resample_poly(audio.astype(np.float32), RATE, int(rate)).astype(np.float32)
        # The time goes by the samples (the radio thread may run a little late, then catch up);
        # only when that is more than a second off the clock were samples lost: start again from now
        ends = self.buffer_start + (len(self.buffer) + len(x)) / RATE
        if not self.buffer_start or abs(now - ends) > 1.0:
            self.buffer, self.buffer_start = x, now - len(x) / RATE
        else:
            self.buffer = np.concatenate([self.buffer, x])
        boundary = np.floor((self.buffer_start + len(self.buffer) / RATE) / self.slot) * self.slot
        slot_start = boundary - self.slot
        # A whole slot is in: decode it, keep from the next one on
        if self.buffer_start <= slot_start + 0.3 and self.buffer_start + len(self.buffer) / RATE >= boundary:
            a = int((slot_start - self.buffer_start) * RATE)
            b = int((boundary - self.buffer_start) * RATE)
            samples = self.buffer[max(0, a) : b]
            self.buffer = self.buffer[b:]
            self.buffer_start = boundary
            if self.decoding < 2:
                self.decoding += 1
                threading.Thread(target=self._decode, args=(samples, slot_start), daemon=True).start()
        # Never more than two slots kept
        excess = len(self.buffer) - int(2 * self.slot * RATE)
        if excess > 0:
            self.buffer = self.buffer[excess:]
            self.buffer_start += excess / RATE

    def _decode(self, samples: np.ndarray, slot_start: float) -> None:
        try:
            path = self.workdir / f"{int(slot_start * 10)}.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(RATE)
                peak = float(np.max(np.abs(samples))) or 1.0
                w.writeframes((samples / peak * 20000).astype("<i2").tobytes())
            command = ["decode_ft8", *(["-ft4"] if self.slot == SLOTS["FT4"] else []), str(path)]
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=20)
                self.error = None
            except (OSError, subprocess.TimeoutExpired) as e:
                self.error = f"decode_ft8: {e}"
                return
            finally:
                path.unlink(missing_ok=True)
            for line in result.stdout.splitlines():
                message = parse(line)
                if message:
                    self._message(message, slot_start)
        finally:
            self.decoding -= 1

    def _message(self, message: dict, slot_start: float) -> None:
        self.count += 1
        message["slot"] = slot_start
        if message.get("grid"):
            lat, lon = grid_position(message["grid"])
            message["lat"], message["lon"] = round(lat, 3), round(lon, 3)
            if self.location:
                message["distance"] = round(distance_km(self.location, (lat, lon)))
        self.publish({"key": message.get("call") or "", **message})
        call = message.get("call")
        if not call:
            return
        now = self.clock()
        with self.lock:
            station = self.stations.get(call)
            if station is None and "lat" not in message:
                return  # on the map once its grid square is known
            if station is None:
                station = self.stations[call] = {"call": call, "first": now, "messages": 0}
            station.update({k: message[k] for k in ("grid", "lat", "lon", "distance") if k in message})
            station.update({"seen": now, "snr": message["snr"], "text": message["text"]})
            station["messages"] += 1
            changed = dict(station)
            gone = [c for c, s in self.stations.items() if now - s["seen"] > LOST_AFTER]
            for c in gone:
                del self.stations[c]
        if self.update:
            self.update({"stations": [changed], "gone": gone})

    def live(self) -> dict:
        with self.lock:
            return {"stations": list(self.stations.values()), "location": self.location}

    def status(self) -> dict:
        with self.lock:
            return {**super().status(), "now": len(self.stations)}

    def stop(self) -> None:
        import shutil

        shutil.rmtree(self.workdir, ignore_errors=True)
