"""Write src/oui.json: the makers of network cards (from the IEEE OUI registry) that say what a device is.

Only makers that help guess a kind are kept (the whole registry is 3 MB). Run with the path of
oui.csv from https://standards-oui.ieee.org/oui/oui.csv: python3 -m tools.make_oui /path/to/oui.csv
"""

import csv
import json
import re
import sys
from pathlib import Path

# Makers worth knowing: the name as the registry has it, matched case-insensitively
MAKERS = re.compile(
    r"espressif|raspberry pi|ubiquiti|synology|qnap|sonos|apple|google|amazon|samsung|xiaomi|oneplus|"
    r"oppo|motorola|nokia|fairphone|signify|philips lighting|ikea|tuya|shelly|allterco|tp-link|netgear|mikrotik|"
    r"avm (gmbh|audiovisuelles)|routerboard|zyxel|asustek|linksys|eero|fortinet|sophos|"
    r"watchguard|palo alto|reolink|hikvision|dahua|axis communications|hanwha|uniview|amcrest|"
    r"nintendo|sony interactive|microsoft|valve|roku|lg electronics|vizio|hisense|tcl|bose|denon|sound united|"
    r"yamaha|bluesound|seiko epson|brother industries|canon|kyocera|lexmark|xerox|ricoh|fujifilm|"
    r"roborock|irobot|ecovacs|dreame|husqvarna|positec|bitmain|canaan|tesla|goodwe|solaredge|enphase|growatt|"
    r"fronius|sma solar|homewizard|space exploration|nabu casa|hardkernel|proxmox|nest labs|ecobee|tado|netatmo|miele|bsh hausger|"
    r"wiz connected|lifx|nanoleaf",
    re.I,
)


def short(name: str) -> str:
    """Without the legal form: "Espressif Inc." -> "Espressif"."""
    name = re.sub(
        r"[,.]?\s+(inc|ltd|llc|gmbh|co|corp|corporation|limited|ag|sa|s\.a|bv|b\.v|ab|oy|as|plc|srl|kg)\b\.?.*$",
        "",
        name.strip(),
        flags=re.I,
    )
    return re.sub(r"\s+", " ", name).strip(" ,.")


def main(source: str) -> None:
    out = {}
    with open(source, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("Registry") == "MA-L" and MAKERS.search(row["Organization Name"]):
                out[row["Assignment"].upper()] = short(row["Organization Name"])
    path = Path(__file__).resolve().parent.parent / "src" / "oui.json"
    path.write_text(json.dumps(dict(sorted(out.items())), separators=(",", ":")))
    print(f"{len(out)} prefixes, {path.stat().st_size // 1024} KB -> {path}")


if __name__ == "__main__":
    main(sys.argv[1])
