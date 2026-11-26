"""Configuration of the Garmin Connect app, read from the app options."""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# App storage: options.json, the database and the token folder live here.
# The GARMIN_* environment variables are for development only (see tools/fake_garmin.py).
DATA_DIR = Path(os.environ.get("GARMIN_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("GARMIN_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("GARMIN_PORT", "8099"))

# The data sets that can be switched on and off, with their default
DATASET_DEFAULTS = {
    "daily": True,
    "sleep": True,
    "activities": True,
    "activity_tracks": True,
    "activity_details": True,
    "training": True,
    "body": True,
    "intraday": True,
    "records": True,
    "health_monitor": False,
    "performance": False,
    "nutrition": False,
    "menstrual": False,
    "golf": False,
    "sleep_detail": False,
    "intraday_more": False,
    "planning": False,
}


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


def _clamp(value, default, minimum, maximum):
    """A number from the options, falling back to the default when missing or out of range."""
    try:
        number = type(default)(value)
    except (TypeError, ValueError):
        return default
    return number if minimum <= number <= maximum else default


@dataclass
class Config:
    """Application configuration."""

    # How often the latest data is fetched
    update_interval_minutes: int = 30
    # Fetch the whole history once, back to start_date or the day the Garmin account was made
    import_history: bool = True
    start_date: str = ""
    datasets: dict[str, bool] = field(default_factory=lambda: dict(DATASET_DEFAULTS))
    log_level: str = "info"

    def enabled(self, group: str) -> bool:
        return bool(self.datasets.get(group, False))

    @classmethod
    def from_options_file(cls, path: Path = OPTIONS_FILE) -> "Config":
        try:
            data = json.loads(Path(path).read_text())
        except FileNotFoundError:
            # Outside Home Assistant (development): run with the defaults
            logger.warning(f"No options file at {path}, using defaults")
            return cls()
        except (OSError, ValueError) as e:
            raise ValueError(f"Cannot read app options from {path}: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"Invalid app options in {path}: expected a JSON object")

        history = data.get("history") or {}
        chosen = data.get("datasets") or {}
        start_date = str(history.get("start_date") or "").strip()
        if start_date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", start_date):
            raise ValueError(f"history.start_date must look like 2020-01-31, not {start_date!r}")
        defaults = cls()
        return cls(
            update_interval_minutes=_clamp(
                data.get("update_interval_minutes"), defaults.update_interval_minutes, 5, 1440
            ),
            import_history=bool(history.get("import", True)),
            start_date=start_date,
            datasets={key: bool(chosen.get(key, default)) for key, default in DATASET_DEFAULTS.items()},
            log_level=str(data.get("log_level") or "info"),
        )
