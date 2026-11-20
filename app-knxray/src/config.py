"""Configuration for KNX-Ray, read from the app options."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

# App storage: options.json, the recorded telegrams, the chosen KNX interface, what devices answered and the
# hidden findings.
# The KNXRAY_* environment variables are for development only; the app never sets them.
DATA_DIR = Path(os.environ.get("KNXRAY_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("KNXRAY_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("KNXRAY_PORT", "8023"))
# Development only: a multicast port of its own keeps the stand-in installation off a real KNX network
MULTICAST_PORT = int(os.environ.get("KNXRAY_MULTICAST_PORT", "3671"))


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

    # How many telegrams the live monitor keeps in memory
    monitor_size: int = 20000
    # The recording on disk: telegrams older than this many days go (0 keeps them), and so do the oldest
    # ones beyond this many rows (0 for no limit)
    retain_days: int = 90
    retain_rows: int = 2000000
    # A group address or device sending more than this per minute is reported as chatty
    chatty_per_minute: int = 30
    # On: stray Home Assistant data can be removed from the Home Assistant page. Off: the app only reports
    allow_remove: bool = True
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
        def number(key: str, default: int, low: int, high: int) -> int:
            try:
                value = int(options.get(key, default))
            except (TypeError, ValueError):
                raise ValueError(f"The option {key} must be a number") from None
            if not low <= value <= high:
                raise ValueError(f"The option {key} must be between {low} and {high}")
            return value

        return cls(
            monitor_size=number("monitor_size", 20000, 1000, 200000),
            retain_days=number("retain_days", 90, 0, 3650),
            retain_rows=number("retain_rows", 2000000, 0, 50000000),
            chatty_per_minute=number("chatty_per_minute", 30, 5, 3000),
            allow_remove=bool(options.get("allow_remove", True)),
            log_level=str(options.get("log_level") or "info").lower(),
        )
