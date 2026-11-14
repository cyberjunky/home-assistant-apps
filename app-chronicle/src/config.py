"""Configuration for Chronicle: the app's own database, as the web UI reaches it."""

import os
import socket
from dataclasses import dataclass
from pathlib import Path

# The web UI's own files (the donate card state). The CHRONICLE_* environment variables
# other than CHRONICLE_LOG_LEVEL are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("CHRONICLE_DATA_DIR", "/data/ui"))
# The passwords init-postgres generated, one file per role
SECRETS_DIR = Path(os.environ.get("CHRONICLE_SECRETS_DIR", "/data/secrets"))
WEB_PORT = int(os.environ.get("CHRONICLE_PORT", "8099"))

# The database the Chronicle integration records into, and the role it records as
DATABASE = "homeassistant"
INTEGRATION_ROLE = "chronicle"
POSTGRES_PORT = 5432


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
    """How the web UI connects: as chronicle_ui over the local socket (peer authentication,
    no password). Development and the tests connect over TCP with the CHRONICLE_DB_* variables."""

    host: str = "/run/postgresql"
    port: int = POSTGRES_PORT
    username: str = "chronicle_ui"
    password: str = ""
    database: str = DATABASE
    log_level: str = "info"

    @classmethod
    def from_environment(cls) -> "Config":
        log_level = os.environ.get("CHRONICLE_LOG_LEVEL", "info").lower()
        return cls(
            host=os.environ.get("CHRONICLE_DB_HOST", cls.host),
            port=int(os.environ.get("CHRONICLE_DB_PORT", str(POSTGRES_PORT))),
            username=os.environ.get("CHRONICLE_DB_USER", cls.username),
            password=os.environ.get("CHRONICLE_DB_PASSWORD", ""),
            database=os.environ.get("CHRONICLE_DB_NAME", DATABASE),
            log_level=log_level if log_level in ("debug", "info", "warning", "error") else "info",
        )

    def integration(self) -> dict:
        """What the Chronicle integration connects with, without the password (Info page)."""
        return {
            "host": socket.gethostname(),
            "port": POSTGRES_PORT,
            "username": INTEGRATION_ROLE,
            "database": DATABASE,
        }


def integration_password() -> str | None:
    """The integration's password, for setting it up by hand; None when it can't be read."""
    try:
        return (SECRETS_DIR / f"{INTEGRATION_ROLE}.password").read_text().strip() or None
    except OSError:
        return None
