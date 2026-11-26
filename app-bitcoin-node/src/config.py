"""Configuration management for Bitcoin Node."""

import base64
import binascii
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

# App storage: options.json, the database and the channel backups live here.
# The BITCOINNODE_* environment variables are for development only; the app itself never sets them.
DATA_DIR = Path(os.environ.get("BITCOINNODE_DATA_DIR", "/data"))
OPTIONS_FILE = Path(os.environ.get("BITCOINNODE_OPTIONS", str(DATA_DIR / "options.json")))
WEB_PORT = int(os.environ.get("BITCOINNODE_PORT", "8099"))


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


def host_of(url: str) -> str:
    """An address without credentials or path, to show."""
    parts = urlsplit(url)
    return parts.hostname + (f":{parts.port}" if parts.port else "") if parts.hostname else url


def clean_url(value, what: str, allow_path: bool = False) -> str:
    url = str(value or "").strip().rstrip("/")
    if url and allow_path and re.match(r"^https?://[^/\s]+(/\S*)?$", url):
        return url
    if url and not re.match(r"^https?://[^/\s]+$", url):
        raise ValueError(f"{what}: url {url!r} must look like http://192.168.1.50:8332, without a path")
    return url


def macaroon_hex(value) -> str:
    """A macaroon as LND's REST API wants it: hex. Accepts hex or base64, with spaces or line breaks."""
    text = re.sub(r"\s+", "", str(value or ""))
    if not text:
        return ""
    if re.fullmatch(r"[0-9a-fA-F]+", text) and len(text) % 2 == 0:
        return text.upper()
    try:
        return (
            base64.b64decode(text.replace("-", "+").replace("_", "/") + "=" * (-len(text) % 4), validate=True)
            .hex()
            .upper()
        )
    except (binascii.Error, ValueError) as e:
        raise ValueError("lightning: the macaroon must be hex (xxd -ps -u -c 1000 readonly.macaroon) or base64") from e


def certificate_pem(value) -> str:
    """LND's tls.cert as PEM. An option field has no line breaks, so the lines are put back."""
    text = str(value or "").strip()
    if not text:
        return ""
    body = re.sub(r"-----(BEGIN|END) CERTIFICATE-----", "", text)
    body = re.sub(r"\s+", "", body)
    try:
        base64.b64decode(body, validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError("lightning: tls_cert must be the content of LND's tls.cert") from e
    lines = "\n".join(body[i : i + 64] for i in range(0, len(body), 64))
    return f"-----BEGIN CERTIFICATE-----\n{lines}\n-----END CERTIFICATE-----\n"


def clean_endpoint(value, what: str) -> str:
    """host:port of Tor's control port."""
    text = str(value or "").strip()
    if text and not re.fullmatch(r"[^\s:/]+:\d{1,5}", text):
        raise ValueError(f"{what}: {text!r} must be host:port, for example 192.168.1.50:9051")
    return text


@dataclass
class Config:
    """Application configuration, read from the app options."""

    # Bitcoin Core's JSON-RPC
    bitcoin_url: str = ""
    bitcoin_user: str = ""
    bitcoin_password: str = ""

    # LND's REST API. Empty: no Lightning pages
    lnd_url: str = ""
    lnd_macaroon: str = ""
    # LND's own certificate; without it an https address is used without checking who answers
    lnd_tls_cert: str = ""
    # This version only reads. The option is here so a later version that can pay stays off until it is turned off
    lnd_read_only: bool = True

    # Tor's control port. Empty: the Tor page shows the onion addresses only
    tor_control: str = ""
    tor_password: str = ""

    # ckpool's logs folder, served read-only over HTTP. Empty: no Pool page
    pool_url: str = ""

    poll_seconds: int = 15
    history_days: int = 90

    # MQTT publishing with Home Assistant discovery
    mqtt_enabled: bool = True
    mqtt_host: str = ""
    mqtt_port: int = 1883
    mqtt_user: str = ""
    mqtt_password: str = ""
    mqtt_base_topic: str = "bitcoin_node"
    mqtt_discovery_prefix: str = "homeassistant"

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
        bitcoin = options.get("bitcoin") or {}
        lightning = options.get("lightning") or {}
        tor = options.get("tor") or {}
        pool = options.get("pool") or {}
        mqtt = options.get("mqtt") or {}
        config = cls(
            bitcoin_url=clean_url(bitcoin.get("url"), "bitcoin"),
            bitcoin_user=str(bitcoin.get("username") or ""),
            bitcoin_password=str(bitcoin.get("password") or ""),
            lnd_url=clean_url(lightning.get("url"), "lightning"),
            lnd_macaroon=macaroon_hex(lightning.get("macaroon")),
            lnd_tls_cert=certificate_pem(lightning.get("tls_cert")),
            lnd_read_only=bool(lightning.get("read_only", True)),
            tor_control=clean_endpoint(tor.get("control"), "tor"),
            tor_password=str(tor.get("password") or ""),
            pool_url=clean_url(pool.get("url"), "pool", allow_path=True),
            poll_seconds=min(600, max(5, int(options.get("poll_seconds") or 15))),
            history_days=min(3650, max(1, int(options.get("history_days") or 90))),
            mqtt_enabled=bool(mqtt.get("enabled", True)),
            mqtt_host=str(mqtt.get("host") or ""),
            mqtt_port=int(mqtt.get("port") or 1883),
            mqtt_user=str(mqtt.get("user") or ""),
            mqtt_password=str(mqtt.get("password") or ""),
            mqtt_base_topic=str(mqtt.get("base_topic") or "bitcoin_node"),
            mqtt_discovery_prefix=str(mqtt.get("discovery_prefix") or "homeassistant"),
            log_level=str(options.get("log_level") or "info"),
        )
        if config.bitcoin_url and not (config.bitcoin_user and config.bitcoin_password):
            raise ValueError("bitcoin: enter the username and password of the RPC user")
        if config.lnd_url and not config.lnd_macaroon:
            raise ValueError("lightning: enter the read-only macaroon")
        return config
