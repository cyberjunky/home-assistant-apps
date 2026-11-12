"""Modes: the radio set up for one kind of signal, with a decoder reading it.

A mode tunes the radio to its own frequency (from the Settings page) and is then fed every
block the receiver delivers: the demodulated audio (pagers) or the samples around the tuned
frequency (sensors). What it decodes it hands to `publish`, which stores it and sends it to
the page. The radio stays usable: the frequency can be changed, and Listen still works.
"""

from collections.abc import Callable

import numpy as np


class Decoder:
    key = ""
    name = ""
    # "audio": the demodulated audio of the tuned channel; "iq": the samples around the tuned frequency
    input = "audio"
    # Settings on the Settings page, with their defaults; frequencies in Hz
    defaults: dict = {}
    # How the radio is set up for this mode (mode and filter); the frequency comes from the settings
    demod = ("nfm", 12_500)

    def __init__(self, settings: dict, publish: Callable[[dict], None]):
        self.settings = {**self.defaults, **(settings or {})}
        self.publish = publish
        self.error: str | None = None
        self.count = 0

    @property
    def frequency(self) -> int:
        return int(self.settings.get("frequency") or 0)

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def feed_audio(self, audio: np.ndarray, rate: float) -> None:
        pass

    def feed_iq(self, iq: np.ndarray, rate: float, offset: float) -> None:
        """iq: one block of the receiver's samples; offset: the tuned frequency minus the centre."""

    def status(self) -> dict:
        return {"error": self.error, "count": self.count}


def registry() -> dict[str, type[Decoder]]:
    from .pager import Pager
    from .sensors import Sensors

    return {cls.key: cls for cls in (Pager, Sensors)}


MODE_KEYS = ("pager", "sensors")
