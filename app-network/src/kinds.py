"""What a device is (phone, printer, access point...), guessed from what Home Assistant knows of it,
and MAC address helpers.

The guess is only a start: a kind picked in the app always wins (see inventory.py).
"""

import re

# key: (label, Material Design icon for Home Assistant, is part of the network itself)
KINDS: dict[str, tuple[str, str, bool]] = {
    "internet": ("Internet", "mdi:web", True),
    "cloud": ("Cloud service", "mdi:cloud-outline", True),
    "modem": ("Modem", "mdi:router-network", True),
    "router": ("Router", "mdi:router", True),
    "firewall": ("Firewall", "mdi:wall-fire", True),
    "switch": ("Switch", "mdi:switch", True),
    "ap": ("Access point", "mdi:access-point", True),
    "server": ("Server", "mdi:server", False),
    "nas": ("NAS", "mdi:nas", False),
    "vm": ("Virtual machine", "mdi:monitor-shimmer", False),
    "sbc": ("Single-board computer", "mdi:raspberry-pi", False),
    "desktop": ("Desktop", "mdi:desktop-tower-monitor", False),
    "laptop": ("Laptop", "mdi:laptop", False),
    "phone": ("Phone", "mdi:cellphone", False),
    "tablet": ("Tablet", "mdi:tablet", False),
    "watch": ("Watch", "mdi:watch", False),
    "printer": ("Printer", "mdi:printer", False),
    "tv": ("TV / media player", "mdi:television", False),
    "speaker": ("Speaker", "mdi:speaker", False),
    "console": ("Game console", "mdi:controller", False),
    "camera": ("Camera", "mdi:cctv", False),
    "doorbell": ("Doorbell", "mdi:doorbell-video", False),
    "hub": ("Smart home hub", "mdi:hub", False),
    "iot": ("IoT / ESP", "mdi:chip", False),
    "plug": ("Smart plug", "mdi:power-socket-eu", False),
    "light": ("Light", "mdi:lightbulb", False),
    "thermostat": ("Thermostat", "mdi:thermostat", False),
    "energy": ("Energy / inverter", "mdi:solar-power", False),
    "car": ("Car / charger", "mdi:car-electric", False),
    "appliance": ("Appliance", "mdi:washing-machine", False),
    "miner": ("Miner", "mdi:pickaxe", False),
    "generic": ("Device", "mdi:devices", False),
}

# Tried in order: the first rule whose pattern matches the device's names, maker and model wins.
# Patterns match whole words where that matters, so "tv" doesn't match "ptvalue".
_RULES: list[tuple[str, str]] = [
    ("internet", r"\binternet\b|\bwan\b|ping 1\.1\.1\.1|ping 8\.8\.8\.8"),
    ("watch", r"apple ?watch|galaxy ?watch|\bwatch\b|fenix|forerunner|garmin|fitbit|pixel ?watch"),
    ("tablet", r"\bipad\b|\btab\b|galaxy ?tab|tablet|kindle|fire ?hd|\bfire ?tablet"),
    (
        "phone",
        r"iphone|android|pixel|galaxy|oneplus|xiaomi|redmi|huawei|\bphone\b|\bmobile\b|\bgsm\b|smartphone|oppo|motorola|nokia|fairphone",
    ),
    ("laptop", r"\blt-|macbook|laptop|notebook|thinkpad|\bxps\b|latitude|zenbook|surface"),
    (
        "printer",
        r"printer|\bprint\b|laserjet|officejet|deskjet|envy|epson|brother|canon|kyocera|\bxerox\b|\bhp[a-z0-9]{6}\b|\bnpi[a-z0-9]+",
    ),
    ("doorbell", r"doorbell|deurbel|\bring\b|nest ?hello|reolink ?doorbell"),
    ("camera", r"camera|\bcam\b|ipcam|\bnvr\b|reolink|hikvision|dahua|axis|wyze|eufy|tapo ?c|foscam|annke"),
    ("console", r"playstation|\bps[345]\b|xbox|nintendo|switch ?console|steam ?deck"),
    (
        "tv",
        r"\btv\b|television|chromecast|apple ?tv|shield|roku|fire ?tv|firetv|kodi|bravia|webos|tizen|\blg-?tv|philips ?tv|ambilight|android ?tv|google ?tv|media ?player",
    ),
    (
        "speaker",
        r"sonos|homepod|echo|alexa|nest ?(mini|audio|hub)|google ?home|speaker|soundbar|denon|marantz|yamaha|bose|bluesound",
    ),
    (
        "ap",
        r"\buap\b|\bu6\b|\bu7\b|access ?point|unifi ?ap|\bap\b|\bap-|mesh|deco|eero|orbi|wifi ?extender|repeater|\bnanohd\b|\bflexhd\b",
    ),
    ("switch", r"\busw\b|\bswitch\b|unifi ?switch|gs1\d\d|gs3\d\d|\bcrs\d|\bsg\d{3}|catalyst|procurve"),
    ("firewall", r"firewall|pfsense|opnsense|fortigate|watchguard|sophos|sonicwall|palo ?alto"),
    ("modem", r"modem|\bont\b|genexis|\bdsl\b|cable ?modem|connectbox|ziggo"),
    (
        "router",
        r"router|gateway|\budm\b|\buxg\b|\busg\b|dream ?machine|fritz|edgerouter|mikrotik|\brb\d|asus ?rt|tp-?link ?archer|\bgw\b",
    ),
    ("nas", r"\bnas\b|synology|diskstation|qnap|truenas|unraid|\bds\d{3}|\brs\d{3}"),
    (
        "server",
        r"server|proxmox|\bpve\d*\b|esxi|\bhost\d*\b|\bsrv\b|nuc|homeassistant|home-assistant|\bhass\b|\bhaos\b|docker|kubernetes|\bk8s\b",
    ),
    ("sbc", r"raspberry|\brpi\b|\bpi[345]?\b|odroid|orange ?pi|beaglebone|jetson|\bpikvm\b"),
    ("miner", r"bitaxe|antminer|whatsminer|nerdqaxe|\bminer\b|avalon"),
    (
        "energy",
        r"inverter|goodwe|solaredge|enphase|growatt|fronius|sma\b|huawei ?sun|p1 ?meter|homewizard|\bdsmr\b|smart ?meter|youless|toon|battery",
    ),
    ("car", r"tesla|wallbox|charger|laadpaal|easee|zaptec|alfen|\bev\b|\bcar\b|polestar|ioniq|id\.\d"),
    (
        "thermostat",
        r"thermostat|tado|nest ?thermostat|ecobee|honeywell|netatmo|\bboiler\b|\bcv\b|\bplugwise\b|\bdaikin\b|mitsubishi|airco|\bheat ?pump\b",
    ),
    (
        "hub",
        r"\bhub\b|hue ?bridge|\bbridge\b|zigbee|z-?wave|homey|smartthings|deconz|conbee|sonoff ?zb|slzb|\bslzb|\bzbbridge|gateway ?zigbee|tradfri|ikea|dirigera|matter",
    ),
    (
        "plug",
        r"\bplug\b|\bsocket\b|shelly ?plug|\bkasa\b|\bhs1\d\d\b|stekker|tasmota ?plug|nous|athom ?plug|energy ?socket",
    ),
    ("light", r"\blight\b|\blamp\b|\bbulb\b|wled|govee|nanoleaf|\blifx\b|\byeelight\b|\bled\b|lamp"),
    (
        "appliance",
        r"washer|wasmachine|dryer|droger|dishwasher|vaatwasser|fridge|koelkast|oven|miele|bosch|siemens|\baeg\b|whirlpool|vacuum|roborock|roomba|\bdreame\b|\bneato\b",
    ),
    (
        "iot",
        r"\besp\b|esp32|esp8266|esphome|espressif|shelly|tasmota|sonoff|tuya|\bwemos\b|nodemcu|\biot\b|athom|meross|broadlink",
    ),
    ("desktop", r"desktop|\bpc\b|imac|mac ?mini|mac ?studio|workstation|\bdesk\b|gaming|tower"),
    ("vm", r"\bvm\b|virtual|\bqemu\b|\blxc\b|\bct\d+"),
]
_COMPILED = [(kind, re.compile(pattern, re.I)) for kind, pattern in _RULES]

# Makers whose network devices are nearly always one kind
_MAKERS: dict[str, str] = {
    "apple": "phone",
    "espressif": "iot",
    "raspberry pi": "sbc",
    "synology": "nas",
    "qnap": "nas",
    "sonos": "speaker",
    "ubiquiti": "ap",
    "hikvision": "camera",
    "reolink": "camera",
    "shelly": "iot",
    "signify": "hub",
    "philips lighting": "hub",
    "tuya": "iot",
    "nintendo": "console",
    "sony interactive": "console",
    "roku": "tv",
    "amazon": "speaker",
    "google": "speaker",
    "brother": "printer",
    "seiko epson": "printer",
    "tesla": "car",
    "goodwe": "energy",
    "proxmox": "vm",
}


def guess_kind(*texts: str | None, manufacturer: str | None = None) -> str:
    """The most likely kind for a device, from its names and model first, then from its maker."""
    text = " ".join(t for t in texts if t).replace("_", " ")
    for kind, pattern in _COMPILED:
        if pattern.search(text):
            return kind
    maker = (manufacturer or "").lower()
    for name, kind in _MAKERS.items():
        if name in maker:
            return kind
    if maker:
        for kind, pattern in _COMPILED:
            if pattern.search(maker):
                return kind
    return "generic"


_MAC = re.compile(r"^[0-9a-f]{12}$")


def normalize_mac(value: str | None) -> str:
    """A MAC address as aa:bb:cc:dd:ee:ff, or "" when it isn't one."""
    digits = re.sub(r"[^0-9a-fA-F]", "", str(value or "")).lower()
    if not _MAC.match(digits) or digits in ("000000000000", "ffffffffffff"):
        return ""
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def is_random_mac(mac: str) -> bool:
    """Locally administered: phones and laptops use one per Wi-Fi network ("private address"),
    so such a device shows up again under another address on another network, or after a reset."""
    mac = normalize_mac(mac)
    return bool(mac) and bool(int(mac[1], 16) & 0x2)
