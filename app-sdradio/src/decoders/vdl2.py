"""VDL2: VHF Data Link mode 2, the digital successor of ACARS, busier over Europe than ACARS itself.

Aircraft and ground stations exchange ACARS messages, controller-pilot messages (CPDLC) and
position reports (ADS-C) over it, at 31.5 kbit/s on 136.725 to 136.975 MHz. dumpvdl2 decodes it
(with libacars for the messages inside) from the receiver's samples: in this mode the receiver
runs at 2.1 MS/s, twenty times VDL2's 105 kHz, as dumpvdl2 wants it, and dumpvdl2 listens on all
the channels at once. The frames that carry a message are kept, as the ACARS mode's are.
"""

import json

import numpy as np

from . import Decoder, ValidationError
from .program import Program

RATE = 2_100_000
OVERSAMPLE = RATE // 105_000
# The common signalling channel and the other European VDL2 channels
EUROPE = [136_725_000, 136_775_000, 136_875_000, 136_975_000]
SPAN = 800_000


def _text(value) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def parse(line: str) -> dict | None:
    """A dumpvdl2 JSON frame as a message, or None for frames without one (link upkeep)."""
    if not line.startswith("{"):
        return None
    try:
        frame = json.loads(line).get("vdl2") or {}
    except (ValueError, AttributeError):
        return None
    avlc = frame.get("avlc") or {}
    src, dst = avlc.get("src") or {}, avlc.get("dst") or {}
    acars = avlc.get("acars")
    aircraft = src if src.get("type") == "Aircraft" else dst if dst.get("type") == "Aircraft" else {}
    base = {
        "frequency": frame.get("freq"),
        "aircraft": aircraft.get("addr"),
        "direction": "down" if src.get("type") == "Aircraft" else "up",
        "signal": round(float(frame.get("sig_level", 0)), 1),
    }
    if acars:
        return {
            **base,
            "kind": "ACARS",
            "registration": str(acars.get("reg") or "").lstrip(".").strip(),
            "flight": str(acars.get("flight") or "").strip() or None,
            "label": acars.get("label"),
            "text": _text(acars.get("msg_text")),
            "crc": acars.get("crc_ok"),
        }
    # CPDLC and ADS-C through ATN (libacars decodes them); everything else is link upkeep
    for key, kind in (("cpdlc", "CPDLC"), ("adsc", "ADS-C")):
        found = _find(avlc, key)
        if found is not None:
            return {**base, "kind": kind, "registration": "", "flight": None, "label": kind,
                    "text": json.dumps(found, separators=(", ", ": "))[:600]}  # fmt: skip
    return None


def _find(d, key):
    if isinstance(d, dict):
        if key in d:
            return d[key]
        for v in d.values():
            found = _find(v, key)
            if found is not None:
                return found
    elif isinstance(d, list):
        for v in d:
            found = _find(v, key)
            if found is not None:
                return found
    return None


class Vdl2(Decoder):
    key = "vdl2"
    name = "VDL2"
    signal = "D8PSK"
    decoded_by = "dumpvdl2"
    input = "iq"
    listen = False
    sample_rate = RATE
    # Tuned to the middle channel, as ACARS is: the receiver's centre (and an RTL-SDR's spike) off them all
    defaults = {"frequency": EUROPE[len(EUROPE) // 2], "channels": EUROPE}
    demod = ("am", 15_000)

    @classmethod
    def validate(cls, value: dict) -> dict:
        clean = super().validate(value)
        try:
            channels = sorted({round(float(c)) for c in value.get("channels", EUROPE)})
        except (TypeError, ValueError):
            raise ValidationError("VDL2: the channels must be frequencies in Hz") from None
        if not channels or channels[-1] - channels[0] > SPAN:
            raise ValidationError("VDL2: one to a few channels, within 800 kHz of each other")
        clean["channels"] = channels
        clean["frequency"] = channels[len(channels) // 2]
        return clean

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.program: Program | None = None
        self.centre = 0.0
        self.rate = 0.0
        self.frames = 0

    def feed_iq(self, iq: np.ndarray, rate: float, offset: float) -> None:
        if rate != RATE:
            self.error = "The receiver is not at VDL2's 2.1 MS/s"
            return
        # The receiver's own centre: where dumpvdl2 has to know the samples are from
        centre = round(self.frequency - offset)
        if self.program is None or centre != self.centre:
            if self.program:
                self.program.stop()
            self.centre = centre
            channels = [str(c) for c in self.settings.get("channels") or EUROPE]
            command = ["dumpvdl2", "--iq-file", "-", "--sample-format", "S16_LE", "--oversample", str(OVERSAMPLE),
                       "--centerfreq", str(centre), "--output", "decoded:json:file:path=-", *channels]  # fmt: skip
            self.program = Program("dumpvdl2", command, self._line, backlog=20)
        interleaved = np.empty(len(iq) * 2, dtype="<i2")
        interleaved[0::2] = np.clip(iq.real * 32767, -32768, 32767)
        interleaved[1::2] = np.clip(iq.imag * 32767, -32768, 32767)
        self.program.write(interleaved.tobytes())
        self.error = self.program.error

    def _line(self, line: str) -> None:
        if line.startswith("{"):
            self.frames += 1
        message = parse(line)
        if message is None:
            return
        self.count += 1
        key = message.get("flight") or message.get("registration") or message.get("aircraft") or "ground"
        self.publish({"key": key, **message})

    def status(self) -> dict:
        return {**super().status(), "frames": self.frames}

    def stop(self) -> None:
        if self.program:
            self.program.stop()
            self.program = None
