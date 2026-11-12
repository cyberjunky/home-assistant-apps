"""Modes: the radio set up for one kind of signal, with a decoder reading it.

A mode tunes the radio to its own frequency (from the Settings page) and is then fed every
block the receiver delivers: the demodulated audio (pagers) or the samples around the tuned
frequency (sensors). What it decodes it hands to `publish`, which stores it and sends it to
the page. The radio stays usable: the frequency can be changed, and Listen still works.
"""

from collections.abc import Callable

import numpy as np

from ..errors import ValidationError

__all__ = ["Decoder", "ValidationError", "registry"]


class Decoder:
    key = ""
    name = ""
    # "audio": the demodulated audio of the tuned channel; "iq": the samples around the tuned frequency
    input = "audio"
    # Settings on the Settings page, with their defaults; frequencies in Hz
    defaults: dict = {}
    # How the radio is set up for this mode (mode and filter); the frequency comes from the settings
    demod = ("nfm", 12_500)
    # The receiver centred on the frequency (a mode that needs all of the receiver's bandwidth)
    centred = False
    # What decodes it, and how the signal is modulated, for the display
    decoded_by = "SDRadio"
    signal = ""

    def __init__(self, settings: dict, publish: Callable[[dict], None],
                 update: Callable[[dict], None] | None = None):  # fmt: skip
        self.settings = {**self.defaults, **(settings or {})}
        # publish: something decoded, stored and sent to the pages; update: the live view
        # changed (positions on a map), only sent
        self.publish = publish
        self.update = update
        self.error: str | None = None
        self.count = 0
        # Where the receiver is (latitude, longitude), when Home Assistant knows it
        self.location: tuple[float, float] | None = None
        # A folder of its own for what it saves (pictures, recordings), in the app's data folder
        self.folder = None
        # Set by the radio: tunes it (frequency, mode, bandwidth), for a mode that follows something
        self.retune = None

    @property
    def frequency(self) -> int:
        return int(self.settings.get("frequency") or 0)

    @classmethod
    def validate(cls, value: dict) -> dict:
        """The mode's settings from the Settings page, checked; raises ValidationError."""
        if not isinstance(value, dict):
            raise ValidationError(f"{cls.name}: settings must be an object")
        try:
            frequency = round(float(value.get("frequency", cls.defaults.get("frequency", 0))))
        except (TypeError, ValueError):
            raise ValidationError(f"{cls.name}: the frequency must be a number") from None
        if not 100_000 <= frequency <= 6_000_000_000:
            raise ValidationError(f"{cls.name}: the frequency must be between 0.1 and 6000 MHz")
        return {**cls.defaults, "frequency": frequency}

    @classmethod
    def available(cls, frequency_range: tuple[float, float] | None) -> bool:
        """Whether the receiver can be used for this mode (its range holds the mode's frequency)."""
        if not frequency_range:
            return True
        low, high = frequency_range
        frequency = cls.defaults.get("frequency", 0)
        return low <= frequency <= high

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

    def live(self) -> dict | None:
        """What the page shows live (aircraft on the map), for a page that opens; None if nothing."""
        return None


def registry() -> dict[str, type[Decoder]]:
    from .acars import Acars
    from .aircraft import Aircraft
    from .aprs import Aprs
    from .pager import Pager
    from .satellites import Satellites
    from .sensors import Sensors
    from .ships import Ships
    from .sstv import Sstv

    return {cls.key: cls for cls in (Pager, Sensors, Aircraft, Acars, Ships, Aprs, Sstv, Satellites)}
