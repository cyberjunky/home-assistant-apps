"""Configuration of the MQTT app's dashboard."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

# App storage: options.json, the system users' passwords and the history database live here.
# The MQTT_* environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("MQTT_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("MQTT_OPTIONS", str(DATA_DIR / "options.json")))
SYSTEM_USERS_FILE = Path(os.environ.get("MQTT_SYSTEM_USERS", str(DATA_DIR / "system_user.json")))
# The broker's log as the log pipe writes it (see logpipe.py), the current file and the one before
BROKER_LOG = Path(os.environ.get("MQTT_BROKER_LOG", "/run/mqtt/broker.log"))
WEB_PORT = int(os.environ.get("MQTT_WEB_PORT", "8099"))
BROKER_HOST = os.environ.get("MQTT_HOST", "127.0.0.1")
BROKER_PORT = int(os.environ.get("MQTT_PORT", "1883"))
# The ports the broker listens on, for the clients list and the Info page
LISTENERS = {1883: "MQTT", 1884: "WebSocket", 8883: "MQTT TLS", 8884: "WebSocket TLS"}


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
    """The options the dashboard uses; the broker's own are read by the init script."""

    keep_days: int = 30
    # Keep the messages' contents (the last value of every topic, the live traffic); off: only counts
    capture_payloads: bool = True
    log_level: str = "info"
    # Usernames of the local logins, to tell them from Home Assistant users
    logins: tuple[str, ...] = ()
    ssl: bool = False

    @classmethod
    def from_options_file(cls, path: Path = OPTIONS_FILE) -> "Config":
        """Read the options. Raises ValueError when they can't be used."""
        try:
            options = json.loads(path.read_text())
        except FileNotFoundError:
            options = {}
        except OSError as e:
            raise ValueError(f"Cannot read the options file {path}: {e}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"The options file {path} is not valid JSON: {e}") from e
        return cls.from_options(options)

    @classmethod
    def from_options(cls, options: dict) -> "Config":
        logins = tuple(str(x.get("username") or "") for x in options.get("logins") or [] if isinstance(x, dict))
        return cls(
            keep_days=min(3650, max(1, int(options.get("keep_days") or 30))),
            capture_payloads=bool(options.get("capture_payloads", True)),
            log_level=str(options.get("log_level") or "info"),
            logins=tuple(x for x in logins if x),
            ssl=Path("/run/mqtt/ssl").exists(),
        )


def system_user(name: str, path: Path = SYSTEM_USERS_FILE) -> tuple[str, str]:
    """A system user's (username, password) that the init script made, e.g. "dashboard"."""
    if os.environ.get("MQTT_USER"):
        return os.environ["MQTT_USER"], os.environ.get("MQTT_PASSWORD", "")
    users = json.loads(path.read_text())
    return {"homeassistant": "homeassistant", "addons": "addons"}.get(name, f"mqtt-{name}"), users[name]["password"]
