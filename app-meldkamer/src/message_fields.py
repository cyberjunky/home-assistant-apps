"""What a message says besides its address: incident type, reference numbers, talk group.

Derived from the text alone, so it also applies to messages stored by older versions.
"""

import re

# (type, label, pattern): first match wins, so the specific come before the general
_INCIDENT_RULES = [
    ("grip", "GRIP", re.compile(r"\bGRIP\s?[1-5]\b", re.I)),
    (
        "alarm",
        "Automatische melding",
        re.compile(r"\bOMS\b|\bBMI\b|automatische (?:brand)?melding|brandmelding|handbrandmelder|handmelder", re.I),
    ),
    (
        "traffic",
        "Verkeersongeval",
        re.compile(
            r"\bVKO\b|verkeersongeval|aanrijding|ongeval wegvervoer|ongeval weg\b|\bVOA\b|eenzijdig ongeval", re.I
        ),
    ),
    ("water", "Waterongeval", re.compile(r"waterongeval|te water|drenkeling|\bWO\b|\bEHAD\b", re.I)),
    ("medical", "Medisch", re.compile(r"reanimatie|\bAED\b|hartstilstand|onwel|bewusteloos|\bCPR\b|\bBLS\b", re.I)),
    (
        "gas",
        "Gas / stank",
        re.compile(r"gaslucht|gaslek|\bstank\b|hinderlijke lucht|hind\. lucht|\bCO-?melding|koolmonoxide", re.I),
    ),
    ("lift", "Liftopsluiting", re.compile(r"liftopsluiting|\blift\b|opsluiting", re.I)),
    (
        "storm",
        "Storm / wateroverlast",
        re.compile(r"stormschade|boom (?:op|over|in)\b|omgewaaide?|wateroverlast", re.I),
    ),
    ("animal", "Dier in nood", re.compile(r"\bdier(?:en)?\b|\bhond\b|\bkat\b|\bpaard\b|\bkoe\b|\bschaap\b", re.I)),
    # "brand" as a word or compound (woningbrand, schoorsteenbr.), not "Brandweer"
    (
        "fire",
        "Brand",
        re.compile(
            r"\b[\w/-]*brand(?:je|en|gerucht|lucht)?\b|\b\w*br\.|\bBR\b|rookontwikkeling|\bNATBR\b|\bBINNENBR\b|\bBUITENBR\b",
            re.I,
        ),
    ),
    (
        "assist",
        "Hulpverlening",
        re.compile(r"\bassistentie\b|\bass\.|\bass\b|hulpverlening|\bHV\b|\bTHV\b|beknelling", re.I),
    ),
]

INCIDENT_TYPES = {kind: label for kind, label, _ in _INCIDENT_RULES}


def is_incident_word(word: str) -> bool:
    """Whether a single word names an incident ("Woningbrand", "Assistentie", "Stank"), not a place or street."""
    return any(pattern.fullmatch(word) for _, _, pattern in _INCIDENT_RULES)


_RIDE = re.compile(r"\brit:?\s*(\d{3,})", re.I)
_BON = re.compile(r"\bbon:?\s*(\d{3,})", re.I)
_ICNUM = re.compile(r"\bicnum:?\s*(\d+)", re.I)
# Talk groups ("BRT-01", "SNH-1525"): which letters are known is in the talkgroup_codes table
_TALK = re.compile(r"(?<![\w-])([A-Z]{3})-(\d{1,4})(?![\w-])")
_DIRECT = re.compile(r"\((?:directe inzet|dia):\s*ja\)", re.I)

# The 25 safety regions, as numbered in the vehicle numbering plan ("17-135" is Rotterdam-Rijnmond)
REGIONS = {
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


# The province each safety region lies in (each lies in one), keyed by the region name
# without spaces, hyphens and case: the capcode list writes "Gelderland Midden",
# "Brabant Zuid-Oost", "Limburg Zuid", the vehicle numbering plan "Gelderland-Midden"
_REGION_PROVINCES = {
    "groningen": "Groningen",
    "fryslan": "Friesland",
    "friesland": "Friesland",
    "drenthe": "Drenthe",
    "ijsselland": "Overijssel",
    "twente": "Overijssel",
    "noordenoostgelderland": "Gelderland",
    "gelderlandmidden": "Gelderland",
    "gelderlandzuid": "Gelderland",
    "utrecht": "Utrecht",
    "noordhollandnoord": "Noord-Holland",
    "zaanstreekwaterland": "Noord-Holland",
    "kennemerland": "Noord-Holland",
    "amsterdamamstelland": "Noord-Holland",
    "gooienvechtstreek": "Noord-Holland",
    "haaglanden": "Zuid-Holland",
    "hollandsmidden": "Zuid-Holland",
    "rotterdamrijnmond": "Zuid-Holland",
    "zuidhollandzuid": "Zuid-Holland",
    "zeeland": "Zeeland",
    "middenenwestbrabant": "Noord-Brabant",
    "brabantnoord": "Noord-Brabant",
    "brabantzuidoost": "Noord-Brabant",
    "limburgnoord": "Limburg",
    "limburgzuid": "Limburg",
    "zuidlimburg": "Limburg",
    "flevoland": "Flevoland",
}


def province_of_region(region: str) -> str:
    """The province of a safety region ("Gelderland Midden" -> "Gelderland"), or "" when unknown."""
    key = re.sub(r"[^a-z]", "", (region or "").casefold().replace("â", "a"))
    key = key.removeprefix("veiligheidsregio").removeprefix("regio")
    return _REGION_PROVINCES.get(key, "")


# Province codes after a town name in a message ("Velp NB", "Hoorn NH") and in the town
# abbreviations (VELPNB -> "Velp NB", HOORFR -> "Hoorn Fr"). They say which of two
# same-named towns is meant
PROVINCE_CODES = {
    "DR": "Drenthe",
    "FL": "Flevoland",
    "FR": "Friesland",
    "GE": "Gelderland",
    "GLD": "Gelderland",
    "GR": "Groningen",
    "LI": "Limburg",
    "NB": "Noord-Brabant",
    "NH": "Noord-Holland",
    "OV": "Overijssel",
    "UT": "Utrecht",
    "ZE": "Zeeland",
    "ZH": "Zuid-Holland",
    "ZL": "Zeeland",
}


def split_province_code(name: str) -> tuple[str, str]:
    """("Velp NB") -> ("Velp", "Noord-Brabant"); a name without a province code comes back as is, with ""."""
    town, _, code = (name or "").rpartition(" ")
    # "L" only from the abbreviation list ("Beek L"), where it can't be a word of the message
    province = PROVINCE_CODES.get(code.upper()) or ("Limburg" if code == "L" else "")
    return (town, province) if town and province else (name, "")


def message_province(msg) -> str:
    """The province a message's town lies in: named in the text, else that of its safety region, else ""."""
    return getattr(msg, "province", "") or province_of_region(msg.region)


def classify_incident(body: str) -> tuple[str, str]:
    """(type, label) of what the message is about, ("", "") when the text doesn't say."""
    for kind, label, pattern in _INCIDENT_RULES:
        if pattern.search(body or ""):
            return kind, label
    return "", ""


def message_references(body: str, is_talkgroup=None) -> dict:
    """Reference numbers, talk group and direct-deployment flag from the text.

    is_talkgroup(code) says whether a code like "BRT-01" is a known talk group (the
    database's talkgroup_codes); without it any code of that shape counts.
    """
    text = body or ""
    talk, talk_name = "", ""
    for m in _TALK.finditer(text):
        decoded = is_talkgroup(m.group(0)) if is_talkgroup else True
        if decoded:
            talk = m.group(0)
            if isinstance(decoded, tuple):
                # ("Brandweer", "Rotterdam", "01") from Database.decode_talkgroup
                talk_name = " ".join(decoded)
            break
    first = lambda pattern: m.group(1) if (m := pattern.search(text)) else ""  # noqa: E731
    return {
        "ride_number": first(_RIDE),
        "bon_number": first(_BON),
        "incident_number": first(_ICNUM),
        "talk_group": talk,
        "talk_group_name": talk_name,
        "direct_deployment": bool(_DIRECT.search(text)),
    }


def derived_fields(body: str, is_talkgroup=None) -> dict:
    """Everything this module derives, for a message dict (stored rows get it on read)."""
    kind, label = classify_incident(body)
    return {"incident_type": kind, "incident_label": label, **message_references(body, is_talkgroup)}


def region_of_vehicle(vehicle_number: str) -> str:
    """The safety region a vehicle number such as "17-135" belongs to, or ""."""
    prefix = (vehicle_number or "").split("-", 1)[0]
    return REGIONS.get(prefix, "")
