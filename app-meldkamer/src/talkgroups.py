"""Talk group codes in P2000 texts ("BRT-01"): the default discipline and region codes.

An alarm text names the C2000 talk group as X YY - ZZZZ: X the discipline, YY the safety
region or meldkamer, then the talk group's quick-dial number (1-4 digits).
Source: Brandweer Nederland / NIPV, "Nota van toelichting Standaard Presentatietekst
Gespreksgroep in GMS pagertekst", versie 25 januari 2019,
https://nipv.nl/documenten/nota-van-toelichting-standaard-presentatietekst-gespreksgroep-in-gms-pagertekst

These are only the defaults: they go into the talkgroup_codes table once (see
Database.seed_defaults), where they can be changed on the Settings tab. Decoding always
reads the table.
"""

import re

TALKGROUP_DISCIPLINES = {
    "B": "Brandweer",
    "A": "Ambulancezorg",
    "P": "Politie",
    "M": "Koninklijke Marechaussee",
    "D": "Defensiediensten",
    "S": "Multidisciplinaire samenwerking",
}
TALKGROUP_REGIONS = {
    "LA": "Bovenregionaal",
    "GR": "Groningen",
    "FR": "Friesland",
    "DR": "Drenthe",
    "IJ": "IJsselland",
    "TW": "Twente",
    "GN": "Noord- en Oost-Gelderland",
    "GM": "Gelderland-Midden",
    "GZ": "Gelderland-Zuid",
    "UT": "Utrecht",
    "ZW": "Zaanstreek-Waterland",
    "KE": "Kennemerland",
    "AM": "Amsterdam-Amstelland",
    "GV": "Gooi en Vechtstreek",
    "HL": "Haaglanden",
    "HM": "Hollands Midden",
    "RR": "Rotterdam-Rijnmond",
    "ZH": "Zuid-Holland Zuid",
    "ZL": "Zeeland",
    "BM": "Midden- en West-Brabant",
    "BN": "Brabant-Noord",
    "BZ": "Brabant-Zuidoost",
    "LN": "Limburg-Noord",
    "LZ": "Limburg-Zuid",
    "FL": "Flevoland",
    "LE": "Landelijke eenheid",
    "AD": "Amsterdam",
    "NN": "Noord-Nederland",
    "ON": "Oost-Nederland",
    "MD": "Midden-Nederland",
    # The note has NH twice: 10 Noord-Holland Noord (region) and 38 Noord-Holland
    # (meldkamer); in alarm texts the meldkamer is meant
    "NH": "Noord-Holland",
    "PA": "Politieacademie",
    "LB": "Limburg",
    "OB": "Oost-Brabant",
    "ZB": "Zeeland-West-Brabant",
    "RT": "Rotterdam",
    "DH": "Den Haag",
}
DEFAULT_TALKGROUP_CODES = [{"kind": "discipline", "code": k, "name": v} for k, v in TALKGROUP_DISCIPLINES.items()] + [
    {"kind": "region", "code": k, "name": v} for k, v in TALKGROUP_REGIONS.items()
]
KINDS = {"discipline": 1, "region": 2}

# A talk group in a text: "BRT-01", "SNH-1525" (capitals, as GMS writes them)
TALKGROUP = re.compile(r"(?<![\w-])([A-Z])([A-Z]{2})-(\d{1,4})(?![\w-])")
