"""Pager: POCSAG and FLEX messages, decoded by multimon-ng from the radio's own NFM audio.

The audio (48 kHz) goes through sox to the 22 050 Hz multimon-ng reads. FLEX has no public
specification, so it is left to multimon-ng rather than decoded here.
"""

import re

import numpy as np

from . import Decoder, ValidationError
from .program import Program

PROTOCOLS = ["FLEX", "POCSAG512", "POCSAG1200", "POCSAG2400"]
MULTIMON_RATE = 22_050

# POCSAG1200: Address:  123456  Function: 0  Alpha:   text
_POCSAG = re.compile(r"^(POCSAG\d+):\s+Address:\s*(\d+)\s+Function:\s*(\d)\s*(?:(Alpha|Numeric|Skyper):\s?(.*))?$")
# FLEX|2026-11-12 10:00:00|1600/2/K/A|07.034|001234567 001234568|ALN|text
_FLEX_TYPES = {"ALN": "alpha", "NUM": "numeric", "TON": "tone", "BIN": "binary", "SEC": "secure", "UNK": "unknown"}


def parse(line: str) -> dict | None:
    """A multimon-ng line as a pager message, or None for anything else."""
    if line.startswith("FLEX|"):
        parts = line.split("|")
        if len(parts) < 7:
            return None
        kind = parts[5].strip()
        return {
            "protocol": "FLEX",
            "rate": parts[2].strip(),
            "address": " ".join(parts[4].split()),
            "type": _FLEX_TYPES.get(kind, kind.lower()),
            # The text may itself hold a |
            "text": "|".join(parts[6:]).strip(),
        }
    match = _POCSAG.match(line.rstrip())
    if match:
        protocol, address, function, kind, text = match.groups()
        return {
            "protocol": protocol,
            "address": address,
            "function": int(function),
            "type": (kind or "tone").lower(),
            # multimon-ng marks control characters like <NUL>, <EOT>
            "text": re.sub(r"<(NUL|EOT|ETX|ETB)>", "", text or "").strip(),
        }
    return None


def multimon_command(rate: float, demods: list[str], options: str = "") -> list[str]:
    """sox (the radio's audio to the 22 050 Hz multimon-ng reads) into multimon-ng."""
    sox = (
        f"sox -q -t raw -e signed-integer -b 16 -c 1 -r {int(rate)} - "
        f"-t raw -e signed-integer -b 16 -c 1 -r {MULTIMON_RATE} -"
    )
    adds = " ".join(f"-a {d}" for d in demods)
    return ["sh", "-c", f"{sox} | multimon-ng -t raw {options} {adds} -".replace("  ", " ")]


class Pager(Decoder):
    key = "pager"
    name = "Pager"
    signal = "FSK"
    decoded_by = "multimon-ng"
    input = "audio"
    # Data, not speech: decoded from the audio, but not sent to the page
    audible = False
    # P2000, the Dutch emergency services' FLEX network
    defaults = {"frequency": 169_650_000, "protocols": PROTOCOLS, "ha_events": False}
    demod = ("nfm", 16_000)

    @classmethod
    def validate(cls, value: dict) -> dict:
        clean = super().validate(value)
        protocols = value.get("protocols", cls.defaults["protocols"])
        if not isinstance(protocols, list) or not protocols or any(p not in PROTOCOLS for p in protocols):
            raise ValidationError(f"Pager protocols: pick one or more of {', '.join(PROTOCOLS)}")
        clean["protocols"] = [p for p in PROTOCOLS if p in protocols]
        clean["ha_events"] = bool(value.get("ha_events", False))
        return clean

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.program: Program | None = None
        self.rate = 0.0

    def _command(self, rate: float) -> list[str]:
        # The function code tells numeric from alpha, as the standard has it (no -f)
        return multimon_command(rate, self.settings["protocols"], "-e")

    def feed_audio(self, audio: np.ndarray, rate: float) -> None:
        if self.program is None or rate != self.rate:
            if self.program:
                self.program.stop()
            self.rate = rate
            self.program = Program("multimon-ng", self._command(rate), self._line)
        pcm = (np.clip(audio, -1.0, 1.0) * 32000).astype("<i2").tobytes()
        self.program.write(pcm)
        self.error = self.program.error

    def _line(self, line: str) -> None:
        message = parse(line)
        if message is None:
            return
        if message["type"] in ("alpha", "numeric") and not message["text"]:
            return
        self.count += 1
        self.publish({"key": message["address"], **message})

    def stop(self) -> None:
        if self.program:
            self.program.stop()
            self.program = None
