"""Write SANE's and ipp-usb's configuration from the app's options. Runs once at start, before the
services (python3 -m src.setup), and again when driverless USB scanners are found."""

import logging
import sys
from pathlib import Path

from .config import IPP_USB_CONF, RUN_DIR, SANE_DIR, Config

logger = logging.getLogger(__name__)

IPP_USB = """# Written by the Scanner app at every start
[network]
  http-min-port = 60000
  http-max-port = 60999
  dns-sd = disable
  interface = loopback
  ipv6 = disable
[logging]
  device-log = error
  main-log = info
  console-log = info
  max-file-size = 256K
  max-backup-files = 1
  console-color = disable
"""


def airscan_conf(scanners: list[tuple[str, str]]) -> str:
    """sane-airscan's configuration: the given scanners, and no searching the network.

    Searching needs multicast, which doesn't reach an app's container; it would only make every
    question to SANE wait for its end.
    """
    lines = ["# Written by the Scanner app", "[devices]"]
    seen: set[str] = set()
    for name, url in scanners:
        name = name.replace('"', "'").strip() or url
        while name in seen:
            name += " (2)"
        seen.add(name)
        lines.append(f'"{name}" = {url}, eSCL')
    lines += ["", "[options]", "discovery = disable", ""]
    return "\n".join(lines)


def write_airscan(scanners: list[tuple[str, str]], sane_dir: Path = SANE_DIR) -> bool:
    """Returns whether the file changed."""
    path = sane_dir / "airscan.conf"
    text = airscan_conf(scanners)
    try:
        if path.is_file() and path.read_text() == text:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    except OSError as e:
        logger.error(f"Cannot write {path}: {e}")
        return False
    return True


def write_all(config: Config, sane_dir: Path = SANE_DIR) -> None:
    # SANE's test scanner (development only) is a driver like any other, switched on by a file naming it
    test = sane_dir / "dll.d" / "test"
    test.parent.mkdir(parents=True, exist_ok=True)
    if config.test_scanner:
        test.write_text("test\n")
    else:
        test.unlink(missing_ok=True)
    write_airscan(list(config.network_scanners), sane_dir)
    IPP_USB_CONF.parent.mkdir(parents=True, exist_ok=True)
    IPP_USB_CONF.write_text(IPP_USB)
    RUN_DIR.mkdir(parents=True, exist_ok=True)
    flag = RUN_DIR / "driverless-usb"
    if config.driverless_usb:
        flag.write_text("1\n")
    else:
        flag.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        write_all(Config.from_options_file())
    except (ValueError, OSError) as e:
        print(f"[ERROR] {e} - fix the app configuration and restart", file=sys.stderr)
        sys.exit(1)
