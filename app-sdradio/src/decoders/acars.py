"""ACARS: the short text messages between aircraft and their airlines, on the VHF air band.

Decoded here from the receiver's samples, on up to three channels at once (the European ones,
131.525, 131.725 and 131.825 MHz, lie within 300 kHz). A message is a burst of AM: the carrier
keyed, modulated with MSK audio at 2400 baud, where each symbol is a full cycle of 2400 Hz (the
bit is the same as the one before) or half a cycle of 1200 Hz (the bit changes). The envelope
of each burst is matched against those two shapes symbol by symbol, following the symbol clock.
The characters are 7-bit ASCII with odd parity, least significant bit first, framed as
+ * SYN SYN SOH, mode, aircraft registration, acknowledgement, label, block, STX, text, ETX
or ETB, and a block check. A frame counts when every character's parity is right; whether its
block check matches as well is shown with it.
"""

import numpy as np

from ..dsp import Decimator, Mixer
from . import Decoder, ValidationError

BAUD = 2400
RATE = 48_000
SPS = RATE // BAUD
WIDE_RATE = 480_000
EUROPE = [131_525_000, 131_725_000, 131_825_000]
# The receiver's samples are cut to this much round the middle of the channels
SPAN = 400_000

LABELS = {
    "_d": "acknowledgement", "SQ": "ground station", "Q0": "link test", "H1": "message", "5Z": "airline",
    "QA": "out", "QB": "off", "QC": "on", "QD": "in", "QE": "out, fuel", "QF": "off, destination",
    "15": "position", "16": "position", "20": "position", "22": "position", "30": "position", "80": "airline",
    "B6": "oceanic clearance", "AA": "free text", "C1": "free text", "RA": "free text", "10": "free text",
}  # fmt: skip

_A = np.sin(np.pi * (np.arange(SPS) + 0.5) / SPS).astype(np.float32)  # half a cycle of 1200 Hz
_B = np.sin(2 * np.pi * (np.arange(SPS) + 0.5) / SPS).astype(np.float32)  # a cycle of 2400 Hz


def _with_parity(c: int) -> int:
    return c | (0 if bin(c).count("1") % 2 else 0x80)


def _bits_of(values: list[int]) -> list[int]:
    return [(b >> i) & 1 for b in values for i in range(8)]


SYNC = np.array(_bits_of([_with_parity(c) for c in (0x16, 0x16, 0x01)]), dtype=np.int8)


def crc_kermit(data: bytes) -> int:
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0x8408 if crc & 1 else crc >> 1
    return crc


def symbols(envelope: np.ndarray) -> np.ndarray:
    """The burst's tones (True: 2400 Hz), following the symbol clock: its phase is found by trying
    every sample, then each symbol may move a sample either way to stay on the boundaries."""
    # Only where the carrier is on, as modulation depth: the envelope over the carrier's level
    level = np.convolve(envelope, np.ones(SPS * 2) / (SPS * 2), mode="same")
    on = np.flatnonzero(level > level.max() / 2)
    if len(on) < 40 * SPS:
        return np.zeros(0, dtype=bool)
    envelope = envelope[on[0] : on[-1] + 1]
    width = 20 * SPS
    padded = np.pad(envelope, width // 2, mode="edge")
    carrier = np.convolve(padded, np.ones(width) / width, mode="same")[width // 2 : width // 2 + len(envelope)]
    a = envelope / np.maximum(carrier, 1e-9) - 1.0
    # The clock from the second half, where the data is: the pre-key's 2400 Hz cycles fit just as
    # well half a symbol off
    half = len(a) // 2 // SPS * SPS
    best, start = -1.0, 0
    for phase in range(SPS):
        n = (len(a) - half - phase) // SPS
        s = a[half + phase : half + phase + n * SPS].reshape(n, SPS)
        score = float(np.maximum(np.abs(s @ _A), np.abs(s @ _B)).sum())
        if score > best:
            best, start = score, phase
    out = []
    pos = start
    while pos + SPS + 1 <= len(a):
        choices = []
        for shift in (-1, 0, 1):
            p = pos + shift
            if p < 0 or p + SPS > len(a):
                continue
            seg = a[p : p + SPS]
            ca, cb = float(seg @ _A), float(seg @ _B)
            choices.append((max(abs(ca), abs(cb)), p, abs(cb) > abs(ca)))
        _, pos, tone = max(choices)
        out.append(tone)
        pos += SPS
    return np.array(out, dtype=bool)


def frames(tones: np.ndarray) -> list[dict]:
    """The frames in a burst's tones."""
    bits = np.empty(len(tones), dtype=np.int8)
    previous = 1
    for i, same in enumerate(tones.tolist()):
        previous = previous if same else 1 - previous
        bits[i] = previous
    found = []
    if len(bits) < len(SYNC):
        return found
    # The tones give the bits only up to their sign (where the burst began): try both
    for stream in (bits, 1 - bits):
        windows = np.lib.stride_tricks.sliding_window_view(stream, len(SYNC))
        for at in np.flatnonzero((windows == SYNC).all(axis=1)):
            message = _read(stream, int(at) + len(SYNC))
            if message:
                found.append(message)
    return found


def _read(bits: np.ndarray, at: int) -> dict | None:
    chars = []
    pos = at
    while pos + 8 <= len(bits) and len(chars) < 260:
        value = sum(int(bits[pos + i]) << i for i in range(8))
        pos += 8
        if bin(value).count("1") % 2 == 0:
            return None  # a parity error: not a frame (or not all of it)
        chars.append(value)
        if value & 0x7F in (0x03, 0x17) and len(chars) >= 13:
            break
    else:
        return None
    if pos + 16 > len(bits):
        return None
    bcs = sum(int(bits[pos + i]) << i for i in range(16))
    text = [c & 0x7F for c in chars]
    # STX and the text, or straight to ETX when there is no text
    if text[12] == 0x02:
        body = "".join(chr(c) for c in text[13:-1])
    elif len(text) == 13:
        body = ""
    else:
        return None
    registration = "".join(chr(c) for c in text[1:8]).lstrip(".").strip()
    label = "".join(chr(c) for c in text[9:11])
    block = chr(text[11])
    message = {
        "mode": chr(text[0]),
        "registration": registration,
        "label": label,
        "label_name": LABELS.get(label),
        "block": block,
        "ack": chr(text[8]) if text[8] != 0x15 else None,
        "text": body.replace("\r\n", "\n").strip(),
        "crc": crc_kermit(bytes(chars)) == bcs,
        "more": text[-1] == 0x17,
    }
    # From the aircraft (block 0-9): the text starts with a message number and the flight
    if block.isdigit() and len(body) >= 10:
        message["number"] = body[:4]
        message["flight"] = body[4:10].strip()
        message["text"] = body[10:].replace("\r\n", "\n").strip()
        message["direction"] = "down"
    else:
        message["direction"] = "up"
    return message


class Channel:
    def __init__(self, frequency: int, offset: float):
        self.frequency = frequency
        self.mixer = Mixer(WIDE_RATE)
        self.mixer.offset = offset
        self.decimate = Decimator(WIDE_RATE // RATE, 5_000 / (WIDE_RATE / 2), k=8)
        self.floor = 0.0
        self.burst: list[np.ndarray] | None = None
        self.quiet = 0
        self.before = np.zeros(0, dtype=np.float32)

    def __call__(self, wide: np.ndarray) -> list[dict]:
        env = np.abs(self.decimate(self.mixer(wide))).astype(np.float32)
        found = []
        # Look at the level a millisecond at a time: a burst is the carrier switched on
        for i in range(0, len(env) - 47, 48):
            chunk = env[i : i + 48]
            level = float(chunk.mean())
            if self.burst is None:
                if self.floor and level > 4 * self.floor:
                    self.burst = [self.before[-96:], chunk]
                    self.quiet = 0
                else:
                    self.floor = level if not self.floor else 0.98 * self.floor + 0.02 * level
                    self.before = np.concatenate([self.before[-48:], chunk])
            else:
                self.burst.append(chunk)
                self.quiet = self.quiet + 1 if level < 2 * self.floor else 0
                if self.quiet >= 5 or len(self.burst) > 1600:
                    burst = np.concatenate(self.burst)
                    self.burst = None
                    if len(burst) > 48 * 40:
                        found += frames(symbols(burst))
        return found


class Acars(Decoder):
    key = "acars"
    name = "ACARS"
    input = "iq"
    defaults = {"frequency": 131_725_000, "channels": EUROPE}
    demod = ("am", 8_333)

    @classmethod
    def validate(cls, value: dict) -> dict:
        clean = super().validate(value)
        channels = value.get("channels", EUROPE)
        try:
            channels = sorted({round(float(c)) for c in channels})
        except (TypeError, ValueError):
            raise ValidationError("ACARS: the channels must be frequencies in Hz") from None
        if not channels or channels[-1] - channels[0] > SPAN - 30_000:
            raise ValidationError("ACARS: one to a few channels, within 370 kHz of each other")
        clean["channels"] = channels
        clean["frequency"] = channels[len(channels) // 2]
        return clean

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.rate = 0.0
        self.mixer: Mixer | None = None
        self.decimate: Decimator | None = None
        self.channels: list[Channel] = []
        self.middle = 0.0

    def feed_iq(self, iq: np.ndarray, rate: float, offset: float) -> None:
        if rate != self.rate:
            self.rate = rate
            self.mixer = Mixer(rate)
            self.decimate = Decimator(max(1, round(rate / WIDE_RATE)), (SPAN / 2) / (rate / 2), k=4)
            wanted = self.settings.get("channels") or EUROPE
            self.middle = (min(wanted) + max(wanted)) / 2
            self.channels = [Channel(f, f - self.middle) for f in wanted]
        # The middle of the channels to 0 Hz, whatever the radio is tuned to
        self.mixer.offset = offset + (self.middle - self.frequency)
        wide = self.decimate(self.mixer(iq))
        for channel in self.channels:
            for message in channel(wide):
                self.count += 1
                key = message.get("flight") or message["registration"] or "ground"
                self.publish({"key": key, "frequency": channel.frequency, **message})
