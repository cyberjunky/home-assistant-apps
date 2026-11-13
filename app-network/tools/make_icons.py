"""Write src/web/mdi.js: the Material Design Icons the dashboard uses, the same icons Home Assistant shows.

Only the icons below are bundled (the whole set is 3 MB). Run with the path of mdi.js from the
@mdi/js package: python3 -m tools.make_icons /path/to/package/mdi.js
"""

import json
import re
import sys
from pathlib import Path

from src.kinds import KINDS

# Icons to pick from for a device, beyond the one of each kind
PICKER = [
    "web",
    "cloud",
    "cloud-outline",
    "earth",
    "router",
    "router-network",
    "router-wireless",
    "router-wireless-settings",
    "wall-fire",
    "shield-lock",
    "switch",
    "lan",
    "lan-connect",
    "lan-pending",
    "ethernet",
    "ethernet-cable",
    "access-point",
    "access-point-network",
    "wifi",
    "wifi-strength-2",
    "antenna",
    "server",
    "server-network",
    "server-security",
    "nas",
    "harddisk",
    "database",
    "docker",
    "kubernetes",
    "monitor-shimmer",
    "raspberry-pi",
    "chip",
    "memory",
    "desktop-tower",
    "desktop-tower-monitor",
    "desktop-classic",
    "monitor",
    "laptop",
    "laptop-account",
    "apple-ios",
    "apple",
    "android",
    "microsoft-windows",
    "linux",
    "cellphone",
    "cellphone-wireless",
    "tablet",
    "tablet-cellphone",
    "watch",
    "watch-variant",
    "headphones",
    "printer",
    "printer-3d",
    "printer-wireless",
    "fax",
    "scanner",
    "television",
    "television-classic",
    "cast",
    "cast-audio",
    "google-chrome",
    "set-top-box",
    "projector",
    "speaker",
    "speaker-wireless",
    "soundbar",
    "amplifier",
    "radio",
    "controller",
    "nintendo-switch",
    "microsoft-xbox",
    "sony-playstation",
    "cctv",
    "webcam",
    "camera",
    "doorbell",
    "doorbell-video",
    "hub",
    "zigbee",
    "z-wave",
    "home-assistant",
    "home-automation",
    "bridge",
    "power-socket-eu",
    "power-plug",
    "toggle-switch",
    "lightbulb",
    "lightbulb-group",
    "led-strip-variant",
    "ceiling-light",
    "lamp",
    "thermostat",
    "radiator",
    "heat-pump",
    "air-conditioner",
    "fan",
    "solar-power",
    "solar-panel",
    "battery-charging",
    "lightning-bolt",
    "meter-electric",
    "gauge",
    "car-electric",
    "ev-station",
    "car",
    "garage",
    "garage-open-variant",
    "gate",
    "washing-machine",
    "tumble-dryer",
    "dishwasher",
    "fridge",
    "stove",
    "robot-vacuum",
    "coffee-maker",
    "pickaxe",
    "bitcoin",
    "devices",
    "help-network-outline",
    "account",
    "account-child",
    "vpn",
    "shield-check",
    "key",
    "lock-smart",
    "smoke-detector",
    "water",
    "motion-sensor",
    "leak",
    "window-open-variant",
    "blinds",
    "sprinkler",
    "pool",
    "weather-cloudy",
]


def camel(name: str) -> str:
    return "mdi" + "".join(part.capitalize() for part in name.split("-"))


def main(source: str) -> None:
    text = Path(source).read_text()
    paths = dict(re.findall(r'export var (mdi\w+) = "([^"]+)";', text))
    wanted = []
    for _label, mdi, _infra in KINDS.values():
        wanted.append(mdi.removeprefix("mdi:"))
    wanted += [n for n in PICKER if n not in wanted]
    icons, missing = {}, []
    for name in wanted:
        path = paths.get(camel(name))
        if path:
            icons[name] = path
        else:
            missing.append(name)
    if missing:
        print("Not in this @mdi/js:", ", ".join(missing))
    out = Path(__file__).resolve().parent.parent / "src" / "web" / "mdi.js"
    out.write_text(
        "// Material Design Icons (https://pictogrammers.com, Pictogrammers Free License: the icons under\n"
        "// Apache 2.0), the icons Home Assistant uses. Made by tools/make_icons.py; don't edit by hand.\n"
        f"const MDI = {json.dumps(icons, indent=0, separators=(',', ':'))};\n"
    )
    print(f"{len(icons)} icons, {out.stat().st_size // 1024} KB -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])
