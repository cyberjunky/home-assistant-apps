"""Configuration management for Freqtrade."""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

# App storage: options.json and the database live here.
# The FREQTRADE_* environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("FREQTRADE_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("FREQTRADE_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("FREQTRADE_PORT", "8099"))


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
    """The bot's key in addresses, the database and MQTT topics: lower case letters, digits and _."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


@dataclass
class BotConfig:
    """One Freqtrade bot, reached over its REST API and WebSocket."""

    name: str
    slug: str
    url: str
    username: str
    password: str
    # Freqtrade's api_server.ws_token; without it the WebSocket logs in with a token from the REST API
    ws_token: str = ""
    verify_ssl: bool = True

    @property
    def api(self) -> str:
        return f"{self.url}/api/v1"

    @property
    def ws_url(self) -> str:
        scheme = "wss" if self.url.startswith("https") else "ws"
        return f"{scheme}{self.url[self.url.index(':') :]}/api/v1/message/ws"

    @property
    def host(self) -> str:
        """The address without credentials or path, to show."""
        parts = urlsplit(self.url)
        return parts.hostname + (f":{parts.port}" if parts.port else "") if parts.hostname else self.url


@dataclass
class Config:
    """Application configuration, read from the app options."""

    bots: list[BotConfig] = field(default_factory=list)
    # How often each bot's state is read; a trade event over the WebSocket reads it at once
    poll_seconds: int = 30
    # Leave out everything that can change a bot
    read_only: bool = False

    # MQTT publishing with Home Assistant discovery
    mqtt_enabled: bool = True
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    mqtt_base_topic: str = "freqtrade"
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
            bots=parse_bots(options.get("bots") or []),
            poll_seconds=min(600, max(10, int(options.get("poll_seconds") or 30))),
            read_only=bool(options.get("read_only", False)),
            mqtt_enabled=bool(mqtt.get("enabled", True)),
            mqtt_host=str(mqtt.get("host") or ""),
            mqtt_port=int(mqtt.get("port") or 1883),
            mqtt_user=str(mqtt.get("user") or ""),
            mqtt_password=str(mqtt.get("password") or ""),
            mqtt_base_topic=str(mqtt.get("base_topic") or "freqtrade"),
            mqtt_discovery_prefix=str(mqtt.get("discovery_prefix") or "homeassistant"),
            log_level=str(options.get("log_level") or "info"),
        )


def parse_bots(items: list) -> list[BotConfig]:
    """The bots in the order given. Raises ValueError for a bot that can't be used."""
    bots: list[BotConfig] = []
    for number, item in enumerate(items, 1):
        name = str(item.get("name") or "").strip()
        if not name:
            raise ValueError(f"bots: bot {number} has no name")
        slug = slugify(name)
        if not slug:
            raise ValueError(f"bots: the name {name!r} needs at least one letter or digit")
        if any(bot.slug == slug for bot in bots):
            raise ValueError(f"bots: two bots are called {name!r}; give each its own name")
        url = str(item.get("url") or "").strip().rstrip("/")
        # The API's own path is added by the app; accept it when it was entered anyway
        url = re.sub(r"/api/v1$", "", url)
        if not re.match(r"^https?://[^/\s]+", url):
            raise ValueError(f"bots: {name}: url {url!r} must start with http:// or https://")
        if not item.get("username") or not item.get("password"):
            raise ValueError(f"bots: {name}: enter the username and password of its api_server")
        bots.append(
            BotConfig(
                name=name,
                slug=slug,
                url=url,
                username=str(item["username"]),
                password=str(item["password"]),
                ws_token=str(item.get("ws_token") or ""),
                verify_ssl=bool(item.get("verify_ssl", True)),
            )
        )
    return bots
