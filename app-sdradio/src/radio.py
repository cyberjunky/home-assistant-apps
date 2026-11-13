"""The radio: one thread that reads the receiver, demodulates, scans and reports.

The web server talks to it through commands (a queue) and gets audio, spectrum frames,
state and hits back through the `emit` callback, which hands them to the event loop.
"""

import logging
import math
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .bands import MODES, STEPS, band_for, default_bandwidth
from .config import Config
from .database import Database
from .decoders import Decoder, registry
from .device import DeviceError
from .dsp import DB_FLOOR, Demodulator, Spectrum, find_signal, noise_floor, quantize, to_pcm16

logger = logging.getLogger(__name__)

BLOCKS_PER_SECOND = 20
# Blocks thrown away after the receiver moved to another centre frequency
SETTLE_BLOCKS = 2
# Keep the tuned frequency this far inside the edge of the received span
EDGE = 0.42
# Blocks of averaged spectrum autotune waits for (after a retune)
AUTOTUNE_BLOCKS = 6
# When the receiver has to move, put the tuned frequency this far (of the span) off the centre,
# away from the receiver's own DC spike in the middle
RECENTRE_OFFSET = 0.1
# Hits shorter than this are noise bursts, not transmissions
MIN_HIT_SECONDS = 0.3
SQUELCH_AUTO_MARGIN = 10.0
SCAN_MIN_SNR = 6.0
LISTEN_HANG_SECONDS = 1.0
# The band view: the receiver steps across the band, using the middle of each capture
# (its edges roll off), at most this wide
SWEEP_USE = 0.8
SWEEP_MAX = 60_000_000
# Around the tuned frequency when it is in no band (or a band narrower than one capture)
SWEEP_DEFAULT = 10_000_000
# Bins of the receiver's DC spike in the middle of a capture, filled in from beside it
DC_BINS = 3


@dataclass
class Signal:
    """A transmission in progress on the tuned frequency."""

    start: float
    frequency: int
    mode: str
    peak: float
    scanning: bool
    last_open: float
    channel: dict | None = None


def channel_mode(channel: dict) -> str | None:
    """The mode a stored channel belongs to: its tag mode:<mode> (mode:dab, mode:pager …), if any."""
    for tag in str(channel.get("tags") or "").split(","):
        tag = tag.strip().lower()
        if tag.startswith("mode:") and tag[5:] in registry():
            return tag[5:]
    return None


@dataclass
class ScanState:
    kind: str = ""  # channels or band
    targets: list = field(default_factory=list)
    index: int = -1
    hold: bool = False  # the HOLD key: stay here
    look: int = 0  # blocks left to look for a signal on this target
    label: str = ""


class Radio:
    def __init__(self, config: Config, database: Database, device_factory: Callable, emit: Callable):
        self.config = config
        self.db = database
        self.device_factory = device_factory
        self.emit = emit
        self.commands: queue.SimpleQueue = queue.SimpleQueue()
        self.device = None
        self.device_info: dict = {}
        self.device_error: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.listeners = 0
        self.spectrum_listeners = 0
        # A page shows the band view: sweep the receiver across the band (silent meanwhile)
        self.sweep_wanted = False
        self.sweep: dict | None = None

        self.bands = self.db.bands()
        self.settings = self.db.settings()
        self.ignored = self.db.ignored_set()
        self.rate = float(config.receiver.sample_rate)
        self.center = 0.0
        self.settle = 0
        self.level = -140.0
        # The noise floor of the spectrum, per FFT bin (median, dBFS)
        self._bin_noise = -140.0
        self.signal: Signal | None = None
        self.scan = ScanState()
        self.priority = False
        self._priority_due = 0.0
        self._priority_return: dict | None = None
        self._priority_look = 0
        self._block = 0
        self._clock_start = 0.0
        self._last_state_emit = 0.0
        self._state_dirty = True
        self._save_due = 0.0
        self.channel: dict | None = None

        state = self.db.state() if config.tune.restore_last else {}
        self.frequency = int(state.get("frequency") or config.tune.frequency)
        self.mode = state.get("mode") if state.get("mode") in MODES else config.tune.mode
        self.bandwidth = int(state.get("bandwidth") or default_bandwidth(self.mode))
        band = band_for(self.bands, self.frequency)
        self.step = int(state.get("step") or (band["step"] if band else 12_500))
        self.squelch: float | None = state.get("squelch")
        gain = state.get("gain", "unset")
        if gain == "unset":
            # A HackRF starts with its own LNA, VGA and amp settings from the options
            gain = None if config.receiver.type == "hackrf" else (config.receiver.sdr_gain or None)
        self.gain: float | None = gain
        self.agc = bool(state.get("agc", True))
        self.noise_blanker = bool(state.get("nb", False))
        self.band_last: dict = state.get("band_last") or {}
        # Off: the receiver is closed and nothing is processed (remembered across a restart)
        self.power = bool(state.get("power", True))
        self.demod: Demodulator | None = None
        self._rds_frequency = 0
        # The mode: "radio", or a decoder mode (pager, sensors) that reads what the radio receives
        self.decoder: Decoder | None = None
        # Where the receiver is (latitude, longitude): Home Assistant's home, for the maps
        self.location: tuple[float, float] | None = None
        # Where the modes keep their pictures and recordings (a folder per mode)
        self.files_dir: Path | None = None
        self.decoder_key = state.get("decoder") if state.get("decoder") in registry() else "radio"
        self.spectrum = Spectrum(int(self.settings["fft_size"]))
        # The spectrum averaged over the last second or so (linear), for autotune
        self._avg_power: np.ndarray | None = None
        self._avg_center = 0.0
        self._avg_count = 0
        self._autotune_wanted = False

    # ================================================================ lifecycle

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="radio", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self._end_signal(self._clock())
        self._save_state()
        if self.decoder:
            self.decoder.stop()
        if self.device:
            self.device.close()

    def command(self, name: str, /, **args) -> None:
        self.commands.put((name, args))

    def reload(self) -> None:
        """Channels, bands or settings changed in the database."""
        self.command("reload")

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.power:
                if self.device is not None or self.device_error:
                    self._power_down()
                self._handle_commands()
                self._emit_state()
                self._stop.wait(0.1)
                continue
            if self.device is None:
                self._open()
                if self.device is None:
                    self._handle_commands()
                    self._stop.wait(5)
                    continue
            try:
                self._handle_commands()
                block = self.device.read(self.block_size)
                self._process(block)
            except DeviceError as e:
                self._fail(str(e))
            except Exception as e:
                logger.exception("The radio stopped")
                self._fail(f"The radio stopped: {e}")

    def _open(self) -> None:
        device = self.device_factory(self.config.receiver)
        center = self._centre_for(self.frequency)
        try:
            self.device_info = device.open(center)
        except DeviceError as e:
            if self.device_error != str(e):
                logger.error(str(e))
            self.device_error = str(e)
            self._state_dirty = True
            self._emit_state(force=True)
            return
        self.device = device
        self.device_error = None
        self.rate = float(device.sample_rate)
        self.block_size = int(self.rate // BLOCKS_PER_SECOND)
        self.center = center
        self.device.set_gain(self.gain)
        self.demod = Demodulator(self.rate, self.mode, self.bandwidth)
        self._apply_demod()
        self.settle = SETTLE_BLOCKS
        if self.decoder_key != "radio" and self.decoder is None:
            # Back in the mode it was in, on the frequency it was on
            self._start_decoder(self.decoder_key, tune=False)
        logger.info(
            f"Receiver {self.device_info.get('driver')} open at {self.rate / 1e6:g} MS/s, "
            f"tuned to {self.frequency / 1e6:.4f} MHz {self.mode.upper()}"
        )
        self._emit_state(force=True)

    def _power_down(self) -> None:
        """Off: let go of the receiver, so it uses nothing (and another program may have it)."""
        self._stop_scan()
        self.sweep = None
        self._end_signal(self._clock())
        # The mode stays chosen; its decoder starts again when the receiver opens
        if self.decoder:
            self.decoder.stop()
            self.decoder = None
        if self.device:
            self.device.close()
            logger.info("Switched off: the receiver is closed")
        self.device = None
        self.device_error = None
        self.level = -140.0
        self._bin_noise = -140.0
        self._avg_power = None
        self._avg_count = 0
        self._autotune_wanted = False
        self._emit_state(force=True)

    def _fail(self, message: str) -> None:
        logger.error(message)
        self.device_error = message
        if self.device:
            self.device.close()
        self.device = None
        self._end_signal(self._clock())
        self._emit_state(force=True)
        self._stop.wait(5)

    # ================================================================ commands

    def _handle_commands(self) -> None:
        while True:
            try:
                name, args = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                handler = getattr(self, f"_cmd_{name}", None)
                if not self.power and name not in ("power", "reload"):
                    continue  # switched off: only the power button works
                if handler is None:
                    logger.warning(f"Unknown command {name}")
                else:
                    handler(**args)
            except (TypeError, ValueError) as e:
                logger.warning(f"Command {name} {args}: {e}")
            self._state_dirty = True

    def _cmd_power(self, on=None) -> None:
        self.power = (not self.power) if on is None else bool(on)
        logger.info("Switched on" if self.power else "Switching off")
        self._save_state()

    def _cmd_reload(self) -> None:
        self.bands = self.db.bands()
        self.settings = self.db.settings()
        self.ignored = self.db.ignored_set()
        if self.spectrum.size != int(self.settings["fft_size"]):
            self.spectrum = Spectrum(int(self.settings["fft_size"]))
        self.channel = self.db.channel_at(self.frequency)
        if self.scan.kind == "channels":
            self._rebuild_scan_targets()
        # The mode's settings changed: start its decoder again with them
        if self.decoder and self.settings.get(f"mode_{self.decoder_key}") != self.decoder.saved_settings:
            old, channel = self.decoder.frequency, self.decoder.channel_frequency
            self.decoder.stop()
            self.decoder = None
            new = channel or self.settings[f"mode_{self.decoder_key}"]["frequency"]
            self._start_decoder(self.decoder_key, tune=new != old, frequency=channel)

    def _cmd_decoder(self, key, frequency=None) -> None:
        """Switch mode: the radio, or a decoder mode that tunes to its own frequency (or to the
        frequency of the stored channel it was picked with)."""
        if key != "radio" and key not in registry():
            raise ValueError(f"Unknown mode {key}")
        if self.decoder:
            self.decoder.stop()
            self.decoder = None
        # The rate the next mode needs: back to the usual after a mode that needed another
        wanted = (registry()[key].sample_rate if key != "radio" else None) or float(self.config.receiver.sample_rate)
        if self.device and self.rate != wanted and (key == "radio" or not registry()[key].sample_rate):
            self._set_rate(float(self.config.receiver.sample_rate))
        self.decoder_key = key
        if key != "radio":
            self._stop_scan()
            self.priority = False
            self._start_decoder(key, tune=True, frequency=frequency)
        self._save_soon()

    def _go_channel(self, channel: dict) -> None:
        """A stored channel picked (the list, ◀ ▶, a favourite): its mode too. A channel tagged
        mode:<mode> switches to that mode on its frequency; any other switches back to the radio."""
        wanted = channel_mode(channel) or "radio"
        if wanted != self.decoder_key or (self.decoder is not None and self.decoder.frequency != channel["frequency"]):
            self._cmd_decoder(wanted, frequency=channel["frequency"] if wanted != "radio" else None)
        self._stop_scan()
        self._tune_channel(channel)

    def _start_decoder(self, key: str, tune: bool, frequency: int | None = None) -> None:
        cls = registry()[key]
        if not cls.usable(self.config.receiver.type, self.device.frequency_range if self.device else None):
            self.emit("notice", {"message": f"The {self.config.receiver.name} can't receive {cls.name}"})
            self.decoder_key = "radio"
            return
        # A mode that needs a faster (wider) receiver has it while it runs
        if cls.sample_rate and self.device and self.rate != cls.sample_rate and not self._set_rate(cls.sample_rate):
            self.decoder_key = "radio"
            return
        saved = self.settings.get(f"mode_{key}") or {}
        settings = dict(saved)
        if frequency:
            settings["frequency"] = int(frequency)  # the stored channel's, for now
        decoder = cls(
            settings,
            lambda item: self._publish(key, item),
            lambda data: self.emit("live", {"mode": key, **data}),
        )
        decoder.location = self.location
        # What it was started with: its saved settings, and a stored channel's frequency if any
        decoder.saved_settings = saved
        decoder.channel_frequency = int(frequency) if frequency else None
        decoder.folder = self.files_dir / key if self.files_dir else None
        # A mode that follows something (a satellite's Doppler shift) tunes the radio itself
        decoder.retune = lambda frequency, mode, bandwidth: self.command(
            "track", frequency=frequency, mode=mode, bandwidth=bandwidth
        )
        self.decoder = decoder
        if tune:
            mode, bandwidth = decoder.demod
            self._tune(decoder.frequency, mode, bandwidth)
        elif decoder.centred and self.device and self.center != self.frequency:
            self._retune_centre()
        decoder.start()
        logger.info(f"Mode {decoder.name} on {self.frequency / 1e6:.4f} MHz")

    def _modes(self) -> list[dict]:
        receiver = self.config.receiver
        span = self.device.frequency_range if self.device else None
        return [
            {"key": k, "name": cls.name, "why_not": cls.why_not(receiver.type, span, receiver.name)}
            for k, cls in registry().items()
        ]

    def _decoder_kind(self, cls) -> dict:
        return {"key": self.decoder_key, "name": cls.name, "decoded_by": cls.decoded_by, "input": cls.input,
                "signal": cls.signal, "listen": cls.listen}  # fmt: skip

    def _decoder_snapshot(self) -> dict:
        if self.decoder:
            return {**self._decoder_kind(type(self.decoder)), **self.decoder.status()}
        if self.decoder_key != "radio":
            # Chosen, but not running (switched off, or the receiver isn't open)
            return {**self._decoder_kind(registry()[self.decoder_key]), "error": None, "count": 0}
        return {"key": "radio"}

    def _publish(self, key: str, item: dict) -> None:
        """A decoder found something (called from its own thread): store it and tell the pages."""
        ts = time.time()
        item = dict(item)
        item_key = str(item.pop("key", ""))
        frequency = self.frequency
        try:
            item_id = self.db.add_decoded(ts, key, item_key, frequency, item)
        except Exception as e:
            logger.warning(f"Could not store a decoded {key} item: {e}")
            return
        self.emit("decoded", {"mode": key, "item": {"id": item_id, "ts": ts, "mode": key, "key": item_key,
                                                    "frequency": frequency, **item}})  # fmt: skip

    def _set_rate(self, rate: float) -> bool:
        """Another sample rate: the receiver, the demodulator and the block size follow."""
        try:
            self.rate = float(self.device.set_sample_rate(rate))
        except (DeviceError, AttributeError) as e:
            self.emit("notice", {"message": str(e)})
            return False
        self.block_size = int(self.rate // BLOCKS_PER_SECOND)
        self.demod = Demodulator(self.rate, self.mode, self.bandwidth)
        self._apply_demod()
        self.center = self._centre_for(self.frequency)
        self.device.set_center(self.center)
        self.settle = SETTLE_BLOCKS
        self._avg_power = None
        self._avg_count = 0
        logger.info(f"Receiver at {self.rate / 1e6:g} MS/s")
        self._state_dirty = True
        return True

    def _cmd_track(self, frequency, mode=None, bandwidth=None) -> None:
        """A mode tuning the radio itself (a satellite pass); not a command of the page."""
        if self.decoder is None:
            return
        self._tune(int(frequency), mode, int(bandwidth) if bandwidth else None)

    def _cmd_autotune(self) -> None:
        """AUTO: to the middle of the strongest signal near the frequency (done once the spectrum
        has been averaged for a moment)."""
        if self.decoder is not None:
            return  # a mode stays on its own frequency
        if self.mode in ("usb", "lsb", "cw"):
            self.emit("notice", {"message": "Autotune centres AM, FM and DSB signals: tune SSB and CW by ear"})
            return
        self._autotune_wanted = True

    def _autotune(self) -> None:
        offset = self.frequency - self.center
        search = max(self.bandwidth, self.step)
        found = find_signal(self._avg_power, self.rate, offset, self.bandwidth, search)
        if found is None:
            self.emit("notice", {"message": f"No signal to tune to near {self.frequency / 1e6:.4f} MHz"})
            return
        frequency = self.center + found[0]
        # On the channel grid when it is that close to a channel, else to 100 Hz
        grid = round(frequency / self.step) * self.step
        bin_hz = self.rate / len(self._avg_power)
        frequency = grid if abs(frequency - grid) <= max(self.step / 4, bin_hz) else round(frequency / 100) * 100
        self._cmd_tune(int(frequency))

    def _average_spectrum(self, db: np.ndarray) -> None:
        power = np.power(10.0, db / 10.0)
        if self._avg_power is None or len(self._avg_power) != len(power) or self._avg_center != self.center:
            self._avg_power = power
            self._avg_center = self.center
            self._avg_count = 1
        else:
            self._avg_power = self._avg_power * 0.8 + power * 0.2
            self._avg_count += 1

    def _cmd_tune(self, frequency, mode=None, bandwidth=None, step=None, stop_scan=True) -> None:
        if stop_scan:
            self._stop_scan()
        if mode:
            self._tune(round(float(frequency)), mode, bandwidth)
        else:
            self._tune_manual(round(float(frequency)))
        if step:
            self.step = int(step)

    def _tune_manual(self, frequency: int) -> None:
        """Tuning by hand (keypad, dial, spectrum): a stored channel brings its own settings,
        and going into another band takes that band's mode, filter and step."""
        channel = self.db.channel_at(frequency, tolerance=10)
        if channel:
            self._tune_channel(channel)
            return
        old = band_for(self.bands, self.frequency)
        new = band_for(self.bands, frequency)
        if new and (old is None or old["name"] != new["name"]):
            self._tune(frequency, new["mode"], new["bandwidth"])
            if new["mode"] == self.mode:
                self.bandwidth = int(new["bandwidth"])
                self._apply_demod()
            self.step = int(new["step"])
        else:
            self._tune(frequency)

    def _cmd_channel(self, id) -> None:
        channel = self.db.channel(int(id))
        if channel is None:
            return
        self._go_channel(channel)

    def _cmd_step_tune(self, steps) -> None:
        """The tune dial: move a number of steps. A step that fits a whole MHz (12.5 kHz, 25 kHz)
        snaps onto its channel grid first; others (8.33 kHz) just move by the step."""
        steps = int(steps)
        step = self.step
        if 1_000_000 % step == 0 and self.frequency % step:
            rounding = math.floor if steps < 0 else math.ceil
            target = rounding(self.frequency / step) * step + (steps - (1 if steps > 0 else -1)) * step
        else:
            target = self.frequency + steps * step
        self._stop_scan()
        self._tune_manual(int(target))

    def _cmd_set(self, **values) -> None:
        if "mode" in values and values["mode"] in MODES:
            self.mode = values["mode"]
            self.bandwidth = default_bandwidth(self.mode)
        if values.get("bandwidth"):
            self.bandwidth = int(values["bandwidth"])
        if values.get("step"):
            self.step = int(values["step"])
        if "squelch" in values:
            value = values["squelch"]
            self.squelch = None if value is None else max(-140.0, min(0.0, float(value)))
        if "gain" in values:
            value = values["gain"]
            self.gain = None if value is None else float(value)
            if self.device:
                self.device.set_gain(self.gain)
        if "agc" in values:
            self.agc = bool(values["agc"])
        if "nb" in values:
            self.noise_blanker = bool(values["nb"])
        self._apply_demod()
        self._save_soon()

    def _cmd_cycle(self, what) -> None:
        """The MODE and STEP keys."""
        if what == "mode" and self.decoder is not None:
            return  # a mode listens the way its decoder needs
        if what == "mode":
            modes = list(MODES)
            self._cmd_set(mode=modes[(modes.index(self.mode) + 1) % len(modes)])
        elif what == "step":
            later = [s for s in STEPS if s > self.step]
            self.step = later[0] if later else STEPS[0]
            self._save_soon()

    def _cmd_band(self, name=None, direction=1) -> None:
        """Go to a band: by name, or the next band with a button."""
        if self.decoder is not None:
            return  # a mode stays on its own frequency
        # The bands this receiver can tune to
        bands = [b for b in self.bands if b["button"] and self._in_range(b["start"], b["stop"])]
        if not bands:
            return
        if name:
            band = next((b for b in self.bands if b["name"] == name), None)
            if band is not None and not self._in_range(band["start"], band["stop"]):
                self._out_of_range(band["name"])
                return
        else:
            current = band_for(self.bands, self.frequency)
            names = [b["name"] for b in bands]
            index = names.index(current["name"]) if current and current["name"] in names else -1
            band = bands[(index + int(direction)) % len(bands)]
        if band is None:
            return
        self._stop_scan()
        last = self.band_last.get(band["name"])
        if last and band["start"] <= last.get("frequency", 0) <= band["stop"]:
            self._tune(int(last["frequency"]), last.get("mode") or band["mode"], last.get("bandwidth"))
        else:
            self._tune(band["start"], band["mode"], band["bandwidth"])
        self.step = int(band["step"])

    def _cmd_scan(self, action, kind=None, band=None) -> None:
        if action == "stop":
            self._stop_scan()
        elif action == "start":
            if self.decoder is not None:
                # A mode listens on its own frequency
                self.emit(
                    "notice", {"message": f"No scanning in the {self.decoder.name} mode: switch Mode to Radio to scan"}
                )
                return
            self._start_scan(kind or "channels", band)
        elif action == "hold":
            self.scan.hold = not self.scan.hold
        elif action == "skip" and self.scan.kind:
            # Go on past a signal (or a held channel) to the next one
            self.scan.hold = False
            if self.signal is not None:
                self._end_signal(self._clock())
            self._next_target()

    def _cmd_priority(self, on=None) -> None:
        if self.decoder is not None:
            self.priority = False
            return
        self.priority = (not self.priority) if on is None else bool(on)
        self._priority_due = time.monotonic() + float(self.settings["priority_interval"])

    def _cmd_lockout(self) -> None:
        """L/O: a stored channel is left out of scans; any other frequency is ignored."""
        if self.channel:
            channel = self.db.channel(self.channel["id"])
            if channel:
                self.db.save_channel({"scan": 0 if channel["scan"] else 1}, channel["id"])
                self.channel = self.db.channel(channel["id"])
        elif self.frequency in self.ignored:
            self.db.unignore(self.frequency)
            self.ignored.discard(self.frequency)
        else:
            self.db.ignore(self.frequency)
            self.ignored.add(self.frequency)
        if self.scan.kind:
            self._next_target()
        self.emit("changed", {"what": "channels"})

    def _cmd_memory(self, direction) -> None:
        """◀ ▶: the previous or next stored channel."""
        channels = self.db.channels()
        if not channels:
            return
        direction = 1 if int(direction) > 0 else -1
        if self.channel:
            numbers = [c["number"] for c in channels]
            index = numbers.index(self.channel["number"]) if self.channel["number"] in numbers else -1
            target = channels[(index + direction) % len(channels)]
        else:
            ahead = [c for c in channels if (c["frequency"] - self.frequency) * direction > 0]
            if ahead:
                target = min(ahead, key=lambda c: abs(c["frequency"] - self.frequency))
            else:
                target = channels[0] if direction > 0 else channels[-1]
        self._go_channel(target)

    # ================================================================ tuning

    def _in_range(self, start: float, stop: float | None = None) -> bool:
        """Whether the receiver can tune here (any part of start..stop)."""
        if not self.device:
            return True
        low, high = self.device.frequency_range
        return (start if stop is None else stop) >= low and start <= high

    def _out_of_range(self, what: str) -> None:
        low, high = self.device.frequency_range
        self.emit(
            "notice",
            {
                "message": f"{what} is out of range: the {self.config.receiver.name} tunes {low / 1e6:g} to {high / 1e6:g} MHz"
            },
        )

    def _centre_for(self, frequency: int) -> float:
        low, high = self.device.frequency_range if self.device else (0, 1e10)
        # A mode that needs the receiver's whole bandwidth (ADS-B) has it centred on its frequency
        centred = self.decoder is not None and self.decoder.centred and frequency == self.decoder.frequency
        centre = frequency if centred else frequency + RECENTRE_OFFSET * self.rate
        return max(low, min(high, centre))

    def _retune_centre(self) -> None:
        self.center = self._centre_for(self.frequency)
        self.device.set_center(self.center)
        self.settle = SETTLE_BLOCKS
        self._apply_demod()

    def _tune(self, frequency: int, mode: str | None = None, bandwidth: int | None = None) -> None:
        frequency = max(10_000, min(6_000_000_000, int(frequency)))
        if not self._in_range(frequency):
            self._out_of_range(f"{frequency / 1e6:g} MHz")
            return
        if mode in MODES and mode != self.mode:
            self.mode = mode
            self.bandwidth = int(bandwidth or default_bandwidth(mode))
        elif bandwidth:
            self.bandwidth = int(bandwidth)
        if frequency != self.frequency:
            self._end_signal(self._clock())
        self.frequency = frequency
        centred = self.decoder is not None and self.decoder.centred and frequency == self.decoder.frequency
        if self.device and (abs(frequency - self.center) > EDGE * self.rate or (centred and self.center != frequency)):
            self.center = self._centre_for(frequency)
            self.device.set_center(self.center)
            self.settle = SETTLE_BLOCKS
        self._apply_demod()
        self.channel = self.db.channel_at(frequency)
        band = band_for(self.bands, frequency)
        if band:
            self.band_last[band["name"]] = {"frequency": frequency, "mode": self.mode, "bandwidth": self.bandwidth}
        self._state_dirty = True
        self._save_soon()

    def _tune_channel(self, channel: dict) -> None:
        self._tune(channel["frequency"], channel["mode"], channel["bandwidth"])
        band = band_for(self.bands, channel["frequency"])
        if channel.get("step"):
            self.step = int(channel["step"])
        elif band:
            self.step = int(band["step"])
        if channel.get("squelch") is not None:
            self.squelch = float(channel["squelch"])
        if channel.get("gain") is not None:
            self.gain = float(channel["gain"])
            if self.device:
                self.device.set_gain(self.gain)

    def _apply_demod(self) -> None:
        if self.demod is None:
            return
        self.demod.configure(self.mode, self.bandwidth)
        self.demod.set_offset(self.frequency - self.center)
        # RDS belongs to one station: start over on another frequency
        if self.frequency != self._rds_frequency:
            if self.demod.rds is not None:
                self.demod.rds.reset()
            # And the tones to one channel
            if self.demod.tones is not None:
                self.demod.tones = type(self.demod.tones)(self.demod.if_rate)
        self._rds_frequency = self.frequency
        self.demod.agc_enabled = self.agc
        self.demod.noise_blanker = self.noise_blanker

    # ================================================================ per block

    def _process(self, block: np.ndarray) -> None:
        self._block += 1
        now = self._clock()
        if self.sweep_wanted != (self.sweep is not None):
            if self.sweep_wanted:
                self._start_sweep()
            else:
                self._stop_sweep()
        if self.sweep is not None:
            self._sweep_step(block)
            if self._save_due and time.monotonic() >= self._save_due:
                self._save_state()
            self._emit_state()
            return
        if self.settle > 0:
            self.settle -= 1
            return
        if self.decoder is not None and not self.decoder.listen:
            # Nothing to hear (the mode's decoder reads the samples): only the channel's level,
            # from the spectrum (the bins within the filter round the tuned frequency)
            audio, level = None, self._channel_level(block)
        else:
            audio, level = self.demod.process(block)
        self.level = level
        every = 1 if int(self.settings["spectrum_rate"]) >= 20 else 2
        if self._block % every == 0 or self.spectrum_listeners == 0:
            db = self.spectrum(block)
            self._average_spectrum(db)
            floor = noise_floor(db)
            self._bin_noise = self._bin_noise * 0.8 + floor * 0.2 if self._bin_noise > -140 else floor
            if self.spectrum_listeners and self._block % every == 0:
                self.emit("spectrum", {"center": self.center, "rate": self.rate, "bins": quantize(db)})

        if self._autotune_wanted and self._avg_count >= AUTOTUNE_BLOCKS:
            self._autotune_wanted = False
            self._autotune()

        threshold = self._threshold()
        open_ = threshold is None or level >= threshold
        # A hit is a real signal: with a squelch set below the noise, noise isn't logged as one
        noise = self.noise
        signal = open_ and (noise <= -140 or level >= noise + SCAN_MIN_SNR)
        self._track_signal(now, level, signal, threshold is not None)
        if self.scan.kind:
            self._scan_step(open_)
        elif self.priority:
            self._priority_step(open_)

        # A mode that decodes speech (Digital voice) is heard by its decoded voice, not the FM audio
        voice = getattr(self.decoder, "take_voice", None) if self.decoder is not None else None
        if voice is None and open_ and self.listeners and not self.settle and audio is not None:
            self.emit("audio", {"rate": self.demod.audio_rate, "pcm": to_pcm16(audio)})

        # The mode's decoder reads every block, squelch or not
        if self.decoder is not None:
            if self.decoder.input in ("audio", "both") and audio is not None:
                self.decoder.feed_audio(audio, self.demod.audio_rate)
            if self.decoder.input in ("iq", "both"):
                self.decoder.feed_iq(block, self.rate, self.frequency - self.center)
        if voice is not None:
            pcm = voice()
            if pcm and self.listeners:
                self.emit("audio", {"rate": self.decoder.voice_rate, "pcm": pcm})

        if self._save_due and time.monotonic() >= self._save_due:
            self._save_state()
        self._emit_state()

    def _channel_level(self, block: np.ndarray) -> float:
        db = self.spectrum(block)
        n = len(db)
        freqs = (np.arange(n) - n // 2) * (self.rate / n)
        inside = np.abs(freqs - (self.frequency - self.center)) <= max(self.bandwidth, self.rate / n) / 2
        power = float(np.sum(np.power(10.0, db[inside] / 10.0)))
        return 10 * math.log10(power + 1e-20)

    def _clock(self) -> float:
        """The time of this block by the samples received: exact durations, even if the thread falls behind.
        Back to the wall clock when samples were lost (an overflow) or the receiver was reopened."""
        wall = time.time()
        if not self._clock_start or abs(self._clock_start + self._block / BLOCKS_PER_SECOND - wall) > 2:
            self._clock_start = wall - self._block / BLOCKS_PER_SECOND
        return self._clock_start + self._block / BLOCKS_PER_SECOND

    @property
    def noise(self) -> float:
        """The noise in the tuned channel, in the same dBFS as the signal level: the spectrum's
        noise per bin, from the median to the mean of noise (+1.6 dB), less the window's 2-bin
        noise bandwidth (-3 dB), times the bins the channel filter is wide."""
        if self._bin_noise <= -140:
            return -140.0
        bin_width = self.rate / self.spectrum.size
        return self._bin_noise + 1.59 - 3.01 + 10 * math.log10(max(self.bandwidth, bin_width) / bin_width)

    def _threshold(self) -> float | None:
        """The squelch level. A scan only stops on a signal clearly above the noise, also when the
        squelch is set below it (it would stop on every channel) or off."""
        squelch = self.squelch if self.squelch is not None and self.squelch > -139.5 else None
        if self.scan.kind or self._priority_return:
            noise = self.noise
            if noise <= -140:
                return squelch if squelch is not None else -60.0
            if squelch is None:
                return noise + SQUELCH_AUTO_MARGIN
            return max(squelch, noise + SCAN_MIN_SNR)
        return squelch

    # ================================================================ band view (sweep)

    def _start_sweep(self) -> None:
        """Steps the receiver across the band the radio is in, one capture at a time."""
        if not self.device:
            self.sweep_wanted = False
            return
        self._stop_scan()
        self._priority_return = None
        self._end_signal(self._clock())
        low, high = self.device.frequency_range
        width = self.rate * SWEEP_USE
        band = band_for(self.bands, self.frequency)
        if band and band["stop"] - band["start"] > width:
            start, stop, label = band["start"], band["stop"], band["name"]
        else:
            start, stop = self.frequency - SWEEP_DEFAULT / 2, self.frequency + SWEEP_DEFAULT / 2
            label = f"{start / 1e6:g} to {stop / 1e6:g} MHz"
        start, stop = max(start, low), min(stop, high)
        if stop - start > SWEEP_MAX:
            start = max(start, min(self.frequency - SWEEP_MAX / 2, stop - SWEEP_MAX))
            stop = start + SWEEP_MAX
            label = f"{start / 1e6:g} to {stop / 1e6:g} MHz"
        count = max(1, math.ceil((stop - start) / width))
        span = count * width
        self.sweep = {
            "start": (start + stop) / 2 - span / 2,
            "span": span,
            "width": width,
            "count": count,
            "index": 0,
            "label": label,
            "bins": None,
        }
        logger.info(f"Band view: sweeping {label} in {count} steps")
        self._sweep_go(0)
        self._state_dirty = True

    def _sweep_go(self, index: int) -> None:
        sweep = self.sweep
        sweep["index"] = index
        low, high = self.device.frequency_range
        self.center = max(low, min(high, sweep["start"] + (index + 0.5) * sweep["width"]))
        self.device.set_center(self.center)
        self.settle = SETTLE_BLOCKS

    def _sweep_step(self, block: np.ndarray) -> None:
        if self.settle > 0:
            self.settle -= 1
            return
        sweep = self.sweep
        db = self.spectrum(block)
        size = len(db)
        mid = size // 2
        beside = np.r_[db[mid - 3 * DC_BINS : mid - DC_BINS], db[mid + DC_BINS + 1 : mid + 3 * DC_BINS + 1]]
        db[mid - DC_BINS : mid + DC_BINS + 1] = np.median(beside)
        keep = round(size * SWEEP_USE)
        first = (size - keep) // 2
        if sweep["bins"] is None or len(sweep["bins"]) != keep * sweep["count"]:
            sweep["bins"] = np.full(keep * sweep["count"], DB_FLOOR, np.float32)
        index = sweep["index"]
        sweep["bins"][index * keep : (index + 1) * keep] = db[first : first + keep]
        following = index + 1
        if following == sweep["count"]:
            bins = sweep["bins"]
            # At most 16k bins a frame: the strongest of neighbouring bins
            factor = math.ceil(len(bins) / 16384)
            if factor > 1:
                bins = np.max(bins[: len(bins) // factor * factor].reshape(-1, factor), axis=1)
            if self.spectrum_listeners:
                self.emit(
                    "spectrum",
                    {"center": sweep["start"] + sweep["span"] / 2, "rate": sweep["span"], "bins": quantize(bins)},
                )
            following = 0
        if sweep["count"] > 1:
            self._sweep_go(following)

    def _stop_sweep(self) -> None:
        self.sweep = None
        self._state_dirty = True
        if self.device:
            self.center = self._centre_for(self.frequency)
            self.device.set_center(self.center)
            self.settle = SETTLE_BLOCKS
            self._apply_demod()

    # ================================================================ hits

    def _track_signal(self, now: float, level: float, open_: bool, squelched: bool) -> None:
        """Logs a hit for every transmission the squelch lets through."""
        if not squelched:
            self._end_signal(now)
            return
        if open_:
            if self.signal is None:
                self.signal = Signal(
                    start=now,
                    frequency=self.frequency,
                    mode=self.mode,
                    peak=level,
                    scanning=bool(self.scan.kind),
                    last_open=now,
                    channel=self.channel,
                )
                self._state_dirty = True
            else:
                self.signal.peak = max(self.signal.peak, level)
                self.signal.last_open = now
        elif self.signal and now - self.signal.last_open > self._hang():
            self._end_signal(now)

    def _hang(self) -> float:
        return float(self.settings["scan_resume"]) if self.scan.kind else LISTEN_HANG_SECONDS

    def _end_signal(self, now: float) -> None:
        sig = self.signal
        if sig is None:
            return
        self.signal = None
        self._state_dirty = True
        duration = sig.last_open - sig.start + 1 / BLOCKS_PER_SECOND
        if duration < MIN_HIT_SECONDS:
            return
        try:
            self.db.add_hit(sig.start, duration, sig.frequency, sig.mode, sig.peak, sig.scanning)
        except Exception as e:
            logger.warning(f"Could not store a hit: {e}")
            return
        self.emit(
            "hit",
            {
                "ts": sig.start,
                "duration": duration,
                "frequency": sig.frequency,
                "mode": sig.mode,
                "peak": sig.peak,
                "channel": sig.channel,
            },
        )

    # ================================================================ scanning

    def _start_scan(self, kind: str, band_name: str | None) -> None:
        self._end_signal(self._clock())
        self.scan = ScanState(kind=kind)
        if kind == "channels":
            self.scan.label = band_name or ""
            self._rebuild_scan_targets()
            if not self.scan.targets:
                self.scan = ScanState()
                self.emit(
                    "notice", {"message": "No stored channels to scan" + (f" in {band_name}" if band_name else "")}
                )
                return
        elif kind == "band":
            # The band the radio is in, with the band's own mode, filter and step
            band = next((b for b in self.bands if b["name"] == band_name), None) or band_for(self.bands, self.frequency)
            if band is None:
                self.scan = ScanState()
                self.emit("notice", {"message": "This frequency is in none of the bands: pick a band first"})
                return
            start, stop, step, mode, bandwidth = (
                band["start"],
                band["stop"],
                band["step"],
                band["mode"],
                band["bandwidth"],
            )
            self.scan.label = band["name"]
            stored = {c["frequency"] for c in self.db.channels() if not c["scan"]}
            skip = self.ignored | stored
            count = int((stop - start) // step) + 1
            self.scan.targets = [
                {"frequency": start + i * step, "mode": mode, "bandwidth": bandwidth}
                for i in range(count)
                if start + i * step not in skip and self._in_range(start + i * step)
            ]
            if not self.scan.targets:
                label = self.scan.label
                self.scan = ScanState()
                if self._in_range(start, stop):
                    self.emit("notice", {"message": f"Nothing to scan in {label}: every step is ignored or locked out"})
                else:
                    self._out_of_range(label)
                return
        else:
            self.scan = ScanState()
            return
        # Start from where the radio is, not from the top of the list
        nearest = min(
            range(len(self.scan.targets)), key=lambda i: abs(self.scan.targets[i]["frequency"] - self.frequency)
        )
        self.scan.index = nearest - 1
        self._next_target()

    def _rebuild_scan_targets(self) -> None:
        # Channels the receiver can't tune to are left out
        channels = [c for c in self.db.scan_channels(self.scan.label or None) if self._in_range(c["frequency"])]
        priority = [c for c in channels if c["priority"]]
        targets = []
        for c in channels:
            targets.append(c)
            # PRI on: the priority channels come round between every other channel
            if self.priority and priority and not c["priority"]:
                targets.extend(priority)
        current = self.scan.targets[self.scan.index] if 0 <= self.scan.index < len(self.scan.targets) else None
        self.scan.targets = targets
        if current is not None:
            self.scan.index = next(
                (i for i, t in enumerate(targets) if t["frequency"] == current["frequency"]), self.scan.index
            )

    def _stop_scan(self) -> None:
        if self.scan.kind:
            self._end_signal(self._clock())
            self.scan = ScanState()
            self._state_dirty = True

    def _look_blocks(self) -> int:
        return max(1, round(BLOCKS_PER_SECOND / max(1, int(self.settings["scan_speed"]))))

    def _next_target(self) -> None:
        targets = self.scan.targets
        if not targets:
            self._stop_scan()
            return
        self.scan.index = (self.scan.index + 1) % len(targets)
        target = targets[self.scan.index]
        if "id" in target:
            self._tune_channel(target)
        else:
            self._tune(target["frequency"], target["mode"], target["bandwidth"])
        self.scan.look = self._look_blocks()

    def _scan_step(self, open_: bool) -> None:
        if self.scan.hold or self.settle:
            return
        if self.signal is not None:
            # Stay while the transmission lasts; _track_signal ends it after the resume time
            return
        self.scan.look -= 1
        if self.scan.look <= 0:
            self._next_target()

    # ================================================================ priority (PRI while listening)

    def _priority_step(self, open_: bool) -> None:
        now = time.monotonic()
        if self._priority_return is not None:
            # Visiting a priority channel: stay while it is busy, else go back
            if self.settle:
                return
            if self.signal is not None:
                return
            self._priority_look -= 1
            if self._priority_look <= 0:
                back = self._priority_return
                self._priority_return = None
                self._tune(back["frequency"], back["mode"], back["bandwidth"])
                self.squelch = back["squelch"]
                self._priority_due = now + float(self.settings["priority_interval"])
            return
        if self.signal is not None or now < self._priority_due:
            return
        channels = [c for c in self.db.priority_channels() if c["frequency"] != self.frequency]
        if not channels:
            self._priority_due = now + float(self.settings["priority_interval"])
            return
        self._priority_return = {
            "frequency": self.frequency,
            "mode": self.mode,
            "bandwidth": self.bandwidth,
            "squelch": self.squelch,
        }
        self._tune_channel(channels[0])
        self._priority_look = 2

    # ================================================================ state

    def _save_soon(self) -> None:
        if not self._save_due:
            self._save_due = time.monotonic() + 5

    def _save_state(self) -> None:
        self._save_due = 0.0
        try:
            self.db.save_state(
                {
                    "frequency": self.frequency,
                    "mode": self.mode,
                    "bandwidth": self.bandwidth,
                    "step": self.step,
                    "squelch": self.squelch,
                    "gain": self.gain,
                    "agc": self.agc,
                    "nb": self.noise_blanker,
                    "band_last": self.band_last,
                    "power": self.power,
                    "decoder": self.decoder_key,
                }
            )
        except Exception as e:
            logger.warning(f"Could not save the radio state: {e}")

    def snapshot(self) -> dict:
        band = band_for(self.bands, self.frequency)
        channel = self.channel
        return {
            "power": self.power,
            "frequency": self.frequency,
            "center": self.center,
            "rate": self.rate,
            "mode": self.mode,
            "bandwidth": self.bandwidth,
            "bandwidths": MODES[self.mode]["bandwidths"],
            "step": self.step,
            "squelch": self.squelch,
            "gain": self.gain,
            "gain_range": list(self.device.gain_range) if self.device else None,
            "agc": self.agc,
            "nb": self.noise_blanker,
            "level": round(self.level, 1),
            "noise": round(self.noise, 1),
            "open": self.signal is not None or (self._threshold() is None),
            "busy": self.signal is not None,
            "band": band["name"] if band else None,
            "channel": {k: channel[k] for k in ("id", "number", "name", "favourite", "scan", "priority")}
            if channel
            else None,
            "ignored": self.frequency in self.ignored,
            "scan": {
                "kind": self.scan.kind,
                "hold": self.scan.hold,
                "label": self.scan.label,
                "position": self.scan.index + 1,
                "count": len(self.scan.targets),
            }
            if self.scan.kind
            else None,
            "priority": self.priority,
            "decoder": self._decoder_snapshot(),
            # Every mode; the ones this receiver can't do say why (ghosted on the page)
            "modes": self._modes(),
            "sweep": {"start": self.sweep["start"], "span": self.sweep["span"], "label": self.sweep["label"]}
            if self.sweep
            else None,
            "audio_rate": self.demod.audio_rate if self.demod else None,
            "rds": self.demod.rds.snapshot() if self.demod and self.demod.rds else None,
            "tones": self.demod.tones.snapshot() if self.demod and self.demod.tones else None,
            "device": {
                "ok": self.device is not None,
                "error": self.device_error,
                "receiver": self.config.receiver.name,
                **self.device_info,
            },
        }

    def _emit_state(self, force: bool = False) -> None:
        now = time.monotonic()
        # The level changes all the time: five updates a second, at once after a change
        if force or (self._state_dirty and now - self._last_state_emit > 0.05) or now - self._last_state_emit > 0.2:
            self._last_state_emit = now
            self._state_dirty = False
            self.emit("state", self.snapshot())
