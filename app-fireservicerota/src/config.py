"""Configuration for FireServiceRota, read from the app options."""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path

logger = logging.getLogger(__name__)

# /data in the app container; FSR_DATA_DIR for development (see DOCS.md)
DATA_DIR = Path(os.environ.get("FSR_DATA_DIR", "/data"))
OPTIONS_FILE = DATA_DIR / "options.json"

_QUIET_HOURS_RE = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$")


def parse_quiet_hours(value: str) -> tuple[time, time] | None:
    """Parse "23:00-07:00" into (start, end) local times; None when empty or invalid."""
    if not value or not value.strip():
        return None
    match = _QUIET_HOURS_RE.match(value)
    try:
        if match:
            return time(int(match.group(1)), int(match.group(2))), time(int(match.group(3)), int(match.group(4)))
    except ValueError:
        pass
    logger.warning(f"Ignoring invalid quiet_hours {value!r} (expected e.g. 23:00-07:00)")
    return None


def in_quiet_hours(window: tuple[time, time] | None, now: datetime | None = None) -> bool:
    """Whether the local time is inside the window; a window may cross midnight."""
    if window is None:
        return False
    start, end = window
    current = (now or datetime.now()).time()
    if start <= end:
        return start <= current < end
    return current >= start or current < end


@dataclass
class Config:
    """The app options."""

    # Entity overrides; empty means: find them in Home Assistant's entity registry
    incidents_entity: str = ""
    duty_entity: str = ""
    response_entity: str = ""
    # How far back to import Home Assistant's recorder history at start
    history_days: int = 30
    # Fire fireservicerota_app_incident on the Home Assistant event bus
    ha_events: bool = True
    announce_tts_entity: str = ""
    announce_media_players: list[str] = field(default_factory=list)
    announce_language: str = "nl"
    # No announcements, browser sounds, speech or notifications between these local times
    announce_quiet_hours: str = ""
    log_level: str = "info"

    @property
    def announce_enabled(self) -> bool:
        return bool(self.announce_tts_entity and self.announce_media_players)

    def quiet_now(self) -> bool:
        """Whether announce.quiet_hours covers the current local time."""
        return in_quiet_hours(parse_quiet_hours(self.announce_quiet_hours))

    @classmethod
    def from_options_file(cls, path: Path = OPTIONS_FILE) -> "Config":
        """Read the options; a missing file (development) gives the defaults."""
        if not Path(path).exists():
            logger.warning(f"No options file at {path}, using the defaults")
            return cls()
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise ValueError(f"Cannot read app options from {path}: {e}") from e
        if not isinstance(data, dict):
            raise ValueError(f"Invalid app options in {path}: expected a JSON object")
        announce = data.get("announce") or {}
        return cls(
            incidents_entity=(data.get("incidents_entity") or "").strip(),
            duty_entity=(data.get("duty_entity") or "").strip(),
            response_entity=(data.get("response_entity") or "").strip(),
            history_days=int(data.get("history_days", 30)),
            ha_events=bool(data.get("ha_events", True)),
            announce_tts_entity=(announce.get("tts_entity") or "").strip(),
            announce_media_players=[p.strip() for p in (announce.get("media_players") or "").split(",") if p.strip()],
            announce_language=(announce.get("language") or "").strip(),
            announce_quiet_hours=announce.get("quiet_hours") or "",
            log_level=data.get("log_level", "info"),
        )
