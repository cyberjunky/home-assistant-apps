"""Configuration management for the Tesla app."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Add-on storage: options.json, the database and the token file live here.
# The TESLA_* environment variables are for development only
# (see tools/mock_tesla.py); the add-on itself never sets them.
DATA_DIR = Path(os.environ.get("TESLA_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("TESLA_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("TESLA_PORT", "8099"))

KM_PER_MILE = 1.609344


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
class Config:
    """Application configuration, read from the add-on options."""

    # Display units of the dashboard; everything is stored in metric
    units: str = "metric"
    currency: str = "EUR"

    # Polling intervals (seconds) per vehicle state
    poll_driving: int = 3
    poll_charging: int = 10
    poll_online: int = 15
    poll_asleep: int = 30
    # After this long without activity the car is left alone so it can fall asleep ...
    idle_minutes: int = 15
    # ... for at most this long, after which its data is read once again
    sleep_window_minutes: int = 20

    # Tesla's streaming channel: 1 Hz positions while driving, and it notices
    # activity without keeping the car awake
    streaming: bool = True

    # Look up addresses at OpenStreetMap Nominatim for where drives and charges start and end
    geocoding: bool = True

    # Price per kWh for new charging sessions (0 = don't calculate costs)
    cost_per_kwh: float = 0.0
    # Read what Tesla billed for sessions at its own chargers from the account's charging history
    supercharger_costs: bool = True
    # Rated consumption in Wh/km; 0 = learn it from charging sessions
    efficiency_wh_km: float = 0.0
    # Usable battery capacity when new (kWh): the 100% of battery health; 0 = the best measured
    battery_capacity_kwh: float = 0.0

    # MQTT publishing with Home Assistant discovery
    mqtt_enabled: bool = True
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    mqtt_base_topic: str = "tesla"
    mqtt_discovery_prefix: str = "homeassistant"

    log_level: str = "info"

    @classmethod
    def from_options_file(cls, path: Path = OPTIONS_FILE) -> "Config":
        try:
            data = json.loads(Path(path).read_text())
        except FileNotFoundError:
            # Outside Home Assistant (development): run with the defaults
            logger.warning(f"No options file at {path}, using defaults")
            return cls()
        except (OSError, ValueError) as e:
            raise ValueError(f"Cannot read add-on options from {path}: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"Invalid add-on options in {path}: expected a JSON object")

        polling = data.get("polling") or {}
        mqtt = data.get("mqtt") or {}
        defaults = cls()
        return cls(
            units="imperial" if data.get("units") == "imperial" else "metric",
            currency=str(data.get("currency") or defaults.currency)[:8],
            poll_driving=_clamp(polling.get("driving_seconds"), defaults.poll_driving, 1, 60),
            poll_charging=_clamp(polling.get("charging_seconds"), defaults.poll_charging, 2, 300),
            poll_online=_clamp(polling.get("online_seconds"), defaults.poll_online, 5, 600),
            poll_asleep=_clamp(polling.get("asleep_seconds"), defaults.poll_asleep, 10, 600),
            idle_minutes=_clamp(polling.get("idle_minutes"), defaults.idle_minutes, 1, 120),
            sleep_window_minutes=_clamp(polling.get("sleep_window_minutes"), defaults.sleep_window_minutes, 5, 120),
            streaming=bool(data.get("streaming", defaults.streaming)),
            geocoding=bool(data.get("geocoding", defaults.geocoding)),
            cost_per_kwh=_clamp(data.get("cost_per_kwh"), defaults.cost_per_kwh, 0.0, 100.0),
            supercharger_costs=bool(data.get("supercharger_costs", defaults.supercharger_costs)),
            efficiency_wh_km=_clamp(data.get("efficiency_wh_km"), defaults.efficiency_wh_km, 0.0, 500.0),
            battery_capacity_kwh=_clamp(data.get("battery_capacity_kwh"), defaults.battery_capacity_kwh, 0.0, 250.0),
            mqtt_enabled=bool(mqtt.get("enabled", defaults.mqtt_enabled)),
            mqtt_host=str(mqtt.get("host") or ""),
            mqtt_port=_clamp(mqtt.get("port"), defaults.mqtt_port, 1, 65535),
            mqtt_user=str(mqtt.get("user") or ""),
            mqtt_password=str(mqtt.get("password") or ""),
            mqtt_base_topic=str(mqtt.get("base_topic") or defaults.mqtt_base_topic).strip("/"),
            mqtt_discovery_prefix=str(mqtt.get("discovery_prefix") or defaults.mqtt_discovery_prefix).strip("/"),
            log_level=str(data.get("log_level") or "info"),
        )
