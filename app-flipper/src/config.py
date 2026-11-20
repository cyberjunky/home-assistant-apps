"""Configuration for the Flipper app, read from the app options."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

# App storage: options.json, the projects and the Flipper SDK live here.
# The FLIPPER_APP_* environment variables are for development only; the app never sets them.
DATA_DIR = Path(os.environ.get("FLIPPER_APP_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("FLIPPER_APP_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("FLIPPER_APP_PORT", "8099"))


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

    # The Flipper's serial port; empty finds the first Flipper plugged in
    device: str = ""
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
        device = str(options.get("device") or "").strip()
        if device and not device.startswith("/dev/"):
            raise ValueError(f"The device {device!r} is not a path under /dev")
        return cls(device=device, log_level=str(options.get("log_level") or "info").lower())
