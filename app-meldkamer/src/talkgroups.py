"""Talk group codes in P2000 texts ("BRT-01"): their shape.

An alarm text names the C2000 talk group as X YY - ZZZZ: X the discipline, YY the safety
region or meldkamer, then the talk group's quick-dial number (1-4 digits).
Source: Brandweer Nederland / NIPV, "Nota van toelichting Standaard Presentatietekst
Gespreksgroep in GMS pagertekst", versie 25 januari 2019,
https://nipv.nl/documenten/nota-van-toelichting-standaard-presentatietekst-gespreksgroep-in-gms-pagertekst

The letters and their names are the talkgroup_codes table (built-in rows in
src/defaults/talkgroup_codes.csv); decoding reads the table.
"""

import re

# The two kinds of letters and their length (the format)
KINDS = {"discipline": 1, "region": 2}

# A talk group in a text: "BRT-01", "SNH-1525" (capitals, as GMS writes them)
TALKGROUP = re.compile(r"(?<![\w-])([A-Z])([A-Z]{2})-(\d{1,4})(?![\w-])")
