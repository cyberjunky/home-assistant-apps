"""Sensors: weather stations, thermometers, doorbells, remotes and meters on 433 and 868 MHz.

rtl_433 does the decoding (it knows some 250 kinds of device). It gets 240 kHz of samples round
the tuned frequency, cut out of the receiver's 2.4 MHz by the app, as 16-bit IQ on its stdin,
and writes one JSON object per reading.
"""

import json

import numpy as np

from ..dsp import Decimator, Mixer
from . import Decoder, ValidationError
from .program import Program

RATE = 240_000
# Fields that identify a device or describe the reading itself, not what it measured
META = {"time", "model", "id", "channel", "subtype", "mic", "mod", "freq", "freq1", "freq2", "rssi", "snr", "noise",
        "protocol"}  # fmt: skip
FREQUENCIES = [433_920_000, 868_300_000, 868_950_000, 915_000_000, 315_000_000, 345_000_000]


def device_key(reading: dict) -> str:
    """model/id/channel: the same device reading after reading."""
    parts = [str(reading.get("model", "unknown"))]
    for field in ("id", "channel"):
        if reading.get(field) is not None:
            parts.append(str(reading[field]))
    return "/".join(parts)


def parse(line: str) -> dict | None:
    """An rtl_433 JSON line as a reading, or None for anything else."""
    if not line.startswith("{"):
        return None
    try:
        reading = json.loads(line)
    except ValueError:
        return None
    if not isinstance(reading, dict) or "model" not in reading:
        return None
    return {
        "key": device_key(reading),
        "model": reading.get("model"),
        "id": reading.get("id"),
        "channel": reading.get("channel"),
        "values": {k: v for k, v in reading.items() if k not in META},
        "rssi": reading.get("rssi"),
        "snr": reading.get("snr"),
    }


class Sensors(Decoder):
    key = "sensors"
    name = "Sensors"
    input = "iq"
    defaults = {"frequency": 433_920_000, "ha_devices": []}
    # Heard as AM (most of these devices switch their carrier on and off)
    demod = ("am", 15_000)

    @classmethod
    def validate(cls, value: dict) -> dict:
        clean = super().validate(value)
        devices = value.get("ha_devices", [])
        if not isinstance(devices, list) or not all(isinstance(d, str) for d in devices):
            raise ValidationError("Sensors: ha_devices must be a list of device keys")
        clean["ha_devices"] = sorted(set(devices))
        return clean

    def __init__(self, settings, publish, update=None):
        super().__init__(settings, publish, update)
        self.program: Program | None = None
        self.mixer: Mixer | None = None
        self.decimate: Decimator | None = None
        self.rate = 0.0

    def feed_iq(self, iq: np.ndarray, rate: float, offset: float) -> None:
        if self.program is None or rate != self.rate:
            if self.program:
                self.program.stop()
            self.rate = rate
            factor = max(1, round(rate / RATE))
            self.mixer = Mixer(rate)
            # 200 kHz wide: the devices drift a lot round their nominal frequency
            self.decimate = Decimator(factor, 100_000 / (rate / 2), k=6)
            command = ["rtl_433", "-r", "cs16:-", "-s", str(round(rate / factor)), "-F", "json"]
            self.program = Program("rtl_433", [*command, "-M", "level", "-M", "protocol"], self._line)
        self.mixer.offset = offset
        x = self.decimate(self.mixer(iq))
        interleaved = np.empty(len(x) * 2, dtype="<i2")
        interleaved[0::2] = np.clip(x.real * 32767, -32768, 32767)
        interleaved[1::2] = np.clip(x.imag * 32767, -32768, 32767)
        self.program.write(interleaved.tobytes())
        self.error = self.program.error

    def _line(self, line: str) -> None:
        reading = parse(line)
        if reading is None:
            return
        self.count += 1
        self.publish(reading)

    def stop(self) -> None:
        if self.program:
            self.program.stop()
            self.program = None
