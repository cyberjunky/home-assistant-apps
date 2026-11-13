"""Digital voice: DMR, D-STAR, YSF (System Fusion), NXDN, dPMR and P25, heard as speech.

Radio amateurs' repeaters and hotspots, businesses, taxis and some public services talk digitally:
4-level FSK (DMR, YSF, NXDN, dPMR, P25) or GMSK (D-STAR) on an FM channel, with the voice in the
AMBE or IMBE codec. DSD-FME reads the radio's FM audio (the discriminator, at 48 kHz) and writes
two things: the decoded voice (8 kHz, a channel for each of DMR's two timeslots) and an event log,
a line for each call with its talkgroup and the radio that spoke, its colour code (DMR) or NAC
(P25), and a text message, talker alias or GPS position when the call carried one.

The voice takes the place of the FM audio when you listen, so you hear the people talking, not the
digital noise. Encrypted calls stay silent (DSD-FME doesn't decode them without the key).
The codecs are decoded by mbelib, a software implementation of AMBE and IMBE.
"""

import re
import shlex
import tempfile
import threading
import time
from fractions import Fraction
from pathlib import Path

import numpy as np
from scipy import signal

from . import Decoder, ValidationError
from .program import Program

INPUT_RATE = 48_000
VOICE_RATE = 8_000
# DSD-FME's -f option and the channels of its voice output, per protocol
PROTOCOLS = {
    "auto": ("a", 2, "DMR, D-STAR, YSF, P25 (found by themselves)"),
    "dmr": ("s", 2, "DMR"),
    "dstar": ("d", 1, "D-STAR"),
    "ysf": ("y", 1, "YSF (System Fusion)"),
    "nxdn48": ("i", 1, "NXDN 6.25 kHz"),
    "nxdn96": ("n", 1, "NXDN 12.5 kHz"),
    "dpmr": ("m", 1, "dPMR"),
    "p25": ("1", 1, "P25 phase 1"),
}
# The voice kept for the page: a second; older is dropped (nobody listening, or the page behind)
VOICE_KEEP = VOICE_RATE
_HEADER = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d:\d\d) (\S+) (.*)$")
_FIELD = re.compile(
    r"\b(TGT|SRC|DST|CC|NAC|RAN|LCN|SYS|CAN|AFS|SITE): (.*?)(?=;|\s+\b(?:TGT|SRC|DST|CC|NAC|RAN|LCN|SYS|CAN|AFS|SITE):|\s*$)"
)
_FLAGS = ("ENC", "Emergency", "Group", "Private", "Broadcast")
_SYSTEMS = {"DSTAR": "D-STAR", "TMS": "DMR text"}


def _value(v: str) -> str:
    v = " ".join(v.split())
    return str(int(v)) if v.isdigit() else v


def parse_events(text: str) -> list[dict]:
    """DSD-FME's event log: a call per header line, with the lines under it (text, alias, GPS)."""
    calls: list[dict | None] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = _HEADER.match(line)
        if m:
            date, clock, system, rest = m.groups()
            # DSD-FME's own lines (started, ...): not a call, and nothing under them belongs to one
            if not re.search(r"\b(?:TGT|SRC|DST):", rest):
                calls.append(None)
                continue
            call: dict = {"system": _SYSTEMS.get(system, system), "time": f"{date} {clock}"}
            for key, value in _FIELD.findall(rest):
                call[{"TGT": "target", "SRC": "source", "DST": "target"}.get(key, key.lower())] = _value(value)
            flags = [f for f in _FLAGS if re.search(rf"\b{f};", rest)]
            if flags:
                call["flags"] = flags
            slot = re.search(r"Slot (\d);", rest)
            if slot:
                call["slot"] = int(slot.group(1))
            calls.append(call)
            continue
        if not calls or calls[-1] is None:
            continue
        extra = line.strip()
        call = calls[-1]
        if extra.startswith("Talker Alias:"):
            call["alias"] = extra.split(":", 1)[1].strip()
        elif extra.startswith("GPS:"):
            call["gps"] = extra.split(":", 1)[1].strip()
        elif extra.startswith("DSD-FME:"):
            continue
        else:
            call["text"] = (call.get("text", "") + "\n" + extra).strip()
    return [c for c in calls if c is not None]


class Digital(Decoder):
    key = "digital"
    name = "Digital voice"
    signal = "4FSK / GMSK"
    decoded_by = "DSD-FME"
    input = "audio"
    # A common DMR simplex frequency on 70 cm; set the repeater or hotspot under Modes
    defaults = {"frequency": 433_450_000, "protocol": "auto"}
    demod = ("nfm", 12_500)
    voice_rate = VOICE_RATE

    @classmethod
    def validate(cls, value: dict) -> dict:
        clean = super().validate(value)
        protocol = value.get("protocol", "auto")
        if protocol not in PROTOCOLS:
            raise ValidationError(f"Digital voice: the protocol is one of {', '.join(PROTOCOLS)}")
        clean["protocol"] = protocol
        return clean

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.protocol = self.settings.get("protocol", "auto")
        self.program: Program | None = None
        self.rate = 0.0
        self.resample: tuple[int, int] | None = None
        self.workdir = Path(tempfile.mkdtemp(prefix="sdradio-dsd-"))
        self.events = self.workdir / "events.txt"
        self.events_read = 0
        self.events_due = 0.0
        self.voice = bytearray()
        self.voice_lock = threading.Lock()
        self.last_voice = 0.0
        self.calls = 0

    def _command(self) -> list[str]:
        flag = PROTOCOLS[self.protocol][0]
        # The console output (a screenful a second) goes nowhere; the calls come from the event log
        return ["sh", "-c", f"exec dsd-fme -f{flag} -i - -o - -J {shlex.quote(str(self.events))} 2>/dev/null"]

    def feed_audio(self, audio: np.ndarray, rate: float) -> None:
        if self.program is None or rate != self.rate:
            if self.program:
                self.program.stop()
            self.rate = rate
            ratio = Fraction(INPUT_RATE, round(rate)).limit_denominator(1000)
            self.resample = None if ratio == 1 else (ratio.numerator, ratio.denominator)
            self.program = Program("dsd-fme", self._command(), self._voice, backlog=40, binary=True)
        x = audio if self.resample is None else signal.resample_poly(audio, *self.resample)
        self.program.write((np.clip(x, -1.0, 1.0) * 32000).astype("<i2").tobytes())
        self.error = self.program.error
        now = time.monotonic()
        if now >= self.events_due:
            self.events_due = now + 0.5
            self._read_events()

    def _voice(self, chunk: bytes) -> None:
        """Decoded speech from DSD-FME (8 kHz, one or two channels): kept as mono for the page."""
        pcm = np.frombuffer(chunk[: len(chunk) // 2 * 2], "<i2")
        channels = PROTOCOLS[self.protocol][1]
        if channels == 2:
            pcm = pcm[: len(pcm) // 2 * 2].reshape(-1, 2)
            # The two DMR timeslots together (one is mostly silent)
            pcm = np.clip(pcm.astype(np.int32).sum(axis=1), -32768, 32767).astype("<i2")
        with self.voice_lock:
            self.voice.extend(pcm.tobytes())
            del self.voice[: max(0, len(self.voice) - VOICE_KEEP * 2)]
            self.last_voice = time.monotonic()

    def take_voice(self) -> bytes | None:
        """The decoded speech since the last call (for the radio to send to the listening pages)."""
        with self.voice_lock:
            if not self.voice:
                return None
            out = bytes(self.voice)
            self.voice.clear()
        return out

    @property
    def speaking(self) -> bool:
        return time.monotonic() - self.last_voice < 0.5

    def _read_events(self) -> None:
        try:
            size = self.events.stat().st_size
        except OSError:
            return
        if size <= self.events_read:
            return
        with self.events.open("rb") as f:
            f.seek(self.events_read)
            data = f.read(size - self.events_read)
        # Only whole lines; a call's text lines come right after it, in the same write
        end = data.rfind(b"\n") + 1
        if not end:
            return
        self.events_read += end
        for call in parse_events(data[:end].decode("utf-8", errors="replace")):
            self.count += 1
            key = call.get("target") or call.get("source") or call["system"]
            self.publish({"key": str(key), **call})

    def status(self) -> dict:
        return {**super().status(), "protocol": PROTOCOLS[self.protocol][2], "speaking": self.speaking}

    def stop(self) -> None:
        if self.program:
            self.program.stop()
            self.program = None
        self._read_events()
        import shutil

        shutil.rmtree(self.workdir, ignore_errors=True)
