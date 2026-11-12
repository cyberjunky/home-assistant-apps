"""An ACARS transmitter: a frame as MSK audio (2400 baud, 1200 and 2400 Hz) on an AM carrier."""

import math

import numpy as np

BAUD = 2400


def with_parity(c: int) -> int:
    """7-bit ASCII with odd parity in bit 8."""
    return c | (0 if bin(c).count("1") % 2 else 0x80)


def crc_kermit(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def frame(mode: str, registration: str, label: str, block: str, text: str, ack: str = "\x15",
          end: int = 0x03) -> bytes:  # fmt: skip
    """The frame's bytes from '+' to DEL, with parity, and the CRC over mode..ETX."""
    body = bytes(with_parity(ord(c)) for c in mode + registration.rjust(7, ".")[:7] + ack + label + block)
    body += bytes([with_parity(0x02)]) + bytes(with_parity(ord(c)) for c in text) + bytes([with_parity(end)])
    crc = crc_kermit(body)
    head = bytes(with_parity(c) for c in (0x2B, 0x2A, 0x16, 0x16, 0x01))
    return head + body + bytes([crc & 0xFF, crc >> 8]) + bytes([with_parity(0x7F)])


def audio(data: bytes, rate: int = 48_000, prekey_bits: int = 128) -> np.ndarray:
    """The bits (each byte least significant bit first, after a pre-key of ones) as MSK audio:
    a bit equal to the one before is a full cycle of 2400 Hz, a change half a cycle of 1200 Hz."""
    bits = [1] * prekey_bits + [(b >> i) & 1 for b in data for i in range(8)]
    per = rate // BAUD
    u = np.arange(per) / per
    out, previous, sign = [], 1, 1.0
    for bit in bits:
        if bit == previous:
            out.append(sign * np.sin(2 * math.pi * u))
        else:
            out.append(sign * np.sin(math.pi * u))
            sign = -sign
        previous = bit
    return np.concatenate(out)


def am_iq(audio_48k: np.ndarray, rate: float = 2_400_000, offset: float = 0.0, level: float = 0.05,
          depth: float = 0.6, lead: float = 0.02) -> np.ndarray:  # fmt: skip
    up = int(rate // 48_000)
    a = np.repeat(audio_48k, up)
    quiet = np.zeros(int(lead * rate))
    carrier = np.concatenate([quiet, np.ones(len(a)), quiet])
    a = np.concatenate([quiet, a, quiet])
    t = np.arange(len(a)) / rate
    return (level * carrier * (1 + depth * a) * np.exp(2j * math.pi * offset * t)).astype(np.complex64)
