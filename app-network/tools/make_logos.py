"""Write src/web/logos.js: brand logos for the device cards on the map (a FRITZ!Box, a MikroTik ..).

From homarr-labs/dashboard-icons (Apache License 2.0; the logos belong to their makers), at a fixed
commit so a run gives the same file. As data: URLs, so they show offline and inside an exported
image. Run: python3 -m tools.make_logos
"""

import base64
import json
import urllib.request
from pathlib import Path

COMMIT = "d4b500dc40a8e1043b1581cabb7208892cf35472"
URL = "https://raw.githubusercontent.com/homarr-labs/dashboard-icons/{commit}/svg/{name}.svg"
# The logo for each name the dashboard uses (see LOGO_RULES in src/web/map.js)
NAMES = [
    "fritzbox",
    "mikrotik",
    "unifi",
    "netgear",
    "tp-link",
    "synology",
    "qnap",
    "proxmox",
    "raspberry-pi",
    "home-assistant",
    "opnsense",
    "pfsense",
    "truenas",
    "asus",
    "linksys",
    "cisco",
    "openwrt",
    "apple",
    "sonos",
    "shelly",
    "esphome",
    "google-home",
]
OUT = Path(__file__).resolve().parent.parent / "src" / "web" / "logos.js"


def main() -> None:
    logos = {}
    for name in NAMES:
        with urllib.request.urlopen(URL.format(commit=COMMIT, name=name), timeout=30) as r:
            svg = r.read()
        logos[name] = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()
    OUT.write_text(
        "// Brand logos for the device cards on the map, written by tools/make_logos.py.\n"
        "// From homarr-labs/dashboard-icons (Apache License 2.0, see vendor/dashboard-icons-LICENSE.txt);\n"
        "// the logos belong to their makers.\n"
        f"const LOGOS = {json.dumps(logos, indent=0)};\n"
    )
    print(f"{len(logos)} logos, {OUT.stat().st_size // 1024} kB")


if __name__ == "__main__":
    main()
