"""Ships: AIS on its two channels (161.975 and 162.025 MHz), decoded here from the receiver's samples.

The radio is tuned between the channels; both are cut out of the receiver's samples (to 96 kHz,
then one at a time to 48 kHz, five samples a bit), and an FM discriminator gives the GMSK
symbols. Instead of recovering the bit clock, the five sample phases are each decoded, and a
frame that more than one phase finds counts once. The bits are NRZI; the frames are HDLC (flags,
bit stuffing, CRC-16), with each byte sent least significant bit first. What the ships send
(ITU-R M.1371): position reports (types 1, 2, 3 and 18), names, call signs and destinations
(types 5, 19 and 24), and aids to navigation and base stations (types 21 and 4).
"""

import itertools
import logging
import threading
import time
from collections import deque

import numpy as np

from ..dsp import Decimator, Mixer
from . import Decoder
from .aircraft import distance_km

logger = logging.getLogger(__name__)

CHANNELS = {"A": 161_975_000, "B": 162_025_000}
CENTRE = 162_000_000
BAUD = 9600
WIDE_RATE = 96_000
RATE = 48_000
SPS = RATE // BAUD
FLAG = np.array([0, 1, 1, 1, 1, 1, 1, 0], dtype=np.int8)
# Frames are 168 to about 1000 bits with the CRC; longer runs between flags are noise
MIN_BITS, MAX_BITS = 56 + 16, 1100
# A ship not heard for this long is gone (and stored); ships send every 2 s to 6 min
LOST_AFTER = 15 * 60.0
TRAIL = 400

SHIP_TYPES = {
    30: "fishing", 31: "towing", 32: "towing, large", 33: "dredging", 34: "diving", 35: "military", 36: "sailing",
    37: "pleasure craft", 50: "pilot", 51: "search and rescue", 52: "tug", 53: "port tender", 54: "anti-pollution",
    55: "law enforcement", 58: "medical", 59: "noncombatant",
}  # fmt: skip
STATUS = ["under way", "at anchor", "not under command", "restricted manoeuvrability", "constrained by draught",
          "moored", "aground", "fishing", "sailing"]  # fmt: skip


def ship_type_name(code: int | None) -> str | None:
    if code is None or code == 0:
        return None
    if code in SHIP_TYPES:
        return SHIP_TYPES[code]
    return {2: "wing in ground", 4: "high speed craft", 6: "passenger", 7: "cargo", 8: "tanker", 9: "other"}.get(
        code // 10
    )


# ---------------------------------------------------------------- CRC and frames


def _crc_table() -> list[int]:
    table = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
        table.append(crc)
    return table


_TABLE = _crc_table()


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc = (crc >> 8) ^ _TABLE[(crc ^ byte) & 0xFF]
    return crc ^ 0xFFFF


def frame_payload(bits: np.ndarray) -> bytes | None:
    """The bits between two flags as the message's bytes (MSB first), or None if not a frame."""
    out, ones = [], 0
    for b in bits.tolist():
        if ones == 5:
            ones = 0
            if b:
                return None  # six ones: not data
            continue  # the stuffed 0
        out.append(b)
        ones = ones + 1 if b else 0
    if len(out) < MIN_BITS or len(out) % 8:
        return None
    raw = bytearray()
    for i in range(0, len(out), 8):
        raw.append(sum(bit << k for k, bit in enumerate(out[i : i + 8])))
    data, fcs = bytes(raw[:-2]), raw[-2] | raw[-1] << 8
    # Each byte came least significant bit first, so the bytes built that way are the message's
    return data if crc16(data) == fcs else None


class Deframer:
    """Finds HDLC frames in a stream of NRZI symbols (+1/-1), carrying the unfinished part over."""

    def __init__(self):
        self.carry = np.zeros(0, dtype=np.int8)
        self.last_level = 1

    def __call__(self, symbols: np.ndarray) -> list[bytes]:
        levels = (symbols > 0).astype(np.int8)
        # NRZI: no change is a 1, a change a 0
        bits = (levels == np.concatenate([[self.last_level], levels[:-1]])).astype(np.int8)
        if len(levels):
            self.last_level = int(levels[-1])
        bits = np.concatenate([self.carry, bits])
        if len(bits) < 8:
            self.carry = bits
            return []
        windows = np.lib.stride_tricks.sliding_window_view(bits, 8)
        flags = np.flatnonzero((windows == FLAG).all(axis=1))
        frames = []
        for start, end in itertools.pairwise(flags):
            length = end - start - 8
            if MIN_BITS <= length <= MAX_BITS + MAX_BITS // 5:
                payload = frame_payload(bits[start + 8 : end])
                if payload:
                    frames.append(payload)
        # Keep what follows the last flag (a frame may be under way), or the last stretch
        keep_from = flags[-1] if len(flags) else len(bits) - 8
        keep_from = max(keep_from, len(bits) - (MAX_BITS + MAX_BITS // 5 + 16))
        self.carry = bits[keep_from:]
        return frames


# ---------------------------------------------------------------- messages


class Bits:
    def __init__(self, data: bytes):
        self.value = int.from_bytes(data, "big")
        self.length = len(data) * 8

    def uint(self, start: int, count: int) -> int:
        if start + count > self.length:
            raise IndexError
        return (self.value >> (self.length - start - count)) & ((1 << count) - 1)

    def int(self, start: int, count: int) -> int:
        v = self.uint(start, count)
        return v - (1 << count) if v >> (count - 1) else v

    def text(self, start: int, chars: int) -> str:
        out = ""
        for i in range(chars):
            if start + 6 * (i + 1) > self.length:
                break
            v = self.uint(start + 6 * i, 6)
            out += chr(v + 64 if v < 32 else v)
        return out.split("@")[0].strip() or ""


def _position(b: Bits, lon_at: int, lat_at: int) -> dict:
    lon, lat = b.int(lon_at, 28) / 600_000, b.int(lat_at, 27) / 600_000
    if abs(lon) > 180 or abs(lat) > 90:
        return {}
    return {"lat": round(lat, 5), "lon": round(lon, 5)}


def _speed(v: int) -> float | None:
    return None if v == 1023 else v / 10


def _course(v: int) -> float | None:
    return None if v >= 3600 else v / 10


def _heading(v: int) -> int | None:
    return None if v == 511 else v


def parse(data: bytes) -> dict | None:
    """An AIS message (its bytes, MSB first) as what it says, or None for kinds not read here."""
    b = Bits(data)
    try:
        kind = b.uint(0, 6)
        msg = {"type": kind, "mmsi": b.uint(8, 30)}
        if kind in (1, 2, 3):
            status = b.uint(38, 4)
            msg.update({"status": STATUS[status] if status < len(STATUS) else None, "speed": _speed(b.uint(50, 10)),
                        "course": _course(b.uint(116, 12)), "heading": _heading(b.uint(128, 9)),
                        **_position(b, 61, 89)})  # fmt: skip
        elif kind == 18:
            msg.update({"speed": _speed(b.uint(46, 10)), "course": _course(b.uint(112, 12)),
                        "heading": _heading(b.uint(124, 9)), **_position(b, 57, 85), "class": "B"})  # fmt: skip
        elif kind == 19:
            msg.update({"speed": _speed(b.uint(46, 10)), "course": _course(b.uint(112, 12)),
                        "heading": _heading(b.uint(124, 9)), **_position(b, 57, 85), "class": "B",
                        "name": b.text(143, 20), "ship_type": b.uint(263, 8)})  # fmt: skip
        elif kind == 5:
            msg.update({"imo": b.uint(40, 30) or None, "callsign": b.text(70, 7), "name": b.text(112, 20),
                        "ship_type": b.uint(232, 8), "length": b.uint(240, 9) + b.uint(249, 9),
                        "width": b.uint(258, 6) + b.uint(264, 6), "draught": b.uint(294, 8) / 10,
                        "destination": b.text(302, 20)})  # fmt: skip
        elif kind == 24:
            if b.uint(38, 2) == 0:
                msg["name"] = b.text(40, 20)
            else:
                msg.update({"ship_type": b.uint(40, 8), "callsign": b.text(90, 7)})
            msg["class"] = "B"
        elif kind == 21:
            msg.update({"name": b.text(43, 20), **_position(b, 164, 192), "aid": b.uint(38, 5)})
        elif kind == 4:
            msg.update(_position(b, 79, 107))
            msg["base_station"] = True
        else:
            return None
    except IndexError:
        return None
    if not 1 <= msg["mmsi"] <= 999_999_999:
        return None
    return msg


def to_nmea(data: bytes, channel: str) -> str:
    """The message as an NMEA !AIVDM sentence (one fragment, as most messages are), for other programs."""
    bits = "".join(f"{byte:08b}" for byte in data)
    fill = -len(bits) % 6
    bits += "0" * fill
    armoured = ""
    for i in range(0, len(bits), 6):
        v = int(bits[i : i + 6], 2)
        armoured += chr(v + 48 if v < 40 else v + 56)
    body = f"AIVDM,1,1,,{channel},{armoured},{fill}"
    checksum = 0
    for c in body:
        checksum ^= ord(c)
    return f"!{body}*{checksum:02X}"


# ---------------------------------------------------------------- the demodulator


class Channel:
    """One AIS channel: from 96 kHz round both channels to symbols, to frames."""

    def __init__(self, name: str, offset: float):
        self.name = name
        self.mixer = Mixer(WIDE_RATE)
        self.mixer.offset = offset
        self.decimate = Decimator(2, 11_000 / (WIDE_RATE / 2), k=16)
        self.last = np.complex64(1)
        self.deframers = [Deframer() for _ in range(SPS)]
        self.pending = np.zeros(0, dtype=np.float32)

    def __call__(self, wide: np.ndarray) -> list[bytes]:
        x = self.decimate(self.mixer(wide))
        if not len(x):
            return []
        # FM discriminator: the phase step between samples
        prev = np.concatenate([[self.last], x[:-1]])
        self.last = x[-1]
        d = np.angle(x * np.conj(prev)).astype(np.float32)
        # The frequency offset of the ship (and the receiver) taken out: a running mean over ~40 bits
        d = d - np.convolve(d, np.ones(200, dtype=np.float32) / 200, mode="same")
        d = np.concatenate([self.pending, d])
        usable = len(d) // SPS * SPS
        self.pending = d[usable:]
        d = d[:usable].reshape(-1, SPS)
        frames = []
        for phase in range(SPS):
            frames += self.deframers[phase](d[:, phase])
        return frames


class Ships(Decoder):
    key = "ships"
    name = "Ships"
    signal = "GMSK"
    input = "iq"
    # Between the two AIS channels, which are 25 kHz either side
    defaults = {"frequency": CENTRE}
    demod = ("nfm", 25_000)

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.rate = 0.0
        self.mixer: Mixer | None = None
        self.decimate: Decimator | None = None
        self.channels: list[Channel] = []
        self.ships: dict[int, dict] = {}
        self.lock = threading.Lock()
        self.recent: deque = deque(maxlen=200)
        self.messages = 0
        self._last_update = 0.0
        self._changed: set[int] = set()

    def feed_iq(self, iq: np.ndarray, rate: float, offset: float) -> None:
        if rate != self.rate:
            self.rate = rate
            self.mixer = Mixer(rate)
            factor = max(1, round(rate / WIDE_RATE))
            # Both channels lie within ±31 kHz of the middle
            self.decimate = Decimator(factor, 36_000 / (rate / 2), k=4)
            self.channels = [Channel(name, f - CENTRE) for name, f in CHANNELS.items()]
        # Centre the 96 kHz on the middle of the two channels (whatever the tuned frequency)
        self.mixer.offset = offset + (CENTRE - self.frequency)
        wide = self.decimate(self.mixer(iq))
        now = time.time()
        for channel in self.channels:
            for data in channel(wide):
                self._frame(data, channel.name, now)
        if self._changed and now - self._last_update >= 1.0:
            self._send_update(now)

    def _frame(self, data: bytes, channel: str, now: float) -> None:
        # The five phases find the same frame: count it once
        signature = (channel, data)
        if any(sig == signature and now - t < 2.0 for t, sig in self.recent):
            return
        self.recent.append((now, signature))
        msg = parse(data)
        self.messages += 1
        if msg is None:
            return
        with self.lock:
            self._merge(msg, now)

    def _merge(self, msg: dict, now: float) -> None:
        mmsi = msg["mmsi"]
        ship = self.ships.get(mmsi)
        if ship is None:
            ship = self.ships[mmsi] = {"mmsi": mmsi, "first": now, "trail": [], "messages": 0}
            self.count += 1
        ship["messages"] += 1
        ship["seen"] = now
        for field in ("name", "callsign", "destination", "status", "speed", "course", "heading", "imo", "length",
                      "width", "draught", "class", "aid", "base_station"):  # fmt: skip
            value = msg.get(field)
            if value not in (None, ""):
                ship[field] = value
        if msg.get("ship_type"):
            ship["ship_type"] = msg["ship_type"]
            ship["kind"] = ship_type_name(msg["ship_type"])
        if "lat" in msg:
            point = [msg["lat"], msg["lon"]]
            if not ship["trail"] or ship["trail"][-1] != point:
                ship["trail"].append(point)
                del ship["trail"][:-TRAIL]
            ship["lat"], ship["lon"] = point
            if self.location:
                ship["distance"] = round(distance_km(self.location, (point[0], point[1])), 1)
        self._changed.add(mmsi)

    def _send_update(self, now: float) -> None:
        self._last_update = now
        with self.lock:
            changed = [self._public(self.ships[m], trail=False) for m in self._changed if m in self.ships]
            self._changed.clear()
            gone = [m for m, s in self.ships.items() if now - s["seen"] > LOST_AFTER]
            lost = [self.ships.pop(m) for m in gone]
        for ship in lost:
            self._store(ship)
        if self.update:
            self.update({"ships": changed, "gone": gone, "messages": self.messages})

    @staticmethod
    def _public(ship: dict, trail: bool) -> dict:
        out = {k: v for k, v in ship.items() if k != "trail"}
        if trail:
            out["trail"] = list(ship["trail"])
        elif ship["trail"]:
            out["point"] = ship["trail"][-1]
        return out

    def _store(self, ship: dict) -> None:
        item = {k: ship.get(k) for k in ("name", "callsign", "kind", "ship_type", "destination", "length",
                                         "class", "first", "seen", "messages")}  # fmt: skip
        item["positions"] = len(ship["trail"])
        if ship["trail"]:
            item["first_position"], item["last_position"] = ship["trail"][0], ship["trail"][-1]
        self.publish({"key": str(ship["mmsi"]), **item})

    def live(self) -> dict:
        with self.lock:
            return {"ships": [self._public(s, trail=True) for s in self.ships.values()], "messages": self.messages,
                    "location": self.location}  # fmt: skip

    def status(self) -> dict:
        with self.lock:
            return {**super().status(), "now": len(self.ships)}

    def stop(self) -> None:
        with self.lock:
            lost = list(self.ships.values())
            self.ships.clear()
        for ship in lost:
            self._store(ship)
