"""What a message says besides its address: incident type, reference numbers, talk group.

Derived from the text alone, so it also applies to messages stored by older versions.
"""

import re

from . import lookups

# Incident types, safety regions and their provinces are database tables (incident_rules,
# vehicle_regions, region_provinces), read through lookups; the defaults are in lookups.py


def incident_types() -> dict[str, str]:
    """{type: label} of the incident types, in the order they are tried."""
    return {kind: label for kind, label, _ in lookups.get("incident_rules")}


def is_incident_word(word: str) -> bool:
    """Whether a single word names an incident ("Woningbrand", "Assistentie", "Stank"), not a place or street."""
    return any(pattern.fullmatch(word) for _, _, pattern in lookups.get("incident_rules"))


_RIDE = re.compile(r"\brit:?\s*(\d{3,})", re.I)
_BON = re.compile(r"\bbon:?\s*(\d{3,})", re.I)
_ICNUM = re.compile(r"\bicnum:?\s*(\d+)", re.I)
# Talk groups ("BRT-01", "SNH-1525"): which letters are known is in the talkgroup_codes table
_TALK = re.compile(r"(?<![\w-])([A-Z]{3})-(\d{1,4})(?![\w-])")
_DIRECT = re.compile(r"\((?:directe inzet|dia):\s*ja\)", re.I)


def province_of_region(region: str) -> str:
    """The province of a safety region ("Gelderland Midden" -> "Gelderland"), or "" when unknown."""
    return lookups.get("region_provinces").get(lookups.region_key(region), "")


# Province codes after a town name in a message ("Velp NB", "Hoorn NH") and in the town
# abbreviations (VELPNB -> "Velp NB", HOORFR -> "Hoorn Fr"). They say which of two
# same-named towns is meant. These are only the defaults of the province_codes table
# (seeded once, editable on the Data tab); the lookups read the table.
# abbreviation_only: "L" only counts in the abbreviation list ("Beek L"), where it can't
# be a word of the message
DEFAULT_PROVINCE_CODES = [
    {"code": code, "province": province, "abbreviation_only": "0"}
    for code, province in (
        ("DR", "Drenthe"),
        ("FL", "Flevoland"),
        ("FR", "Friesland"),
        ("GE", "Gelderland"),
        ("GLD", "Gelderland"),
        ("GR", "Groningen"),
        ("LI", "Limburg"),
        ("NB", "Noord-Brabant"),
        ("NH", "Noord-Holland"),
        ("OV", "Overijssel"),
        ("UT", "Utrecht"),
        ("ZE", "Zeeland"),
        ("ZH", "Zuid-Holland"),
        ("ZL", "Zeeland"),
    )
] + [{"code": "L", "province": "Limburg", "abbreviation_only": "1"}]


def split_province_code(name: str, codes: dict[str, tuple[str, bool]]) -> tuple[str, str]:
    """("Velp NB") -> ("Velp", "Noord-Brabant"); a name without a province code comes back as is, with "".

    codes is {code: (province, abbreviation_only)} from the province_codes table; this is
    for names from the abbreviation list, so abbreviation-only codes ("L") count too.
    """
    town, _, code = (name or "").rpartition(" ")
    province = codes.get(code.upper(), ("", False))[0]
    return (town, province) if town and province else (name, "")


def message_province(msg) -> str:
    """The province a message's town lies in: named in the text, else that of its safety region, else ""."""
    return getattr(msg, "province", "") or province_of_region(msg.region)


def classify_incident(body: str) -> tuple[str, str]:
    """(type, label) of what the message is about, ("", "") when the text doesn't say."""
    for kind, label, pattern in lookups.get("incident_rules"):
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
    return lookups.get("vehicle_regions").get(prefix, "")
