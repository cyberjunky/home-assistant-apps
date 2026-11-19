"""Configuration for the Marshal app, read from the app options."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

# App storage: options.json, the results and the cached catalog live here.
# The MARSHAL_APP_* environment variables are for development only; the app never sets them.
DATA_DIR = Path(os.environ.get("MARSHAL_APP_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("MARSHAL_APP_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("MARSHAL_APP_PORT", "8099"))
# Home Assistant's configuration folder, mapped read-only (config.yaml map:)
HA_CONFIG_DIR = Path(os.environ.get("MARSHAL_APP_HA_CONFIG", "/homeassistant"))
# Home Assistant Core's WebSocket API, through the Supervisor's proxy
CORE_WEBSOCKET_URL = os.environ.get("MARSHAL_APP_CORE_WEBSOCKET", "ws://supervisor/core/websocket")


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
USER_AGENT = f"marshal-app/{APP_VERSION}"


@dataclass
class Config:
    """Application configuration, read from the app options."""

    # Everything is scanned at start, when an integration is added or updated, and again after this many hours
    rescan_hours: int = 24
    # The Marketplace catalog: removed repositories, last change, installs
    catalog_lookups: bool = True
    # Pinned requirements are looked up in the OSV vulnerability database
    vulnerability_lookups: bool = True
<<<<<<< HEAD
    # Each direct requirement is downloaded from PyPI and read like the integration itself
    dependency_scans: bool = True
=======
>>>>>>> origin/main
    # Setup times and log entries, asked of Home Assistant
    runtime_checks: bool = True
    log_level: str = "info"

    @classmethod
    def from_options_file(cls, path: Path = OPTIONS_FILE) -> "Config":
        try:
            options = json.loads(path.read_text())
        except OSError as e:
            raise ValueError(f"Cannot read the options file {path}: {e}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"The options file {path} is not valid JSON: {e}") from e
        return cls.from_options(options)

    @classmethod
    def from_options(cls, options: dict) -> "Config":
        def flag(key: str, default: bool) -> bool:
            return bool(options.get(key, default))

        return cls(
            rescan_hours=max(1, min(720, int(options.get("rescan_hours") or 24))),
            catalog_lookups=flag("catalog_lookups", True),
            vulnerability_lookups=flag("vulnerability_lookups", True),
<<<<<<< HEAD
            dependency_scans=flag("dependency_scans", True),
=======
>>>>>>> origin/main
            runtime_checks=flag("runtime_checks", True),
            log_level=str(options.get("log_level") or "info").lower(),
        )

    def public(self) -> dict:
        return {
            "rescan_hours": self.rescan_hours,
            "catalog_lookups": self.catalog_lookups,
            "vulnerability_lookups": self.vulnerability_lookups,
<<<<<<< HEAD
            "dependency_scans": self.dependency_scans,
=======
>>>>>>> origin/main
            "runtime_checks": self.runtime_checks,
        }
