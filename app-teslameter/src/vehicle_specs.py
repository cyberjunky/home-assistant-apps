"""Readable names, paint colours and a picture for a car, from Tesla's vehicle_config codes.

Tesla reports the model, trim, paint and wheels as short codes ("model3", "50",
"MidnightSilver", "Pinwheel18"). This turns them into readable text, a real
paint colour (hex, for a swatch and for tinting the dashboard car), and a link
to Tesla's own configurator image when the paint can be mapped to its code.
Unknown codes are formatted as best as possible instead of dropped.
"""

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
    "Quicksilver": ("Quicksilver", "#b0b4b8", "PN01"),
    "QuickSilver": ("Quicksilver", "#b0b4b8", "PN01"),
    "StealthGrey": ("Stealth Grey", "#4a4d50", "PN00"),
    "StealthGray": ("Stealth Grey", "#4a4d50", "PN00"),
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
# Tesla only serves the Model Y at this angle, not from the side
_COMPOSITOR_VIEW = {"my": "STUD_3QTR"}
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
    """The silhouette to draw: "sedan", "suv", "truck" or "roadster"."""
    key = str(car_type or "").lower() or (car_type_from_vin(vin) or "")
    if key in ("modely", "modelx", "tamarind"):
        return "suv"
    if key == "cybertruck":
        return "truck"
    if key == "roadster":
        return "roadster"
    return "sedan"


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


def image_url(car_type: str | None, exterior_color: str | None, wheel_type: str | None) -> str | None:
    """A link to Tesla's configurator render of a car in this paint and wheels, None when the paint is unknown.

    Only built when the paint code is known, so the picture's colour is right;
    the dashboard tints its own car silhouette to color_hex otherwise.
    """
    model = _COMPOSITOR_MODEL.get(str(car_type or "").lower())
    paint = COLORS.get(exterior_color or "", (None, None, None))[2]
    if not model or not paint:
        return None
    # A wheel code must always be sent, else the compositor draws empty wheel wells.
    # Fall back to the model's base wheel when the exact one isn't known.
    wheel = _COMPOSITOR_WHEEL.get(wheel_type or "") or _COMPOSITOR_DEFAULT_WHEEL.get(model)
    options = [paint, wheel] if wheel else [paint]
    query = (
        f"context=design_studio_2&options=${',$'.join(options)}"
        f"&view={_COMPOSITOR_VIEW.get(model, 'STUD_SIDE')}&model={model}&size=1400&bkba_opt=1&crop=0,0,0,0"
    )
    return f"https://static-assets.tesla.com/configurator/compositor?{query}"


def describe(
    car_type: str | None,
    trim_badging: str | None,
    exterior_color: str | None,
    wheel_type: str | None,
    vin: str | None = None,
) -> dict:
    """All of the above in one call, for a car's view."""
    return {
        "model_name": model_name(car_type, vin),
        "body_style": body_style(car_type, vin),
        "trim_name": trim_name(car_type, trim_badging, vin),
        "color_name": color_name(exterior_color),
        "color_hex": color_hex(exterior_color),
        "wheels_name": wheels_name(wheel_type),
        "image_url": image_url(car_type or car_type_from_vin(vin), exterior_color, wheel_type),
    }
