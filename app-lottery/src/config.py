"""Configuration management for Lottery."""

import ipaddress
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# App storage: options.json and the database live here.
# The LOTTERY_* environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("LOTTERY_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("LOTTERY_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("LOTTERY_PORT", "8099"))

HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$")


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


def slugify(name: str) -> str:
    """The miner's key in addresses, the database and MQTT topics: lower case letters, digits and _."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@dataclass
class MinerConfig:
    """One miner, reached over its AxeOS web API."""

    name: str
    slug: str
    host: str

    @property
    def url(self) -> str:
        return f"http://{self.host}"


@dataclass
class Config:
    """Application configuration, read from the app options."""

    miners: list[MinerConfig] = field(default_factory=list)
    # How often each miner is read
    poll_seconds: int = 15
    # Read the network difficulty, the block height and recent fees from mempool.space
    network_data: bool = True
    # Look up the newest firmware release on GitHub, for the firmware entity
    firmware_check: bool = True

    # MQTT publishing with Home Assistant discovery
    mqtt_enabled: bool = True
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    mqtt_base_topic: str = "lottery"
    mqtt_discovery_prefix: str = "homeassistant"

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
        mqtt = options.get("mqtt") or {}
        return cls(
            miners=parse_miners(options.get("miners") or []),
            poll_seconds=min(300, max(5, int(options.get("poll_seconds") or 15))),
            network_data=bool(options.get("network_data", True)),
            firmware_check=bool(options.get("firmware_check", True)),
            mqtt_enabled=bool(mqtt.get("enabled", True)),
            mqtt_host=str(mqtt.get("host") or ""),
            mqtt_port=int(mqtt.get("port") or 1883),
            mqtt_user=str(mqtt.get("user") or ""),
            mqtt_password=str(mqtt.get("password") or ""),
            mqtt_base_topic=str(mqtt.get("base_topic") or "lottery"),
            mqtt_discovery_prefix=str(mqtt.get("discovery_prefix") or "homeassistant"),
            log_level=str(options.get("log_level") or "info"),
        )


def parse_host(value: str) -> str:
    """An address as entered, without http:// or a path. Raises ValueError when it isn't one."""
    host = re.sub(r"^https?://", "", value.strip()).split("/", 1)[0]
    if host.startswith("["):
        # An IPv6 address, optionally with a port
        address = host[1:].split("]", 1)[0]
        ipaddress.IPv6Address(address)
        return host
    if not HOST_PATTERN.match(host):
        raise ValueError(f"{value!r} is not an IP address or host name")
    return host


def parse_miners(items: list) -> list[MinerConfig]:
    """The miners in the order given. Raises ValueError for a miner that can't be used."""
    miners: list[MinerConfig] = []
    for number, item in enumerate(items, 1):
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError(f"miners: miner {number} has no name")
        slug = slugify(name)
        if not slug:
            raise ValueError(f"miners: the name {name!r} needs at least one letter or digit")
        if any(miner.slug == slug for miner in miners):
            raise ValueError(f"miners: two miners are called {name!r}; give each its own name")
        try:
            host = parse_host(str(item.get("host") or ""))
        except ValueError as e:
            raise ValueError(f"miners: {name}: {e}") from e
        miners.append(MinerConfig(name=name, slug=slug, host=host))
    return miners
