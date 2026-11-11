"""The lookup data the parser reads, loaded from the database tables.

The lists live in database tables (see Database.LOOKUP_TABLES), seeded once with the
defaults in DEFAULTS and editable on the Data tab. Database.load_lookups() puts their
current contents here, at start and after every change, so the module-level parsing
functions (message_fields, parser, vehicle_types) need no database handle. Until a
database is opened (some unit tests), the defaults are used.
"""

import re
from typing import Any

# --------------------------------------------------------------------------- defaults

# Incident types: (kind, label, pattern), first match wins, so the specific come before
# the general. Patterns are case-insensitive.
DEFAULT_INCIDENT_RULES = [
    ("grip", "GRIP", r"\bGRIP\s?[1-5]\b"),
    (
        "alarm",
        "Automatische melding",
        r"\bOMS\b|\bBMI\b|automatische (?:brand)?melding|brandmelding|handbrandmelder|handmelder",
    ),
    (
        "traffic",
        "Verkeersongeval",
        r"\bVKO\b|verkeersongeval|aanrijding|ongeval wegvervoer|ongeval weg\b|\bVOA\b|eenzijdig ongeval",
    ),
    ("water", "Waterongeval", r"waterongeval|te water|drenkeling|\bWO\b|\bEHAD\b"),
    ("medical", "Medisch", r"reanimatie|\bAED\b|hartstilstand|onwel|bewusteloos|\bCPR\b|\bBLS\b"),
    ("gas", "Gas / stank", r"gaslucht|gaslek|\bstank\b|hinderlijke lucht|hind\. lucht|\bCO-?melding|koolmonoxide"),
    ("lift", "Liftopsluiting", r"liftopsluiting|\blift\b|opsluiting"),
    ("storm", "Storm / wateroverlast", r"stormschade|boom (?:op|over|in)\b|omgewaaide?|wateroverlast"),
    ("animal", "Dier in nood", r"\bdier(?:en)?\b|\bhond\b|\bkat\b|\bpaard\b|\bkoe\b|\bschaap\b"),
    # "brand" as a word or compound (woningbrand, schoorsteenbr.), not "Brandweer"
    (
        "fire",
        "Brand",
        r"\b[\w/-]*brand(?:je|en|gerucht|lucht)?\b|\b\w*br\.|\bBR\b|rookontwikkeling|\bNATBR\b|\bBINNENBR\b|\bBUITENBR\b",
    ),
    ("assist", "Hulpverlening", r"\bassistentie\b|\bass\.|\bass\b|hulpverlening|\bHV\b|\bTHV\b|beknelling"),
]

# Priority of a message: (level, pattern), the first level whose pattern matches (case-insensitive)
DEFAULT_PRIORITY_RULES = [
    (1, r"^A\s?1|\bA\s?1|PRIO\s?1|^P\s?1"),
    (2, r"^A\s?2|\bA\s?2|PRIO\s?2|^P\s?2"),
    (3, r"^B\s?[123]|PRIO\s?3|^P\s?3"),
    (4, r"PRIO\s?4|^P\s?4"),
]

# Word lists of the address search, by kind
DEFAULT_PARSER_WORDS = {
    # Common endings of Dutch street names (in this order)
    "street_suffix": [
        "straat",
        "weg",
        "laan",
        "plein",
        "singel",
        "kade",
        "gracht",
        "steeg",
        "pad",
        "dreef",
        "hof",
        "dijk",
        "park",
    ],
    # Small words inside a street name ("Burgemeester van ...straat"), lowercase
    "street_connector": ["van", "de", "den", "der", "het", "ter", "ten", "op", "aan", "in", "t", "'t", "s", "'s"],
    # Capitalised words that P2000 texts put before an address but that are no part of it, lowercase
    "not_street_word": [
        "ambulance",
        "ambulancepost",
        "ambu",
        "brandweer",
        "brandweerkazerne",
        "kazerne",
        "politie",
        "melding",
        "ongeval",
        "nacontrole",
        "buitensluiting",
        "binnensluiting",
        "afhijsen",
        "reanimatie",
        "prio",
        "test",
        "oefening",
    ],
    # Words at the end of a message that are no town, lowercase
    "not_city_word": [
        "bon",
        "rit",
        "ambu",
        "prio",
        "icnum",
        "regio",
        "alert",
        "melding",
        "dienst",
        "post",
        "dhn",
        "dmt",
        "nhn",
        "hgl",
    ],
    # Words never taken as a street when the town is known (as written, or in capitals)
    "street_skip_word": [
        "AMBU",
        "BRAND",
        "PRIO",
        "A1",
        "A2",
        "B1",
        "B2",
        "P1",
        "P2",
        "bon",
        "Rit",
        "Regio",
        "Ambu",
        "Brandweer",
    ],
}

# The 25 safety regions, as numbered in the vehicle numbering plan ("17-135" is Rotterdam-Rijnmond)
DEFAULT_VEHICLE_REGIONS = {
    "01": "Groningen",
    "02": "Fryslân",
    "03": "Drenthe",
    "04": "IJsselland",
    "05": "Twente",
    "06": "Noord- en Oost-Gelderland",
    "07": "Gelderland-Midden",
    "08": "Gelderland-Zuid",
    "09": "Utrecht",
    "10": "Noord-Holland-Noord",
    "11": "Zaanstreek-Waterland",
    "12": "Kennemerland",
    "13": "Amsterdam-Amstelland",
    "14": "Gooi en Vechtstreek",
    "15": "Haaglanden",
    "16": "Hollands-Midden",
    "17": "Rotterdam-Rijnmond",
    "18": "Zuid-Holland-Zuid",
    "19": "Zeeland",
    "20": "Midden- en West-Brabant",
    "21": "Brabant-Noord",
    "22": "Brabant-Zuidoost",
    "23": "Limburg-Noord",
    "24": "Zuid-Limburg",
    "25": "Flevoland",
}

# The province each safety region lies in. Region names are compared without spaces, hyphens,
# accents and case (the capcode list writes "Gelderland Midden", "Brabant Zuid-Oost")
DEFAULT_REGION_PROVINCES = {
    **{
        name: province
        for name, province in zip(
            DEFAULT_VEHICLE_REGIONS.values(),
            [
                "Groningen",
                "Friesland",
                "Drenthe",
                "Overijssel",
                "Overijssel",
                "Gelderland",
                "Gelderland",
                "Gelderland",
                "Utrecht",
                "Noord-Holland",
                "Noord-Holland",
                "Noord-Holland",
                "Noord-Holland",
                "Noord-Holland",
                "Zuid-Holland",
                "Zuid-Holland",
                "Zuid-Holland",
                "Zuid-Holland",
                "Zeeland",
                "Noord-Brabant",
                "Noord-Brabant",
                "Noord-Brabant",
                "Limburg",
                "Limburg",
                "Flevoland",
            ],
            strict=True,
        )
    },
    "Friesland": "Friesland",
    "Limburg-Zuid": "Limburg",
}

# Vehicle type codes in messages and the broad category they are (the categories are the
# app's icons and stock photos, see vehicle_types.CATEGORY_ICONS)
DEFAULT_VEHICLE_TYPE_CODES = {
    # Ambulance
    "AMBU": "ambulance",
    "MUG": "ambulance",
    # Helicopters (air ambulance / trauma team)
    "MUH": "helikopter",
    "MAA": "helikopter",
    "HTT": "helikopter",
    # Fire - pumping/engine appliances
    "TS": "brandweer_tankautospuit",
    "TAS": "brandweer_tankautospuit",
    "TS-HV": "brandweer_tankautospuit",
    # Fire - aerial appliances
    "AL": "brandweer_ladder",
    "AL-42": "brandweer_ladder",
    "AL-36": "brandweer_ladder",
    "AL-32": "brandweer_ladder",
    "HW": "brandweer_hoogwerker",
    "HW-36": "brandweer_hoogwerker",
    "HW-24": "brandweer_hoogwerker",
    # Fire - specialized appliances
    "SL": "brandweer_slangenwagen",
    "SL-4": "brandweer_slangenwagen",
    "SL-6": "brandweer_slangenwagen",
    "WO": "brandweer_waterongevallen",
    "PB": "brandweer_poederblus",
    "SB": "brandweer_schuimblus",
    "MS": "brandweer_motorspuit",
    "DPA": "brandweer_dompelpomp",
    "DPU": "brandweer_dompelpomp",
    "RIV": "brandweer_riv",
    "HVH": "brandweer_haakarmbak",
    "GSH": "brandweer_haakarmbak",
    "VZH": "brandweer_haakarmbak",
    "LHH": "brandweer_haakarmbak",
    "CHH": "brandweer_haakarmbak",
    "HV": "brandweer_hulpverlening",
    "OvD": "brandweer_officier",
    "HOvD": "brandweer_officier",
    "CvD": "brandweer_officier",
    # Police
    "POL": "politie",
    "AE": "politie",
    "AT": "politie",
    "ME": "politie",
    # Water rescue
    "RB": "reddingboot",
}


def region_key(region: str) -> str:
    """A safety region name as compared: "Veiligheidsregio Gelderland Midden" -> "gelderlandmidden"."""
    key = re.sub(r"[^a-z]", "", (region or "").casefold().replace("â", "a"))
    return key.removeprefix("veiligheidsregio").removeprefix("regio")


def _defaults() -> dict[str, Any]:
    return {
        "incident_rules": [(k, label, re.compile(p, re.I)) for k, label, p in DEFAULT_INCIDENT_RULES],
        "priority_rules": list(DEFAULT_PRIORITY_RULES),
        "parser_words": {k: list(v) for k, v in DEFAULT_PARSER_WORDS.items()},
        "vehicle_regions": dict(DEFAULT_VEHICLE_REGIONS),
        "region_provinces": {region_key(r): p for r, p in DEFAULT_REGION_PROVINCES.items()},
        "vehicle_type_codes": dict(DEFAULT_VEHICLE_TYPE_CODES),
    }


# ------------------------------------------------------------------------------ store

_data: dict[str, Any] = _defaults()
# Bumped on every load, so derived data (vehicle_types' patterns) knows to rebuild
version = 0


def load(values: dict[str, Any]) -> None:
    """Replace lookup data (from Database.load_lookups)."""
    global version
    _data.update(values)
    version += 1


def get(name: str) -> Any:
    return _data[name]
