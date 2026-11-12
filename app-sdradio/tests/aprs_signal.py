"""An APRS transmitter: AX.25 UI frames as AFSK1200 (Bell 202), for testing the APRS mode."""

import math

import numpy as np

from tests.ais_signal import crc16

AUDIO_RATE = 48_000


def _address(call: str, last: bool) -> bytes:
    name, _, ssid = call.partition("-")
    out = bytes(ord(c) << 1 for c in name.upper().ljust(6)[:6])
    return out + bytes([0x60 | (int(ssid or 0) & 0xF) << 1 | int(last)])


def frame(source: str, destination: str, path: list[str], info: str) -> bytes:
    calls = [destination, source, *path]
    head = b"".join(_address(c, i == len(calls) - 1) for i, c in enumerate(calls))
    body = head + b"\x03\xf0" + info.encode("latin-1")
    fcs = crc16(body)
    return body + bytes([fcs & 0xFF, fcs >> 8])


def bits(data: bytes, flags: int = 40) -> list[int]:
    raw = [(byte >> i) & 1 for byte in data for i in range(8)]
    stuffed, ones = [], 0
    for b in raw:
        stuffed.append(b)
        ones = ones + 1 if b else 0
        if ones == 5:
            stuffed.append(0)
            ones = 0
    flag = [0, 1, 1, 1, 1, 1, 1, 0]
    return flag * flags + stuffed + flag * 4


def afsk(data: bytes, rate: int = AUDIO_RATE) -> np.ndarray:
    """The frame as audio: NRZI on 1200 (mark) and 2200 Hz (space), 1200 baud."""
    tone, freqs = 1200, []
    for b in bits(data):
        if b == 0:
            tone = 2200 if tone == 1200 else 1200
        freqs.append(tone)
    per_bit = rate / 1200
    n = int(len(freqs) * per_bit)
    f = np.array(freqs)[(np.arange(n) / per_bit).astype(int)]
    return 0.8 * np.sin(2 * math.pi * np.cumsum(f) / rate)


def fm_iq(audio: np.ndarray, rate: float = 2_400_000, offset: float = 0.0, deviation: float = 3_000,
          level: float = 0.05, audio_rate: int = AUDIO_RATE) -> np.ndarray:  # fmt: skip
    """The audio on an FM carrier, as IQ at the rate."""
    up = int(rate // audio_rate)
    a = np.repeat(audio, up)
    phase = 2 * math.pi * np.cumsum(offset + deviation * a) / rate
    return (level * np.exp(1j * phase)).astype(np.complex64)
