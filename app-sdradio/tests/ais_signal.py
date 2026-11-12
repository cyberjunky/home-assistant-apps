"""An AIS transmitter (ITU-R M.1371): messages as GMSK bursts, for testing the Ships mode.

A message's bits go into bytes (most significant bit first), each byte is sent least significant
bit first, then the CRC; HDLC bit stuffing and flags, NRZI, and GMSK (BT 0.4, 9600 baud, 2.4 kHz
deviation), after a training sequence.
"""

import math

import numpy as np
from scipy import signal

BAUD = 9600


def _uint(value: int, bits: int) -> str:
    return format(value & ((1 << bits) - 1), f"0{bits}b")


def _text(text: str, chars: int) -> str:
    out = ""
    for c in text.upper().ljust(chars, "@")[:chars]:
        v = ord(c)
        out += _uint(v - 64 if v >= 64 else v, 6)
    return out


def position(mmsi: int, lat: float, lon: float, speed_kn: float, course: float, heading: int = 511,
             status: int = 0, kind: int = 1) -> str:  # fmt: skip
    """A position report (type 1, 2 or 3; class A)."""
    return (_uint(kind, 6) + _uint(0, 2) + _uint(mmsi, 30) + _uint(status, 4) + _uint(-128, 8)
            + _uint(round(speed_kn * 10), 10) + "1" + _uint(round(lon * 600_000), 28)
            + _uint(round(lat * 600_000), 27) + _uint(round(course * 10), 12) + _uint(heading, 9)
            + _uint(30, 6) + _uint(0, 2) + _uint(0, 3) + "0" + _uint(0, 19))  # fmt: skip


def static(mmsi: int, name: str, callsign: str, ship_type: int, destination: str, draught_m: float = 5.0,
           bow: int = 80, stern: int = 20, port: int = 6, starboard: int = 6) -> str:  # fmt: skip
    """Static and voyage data (type 5)."""
    return (_uint(5, 6) + _uint(0, 2) + _uint(mmsi, 30) + _uint(0, 2) + _uint(9_123_456, 30)
            + _text(callsign, 7) + _text(name, 20) + _uint(ship_type, 8) + _uint(bow, 9) + _uint(stern, 9)
            + _uint(port, 6) + _uint(starboard, 6) + _uint(1, 4) + _uint(11, 4) + _uint(12, 5) + _uint(14, 5)
            + _uint(30, 6) + _uint(round(draught_m * 10), 8) + _text(destination, 20) + "0" + "0")  # fmt: skip


def class_b(mmsi: int, lat: float, lon: float, speed_kn: float, course: float) -> str:
    """A class B position report (type 18)."""
    return (_uint(18, 6) + _uint(0, 2) + _uint(mmsi, 30) + _uint(0, 8) + _uint(round(speed_kn * 10), 10) + "0"
            + _uint(round(lon * 600_000), 28) + _uint(round(lat * 600_000), 27) + _uint(round(course * 10), 12)
            + _uint(511, 9) + _uint(30, 6) + _uint(0, 2) + "1" + "0" + "0" + "0" + "0" + "0" + "0"
            + _uint(0, 20))  # fmt: skip


def name_b(mmsi: int, name: str) -> str:
    """Static data of a class B ship, part A: its name (type 24)."""
    return _uint(24, 6) + _uint(0, 2) + _uint(mmsi, 30) + _uint(0, 2) + _text(name, 20) + _uint(0, 8)


def crc16(data: bytes) -> int:
    """CRC-16 of HDLC (X.25): reflected 0x1021, starting at 0xFFFF, inverted at the end."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc ^ 0xFFFF


def frame_bits(payload: str) -> list[int]:
    """The bits as sent: training, flag, the stuffed bytes and CRC (each byte LSB first), flag."""
    payload += "0" * (-len(payload) % 8)
    data = bytes(int(payload[i : i + 8], 2) for i in range(0, len(payload), 8))
    fcs = crc16(data)
    raw = []
    for byte in data + bytes([fcs & 0xFF, fcs >> 8]):
        raw += [(byte >> i) & 1 for i in range(8)]
    stuffed, ones = [], 0
    for b in raw:
        stuffed.append(b)
        ones = ones + 1 if b else 0
        if ones == 5:
            stuffed.append(0)
            ones = 0
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    return [0, 1] * 12 + flag + stuffed + flag + [0] * 4


def nrzi(bits: list[int]) -> np.ndarray:
    """NRZI: a 0 changes the level, a 1 keeps it."""
    level, out = 1, []
    for b in bits:
        if b == 0:
            level = -level
        out.append(level)
    return np.array(out, dtype=np.float64)


def gmsk_iq(payload: str, rate: float = 2_400_000, offset: float = 0.0, level: float = 0.1,
            lead: float = 0.002) -> np.ndarray:  # fmt: skip
    """One AIS burst as IQ samples at the rate, `offset` Hz from the centre."""
    sps = round(rate / BAUD)
    symbols = np.repeat(nrzi(frame_bits(payload)), sps)
    # Gaussian filter, BT 0.4, three bits long
    bt = 0.4
    t = np.arange(-1.5 * sps, 1.5 * sps + 1) / sps
    sigma = math.sqrt(math.log(2)) / (2 * math.pi * bt)
    g = np.exp(-(t**2) / (2 * sigma**2))
    g /= g.sum()
    shaped = signal.fftconvolve(np.concatenate([symbols, np.zeros(len(g))]), g)[: len(symbols) + len(g)]
    quiet = np.zeros(int(lead * rate))
    freq = np.concatenate([quiet, offset + 2400 * shaped, quiet + offset])
    phase = 2 * math.pi * np.cumsum(freq) / rate
    envelope = np.concatenate([quiet * 0, np.ones(len(shaped)), quiet * 0])
    return (level * envelope * np.exp(1j * phase)).astype(np.complex64)
