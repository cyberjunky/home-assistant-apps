"""Configuration for SDRadio: the receiver and where it starts, from the app options."""

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .bands import MODES

logger = logging.getLogger(__name__)

# App storage: options.json, the database and its backups live here. The SDRADIO_*
# environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("SDRADIO_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("SDRADIO_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("SDRADIO_PORT", "8099"))

RECEIVER_TYPES = ("rtl-sdr", "hackrf", "soapysdr", "network")
RECEIVER_NAMES = {"rtl-sdr": "RTL-SDR", "hackrf": "HackRF", "soapysdr": "SoapySDR", "network": "rtl_tcp"}


def addon_version() -> str:
    """The app version from config.yaml (copied next to the sources in the image)."""
    for path in (Path("/app/config.yaml"), Path(__file__).resolve().parent.parent / "config.yaml"):
        try:
            for line in path.read_text().splitlines():
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
        except OSError:
            continue
    return "dev"


ADDON_VERSION = addon_version()


def _int(value, default: int, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


def _float(value, default: float, low: float, high: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if low <= number <= high else default


@dataclass
class ReceiverConfig:
    type: str = "rtl-sdr"
    serial: str = ""
    network_host: str = ""
    network_port: int = 1234
    sample_rate: int = 2_400_000
    ppm_correction: int = 0
    # 0 = automatic gain
    sdr_gain: float = 0.0
    hackrf_lna_gain: int = 32
    hackrf_vga_gain: int = 30
    hackrf_amp_enable: bool = False
    bias_tee: bool = False

    @property
    def name(self) -> str:
        return RECEIVER_NAMES.get(self.type, self.type)

    def device_args(self) -> dict:
        """The SoapySDR device arguments for this receiver."""
        args: dict = {}
        if self.type == "rtl-sdr":
            args["driver"] = "rtlsdr"
        elif self.type == "hackrf":
            args["driver"] = "hackrf"
        elif self.type == "network":
            args["driver"] = "rtltcp"
            args["rtltcp"] = f"{self.network_host}:{self.network_port}"
        if self.serial and self.type != "network":
            args["serial"] = self.serial
        return args


@dataclass
class TuneConfig:
    frequency: int = 145_500_000
    mode: str = "nfm"
    restore_last: bool = True


@dataclass
class Config:
    """Application configuration, read from the app options."""

    receiver: ReceiverConfig = field(default_factory=ReceiverConfig)
    tune: TuneConfig = field(default_factory=TuneConfig)
    log_level: str = "info"

    def public(self) -> dict:
        """The options as the Settings page shows them (frequency in MHz, as entered)."""
        tune = asdict(self.tune)
        tune["frequency"] = self.tune.frequency / 1e6
        return {"receiver": asdict(self.receiver), "tune": tune, "log_level": self.log_level}

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        if not isinstance(data, dict):
            raise ValueError("Invalid app options: expected a JSON object")
        receiver = data.get("receiver") or {}
        tune = data.get("tune") or {}
        if not isinstance(receiver, dict) or not isinstance(tune, dict):
            raise ValueError("Invalid app options: receiver and tune must be sections")

        kind = str(receiver.get("type") or "rtl-sdr")
        if kind not in RECEIVER_TYPES:
            raise ValueError(f"Unknown receiver type {kind!r}: use one of {', '.join(RECEIVER_TYPES)}")
        host = str(receiver.get("network_host") or "").strip()
        if kind == "network" and not host:
            raise ValueError("Receiver type network needs network_host: the address of the rtl_tcp server")
        rx = ReceiverConfig(
            type=kind,
            serial=str(receiver.get("serial") or "").strip(),
            network_host=host,
            network_port=_int(receiver.get("network_port"), 1234, 1, 65535),
            sample_rate=_int(receiver.get("sample_rate"), 2_400_000, 250_000, 20_000_000),
            ppm_correction=_int(receiver.get("ppm_correction"), 0, -200, 200),
            sdr_gain=_float(receiver.get("sdr_gain"), 0.0, 0.0, 110.0),
            hackrf_lna_gain=_int(receiver.get("hackrf_lna_gain"), 32, 0, 40),
            hackrf_vga_gain=_int(receiver.get("hackrf_vga_gain"), 30, 0, 62),
            hackrf_amp_enable=bool(receiver.get("hackrf_amp_enable", False)),
            bias_tee=bool(receiver.get("bias_tee", False)),
        )
        if rx.bias_tee and kind != "rtl-sdr":
            raise ValueError("bias_tee only works with receiver type rtl-sdr")

        mode = str(tune.get("mode") or "nfm")
        if mode not in MODES:
            raise ValueError(f"Unknown mode {mode!r}: use one of {', '.join(MODES)}")
        frequency = _float(tune.get("frequency"), 145.5, 0.01, 6000.0)
        return cls(
            receiver=rx,
            tune=TuneConfig(
                frequency=round(frequency * 1e6), mode=mode, restore_last=bool(tune.get("restore_last", True))
            ),
            log_level=str(data.get("log_level") or "info"),
        )

    @classmethod
    def from_options_file(cls, path: Path = OPTIONS_FILE) -> "Config":
        try:
            data = json.loads(Path(path).read_text())
        except FileNotFoundError:
            # Outside Home Assistant (development): the defaults
            logger.warning(f"No options file at {path}, using the defaults")
            return cls()
        except (OSError, ValueError) as e:
            raise ValueError(f"Cannot read app options from {path}: {e}") from e
        return cls.from_dict(data)
