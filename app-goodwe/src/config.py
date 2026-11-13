"""Configuration management for GoodWe."""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Add-on storage: options.json, the database and the controller state live here.
# The GOODWE_* environment variables are for development only (see tools/mock_inverter.py);
# the add-on itself never sets them.
DATA_DIR = Path(os.environ.get("GOODWE_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("GOODWE_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("GOODWE_PORT", "8099"))


def addon_version() -> str:
    """The add-on version from config.yaml (copied next to the sources in the image)."""
    for path in (Path("/app/config.yaml"), Path(__file__).resolve().parent.parent / "config.yaml"):
        try:
            for line in path.read_text().splitlines():
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
        except OSError:
            continue
    return "dev"


ADDON_VERSION = addon_version()


def _clamp(value, default, minimum, maximum):
    """A number from the options, falling back to the default when missing or out of range."""
    try:
        number = type(default)(value)
    except (TypeError, ValueError):
        return default
    return number if minimum <= number <= maximum else default


@dataclass
class Plane:
    """One group of panels facing the same way (a roof plane), for the solar forecast."""

    name: str = "Roof"
    kwp: float = 4.0
    tilt: int = 35  # degrees, 0 = flat
    azimuth: int = 0  # degrees, 0 = south, -90 = east, 90 = west

    def as_dict(self) -> dict:
        return {"name": self.name, "kwp": self.kwp, "tilt": self.tilt, "azimuth": self.azimuth}


ENTITY_RE = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
MAX_MINERS = 8


@dataclass
class Miner:
    """One surplus load (a bitcoin miner): the switch that starts and stops it, the sensor with its
    power, and its rated power (optional with a sensor: the app learns the draw from it)."""

    name: str = "Miner"
    switch: str = ""
    sensor: str = ""
    power_w: int = 0

    def as_dict(self) -> dict:
        return {"name": self.name, "switch": self.switch, "sensor": self.sensor, "power_w": self.power_w}


def _miners(items) -> list[Miner]:
    """The miners from the options: a list, each with a switch or a sensor (or a rated power)."""
    out: list[Miner] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or len(out) >= MAX_MINERS:
            continue
        switch = str(item.get("switch") or "").strip()
        sensor = str(item.get("sensor") or "").strip()
        for entity in (switch, sensor):
            if entity and not ENTITY_RE.match(entity):
                raise ValueError(f"miners: {entity!r} is not an entity id (like switch.miner_1)")
        miner = Miner(
            name=str(item.get("name") or "").strip()[:40] or f"Miner {len(out) + 1}",
            switch=switch,
            sensor=sensor,
            power_w=_clamp(item.get("power_w"), 0, 0, 100000),
        )
        if miner.switch or miner.sensor or miner.power_w:
            out.append(miner)
    names = [m.name for m in out]
    if len(set(names)) != len(names):
        raise ValueError("miners: give every miner its own name")
    return out


@dataclass
class Config:
    """Application configuration, read from the add-on options."""

    inverter_host: str = ""
    # Seconds between inverter reads. The ES/AA55 network stack stops answering when polled too fast
    poll_seconds: int = 20

    # Solar forecast (Forecast.Solar with Open-Meteo as fallback); 0/0 = Home Assistant's home location
    forecast_enabled: bool = True
    latitude: float = 0.0
    longitude: float = 0.0
    planes: list[Plane] = field(default_factory=lambda: [Plane()])

    # MQTT publishing with Home Assistant discovery
    mqtt_enabled: bool = True
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    mqtt_base_topic: str = "goodwe"
    mqtt_discovery_prefix: str = "homeassistant"

    # The surplus loads (bitcoin miners): each with the Home Assistant switch that starts and stops
    # it, the power sensor that says what it draws, and its rated power
    miners: list["Miner"] = field(default_factory=list)

    log_level: str = "info"

    @property
    def total_kwp(self) -> float:
        return round(sum(p.kwp for p in self.planes), 2)

    @classmethod
    def from_options_file(cls, path: Path = OPTIONS_FILE) -> "Config":
        try:
            data = json.loads(Path(path).read_text())
        except FileNotFoundError:
            # Outside Home Assistant (development): run with the defaults and the environment
            logger.warning(f"No options file at {path}, using defaults")
            config = cls(inverter_host=os.environ.get("GOODWE_INVERTER_HOST", ""))
            config.mqtt_enabled = os.environ.get("GOODWE_MQTT_HOST", "") != ""
            config.mqtt_host = os.environ.get("GOODWE_MQTT_HOST", "")
            return config
        except (OSError, ValueError) as e:
            raise ValueError(f"Cannot read add-on options from {path}: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"Invalid add-on options in {path}: expected a JSON object")

        inverter = data.get("inverter") or {}
        forecast = data.get("forecast") or {}
        mqtt = data.get("mqtt") or {}
        defaults = cls()
        planes = []
        for item in forecast.get("panels") or []:
            if not isinstance(item, dict):
                continue
            planes.append(
                Plane(
                    name=str(item.get("name") or f"Panels {len(planes) + 1}")[:40],
                    kwp=_clamp(item.get("kwp"), 4.0, 0.1, 1000.0),
                    tilt=_clamp(item.get("tilt"), 35, 0, 90),
                    azimuth=_clamp(item.get("azimuth"), 0, -180, 180),
                )
            )
        host = str(inverter.get("host") or os.environ.get("GOODWE_INVERTER_HOST", "")).strip()
        if not host:
            raise ValueError("inverter.host is not set: enter the inverter's IP address in the add-on configuration")
        return cls(
            inverter_host=host,
            poll_seconds=_clamp(inverter.get("poll_seconds"), defaults.poll_seconds, 5, 300),
            forecast_enabled=bool(forecast.get("enabled", defaults.forecast_enabled)),
            latitude=_clamp(forecast.get("latitude"), 0.0, -90.0, 90.0),
            longitude=_clamp(forecast.get("longitude"), 0.0, -180.0, 180.0),
            planes=planes or [Plane()],
            mqtt_enabled=bool(mqtt.get("enabled", defaults.mqtt_enabled)),
            mqtt_host=str(mqtt.get("host") or ""),
            mqtt_port=_clamp(mqtt.get("port"), defaults.mqtt_port, 1, 65535),
            mqtt_user=str(mqtt.get("user") or ""),
            mqtt_password=str(mqtt.get("password") or ""),
            mqtt_base_topic=str(mqtt.get("base_topic") or defaults.mqtt_base_topic).strip("/"),
            mqtt_discovery_prefix=str(mqtt.get("discovery_prefix") or defaults.mqtt_discovery_prefix).strip("/"),
            miners=_miners(data.get("miners")),
            log_level=str(data.get("log_level") or "info"),
        )
