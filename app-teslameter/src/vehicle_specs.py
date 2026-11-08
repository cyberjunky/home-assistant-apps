"""Readable names, paint colours and a picture for a car, from Tesla's vehicle_config codes.

Tesla reports the model, trim, paint and wheels as short codes ("model3", "50",
"MidnightSilver", "Pinwheel18"). This turns them into readable text, a real
paint colour (hex, for a swatch and for tinting the dashboard car), and a link
to Tesla's own configurator image when the paint can be mapped to its code.
Unknown codes are formatted as best as possible instead of dropped.
"""

import hashlib
import re

MODELS = {
    "model3": "Model 3",
    "modely": "Model Y",
    "models": "Model S",
    "models2": "Model S",
    "lychee": "Model S",
    "modelx": "Model X",
    "tamarind": "Model X",
    "cybertruck": "Cybertruck",
    "semi": "Semi",
    "roadster": "Roadster",
}

# Trim badge -> readable name, per model family. Falls back to the raw badge, uppercased.
TRIMS = {
    "model3": {
        "50": "Standard Range",
        "62": "Mid Range",
        "74": "Long Range RWD",
        "74d": "Long Range AWD",
        "p74d": "Performance",
    },
    "modely": {
        "50": "Standard Range",
        "62": "Standard Range",
        "74": "Long Range RWD",
        "74d": "Long Range AWD",
        "p74d": "Performance",
    },
    "models": {
        "60": "60",
        "75": "75",
        "75d": "75D",
        "90d": "90D",
        "100d": "Long Range",
        "p100d": "Plaid",
        "pawd": "Plaid",
    },
    "modelx": {
        "75d": "75D",
        "90d": "90D",
        "100d": "Long Range",
        "p100d": "Plaid",
        "pawd": "Plaid",
    },
}

# exterior_color -> (readable name, hex, configurator paint code). Hex is approximate.
COLORS = {
    "PearlWhite": ("Pearl White Multi-Coat", "#edeef0", "PPSW"),
    "PearlWhiteMultiCoat": ("Pearl White Multi-Coat", "#edeef0", "PPSW"),
    "White": ("White", "#edeef0", "PBCW"),
    "SolidBlack": ("Solid Black", "#1a1a1c", "PBSB"),
    "ObsidianBlack": ("Obsidian Black", "#17181a", "PMBL"),
    "Black": ("Black", "#1a1a1c", "PBSB"),
    "MidnightSilver": ("Midnight Silver Metallic", "#45484c", "PMNG"),
    "MidnightSilverMetallic": ("Midnight Silver Metallic", "#45484c", "PMNG"),
    "SteelGrey": ("Steel Grey", "#53565a", "PMNG"),
    "DeepBlue": ("Deep Blue Metallic", "#223a5e", "PPSB"),
    "DeepBlueMetallic": ("Deep Blue Metallic", "#223a5e", "PPSB"),
    "MetallicBlue": ("Deep Blue Metallic", "#223a5e", "PPSB"),
    "Blue": ("Blue", "#223a5e", "PMBL"),
    "RedMulticoat": ("Red Multi-Coat", "#9a1b23", "PPMR"),
    "Red": ("Red Multi-Coat", "#9a1b23", "PPMR"),
    "SignatureRed": ("Signature Red", "#9a1b23", "PPSR"),
    "Silver": ("Silver Metallic", "#b4b7ba", "PMSS"),
    "SilverMetallic": ("Silver Metallic", "#b4b7ba", "PMSS"),
    "TitaniumMetallic": ("Titanium Metallic", "#6b6a67", "PMTG"),
    "Quicksilver": ("Quicksilver", "#b0b4b8", "PN00"),
    "QuickSilver": ("Quicksilver", "#b0b4b8", "PN00"),
    "StealthGrey": ("Stealth Grey", "#4a4d50", "PN01"),
    "StealthGray": ("Stealth Grey", "#4a4d50", "PN01"),
    "UltraRed": ("Ultra Red", "#8c1420", "PR01"),
    "MidnightCherryRed": ("Midnight Cherry Red", "#43101a", "PR00"),
    "DiamondBlack": ("Diamond Black", "#17181a", "PMBL"),
    "Pastel": ("Pastel", "#dcdcdc", ""),
    "Green": ("Green", "#2f5d45", ""),
    "Brown": ("Brown", "#5a4433", ""),
}

# wheel_type -> readable name
WHEELS = {
    "Pinwheel18": '18" Aero',
    "PinWheel18": '18" Aero',
    "Photon18": '18" Photon',
    "Stiletto19": '19" Sport',
    "Sportwheel19": '19" Sport',
    "Photon19": '19" Photon',
    "Apollo19": '19" Apollo',
    "Gemini19": '19" Gemini',
    "Induction20": '20" Induction',
    "Uberturbine20": '20" Überturbine',
    "Nova20": '20" Nova',
    "Tempest20": '20" Tempest',
    "Sport20": '20" Sport',
    "Performance20": '20" Performance',
    "Arachnid21": '21" Arachnid',
    "Turbine22": '22" Turbine',
    "Cyberstream22": '22" Cyberstream',
}

# car_type -> configurator model code and view
# Tesla's picture service has no Cybertruck or Roadster: those get the dashboard's own drawing
_COMPOSITOR_MODEL = {
    "model3": "m3",
    "modely": "my",
    "models": "ms",
    "models2": "ms",
    "lychee": "ms",
    "modelx": "mx",
    "tamarind": "mx",
}
# The 10th character of a VIN is the model year
_VIN_YEARS = {
    "L": 2020,
    "M": 2021,
    "N": 2022,
    "P": 2023,
    "R": 2024,
    "S": 2025,
    "T": 2026,
    "V": 2027,
    "W": 2028,
    "X": 2029,
    "Y": 2030,
}
# The new Model 3 and Model Y bodies: the first model year that is all new, and wheels only they have
_REFRESH_YEAR = {"m3": 2024, "my": 2026}
_REFRESH_WHEELS = {
    "m3": {"Photon18", "Glider18", "Nova19", "Nova20", "Photon19"},
    "my": {"HelixV220", "Helix20", "Crossflow19"},
}
# Paints of the older bodies that the new ones don't come in, and the closest they do
_REFRESH_PAINT = {
    "PMNG": "PN01",
    "PMSS": "PN00",
    "PMTG": "PN01",
    "PPMR": "PR01",
    "PR00": "PR01",
    "PPSR": "PR01",
    "PMBL": "PX02",
    "PBCW": "PPSW",
}
_COMPOSITOR_WHEEL = {
    # Model 3
    "Pinwheel18": "W38B",
    "PinWheel18": "W38B",
    "Stiletto19": "W39B",
    "Photon19": "W39B",
    "Nova20": "W40B",
    "Induction20": "W41B",
    "Uberturbine20": "W41B",
    "Performance20": "W41B",
    "Photon18": "W38B",
    # Model Y
    "Gemini19": "WY19B",
    "Apollo19": "WY19B",
    "Induction21": "WY21P",
}
# A base wheel per model, used when the car's exact wheel isn't in the map above
_COMPOSITOR_DEFAULT_WHEEL = {"m3": "W38B", "my": "WY19B", "ms": "WT19", "mx": "WT20"}


def _spaced(code: str) -> str:
    """A raw code made readable: "MidnightSilver" -> "Midnight Silver", "p74d" -> "P74D"."""
    if not code:
        return ""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", str(code)).replace("_", " ")
    text = re.sub(r"(?<=[A-Za-z])(?=[0-9])", " ", text).strip()
    return text if " " in text or text.istitle() else text.upper()


# The 4th character of a Tesla VIN is the model, a fallback when vehicle_config has no car_type yet
_VIN_MODEL = {"S": "models", "3": "model3", "X": "modelx", "Y": "modely", "C": "cybertruck", "R": "roadster"}


def car_type_from_vin(vin: str | None) -> str | None:
    if vin and len(vin) >= 4:
        return _VIN_MODEL.get(vin[3].upper())
    return None


def model_name(car_type: str | None, vin: str | None = None) -> str:
    key = str(car_type or "").lower() or (car_type_from_vin(vin) or "")
    return MODELS.get(key) or (_spaced(car_type) if car_type else "Tesla")


def body_style(car_type: str | None, vin: str | None = None) -> str:
    """Which of the dashboard's drawings fits the car."""
    key = str(car_type or "").lower() or (car_type_from_vin(vin) or "")
    return {
        "modely": "modely",
        "models": "models",
        "models2": "models",
        "lychee": "models",
        "modelx": "modelx",
        "tamarind": "modelx",
        "cybertruck": "truck",
        "roadster": "roadster",
    }.get(key, "model3")


def trim_name(car_type: str | None, trim_badging: str | None, vin: str | None = None) -> str:
    if not trim_badging:
        return ""
    key = str(car_type or "").lower() or (car_type_from_vin(vin) or "")
    return TRIMS.get(key, {}).get(str(trim_badging).lower()) or str(trim_badging).upper()


def color_name(exterior_color: str | None) -> str:
    if not exterior_color:
        return ""
    entry = COLORS.get(exterior_color)
    return entry[0] if entry else _spaced(exterior_color)


def color_hex(exterior_color: str | None) -> str | None:
    entry = COLORS.get(exterior_color or "")
    return entry[1] if entry else None


def wheels_name(wheel_type: str | None) -> str:
    if not wheel_type:
        return ""
    return WHEELS.get(wheel_type) or _spaced(wheel_type)


def _model_year(vin: str | None) -> int | None:
    """The model year in a VIN's 10th character, from 2020 on."""
    code = vin[9].upper() if vin and len(vin) >= 10 else ""
    return _VIN_YEARS.get(code)


def _is_refreshed(model: str, vin: str | None, wheel_type: str | None) -> bool:
    """Whether a Model 3 or Y has the new body (2024 "Highland", 2025 "Juniper"). No report says so: the
    wheels only fit one body, and otherwise the model year decides, from the first full year of the new one."""
    wheel = wheel_type or ""
    if wheel in _REFRESH_WHEELS[model]:
        return True
    if wheel in _COMPOSITOR_WHEEL:
        return False
    year = _model_year(vin)
    return bool(year) and year >= _REFRESH_YEAR[model]


def image_url(
    car_type: str | None, exterior_color: str | None, wheel_type: str | None, vin: str | None = None
) -> str | None:
    """A link to Tesla's configurator picture of the car. In white while the paint isn't known or has no
    picture, and None for the models Tesla has no pictures of."""
    model = _COMPOSITOR_MODEL.get(str(car_type or "").lower())
    if not model:
        return None
    paint = COLORS.get(exterior_color or "", (None, None, None))[2] or "PPSW"
    view = "STUD_SIDE"
    if model in _REFRESH_YEAR and _is_refreshed(model, vin, wheel_type):
        # The new bodies only come with some paints, and with a trim and interior code or not at all
        paint = _REFRESH_PAINT.get(paint, paint)
        if model == "m3":
            big_wheels = "19" in (wheel_type or "") or "20" in (wheel_type or "")
            options = ["MT369", paint, "W39S" if big_wheels else "W38A", "IPB1"]
        else:
            options = ["MTY47", paint, "WY19P", "IPB10"]
    else:
        # A wheel code must always be sent, else the compositor draws empty wheel wells.
        # Fall back to the model's base wheel when the exact one isn't known.
        wheel = _COMPOSITOR_WHEEL.get(wheel_type or "") or _COMPOSITOR_DEFAULT_WHEEL.get(model)
        options = [paint, wheel] if wheel else [paint]
        # Tesla only serves the older Model Y at this angle, not from the side
        if model == "my":
            view = "STUD_3QTR"
    query = f"context=design_studio_2&options=${',$'.join(options)}&view={view}&model={model}&size=1400&bkba_opt=1&crop=0,0,0,0"
    return f"https://static-assets.tesla.com/configurator/compositor?{query}"


def image_scale(url: str | None) -> float:
    """How much to enlarge a picture: Tesla leaves wide margins around the car, wider on some."""
    if not url:
        return 1.0
    return 2.0 if "$MT369" in url or "$MTY47" in url or "model=mx" in url else 1.6


def describe(
    car_type: str | None,
    trim_badging: str | None,
    exterior_color: str | None,
    wheel_type: str | None,
    vin: str | None = None,
) -> dict:
    """All of the above in one call, for a car's view."""
    url = image_url(car_type or car_type_from_vin(vin), exterior_color, wheel_type, vin)
    return {
        "model_name": model_name(car_type, vin),
        "body_style": body_style(car_type, vin),
        "trim_name": trim_name(car_type, trim_badging, vin),
        "color_name": color_name(exterior_color),
        "color_hex": color_hex(exterior_color),
        "wheels_name": wheels_name(wheel_type),
        "image_url": url,
        "image_scale": image_scale(url),
        "image_key": hashlib.sha256(url.encode()).hexdigest()[:12] if url else None,
    }
