"""APRS: amateur radio stations, cars, weather stations and balloons on 144.800 MHz.

multimon-ng decodes the AFSK1200 packets from the radio's own NFM audio and prints each as a
TNC2 line (SOURCE>DEST,PATH:info). What the packet says is read here (the APRS protocol, 1.0.1
and its Mic-E encoding): positions plain, compressed and Mic-E, objects and items, status texts
and messages. Every packet is stored; the stations with a position are on the map.
"""

import re
import threading
import time

import numpy as np

from . import Decoder
from .aircraft import distance_km
from .pager import multimon_command
from .program import Program

# A station not heard for this long leaves the map (its packets stay in the history)
LOST_AFTER = 60 * 60.0
TRAIL = 200

# What the common symbols are (primary table "/"), for the list and the map
SYMBOLS = {
    ">": ("car", "🚗"), "-": ("house", "🏠"), "#": ("digipeater", "📡"), "&": ("gateway", "📡"),
    "_": ("weather station", "🌤"), "k": ("truck", "🚚"), "v": ("van", "🚐"), "[": ("walker", "🚶"),
    "b": ("bicycle", "🚲"), "Y": ("sailboat", "⛵"), "s": ("boat", "🚤"), "O": ("balloon", "🎈"),
    "<": ("motorcycle", "🏍"), "j": ("jeep", "🚙"), "R": ("camper", "🚐"), "U": ("bus", "🚌"),
    "X": ("helicopter", "🚁"), "^": ("aircraft", "✈"), "'": ("small aircraft", "✈"), "y": ("antenna", "📡"),
    "r": ("repeater", "📡"), "a": ("ambulance", "🚑"), "f": ("fire engine", "🚒"), "u": ("truck", "🚚"),
    "n": ("node", "📡"), "l": ("laptop", "💻"), "$": ("phone", "📱"), "/": ("dot", "•"),
}  # fmt: skip
# The Mic-E standard messages by their three bits (111 off duty ... 000 emergency)
MIC_E_STATUS = ["emergency", "priority", "special", "committed", "returning", "in service", "en route", "off duty"]

_TNC2 = re.compile(r"^([A-Z0-9-]{1,9})>([A-Z0-9-]{1,9})((?:,[A-Z0-9*-]{1,10})*):(.*)$", re.IGNORECASE)
_UNCOMPRESSED = re.compile(r"^(\d{2})([\d ]{2}\.[\d ]{2})([NS])(.)(\d{3})([\d ]{2}\.[\d ]{2})([EW])(.)(.*)$")
_CSE_SPD = re.compile(r"^(\d{3})/(\d{3})")
_ALTITUDE = re.compile(r"/A=(-?\d{6})")
# Power, height, gain and directivity of a fixed station: data, not part of the comment
_PHG = re.compile(r"^PHG\d{4}")


def _base91(text: str) -> int:
    value = 0
    for c in text:
        value = value * 91 + ord(c) - 33
    return value


_WEATHER = re.compile(r"([cstgrpPhbL])(-?[\d.]{2,5})")


def weather(text: str) -> dict:
    """A weather report's readings (wind direction/speed first, then t054 h85 b10132 ...), metric."""
    out = {}
    wind = re.match(r"^(\d{3}|\.{3})/(\d{3}|\.{3})", text)
    if wind:
        if wind.group(1).isdigit():
            out["wind_direction"] = int(wind.group(1))
        if wind.group(2).isdigit():
            out["wind"] = round(int(wind.group(2)) * 1.609, 1)
        text = text[7:]
    for key, value in _WEATHER.findall(text):
        try:
            v = float(value)
        except ValueError:
            continue
        if key == "t":
            out["temperature"] = round((v - 32) * 5 / 9, 1)
        elif key == "h":
            out["humidity"] = 100 if v == 0 else int(v)
        elif key == "b":
            out["pressure"] = round(v / 10, 1)
        elif key == "g":
            out["gust"] = round(v * 1.609, 1)
        elif key == "r":
            out["rain_hour"] = round(v * 0.254, 1)
        elif key == "c" and "wind_direction" not in out:
            out["wind_direction"] = int(v)
        elif key == "s" and "wind" not in out:
            out["wind"] = round(v * 1.609, 1)
    return out


def _uncompressed(text: str) -> dict | None:
    m = _UNCOMPRESSED.match(text)
    if not m:
        return None
    lat_d, lat_m, ns, table, lon_d, lon_m, ew, code, rest = m.groups()
    # Spaces stand for digits left out (position ambiguity): the middle of what is left
    lat = int(lat_d) + float(lat_m.replace(" ", "5")) / 60
    lon = int(lon_d) + float(lon_m.replace(" ", "5")) / 60
    out = {"lat": round(-lat if ns == "S" else lat, 5), "lon": round(-lon if ew == "W" else lon, 5),
           "symbol": table + code}  # fmt: skip
    if code == "_":
        # A weather station: wind and the readings follow, not course and speed
        out["weather"] = weather(rest)
        return {**out, "comment": re.sub(r"^[\d./cstgrpPhbL-]+", "", rest).strip()}
    cs = _CSE_SPD.match(rest)
    if cs:
        course, speed = int(cs.group(1)), int(cs.group(2))
        if 0 < course <= 360:
            out["course"] = course % 360
        out["speed"] = round(speed * 1.852, 1)  # km/h
        rest = rest[7:]
    out["comment"] = rest
    return out


def _compressed(text: str) -> dict | None:
    if len(text) < 13:
        return None
    table, y, x, code, c, s = text[0], text[1:5], text[5:9], text[9], text[10], text[11]
    if not all(33 <= ord(ch) <= 123 for ch in y + x):
        return None
    out = {"lat": round(90 - _base91(y) / 380926, 5), "lon": round(-180 + _base91(x) / 190463, 5),
           "symbol": table + code, "comment": text[13:]}  # fmt: skip
    if c not in " {" and "!" <= c <= "z":
        out["course"] = ((ord(c) - 33) * 4) % 360
        out["speed"] = round((1.08 ** (ord(s) - 33) - 1) * 1.852, 1)
    return out


def _position(text: str) -> dict | None:
    if text[:1].isdigit():
        return _uncompressed(text)
    return _compressed(text)


def _mic_e(destination: str, info: str) -> dict | None:
    """Mic-E: the latitude and message bits in the destination call, the rest packed in the info."""
    dest = destination.split("-")[0]
    if len(dest) < 6 or len(info) < 9:
        return None
    digits, bits = "", []
    for ch in dest[:6]:
        if ch.isdigit():
            digits += ch
            bits.append(0)
        elif "A" <= ch <= "J":
            digits += str(ord(ch) - 65)
            bits.append(1)
        elif "P" <= ch <= "Y":
            digits += str(ord(ch) - 80)
            bits.append(1)
        elif ch in "KLZ":
            digits += "0"
            bits.append(1 if ch != "L" else 0)
        else:
            return None
    north = "P" <= dest[3] <= "Z"
    plus100 = "P" <= dest[4] <= "Z"
    west = "P" <= dest[5] <= "Z"
    lat = int(digits[:2]) + (int(digits[2:4]) + int(digits[4:6]) / 100) / 60
    d = ord(info[1]) - 28 + (100 if plus100 else 0)
    if 180 <= d <= 189:
        d -= 80
    elif 190 <= d <= 199:
        d -= 190
    m = ord(info[2]) - 28
    if m >= 60:
        m -= 60
    h = ord(info[3]) - 28
    lon = d + (m + h / 100) / 60
    sp, dc, se = ord(info[4]) - 28, ord(info[5]) - 28, ord(info[6]) - 28
    speed = sp * 10 + dc // 10
    course = (dc % 10) * 100 + se
    if speed >= 800:
        speed -= 800
    if course >= 400:
        course -= 400
    if not (0 <= lat <= 90 and 0 <= lon <= 180):
        return None
    out = {"lat": round(lat if north else -lat, 5), "lon": round(-lon if west else lon, 5),
           "symbol": info[8] + info[7], "speed": round(speed * 1.852, 1)}  # fmt: skip
    if 0 < course <= 360:
        out["course"] = course % 360
    status = bits[0] << 2 | bits[1] << 1 | bits[2]
    out["status"] = MIC_E_STATUS[status]
    rest = info[9:]
    # The altitude may come as "xxx}" (base 91, metres above -10 km), after a radio's type mark
    alt = re.search(r"([!-{]{3})\}", rest)
    if alt:
        out["altitude"] = _base91(alt.group(1)) - 10000
        rest = rest[: alt.start()] + rest[alt.end() :]
    # Kenwood marks (] and > first) say which radio sent it, not part of the comment
    out["comment"] = rest.lstrip("]>").strip()
    return out


def parse(line: str) -> dict | None:
    """A multimon-ng APRS line as a packet: who sent it, through which path, and what it says."""
    if not line.startswith("APRS: "):
        return None
    m = _TNC2.match(line[6:].strip())
    if not m:
        return None
    source, destination, path, info = m.groups()
    packet = {"source": source.upper(), "destination": destination.upper(),
              "path": [p for p in path.split(",") if p], "raw": info}  # fmt: skip
    if not info:
        return packet
    kind = info[0]
    body: dict | None = None
    if kind in "!=":
        body = _position(info[1:])
        packet["type"] = "position"
    elif kind in "/@":
        body = _position(info[8:])
        packet["type"] = "position"
    elif kind in "`'\x1c\x1d":
        body = _mic_e(destination, info)
        packet["type"] = "position"
    elif kind == ";" and len(info) >= 18:
        body = _position(info[18:])
        packet.update({"type": "object", "name": info[1:10].strip(), "killed": info[10] == "_"})
    elif kind == ")":
        m2 = re.match(r"^\)([^!_]{3,9})([!_])(.*)$", info)
        if m2:
            body = _position(m2.group(3))
            packet.update({"type": "item", "name": m2.group(1).strip(), "killed": m2.group(2) == "_"})
    elif kind == ">":
        packet.update({"type": "status", "text": info[1:].strip()})
    elif kind == ":" and len(info) >= 11 and info[10] == ":":
        packet.update({"type": "message", "to": info[1:10].strip(), "text": info[11:].strip()})
    elif kind == "_":
        packet.update({"type": "weather", "text": info[1:].strip()})
    elif kind == "T":
        packet.update({"type": "telemetry", "text": info.strip()})
    else:
        packet.update({"type": "other", "text": info.strip()})
    if body:
        if "comment" in body:
            alt = _ALTITUDE.search(body["comment"])
            if alt:
                body["altitude"] = round(int(alt.group(1)) * 0.3048)
                body["comment"] = _ALTITUDE.sub("", body["comment"])
            body["comment"] = _PHG.sub("", body["comment"]).strip()
            if not body["comment"]:
                del body["comment"]
        symbol = SYMBOLS.get(body.get("symbol", "  ")[1]) if body.get("symbol", "")[:1] == "/" else None
        if symbol:
            body["kind"] = symbol[0]
        packet.update(body)
    return packet


class Aprs(Decoder):
    key = "aprs"
    name = "APRS"
    input = "audio"
    # The European APRS frequency (North America: 144.390)
    defaults = {"frequency": 144_800_000}
    demod = ("nfm", 12_500)

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.program: Program | None = None
        self.rate = 0.0
        self.stations: dict[str, dict] = {}
        self.lock = threading.Lock()
        self.messages = 0

    def feed_audio(self, audio: np.ndarray, rate: float) -> None:
        if self.program is None or rate != self.rate:
            if self.program:
                self.program.stop()
            self.rate = rate
            self.program = Program("multimon-ng", multimon_command(rate, ["AFSK1200"], "-A"), self._line)
        self.program.write((np.clip(audio, -1.0, 1.0) * 32000).astype("<i2").tobytes())
        self.error = self.program.error

    def _line(self, line: str) -> None:
        packet = parse(line)
        if packet is None:
            return
        self.messages += 1
        now = time.time()
        # Objects and items are stations of their own, named by their sender
        name = packet.get("name") if packet.get("type") in ("object", "item") else None
        station_id = name or packet["source"]
        self.count += 1
        self.publish({"key": station_id, **packet})
        changed = None
        with self.lock:
            station = self.stations.get(station_id)
            # On the map once it has sent a position
            if station is not None or "lat" in packet:
                if station is None:
                    station = self.stations[station_id] = {"call": station_id, "first": now, "trail": [], "packets": 0}
                station["packets"] += 1
                station["seen"] = now
                station["path"] = packet["path"]
                if name:
                    station["by"] = packet["source"]
                for field in (
                    "symbol",
                    "kind",
                    "comment",
                    "speed",
                    "course",
                    "altitude",
                    "status",
                    "killed",
                    "weather",
                ):
                    if field in packet:
                        station[field] = packet[field]
                if packet.get("type") == "status":
                    station["status_text"] = packet.get("text")
                if "lat" in packet:
                    point = [packet["lat"], packet["lon"]]
                    if not station["trail"] or station["trail"][-1] != point:
                        station["trail"].append(point)
                        del station["trail"][:-TRAIL]
                    station["lat"], station["lon"] = point
                    if self.location:
                        station["distance"] = round(distance_km(self.location, (point[0], point[1])), 1)
                changed = self._public(station, trail=False)
            gone = [s for s, v in self.stations.items() if now - v["seen"] > LOST_AFTER]
            for s in gone:
                del self.stations[s]
        if self.update and (changed or gone):
            self.update({"stations": [changed] if changed else [], "gone": gone, "messages": self.messages})

    @staticmethod
    def _public(station: dict, trail: bool) -> dict:
        out = {k: v for k, v in station.items() if k != "trail"}
        if trail:
            out["trail"] = list(station["trail"])
        elif station["trail"]:
            out["point"] = station["trail"][-1]
        return out

    def live(self) -> dict:
        with self.lock:
            return {"stations": [self._public(s, trail=True) for s in self.stations.values()],
                    "messages": self.messages, "location": self.location}  # fmt: skip

    def status(self) -> dict:
        with self.lock:
            return {**super().status(), "now": len(self.stations)}

    def stop(self) -> None:
        if self.program:
            self.program.stop()
            self.program = None
