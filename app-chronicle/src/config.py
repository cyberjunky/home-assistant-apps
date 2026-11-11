"""Configuration for Chronicle: the database the Chronicle integration records into."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# App storage: options.json and the donate prompt state live here. The CHRONICLE_*
# environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("CHRONICLE_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("CHRONICLE_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("CHRONICLE_PORT", "8099"))

DEFAULT_HOST = "1b41fb55-timescaledb"


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


@dataclass
class Config:
    """Application configuration, read from the app options."""

    host: str = DEFAULT_HOST
    port: int = 5432
    username: str = "postgres"
    password: str = ""
    database: str = "homeassistant"
    log_level: str = "info"

    def public(self) -> dict:
        """The connection without the password, for the Info page."""
        return {"host": self.host, "port": self.port, "username": self.username, "database": self.database}

    @classmethod
    def from_options_file(cls, path: Path = OPTIONS_FILE) -> "Config":
        try:
            data = json.loads(Path(path).read_text())
        except FileNotFoundError:
            # Outside Home Assistant (development): the defaults and the environment
            logger.warning(f"No options file at {path}, using defaults and CHRONICLE_DB_* variables")
            return cls(
                host=os.environ.get("CHRONICLE_DB_HOST", DEFAULT_HOST),
                port=int(os.environ.get("CHRONICLE_DB_PORT", "5432")),
                username=os.environ.get("CHRONICLE_DB_USER", "postgres"),
                password=os.environ.get("CHRONICLE_DB_PASSWORD", ""),
                database=os.environ.get("CHRONICLE_DB_NAME", "homeassistant"),
            )
        except (OSError, ValueError) as e:
            raise ValueError(f"Cannot read app options from {path}: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"Invalid app options in {path}: expected a JSON object")

        host = str(data.get("host") or "").strip()
        if not host:
            raise ValueError("host is not set: enter the TimescaleDB host on the app's Configuration tab")
        try:
            port = int(data.get("port") or 5432)
        except (TypeError, ValueError):
            port = 5432
        return cls(
            host=host,
            port=port if 1 <= port <= 65535 else 5432,
            username=str(data.get("username") or "postgres").strip(),
            password=str(data.get("password") or ""),
            database=str(data.get("database") or "homeassistant").strip(),
            log_level=str(data.get("log_level") or "info"),
        )
