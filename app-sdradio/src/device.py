"""The SDR receiver, through SoapySDR: RTL-SDR, HackRF, any SoapySDR device or an rtl_tcp server."""

import logging

import numpy as np

from .config import ReceiverConfig

logger = logging.getLogger(__name__)


class DeviceError(RuntimeError):
    """The receiver can't be opened or stopped working; the message is shown in the page."""


class SoapyDevice:
    def __init__(self, config: ReceiverConfig):
        self.config = config
        self.dev = None
        self.stream = None
        self.info: dict = {}
        self.gain_range = (0.0, 50.0, 0.0)
        self.frequency_range = (24_000_000.0, 1_766_000_000.0)

    def open(self, center: float) -> dict:
        try:
            import SoapySDR
            from SoapySDR import SOAPY_SDR_CF32, SOAPY_SDR_RX
        except ImportError as e:
            raise DeviceError(f"SoapySDR is not installed: {e}") from e
        self._soapy, self._rx = SoapySDR, SOAPY_SDR_RX
        args = self.config.device_args()
        try:
            found = SoapySDR.Device.enumerate(args)
        except Exception as e:
            raise DeviceError(f"Cannot look for the receiver: {e}") from e
        if not found and self.config.type != "network":
            raise DeviceError(f"No {self.config.name} found: is it plugged in, and not in use by another app?")
        try:
            self.dev = SoapySDR.Device(args)
        except Exception as e:
            logger.warning("Opening %s (%s) failed: %s", self.config.name, args, e)
            if found and self.config.type != "network":
                # Found but not opened: almost always another program holds it
                raise DeviceError(
                    f"The {self.config.name} is found but can't be opened: another app or program is "
                    "probably using it. Stop that one, or share the receiver through rtl_tcp."
                ) from e
            raise DeviceError(f"Cannot open the {self.config.name}: {e}") from e

        dev, rx = self.dev, SOAPY_SDR_RX
        try:
            dev.setSampleRate(rx, 0, float(self.config.sample_rate))
            if self.config.ppm_correction and dev.hasFrequencyCorrection(rx, 0):
                dev.setFrequencyCorrection(rx, 0, float(self.config.ppm_correction))
            if self.config.bias_tee:
                dev.writeSetting("biastee", "true")
            if self.config.type == "hackrf":
                dev.setGain(rx, 0, "LNA", float(self.config.hackrf_lna_gain))
                dev.setGain(rx, 0, "VGA", float(self.config.hackrf_vga_gain))
                dev.setGain(rx, 0, "AMP", 14.0 if self.config.hackrf_amp_enable else 0.0)
            dev.setFrequency(rx, 0, float(center))
            gain = dev.getGainRange(rx, 0)
            self.gain_range = (gain.minimum(), gain.maximum(), gain.step())
            ranges = dev.getFrequencyRange(rx, 0)
            if ranges:
                self.frequency_range = (min(r.minimum() for r in ranges), max(r.maximum() for r in ranges))
            # Small driver buffers: retuning (scanning) is then heard within a few milliseconds
            stream_args = {"buffers": "8", "bufflen": "65536"} if self.config.type == "rtl-sdr" else {}
            self.stream = dev.setupStream(rx, SOAPY_SDR_CF32, [0], stream_args)
            dev.activateStream(self.stream)
            hardware = dict(dev.getHardwareInfo())
        except Exception as e:
            self.close()
            raise DeviceError(f"Cannot set up the {self.config.name}: {e}") from e
        self.info = {
            "driver": dev.getDriverKey(),
            "hardware": dev.getHardwareKey(),
            "details": hardware,
            "sample_rate": dev.getSampleRate(rx, 0),
            "gain_range": list(self.gain_range),
            "frequency_range": list(self.frequency_range),
        }
        return self.info

    @property
    def sample_rate(self) -> float:
        return float(self.info.get("sample_rate") or self.config.sample_rate)

    def set_center(self, frequency: float) -> None:
        self.dev.setFrequency(self._rx, 0, float(frequency))

    def set_gain(self, gain: float | None) -> None:
        """None = automatic gain (when the receiver has it)."""
        dev, rx = self.dev, self._rx
        if gain is None:
            if dev.hasGainMode(rx, 0):
                dev.setGainMode(rx, 0, True)
            return
        if dev.hasGainMode(rx, 0):
            dev.setGainMode(rx, 0, False)
        dev.setGain(rx, 0, float(gain))

    def read(self, count: int) -> np.ndarray:
        buffer = np.empty(count, np.complex64)
        filled = 0
        failures = 0
        while filled < count:
            result = self.dev.readStream(self.stream, [buffer[filled:]], count - filled, timeoutUs=1_000_000)
            if result.ret > 0:
                filled += result.ret
                failures = 0
            elif result.ret in (-4,):  # overflow: samples were lost, go on
                continue
            else:
                failures += 1
                if failures >= 3:
                    raise DeviceError(f"The {self.config.name} stopped sending samples ({result.ret})")
        return buffer

    def close(self) -> None:
        if self.dev is None:
            return
        try:
            if self.stream is not None:
                self.dev.deactivateStream(self.stream)
                self.dev.closeStream(self.stream)
        except Exception as e:
            logger.debug(f"Closing the stream: {e}")
        self.stream = None
        self.dev = None
