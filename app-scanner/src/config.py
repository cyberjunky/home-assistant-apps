"""Configuration of the Scanner app."""

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

# App storage: options.json, the settings, the pages being scanned and the archive live here.
# The SCANNER_* environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("SCANNER_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("SCANNER_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("SCANNER_WEB_PORT", "8099"))
# Where documents can be copied to: Home Assistant's share and media folders, with the network
# storage (SMB, NFS) that Home Assistant mounts in them
DESTINATION_ROOTS = tuple(Path(p) for p in os.environ.get("SCANNER_ROOTS", "/share:/media").split(":") if p)
# SANE's configuration, written at start (see setup.py)
SANE_DIR = Path(os.environ.get("SCANNER_SANE_DIR", "/etc/sane.d"))
IPP_USB_CONF = Path(os.environ.get("SCANNER_IPP_USB_CONF", "/etc/ipp-usb/ipp-usb.conf"))
RUN_DIR = Path(os.environ.get("SCANNER_RUN_DIR", "/run/scanner"))
# Pages as the scanner gives them, before they are a JPEG or PNG: large, so on disk and not in /data's backups
WORK_DIR = Path(os.environ.get("SCANNER_WORK_DIR", "/tmp/scanner"))
# ipp-usb gives every driverless USB scanner a port on this machine, counting from here
IPP_USB_PORTS = range(60000, 60008)
# Languages the image has Tesseract data for
OCR_LANGUAGES = {
    "eng": "English",
    "nld": "Dutch",
    "deu": "German",
    "fra": "French",
    "spa": "Spanish",
    "ita": "Italian",
}


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


def escl_url(text: str) -> str:
    """A network scanner's address as sane-airscan wants it: http://host[:port]/eSCL."""
    text = text.strip()
    if not re.match(r"^https?://", text):
        text = "http://" + text
    scheme, rest = text.split("://", 1)
    if "/" not in rest.rstrip("/"):
        text = f"{scheme}://{rest.rstrip('/')}/eSCL"
    return text


@dataclass
class Config:
    """The app's options in Home Assistant. What you choose while scanning is in settings.py."""

    # SANE's test scanner: a scanner that isn't there, to try the app without one
    test_scanner: bool = False
    # Driverless USB (IPP over USB): newer printers with a scanner that have no SANE driver
    driverless_usb: bool = True
    # Scanners on the network that speak eSCL (AirScan): (name, url)
    network_scanners: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    log_level: str = "info"

    @classmethod
    def from_options_file(cls, path: Path = OPTIONS_FILE) -> "Config":
        """Read the options. Raises ValueError when they can't be used."""
        try:
            options = json.loads(path.read_text())
        except FileNotFoundError:
            options = {}
        except OSError as e:
            raise ValueError(f"Cannot read the options file {path}: {e}") from e
        except json.JSONDecodeError as e:
            raise ValueError(f"The options file {path} is not valid JSON: {e}") from e
        return cls.from_options(options)

    @classmethod
    def from_options(cls, options: dict) -> "Config":
        scanners = []
        for item in options.get("network_scanners") or []:
            if not isinstance(item, dict):
                continue
            name = re.sub(r'["\n\r=]', " ", str(item.get("name") or "")).strip()
            address = str(item.get("address") or "").strip()
            if not address or re.search(r"[\s\",]", address):
                raise ValueError(f"Network scanner {name or '?'}: give its address, such as 192.168.1.50")
            scanners.append((name or address, escl_url(address)))
        return cls(
            test_scanner=bool(options.get("test_scanner", False)),
            driverless_usb=bool(options.get("driverless_usb", True)),
            network_scanners=tuple(scanners),
            log_level=str(options.get("log_level") or "info"),
        )
