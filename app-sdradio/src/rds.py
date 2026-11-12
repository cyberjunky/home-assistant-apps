"""RDS: the station name, radio text and programme type an FM broadcast station sends along.

The decoder is fed the FM-demodulated signal (the multiplex, before de-emphasis) at the WFM rate.
RDS is a 57 kHz subcarrier carrying 1187.5 bit/s, biphase and differentially coded. The bits
form 26-bit blocks (16 data bits and a 10-bit check word with an offset that tells A, B, C, C'
and D apart); four blocks make a group. Group 0 carries the station name (PS), group 2 the
radio text (RT), and RadioText+ (an open data application) marks the artist and title in it.
"""

import math

import numpy as np

from .dsp import Decimator, Mixer, StatefulFilter

BIT_RATE = 1187.5
SUBCARRIER = 57_000.0
# The RDS check polynomial x^10 + x^8 + x^7 + x^5 + x^4 + x^3 + 1
POLY = 0x5B9
OFFSETS = {"A": 0x0FC, "B": 0x198, "C": 0x168, "C'": 0x350, "D": 0x1B4}
NEXT = {"A": "B", "B": "C", "C": "D", "C'": "D", "D": "A"}
RTPLUS_AID = 0x4BD7
RTPLUS_TITLE, RTPLUS_ARTIST = 1, 4

PTY_NAMES = [
    "", "News", "Current affairs", "Information", "Sport", "Education", "Drama", "Culture", "Science",
    "Varied", "Pop music", "Rock music", "Easy listening", "Light classical", "Serious classical",
    "Other music", "Weather", "Finance", "Children's programmes", "Social affairs", "Religion", "Phone-in",
    "Travel", "Leisure", "Jazz music", "Country music", "National music", "Oldies music", "Folk music",
    "Documentary", "Alarm test", "Alarm",
]  # fmt: skip

# The RDS character set where it differs from ASCII (EN 50067 annex E, the common letters)
CHARSET = dict(zip(range(0x80, 0xA0), "áàéèíìóòúùÑÇŞβ¡Ĳâäêëîïôöûüñçşğıĳ", strict=True)) | dict(
    zip(range(0xC0, 0xE0), "ÁÀÉÈÍÌÓÒÚÙŘČŠŽĐĿÂÄÊËÎÏÔÖÛÜřčšžđŀ", strict=True)
)


def syndrome(word: int) -> int:
    """The remainder of a 26-bit block divided by the check polynomial: the block's offset word if it is intact."""
    for bit in range(25, 9, -1):
        if word >> bit & 1:
            word ^= POLY << (bit - 10)
    return word


def encode_block(data: int, offset: str) -> int:
    """A 26-bit block: the data and its check word (used by the tests and the simulator)."""
    return (data << 10) | (syndrome(data << 10) ^ OFFSETS[offset])


def _burst_table() -> dict[int, int]:
    """The syndromes of every error burst of up to two bits, to correct those."""
    table: dict[int, int] = {}
    for pattern in (0b1, 0b11):
        for shift in range(26 - pattern.bit_length() + 1):
            error = pattern << shift
            table.setdefault(syndrome(error), error)
    return table


BURSTS = _burst_table()
SYNDROMES = {value: name for name, value in OFFSETS.items()}


def _char(code: int) -> str:
    if 0x20 <= code < 0x7F:
        return chr(code)
    if code == 0x0D:
        return "\r"
    return CHARSET.get(code, " ")


class RdsDecoder:
    def __init__(self, rate: float):
        self.rate = float(rate)
        factor = max(1, round(self.rate / 12_000))
        # The subcarrier at 0 Hz, then down to about 12 kHz: RDS is 2.4 kHz either side
        self.decimate = Decimator(factor, 2_800 / (self.rate / 2), k=8)
        self.sample_rate = self.rate / factor
        self.sps = self.sample_rate / BIT_RATE
        # Matched filter for one biphase bit: +1 for the first half, -1 for the second (time reversed)
        n = round(self.sps)
        half = n // 2
        self.matched = StatefulFilter(np.r_[-np.ones(n - half), np.ones(half)].astype(np.float32), dtype=np.float32)
        # The carrier's phase, from the squared signal (which drops the ±1 data), smoothed over ~8 ms
        alpha = 0.01
        self.carrier = StatefulFilter([alpha], [1, alpha - 1])
        self.mixer = Mixer(self.rate)
        self.mixer.offset = SUBCARRIER
        self.reset()

    def reset(self) -> None:
        """A new station: forget everything received."""
        self.angle2 = 0.0
        self.profile = np.zeros(32)
        self.index = 0
        self.next_bit: float | None = None
        self.last_y = 0.0
        self.last_symbol = False
        self.register = 0
        self.bit_count = 0
        self.synced = False
        self.candidate: tuple[str, int] | None = None
        self.expect = "A"
        self.in_block = 0
        self.bad = 0
        self.group: dict[str, int] = {}
        self.pi: int | None = None
        self.pty = 0
        self.tp = False
        self.ta = False
        self.ps = [" "] * 8
        self.ps_seen = 0
        self.ps_text = ""
        self.rt = [""] * 64
        self.rt_seen = 0
        self.rt_ab: int | None = None
        self.rt_text = ""
        self.rtplus_group: int | None = None
        self.artist = ""
        self.title = ""
        self.groups = 0
        self.good_blocks = 0

    # ---------------------------------------------------------------- signal → bits

    def process(self, mpx: np.ndarray) -> None:
        if len(mpx) == 0:
            return
        z = self.decimate(self.mixer(mpx.astype(np.float32)))
        if len(z) == 0:
            return
        # Carrier phase: half the angle of the smoothed square, unwrapped across blocks
        squared = self.carrier(z * z)
        angle2 = np.unwrap(np.concatenate([[self.angle2], np.angle(squared)]))[1:]
        self.angle2 = float(angle2[-1] % (4 * math.pi))
        baseband = np.real(z * np.exp(-0.5j * angle2)).astype(np.float32)
        y = self.matched(baseband).astype(np.float64)

        # Bit timing: the matched filter's output is largest at the end of each bit, so look for
        # the point in the bit period where |y| is largest on average
        idx = self.index + np.arange(len(y))
        bins = ((idx % self.sps) / self.sps * len(self.profile)).astype(int) % len(self.profile)
        counts = np.bincount(bins, minlength=len(self.profile))
        sums = np.bincount(bins, weights=np.abs(y), minlength=len(self.profile))
        block_profile = np.where(counts > 0, sums / np.maximum(counts, 1), 0)
        self.profile = block_profile if not self.profile.any() else self.profile * 0.8 + block_profile * 0.2
        centroid = np.sum(self.profile * np.exp(2j * math.pi * np.arange(len(self.profile)) / len(self.profile)))
        best = (math.atan2(centroid.imag, centroid.real) / (2 * math.pi) % 1) * self.sps
        if self.next_bit is None:
            self.next_bit = self.index + best
        else:
            current = self.next_bit % self.sps
            delta = (best - current + self.sps / 2) % self.sps - self.sps / 2
            self.next_bit += 0.5 * delta

        last = self.index + len(y) - 1
        times = np.arange(max(self.next_bit, self.index - 1), last + 1e-9, self.sps)
        if len(times):
            values = np.interp(times, np.r_[self.index - 1, idx], np.r_[self.last_y, y])
            self.next_bit = float(times[-1] + self.sps)
            for value in values:
                symbol = value > 0
                self._bit(int(symbol != self.last_symbol))
                self.last_symbol = symbol
        self.last_y = float(y[-1])
        self.index += len(y)

    # ---------------------------------------------------------------- bits → blocks

    def _bit(self, bit: int) -> None:
        self.register = ((self.register << 1) | bit) & 0x3FFFFFF
        self.bit_count += 1
        if not self.synced:
            kind = SYNDROMES.get(syndrome(self.register))
            if kind is None:
                return
            if self.candidate and self.bit_count - self.candidate[1] == 26 and NEXT[self.candidate[0]] == kind[:1]:
                self.synced = True
                self.bad = 0
                self.in_block = 0
                self.group = {}
                self.expect = kind
                self._block(self.register)
            else:
                self.candidate = (kind, self.bit_count)
            return
        self.in_block += 1
        if self.in_block == 26:
            self.in_block = 0
            self._block(self.register)

    def _block(self, word: int) -> None:
        expect = self.expect
        found = syndrome(word)
        names = ("C", "C'") if expect == "C" else (expect,)
        name = next((n for n in names if found == OFFSETS[n]), None)
        if name is None:
            # Try correcting a burst of one or two bits
            for n in names:
                error = BURSTS.get(found ^ OFFSETS[n])
                if error is not None:
                    word ^= error
                    name = n
                    break
        self.expect = NEXT[expect]
        if name is None:
            self.bad += 1
            if self.bad > 12:
                self.synced = False
                self.candidate = None
            if expect == "D":
                self.group = {}
            return
        self.bad = max(0, self.bad - 1)
        self.good_blocks += 1
        self.group[name] = word >> 10
        if expect == "D":
            self._group(self.group)
            self.group = {}

    # ---------------------------------------------------------------- groups

    def _group(self, g: dict[str, int]) -> None:
        pi = g.get("A", g.get("C'"))
        if pi is not None:
            if self.pi is not None and pi != self.pi:
                # Another station on this frequency: start over (keeping the bit sync)
                self._forget_station()
            self.pi = pi
        b = g.get("B")
        if b is None:
            return
        self.groups += 1
        group_type, version = b >> 12, (b >> 11) & 1
        self.tp = bool(b >> 10 & 1)
        self.pty = (b >> 5) & 0x1F
        c, d = g.get("C"), g.get("D")
        if group_type == 0:
            self.ta = bool(b >> 4 & 1)
            if d is not None:
                self._ps(b & 3, d)
        elif group_type == 2:
            self._rt(b, c, d, version)
        elif group_type == 3 and version == 0 and d == RTPLUS_AID:
            self.rtplus_group = b & 0x1F
        elif self.rtplus_group is not None and (b >> 11) == self.rtplus_group and c is not None and d is not None:
            self._rtplus(b, c, d)

    def _forget_station(self) -> None:
        self.ps, self.ps_seen, self.ps_text = [" "] * 8, 0, ""
        self.rt, self.rt_seen, self.rt_ab, self.rt_text = [""] * 64, 0, None, ""
        self.rtplus_group, self.artist, self.title = None, "", ""
        self.groups = 0

    def _ps(self, segment: int, d: int) -> None:
        self.ps[segment * 2] = _char(d >> 8)
        self.ps[segment * 2 + 1] = _char(d & 0xFF)
        self.ps_seen |= 1 << segment
        if self.ps_seen == 0b1111:
            self.ps_text = "".join(self.ps).strip()

    def _rt(self, b: int, c: int | None, d: int | None, version: int) -> None:
        ab = b >> 4 & 1
        if self.rt_ab is not None and ab != self.rt_ab:
            # A/B flips: a new text follows
            self.rt, self.rt_seen = [""] * 64, 0
            self.artist = self.title = ""
        self.rt_ab = ab
        segment = b & 0xF
        if version == 0:
            if c is None or d is None:
                return
            start, codes = segment * 4, (c >> 8, c & 0xFF, d >> 8, d & 0xFF)
        else:
            if d is None:
                return
            start, codes = segment * 2, (d >> 8, d & 0xFF)
        for i, code in enumerate(codes):
            self.rt[start + i] = _char(code)
        self.rt_seen |= 1 << segment
        # Complete when every segment up to the end (a carriage return, or all 16) has come in
        text = "".join(ch or " " for ch in self.rt[: 64 if version == 0 else 32])
        end = text.find("\r")
        length = end if end >= 0 else len(text)
        needed = (1 << (end // len(codes) + 1 if end >= 0 else 16)) - 1
        if self.rt_seen & needed == needed:
            self.rt_text = " ".join(text[:length].split())

    def _rtplus(self, b: int, c: int, d: int) -> None:
        running = b >> 3 & 1
        if not running:
            self.artist = self.title = ""
            return
        tags = (
            (((b & 7) << 3) | (c >> 13), (c >> 7) & 0x3F, (c >> 1) & 0x3F),
            (((c & 1) << 5) | (d >> 11), (d >> 5) & 0x3F, d & 0x1F),
        )
        text = "".join(ch or " " for ch in self.rt)
        for kind, start, marker in tags:
            if kind not in (RTPLUS_TITLE, RTPLUS_ARTIST) or start >= len(text):
                continue
            value = " ".join(text[start : start + marker + 1].split())
            if not value or "\r" in value:
                continue
            if kind == RTPLUS_TITLE:
                self.title = value
            else:
                self.artist = value

    # ---------------------------------------------------------------- result

    def snapshot(self) -> dict | None:
        """What is known about the station, or None before the first group."""
        if self.pi is None and not self.groups:
            return None
        return {
            "pi": f"{self.pi:04X}" if self.pi is not None else None,
            "ps": self.ps_text,
            "rt": self.rt_text,
            "pty": PTY_NAMES[self.pty] if self.groups else "",
            "tp": self.tp,
            "ta": self.ta,
            "artist": self.artist,
            "title": self.title,
            "synced": self.synced,
        }
