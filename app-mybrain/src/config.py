"""Configuration management for MyBrain."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

# App storage: options.json, the database and the thumbnails live here.
# The MYBRAIN_* environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("MYBRAIN_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("MYBRAIN_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("MYBRAIN_PORT", "8099"))

DEFAULT_CATEGORIES = [
    "Tech",
    "Home Assistant",
    "Home & DIY",
    "Crypto & Finance",
    "Recipes & Food",
    "Shopping",
    "Travel",
    "Health & Sport",
    "News",
    "Entertainment",
    "Learning",
    "Work",
    "Personal",
    "Other",
]


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

    # Save what is shared to Home Assistant from the companion app (the mobile_app.share event)
    listen_shares: bool = True
    # Read the page behind a link: title, description, picture, text
    fetch_pages: bool = True
    # Let an AI Task entity in Home Assistant pick the category, tags and a summary
    ai_classify: bool = True
    # Which AI Task entity; empty uses the one set as preferred in Home Assistant, or the only one there is
    ai_task_entity: str = ""
    # Keep a Local Calendar in Home Assistant in step with the items that have a date
    calendar_sync: bool = True
    # Which calendar; empty is calendar.mybrain (a Local Calendar named MyBrain)
    calendar_entity: str = ""
    categories: list[str] = field(default_factory=lambda: list(DEFAULT_CATEGORIES))
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
        categories = []
        for name in options.get("categories") or DEFAULT_CATEGORIES:
            name = str(name).strip()
            if name and name.lower() not in (c.lower() for c in categories):
                categories.append(name[:40])
        if "Other" not in categories:
            categories.append("Other")
        entity = str(options.get("ai_task_entity") or "").strip()
        if entity and not entity.startswith("ai_task."):
            raise ValueError(f"ai_task_entity must be an ai_task entity, like ai_task.openai: {entity!r}")
        calendar = str(options.get("calendar_entity") or "").strip()
        if calendar and not calendar.startswith("calendar."):
            raise ValueError(f"calendar_entity must be a calendar entity, like calendar.mybrain: {calendar!r}")
        return cls(
            listen_shares=bool(options.get("listen_shares", True)),
            fetch_pages=bool(options.get("fetch_pages", True)),
            ai_classify=bool(options.get("ai_classify", True)),
            ai_task_entity=entity,
            calendar_sync=bool(options.get("calendar_sync", True)),
            calendar_entity=calendar,
            categories=categories,
            log_level=str(options.get("log_level") or "info"),
        )
