"""A Mode S transmitter (1090 MHz extended squitter), for testing the Aircraft mode against dump1090."""

import numpy as np

# CRC-24 of Mode S: x^24 + x^23 + x^22 + ... (0x1FFF409)
GENERATOR = 0x1FFF409


def crc(bits: int, length: int) -> int:
    """The remainder of a message's first length-24 bits (the parity it should end with)."""
    data = bits >> 24 << 24
    for i in range(length - 1, 23, -1):
        if data >> i & 1:
            data ^= GENERATOR << (i - 24)
    return data & 0xFFFFFF


def message(hex_without_parity: str) -> str:
    """88 bits of a DF17 message, with its parity added."""
    value = int(hex_without_parity, 16) << 24
    return f"{value | crc(value, 112):028X}"


# Known messages (from "The 1090 MHz Riddle"): KLM1023's identity, a position pair near
# 52.257 N 3.919 E at 38 000 ft, and a velocity
IDENTITY = "8D4840D6202CC371C32CE0576098"
EVEN = "8D40621D58C382D690C8AC2863A7"
ODD = "8D40621D58C386435CC412692AD6"
VELOCITY = "8D485020994409940838175B284F"


# ---------------------------------------------------------------- encoding an aircraft that flies

NZ = 15


def _nl(lat: float) -> int:
    """The number of longitude zones at a latitude (the CPR NL function)."""
    import math

    if abs(lat) >= 87:
        return 1 if abs(lat) > 87 else 2
    a = 1 - math.cos(math.pi / (2 * NZ))
    b = math.cos(math.pi / 180 * abs(lat)) ** 2
    return math.floor(2 * math.pi / math.acos(1 - a / b))


def cpr(lat: float, lon: float, odd: int) -> tuple[int, int]:
    """Airborne CPR: the 17-bit latitude and longitude of a position, even (0) or odd (1)."""
    import math

    dlat = 360 / (4 * NZ - odd)
    yz = math.floor(2**17 * (lat % dlat) / dlat + 0.5)
    rlat = dlat * (yz / 2**17 + math.floor(lat / dlat))
    dlon = 360 / max(_nl(rlat) - odd, 1)
    xz = math.floor(2**17 * (lon % dlon) / dlon + 0.5)
    return yz % 2**17, xz % 2**17


def _altitude(feet: int) -> int:
    n = round((feet + 1000) / 25)
    return (n >> 4) << 5 | 1 << 4 | (n & 0xF)


def position(icao: int, lat: float, lon: float, feet: int, odd: int) -> str:
    yz, xz = cpr(lat, lon, odd)
    me = 11 << 51 | _altitude(feet) << 36 | odd << 34 | yz << 17 | xz
    return message(f"8D{icao:06X}{me:014X}")


_CHARS = "#ABCDEFGHIJKLMNOPQRSTUVWXYZ##### ###############0123456789######"


def identity(icao: int, callsign: str) -> str:
    me = 4 << 51
    for i, c in enumerate(callsign.ljust(8)[:8]):
        me |= _CHARS.index(c) << (42 - 6 * i)
    return message(f"8D{icao:06X}{me:014X}")


def flight(icao: int, callsign: str, lat: float, lon: float, feet: int, heading: float, speed_ms: float,
           seconds: float, every: float = 0.25, start: float = 0.0) -> list[tuple[float, str]]:  # fmt: skip
    """(time, message) of an aircraft flying straight: positions every `every` s, even and odd in
    turn, and its callsign every second; the first at `start` s."""
    import math

    out = []
    t = 0.0
    n = 0
    while t < seconds:
        north = speed_ms * t * math.cos(math.radians(heading))
        east = speed_ms * t * math.sin(math.radians(heading))
        la = lat + north / 111_320
        lo = lon + east / (111_320 * math.cos(math.radians(lat)))
        out.append((start + t, position(icao, la, lo, feet, n % 2)))
        if n % 4 == 0:
            out.append((start + t + 0.01, identity(icao, callsign)))
        n += 1
        t += every
    return out


def pulses(text: str, rate: float = 2_400_000) -> np.ndarray:
    """One message as its pulse envelope at the rate (0 to 1)."""
    over = 10
    half = round(rate * over / 2_000_000)
    value = int(text, 16)
    chips = [1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 0, 0, 0, 0, 0]
    for i in range(len(text) * 4 - 1, -1, -1):
        chips += [1, 0] if value >> i & 1 else [0, 1]
    fine = np.repeat(np.array(chips, dtype=np.float32), half)
    fine = np.concatenate([fine, np.zeros(-len(fine) % over, dtype=np.float32)])
    return fine.reshape(-1, over).mean(axis=1)


def timed_iq(events: list[tuple[float, str]], seconds: float, rate: float = 2_400_000, level: float = 0.3,
             offset: float = 0.0, seed: int = 1) -> np.ndarray:  # fmt: skip
    """Messages at their times (seconds) in `seconds` of samples, with noise."""
    rng = np.random.default_rng(seed)
    n = int(seconds * rate)
    envelope = np.zeros(n, dtype=np.float32)
    for t, text in events:
        p = pulses(text, rate)
        start = int(t * rate)
        if start + len(p) <= n:
            envelope[start : start + len(p)] += p
    phase = 2 * np.pi * offset * np.arange(n) / rate + rng.uniform(0, 2 * np.pi)
    noise = (rng.standard_normal(n) + 1j * rng.standard_normal(n)) * 0.01
    return (level * envelope * np.exp(1j * phase) + noise).astype(np.complex64)


def velocity(icao: int, east_kt: float, north_kt: float, climb_fpm: float) -> str:
    """Airborne velocity (ground speed, subtype 1): east and north in knots, climb in ft/min."""
    ew, ns = round(east_kt), round(north_kt)
    vr = round(climb_fpm / 64)
    me = 19 << 51 | 1 << 48
    me |= (1 if ew < 0 else 0) << 42 | (abs(ew) + 1) << 32
    me |= (1 if ns < 0 else 0) << 31 | (abs(ns) + 1) << 21
    me |= (1 if vr < 0 else 0) << 19 | (abs(vr) + 1) << 10
    return message(f"8D{icao:06X}{me:014X}")
