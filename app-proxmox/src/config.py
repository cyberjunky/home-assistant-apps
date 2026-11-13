"""Configuration management for Proxmox."""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

# App storage: options.json and the map layout live here.
# The PROXMOX_* environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("PROXMOX_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("PROXMOX_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("PROXMOX_PORT", "8099"))
DEFAULT_PORT = 8006
TOKEN_ID = re.compile(r"^[^@\s!]+@[^@\s!]+![A-Za-z0-9][A-Za-z0-9_.-]*$")


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


def normalize_host(text: str) -> str:
    """An address of a node's API: https and port 8006 unless given, without a path.

    Accepts "pve1", "192.168.1.10", "pve1.lan:8443" and "https://pve1:8006/api2/json".
    """
    text = text.strip().rstrip("/")
    if not text:
        raise ValueError("an empty address")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"{text!r} is not an address of a Proxmox node")
    host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
    return f"{parts.scheme}://{host}:{parts.port or DEFAULT_PORT}"


@dataclass
class Config:
    """Application configuration, read from the app options."""

    # Nodes of one cluster to reach the API through; the next is tried when one is down
    hosts: list[str] = field(default_factory=list)
    token_id: str = ""
    token_secret: str = ""
    verify_ssl: bool = False
    # How often the cluster's state is read; configuration (network, firewall) every few minutes
    poll_seconds: int = 10
    # Leave out everything that can change the cluster
    read_only: bool = True
    log_level: str = "info"

    @property
    def configured(self) -> bool:
        return bool(self.hosts and self.token_id and self.token_secret)

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
        hosts: list[str] = []
        for item in options.get("hosts") or []:
            try:
                host = normalize_host(str(item))
            except ValueError as e:
                raise ValueError(f"hosts: {e}") from e
            if host not in hosts:
                hosts.append(host)
        token_id = str(options.get("token_id") or "").strip()
        if token_id and not TOKEN_ID.match(token_id):
            raise ValueError(
                f"token_id {token_id!r} must look like user@realm!tokenname, for example homeassistant@pve!dashboard"
            )
        return cls(
            hosts=hosts,
            token_id=token_id,
            token_secret=str(options.get("token_secret") or "").strip(),
            verify_ssl=bool(options.get("verify_ssl", False)),
            poll_seconds=min(300, max(5, int(options.get("poll_seconds") or 10))),
            read_only=bool(options.get("read_only", True)),
            log_level=str(options.get("log_level") or "info"),
        )
