"""Configuration management for Toon."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# App storage: options.json, the database and the Toon's SSH key live here.
# The TOON_* environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("TOON_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("TOON_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("TOON_PORT", "8099"))


def app_version() -> str:
    """The app version from config.yaml (copied next to the sources in the image)."""
    for path in (Path("/app/config.yaml"), Path(__file__).resolve().parent.parent / "config.yaml"):
        try:
            for line in path.read_text().splitlines():
                if line.startswith("version:"):
                    return line.split(":", 1)[1].strip().strip("\"'")
        except OSError:
            continue
    return "dev"


APP_VERSION = app_version()


@dataclass
class Config:
    """Application configuration, read from the app options."""

    # The only connection to the Toon; see toon_link.py
    ssh_host: str = ""
    ssh_port: int = 22
    ssh_username: str = "root"
    ssh_password: str = ""
    # Accept a changed SSH key once, for example after the Toon's software was reinstalled
    trust_new_host_key: bool = False

    # Read the history the Toon keeps itself (up to 10 years) into the database
    import_history: bool = True
    # Every sample is kept this many days; hourly values are kept for good
    raw_retention_days: int = 90

    # MQTT publishing with Home Assistant discovery
    mqtt_enabled: bool = True
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    mqtt_base_topic: str = "toon"
    mqtt_discovery_prefix: str = "homeassistant"

    # Your boiler's name, only shown on the pages
    boiler_name: str = ""
    # Leave out everything that can change the thermostat
    read_only: bool = False

    # Energy in a cubic metre of Dutch gas (upper heating value)
    gas_kwh_per_m3: float = 9.77
    # Sensors read from Home Assistant: the outside temperature, and any others
    outside_temperature: str = ""
    external_sensors: list[dict] = field(default_factory=list)

    log_level: str = "info"

    @classmethod
    def from_options_file(cls, path: Path = OPTIONS_FILE) -> "Config":
        """Read the options. Raises ValueError when they can't be used."""
        try:
            options = json.loads(path.read_text())
        except OSError as e:
            raise ValueError(f"Cannot read the options file {path}: {e}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"The options file {path} is not valid JSON: {e}") from e
        return cls.from_options(options)

    @classmethod
    def from_options(cls, options: dict) -> "Config":
        ssh = options.get("ssh") or {}
        mqtt = options.get("mqtt") or {}
        config = cls(
            ssh_host=str(ssh.get("host") or "").strip(),
            ssh_port=int(ssh.get("port") or 22),
            ssh_username=str(ssh.get("username") or "root"),
            ssh_password=str(ssh.get("password") or ""),
            trust_new_host_key=bool(ssh.get("trust_new_host_key", False)),
            import_history=bool(options.get("import_history", True)),
            raw_retention_days=max(1, int(options.get("raw_retention_days") or 90)),
            mqtt_enabled=bool(mqtt.get("enabled", True)),
            mqtt_host=str(mqtt.get("host") or ""),
            mqtt_port=int(mqtt.get("port") or 1883),
            mqtt_user=str(mqtt.get("user") or ""),
            mqtt_password=str(mqtt.get("password") or ""),
            mqtt_base_topic=str(mqtt.get("base_topic") or "toon"),
            mqtt_discovery_prefix=str(mqtt.get("discovery_prefix") or "homeassistant"),
            boiler_name=str(options.get("boiler_name") or "").strip(),
            read_only=bool(options.get("read_only", False)),
            gas_kwh_per_m3=float(options.get("gas_kwh_per_m3") or 9.77),
            outside_temperature=str(options.get("outside_temperature") or "").strip(),
            external_sensors=[
                {"name": str(item["name"]).strip(), "entity": str(item["entity"]).strip()}
                for item in options.get("external_sensors") or []
                if item.get("name") and item.get("entity")
            ],
            log_level=str(options.get("log_level") or "info"),
        )
        if not config.ssh_host:
            raise ValueError("ssh.host is not set: enter the address of your Toon")
        if not config.ssh_password:
            raise ValueError("ssh.password is not set: enter the root password of your Toon")
        return config
