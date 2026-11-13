"""Configuration and paths for the Proxmox Backup Server app."""

import json
import os
from pathlib import Path

# The PBSAPP_* environment variables are for development and tests only; the app never sets them.
DATA_DIR = Path(os.environ.get("PBSAPP_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("PBSAPP_OPTIONS", str(DATA_DIR / "options.json")))
# Where PBS writes the mount units of the disks it set up (kept in /data/pbs/systemd)
UNIT_DIR = Path(os.environ.get("PBSAPP_UNIT_DIR", "/etc/systemd/system"))
DATASTORE_CFG = Path(os.environ.get("PBSAPP_DATASTORE_CFG", "/etc/proxmox-backup/datastore.cfg"))
MOUNTINFO = Path(os.environ.get("PBSAPP_MOUNTINFO", "/proc/self/mountinfo"))


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


def log_level(path: Path = OPTIONS_FILE) -> str:
    try:
        return str(json.loads(path.read_text()).get("log_level") or "info")
    except (OSError, ValueError):
        return "info"
