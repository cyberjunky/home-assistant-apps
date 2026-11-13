"""The tones under and in FM speech: CTCSS, DCS and DTMF, read from the radio's NFM audio.

CTCSS is a steady tone under the speech (67 to 254.1 Hz) that opens the squelch of the radios set
to it, the way a repeater or a group of walkie-talkies is told apart; DCS does the same with a
23-bit code word sent over and over at 134.4 bit/s below 300 Hz. DTMF is the keypad's two-tone
digits. All three are shown on the display, under the frequency, as RDS is for broadcast FM.

The code words are built from the format (a (23,12) Golay code, generator 0xC75, nine code bits
then 100, sent lowest bit first); code 023 gives 0x763813, and +023 sends what -047 does, as the
format says it must. A word can be read from any of its 23 bits, so the codes that are each
other's rotation can't be told apart (as on any radio): the lowest of them is shown.
"""

import numpy as np

from .dsp import Decimator

CTCSS = [67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5, 94.8, 97.4, 100.0, 103.5, 107.2, 110.9,
         114.8, 118.8, 123.0, 127.3, 131.8, 136.5, 141.3, 146.2, 150.0, 151.4, 156.7, 159.8, 162.2, 165.5, 167.9,
         171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9, 192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7,
         229.1, 233.6, 241.8, 250.3, 254.1]  # fmt: skip
DCS = [23, 25, 26, 31, 32, 36, 43, 47, 51, 53, 54, 65, 71, 72, 73, 74, 114, 115, 116, 122, 125, 131, 132, 134, 143,
       145, 152, 155, 156, 162, 165, 172, 174, 205, 212, 223, 225, 226, 243, 244, 245, 246, 251, 252, 255, 261, 263,
       265, 266, 271, 274, 306, 311, 315, 325, 331, 332, 343, 346, 351, 356, 364, 365, 371, 411, 412, 413, 423, 431,
       432, 445, 446, 452, 454, 455, 462, 464, 465, 466, 503, 506, 516, 523, 526, 532, 546, 565, 606, 612, 624, 627,
       631, 632, 654, 662, 664, 703, 712, 723, 731, 732, 734, 743, 754]  # fmt: skip
DTMF_ROWS = [697.0, 770.0, 852.0, 941.0]
DTMF_COLS = [1209.0, 1336.0, 1477.0, 1633.0]
KEYS = ["123A", "456B", "789C", "*0#D"]
GOLAY = 0xC75
DCS_RATE = 134.4
MASK = (1 << 23) - 1
WINDOW = 1.5  # seconds of the low band looked at, every half second
HOLD = 2.0  # a tone or code stays on the display this long after it was last heard
DIGITS_HOLD = 10.0


def dcs_word(code: int) -> int:
    """The 23-bit word of a DCS code (written in octal, as 023), lowest bit sent first."""
    info = int(str(code), 8) | 0x800
    r = info << 11
    for i in range(22, 10, -1):
        if r >> i & 1:
            r ^= GOLAY << (i - 11)
    return info | (r & 0x7FF) << 12


def _words() -> dict[int, str]:
    found: dict[int, str] = {}
    for code in DCS:
        w = dcs_word(code)
        for value, name in ((w, f"D{code:03d}N"), (w ^ MASK, f"D{code:03d}I")):
            found.setdefault(value, name)
    return found


WORDS = _words()


class ToneReader:
    """Fed with every block of NFM audio; `snapshot()` is what is heard now."""

    def __init__(self, rate: float):
        self.rate = float(rate)
        self.to_8k = Decimator(max(1, round(rate / 8_000)), 3_400 / (rate / 2), k=8, dtype=np.float32)
        self.rate_8k = self.rate / self.to_8k.factor
        self.to_1k = Decimator(8, 280 / (self.rate_8k / 2), k=12, dtype=np.float32)
        self.rate_1k = self.rate_8k / 8
        self.low = np.zeros(0, np.float32)
        self.since = 0
        self.dtmf_buffer = np.zeros(0, np.float32)
        self.frame = int(self.rate_8k * 0.025)
        self.time = 0.0  # seconds of audio fed: the reader's own clock
        self.ctcss: float | None = None
        self.dcs: str | None = None
        self.digits = ""
        self.heard = {"ctcss": -1e9, "dcs": -1e9, "digit": -1e9}
        self._candidate: tuple | None = None
        self._key: str | None = None
        self._key_frames = 0

    def process(self, audio: np.ndarray) -> None:
        self.time += len(audio) / self.rate
        x = self.to_8k(audio.astype(np.float32))
        self._dtmf(x)
        low = self.to_1k(x)
        self.low = np.concatenate([self.low, low])[-int(WINDOW * self.rate_1k) :]
        self.since += len(low)
        if self.since >= self.rate_1k / 2 and len(self.low) >= int(WINDOW * self.rate_1k) - 1:
            self.since = 0
            self._low_band()

    # ---------------------------------------------------------------- CTCSS and DCS

    def _low_band(self) -> None:
        x = self.low - np.mean(self.low)
        total = float(np.sum(x * x))
        if total <= 1e-12:
            return
        tone = self._tone(x, total)
        code = None if tone else self._code(x)
        found = ("ctcss", tone) if tone else ("dcs", code) if code else None
        # Twice in a row (a second) before it is shown
        if found and found == self._candidate:
            setattr(self, found[0], found[1])
            self.heard[found[0]] = self.time
        self._candidate = found
        for kind in ("ctcss", "dcs"):
            if self.time - self.heard[kind] > HOLD:
                setattr(self, kind, None)

    def _tone(self, x: np.ndarray, total: float) -> float | None:
        n = 1 << 15
        spectrum = np.abs(np.fft.rfft(x * np.hanning(len(x)), n)) ** 2
        freqs = np.fft.rfftfreq(n, 1 / self.rate_1k)
        band = (freqs >= 60) & (freqs <= 262)
        if not band.any():
            return None
        i = int(np.argmax(np.where(band, spectrum, 0)))
        # A tone: most of the low band's power in the peak's main lobe
        lobe = np.abs(freqs - freqs[i]) <= 1.5 / WINDOW * 2
        share = float(np.sum(spectrum[lobe])) / float(np.sum(spectrum[freqs <= 280]) or 1)
        if share < 0.5:
            return None
        nearest = min(CTCSS, key=lambda t: abs(t - freqs[i]))
        return nearest if abs(nearest - freqs[i]) <= 0.6 else None

    def _code(self, x: np.ndarray) -> str | None:
        per_bit = self.rate_1k / DCS_RATE
        best: dict[str, int] = {}
        for phase in np.arange(8) / 8:
            at = (phase + np.arange(int((len(x) - per_bit) / per_bit))) * per_bit
            bits = (x[at.astype(int)] > 0).astype(np.int64)
            if len(bits) < 46:
                return None
            windows = np.zeros(len(bits) - 22, np.int64)
            for j in range(23):
                windows |= bits[j : j + len(windows)] << j
            hits: dict[str, int] = {}
            for w in windows.tolist():
                name = WORDS.get(w)
                if name:
                    hits[name] = hits.get(name, 0) + 1
            if sum(hits.values()) > sum(best.values()):
                best = hits
        if not best:
            return None
        most = max(best.values())
        # A code is heard once every 23 bits: at least four times in the window, not by chance
        if most < 4:
            return None
        return min((n for n, c in best.items() if c == most), key=lambda n: (n[-1] == "I", n))

    # ---------------------------------------------------------------- DTMF

    def _dtmf(self, x: np.ndarray) -> None:
        self.dtmf_buffer = np.concatenate([self.dtmf_buffer, x])
        while len(self.dtmf_buffer) >= self.frame:
            frame, self.dtmf_buffer = self.dtmf_buffer[: self.frame], self.dtmf_buffer[self.frame :]
            key = self._key_in(frame)
            if key and key == self._key:
                self._key_frames += 1
                # Two frames (50 ms) of one key: a press, counted once
                if self._key_frames == 2:
                    if self.time - self.heard["digit"] > 3.0:
                        self.digits = ""
                    self.digits = (self.digits + key)[-24:]
                    self.heard["digit"] = self.time
            else:
                self._key, self._key_frames = key, 1 if key else 0
        if self.digits and self.time - self.heard["digit"] > DIGITS_HOLD:
            self.digits = ""

    def _key_in(self, frame: np.ndarray) -> str | None:
        frame = frame - np.mean(frame)
        total = float(np.sum(frame * frame))
        if total <= 1e-10:
            return None
        t = np.arange(len(frame)) / self.rate_8k
        window = np.hanning(len(frame))

        def power(f):
            return abs(np.sum(frame * window * np.exp(-2j * np.pi * f * t))) ** 2

        rows = [power(f) for f in DTMF_ROWS]
        cols = [power(f) for f in DTMF_COLS]
        r, c = int(np.argmax(rows)), int(np.argmax(cols))
        others_r = sorted(rows)[-2]
        others_c = sorted(cols)[-2]
        # Both tones clear of the other rows and columns, and most of the sound
        if rows[r] < 6 * others_r or cols[c] < 6 * others_c:
            return None
        # A tone of power P comes out as P · (Σw)² / 2N: the two together near the whole frame's
        share = (rows[r] + cols[c]) / (total * float(np.sum(window)) ** 2 / (2 * len(frame)))
        if share < 0.6 or not 0.15 < rows[r] / cols[c] < 6.5:
            return None
        return KEYS[r][c]

    def snapshot(self) -> dict | None:
        if not (self.ctcss or self.dcs or self.digits):
            return None
        return {"ctcss": self.ctcss, "dcs": self.dcs, "dtmf": self.digits or None}
