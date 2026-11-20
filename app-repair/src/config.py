"""Configuration management for Repair."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

# App storage: options.json and the database live here.
# The REPAIR_* environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("REPAIR_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("REPAIR_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("REPAIR_PORT", "8099"))
# Home Assistant's configuration folder, as the app sees it. Only read, and only with deep_scan on
HA_CONFIG_DIR = Path(os.environ.get("REPAIR_HA_CONFIG_DIR", "/homeassistant"))


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

    # How often Home Assistant is looked over
    scan_minutes: int = 30
    # Also run Home Assistant's own check of configuration.yaml on every scan
    check_config: bool = True
    # Off: the app only reports, and every repair button is gone
    allow_fixes: bool = True
    # Also read Home Assistant's database and registry files, for leftovers its API doesn't show.
    # Off unless asked for: it is the one thing the app does outside Home Assistant's API
    deep_scan: bool = False
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
        return cls(
            scan_minutes=min(1440, max(5, int(options.get("scan_minutes") or 30))),
            check_config=bool(options.get("check_config", True)),
            allow_fixes=bool(options.get("allow_fixes", True)),
            deep_scan=bool(options.get("deep_scan", False)),
            log_level=str(options.get("log_level") or "info"),
        )
