"""Configuration management for Dashboard."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

# App storage: options.json and the dashboard live here.
# The DASHBOARD_* environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("DASHBOARD_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("DASHBOARD_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("DASHBOARD_PORT", "8099"))


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

    log_level: str = "info"

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
        return cls(log_level=str(options.get("log_level") or "info"))
