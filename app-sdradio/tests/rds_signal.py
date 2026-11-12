"""An RDS transmitter, for the tests and the simulated FM station: PS, radio text and RadioText+."""

import math

import numpy as np

from src.rds import BIT_RATE, RTPLUS_AID, encode_block

PTY_POP = 10
RTPLUS_GROUP = (11 << 1) | 0  # group 11A


def _chars(text: str, length: int) -> list[int]:
    return [ord(c) for c in text.ljust(length)[:length]]


def groups(pi: int, ps: str, rt: str, artist: str | None = None, title: str | None = None) -> list[list[tuple]]:
    """One cycle of groups: the station name, the radio text and the RT+ tags."""
    b0 = PTY_POP << 5
    ps_codes = _chars(ps, 8)
    text = rt + "\r" if len(rt) < 64 else rt[:64]
    rt_codes = _chars(text, (len(text) + 3) // 4 * 4)
    out = []
    for seg in range(4):
        out.append([(pi, "A"), (b0 | seg, "B"), (0xE0CD, "C"), ((ps_codes[seg * 2] << 8) | ps_codes[seg * 2 + 1], "D")])
    for seg in range(len(rt_codes) // 4):
        c = (rt_codes[seg * 4] << 8) | rt_codes[seg * 4 + 1]
        d = (rt_codes[seg * 4 + 2] << 8) | rt_codes[seg * 4 + 3]
        out.append([(pi, "A"), ((2 << 12) | b0 | seg, "B"), (c, "C"), (d, "D")])
    if artist and title:
        out.append([(pi, "A"), ((3 << 12) | b0 | RTPLUS_GROUP, "B"), (0, "C"), (RTPLUS_AID, "D")])
        a_start, t_start = rt.index(artist), rt.index(title)
        tag1 = (4, a_start, len(artist) - 1)  # ITEM.ARTIST
        tag2 = (1, t_start, len(title) - 1)  # ITEM.TITLE
        b = (11 << 12) | b0 | (1 << 3) | (tag1[0] >> 3)
        c = ((tag1[0] & 7) << 13) | (tag1[1] << 7) | (tag1[2] << 1) | (tag2[0] >> 5)
        d = ((tag2[0] & 31) << 11) | (tag2[1] << 5) | tag2[2]
        out.append([(pi, "A"), (b, "B"), (c, "C"), (d, "D")])
    return out


def bits(group_list: list[list[tuple]], repeat: int = 1) -> np.ndarray:
    """The groups as differentially coded bits, as sent."""
    data = []
    for _ in range(repeat):
        for group in group_list:
            for value, offset in group:
                word = encode_block(value, offset)
                data.extend((word >> (25 - i)) & 1 for i in range(26))
    out = np.zeros(len(data), dtype=np.int8)
    previous = 0
    for i, bit in enumerate(data):
        previous ^= bit
        out[i] = previous
    return out


def subcarrier(
    coded: np.ndarray,
    rate: float,
    t0: float,
    count: int,
    bit_rate: float = BIT_RATE,
    carrier: float = 57_000.0,
    phase: float = 0.7,
) -> np.ndarray:
    """The 57 kHz RDS signal (about ±1) for `count` samples from t0: biphase symbols on the subcarrier."""
    t = t0 + np.arange(count) / rate
    position = t * bit_rate
    index = position.astype(np.int64) % len(coded)
    symbol = coded[index] * 2.0 - 1.0
    biphase = np.where((position % 1) < 0.5, symbol, -symbol)
    return (biphase * np.sin(2 * math.pi * carrier * t + phase)).astype(np.float32)
