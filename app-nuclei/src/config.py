"""Configuration for the Nuclei Scanner app, read from the app options."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

# App storage: options.json, the database and the nuclei templates live here.
# The NUCLEI_APP_* environment variables are for development only; the app never sets them.
DATA_DIR = Path(os.environ.get("NUCLEI_APP_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("NUCLEI_APP_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("NUCLEI_APP_PORT", "8099"))


def nuclei_env() -> dict[str, str]:
    """The environment for nuclei. It keeps its config (including where the templates are) and its cache
    under XDG dirs; they must live in /data, because nothing else in the container survives a restart, and
    without that config -as cannot find the templates."""
    return {
        **os.environ,
        "XDG_CONFIG_HOME": str(DATA_DIR / "nuclei-config"),
        "XDG_CACHE_HOME": str(DATA_DIR / "nuclei-cache"),
    }


SEVERITIES = ("info", "low", "medium", "high", "critical")


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

    max_concurrent_hosts: int = 25
    requests_per_second: int = 150
    min_severity: str = "low"
    nuclei_tags: str = ""
    nuclei_exclude_tags: str = "dos,fuzzing,intrusive"
    default_credentials: bool = False
    retention_days: int = 365
    # How often known devices are checked for being online, in minutes; 0 turns the check off
    presence_interval_minutes: int = 5
    update_templates: bool = True
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
        severity = str(options.get("min_severity") or "low").lower()
        if severity not in SEVERITIES:
            severity = "low"
        return cls(
            max_concurrent_hosts=max(1, int(options.get("max_concurrent_hosts") or 25)),
            requests_per_second=max(1, int(options.get("requests_per_second") or 150)),
            min_severity=severity,
            nuclei_tags=str(options.get("nuclei_tags") or "").strip(),
            nuclei_exclude_tags=str(options.get("nuclei_exclude_tags") or "").strip(),
            default_credentials=bool(options.get("default_credentials", False)),
            retention_days=max(1, int(options.get("retention_days") or 365)),
            presence_interval_minutes=max(0, min(1440, int(options.get("presence_interval_minutes", 5) or 0))),
            update_templates=bool(options.get("update_templates", True)),
            log_level=str(options.get("log_level") or "info").lower(),
        )

    def public(self) -> dict:
        """The options to show on the Settings page; nothing secret to hide here."""
        return {
            "max_concurrent_hosts": self.max_concurrent_hosts,
            "requests_per_second": self.requests_per_second,
            "min_severity": self.min_severity,
            "nuclei_tags": self.nuclei_tags,
            "nuclei_exclude_tags": self.nuclei_exclude_tags,
            "default_credentials": self.default_credentials,
            "retention_days": self.retention_days,
            "presence_interval_minutes": self.presence_interval_minutes,
            "update_templates": self.update_templates,
            "log_level": self.log_level,
        }
