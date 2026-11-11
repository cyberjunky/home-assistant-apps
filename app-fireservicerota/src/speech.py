"""The spoken text of an incident: the alarm text made readable for a text-to-speech voice.

Ported from the Meldkamer app (its tts.py and abbreviation list), with stricter rules
because firefighters act on what they hear:

- nothing is left out: every word, code and number of the alarm text is spoken, as
  itself or as its meaning;
- nothing is guessed: a code is only spoken as words when its meaning is certain;
  otherwise it is spelled out ("XQZ" -> "X Q Z");
- numbers are spoken exactly ("hmp 67.4" -> "hectometerpaal 67 komma 4", reference
  numbers digit by digit, "00:00" -> "0 uur 0").

Every list it uses is a database table, editable on the Data tab: replacement rules,
abbreviations, place-name codes and talk group codes. The lists below are only the
defaults the tables get once (Database.seed_speech): a row you change keeps your text and
a default you delete stays deleted, also after updates.

The steps, in order (explain() returns each change it made):
1. replacement rules (regex, applied in order);
2. shortenings with dots ("t.h.v."), times, postal codes and decimal numbers;
3. each word: abbreviations, talk groups ("BRT-01"), place-name codes, letter codes with
   numbers, then unknown codes spelled out and long numbers digit by digit.

The same text goes to Home Assistant's tts.speak and to the dashboard's browser speech.
"""

import logging
import re

from .abbreviation_import import ABBREVIATIONS
from .city_abbreviations import CITY_ABBREVIATIONS
from .database import Database

logger = logging.getLogger(__name__)

# ----------------------------------------------------------------- defaults (seeded once)

# Applied case-insensitively, top to bottom. A replacement may never be empty.
DEFAULT_TTS_REPLACEMENTS = [
    # Priority and urgency at the start of the alarm text ("P 1 ...", "A1 ...", "B2 ...").
    # Sources: nl.wikipedia.org/wiki/P2000_(netwerk), 112hier.nl/uitleg, persmonitor.nl
    {"pattern": r"^\s*P\s?([1-5])\b", "replacement": r"Prio \1"},
    {"pattern": r"^\s*A\s?1\b", "replacement": "Ambulance met spoed"},
    {"pattern": r"^\s*A\s?2\b", "replacement": "Ambulance zonder spoed"},
    {"pattern": r"^\s*B\s?1\b", "replacement": "Ambulance besteld vervoer, hoogcomplexe zorgvraag"},
    {"pattern": r"^\s*B\s?2\b", "replacement": "Ambulance besteld vervoer, midden- of laagcomplexe zorgvraag"},
    # Elsewhere in the text P1 or B2 is not a documented priority (P1 is also a car park):
    # spoken as a letter and a number
    {"pattern": r"\b([PB])([1-5])\b", "replacement": r"\1 \2"},
    # Road numbers: "A2" or "N209" is spoken as a letter and a number (never "ambulance")
    {"pattern": r"\b([AN])(\d{1,3})\b", "replacement": r"\1 \2"},
    # Hectometer markers ("hmp 67.4"; the number step makes "67 komma 4")
    {"pattern": r"\bhmp\b\.?", "replacement": "hectometerpaal"},
    # Reference numbers: the word in full, the number digit by digit (number step)
    {"pattern": r"\bbon\b:?(?=\s*\d)", "replacement": "bonnummer"},
    {"pattern": r"\brit\b:?(?=\s*\d)", "replacement": "ritnummer"},
    {"pattern": r"\bicnum\b:?(?=\s*\d)", "replacement": "incidentnummer"},
    # Dutch shortenings
    {"pattern": r"\bass\.(?=\s|$)", "replacement": "assistentie"},
    {"pattern": r"\bivm\b\.?", "replacement": "in verband met"},
    {"pattern": r"\btbv\b\.?", "replacement": "ten behoeve van"},
    {"pattern": r"\bevt\.?(?=\s|$)", "replacement": "eventueel"},
    # Spoken the way people say them
    {"pattern": r"\bAED\b", "replacement": "A E D"},
    {"pattern": r"\bSGRAVH\b", "replacement": "Den Haag"},
]

# Meanings for codes the public list gives two meanings ("AC": "Alarmcentrale (BRW) /
# Actiecentrum"), as a brigade documents them for alarm texts:
# brandweeruitgeest.nl/betekenis-p2000-meldingen
SOURCED_MEANINGS = {
    "AC": "Alarmcentrale",
    "BOT": "Bedrijfs Opvang Team",
    "CCK": "Calamiteiten Commando Kamer",
    "MOB": "Medische Ondersteuning Brandweer",
    "SPB": "Schuim- Poederbluswagen",
    "GM": "Gereedschap materiaalwagen",
}

# Standard Dutch shortenings written with dots, not in the P2000 list
DUTCH_ABBREVIATIONS = {
    "t.h.v.": "ter hoogte van",
    "i.v.m.": "in verband met",
    "z.s.m.": "zo snel mogelijk",
    "n.a.v.": "naar aanleiding van",
    "o.a.": "onder andere",
    "i.p.v.": "in plaats van",
    "m.b.t.": "met betrekking tot",
    "t.b.v.": "ten behoeve van",
    "t.o.v.": "ten opzichte van",
    "i.o.m.": "in overleg met",
    "i.c.m.": "in combinatie met",
    "m.u.v.": "met uitzondering van",
    "o.l.v.": "onder leiding van",
    "d.w.z.": "dat wil zeggen",
    "n.v.t.": "niet van toepassing",
    "e.d.": "en dergelijke",
}

# Short Dutch words, so a text in capitals ("BR WONING VAN DE ...") doesn't get them spelled.
# Stored as abbreviations of themselves; a code in the P2000 list (OM) keeps its meaning.
COMMON_WORDS = (
    "AAN AF ALS BIJ DAN DAT DE DIE DIT EEN EN ER HET HIJ IN IS JA MET NA NAAR NEE NIET NOG OF "
    "ONDER OP OVER PER TE TER TOT UIT VAN VIA VOOR WEG ZIJ"
)

# Talk groups in alarm texts ("BRT-01"): discipline letter + meldkamer/region code + the
# talk group's quick-dial number. Source: Brandweer Nederland / NIPV, "Nota van toelichting
# Standaard Presentatietekst Gespreksgroep in GMS pagertekst", versie 25 januari 2019,
# nipv.nl/documenten/nota-van-toelichting-standaard-presentatietekst-gespreksgroep-in-gms-pagertekst
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
    # The note has NH twice: 10 Noord-Holland Noord (region) and 38 Noord-Holland (meldkamer);
    # in alarm texts the meldkamer is meant
    "NH": "Noord-Holland",
    "PA": "Politieacademie",
    "LB": "Limburg",
    "OB": "Oost-Brabant",
    "ZB": "Zeeland-West-Brabant",
    "RT": "Rotterdam",
    "DH": "Den Haag",
}


def _default_abbreviations() -> dict[str, str]:
    words = {w: w.lower() for w in COMMON_WORDS.split() if w not in {k.upper() for k in ABBREVIATIONS}}
    merged = {code.upper(): text for code, text in ABBREVIATIONS.items()}
    merged.update(SOURCED_MEANINGS)
    merged.update({code.upper(): text for code, text in DUTCH_ABBREVIATIONS.items()})
    merged.update(words)
    return merged


DEFAULTS: dict[str, list[dict]] = {
    "tts_replacements": DEFAULT_TTS_REPLACEMENTS,
    "abbreviations": [{"abbreviation": k, "full_text": v} for k, v in _default_abbreviations().items()],
    "city_abbreviations": [{"abbreviation": a, "city": c} for c, a in CITY_ABBREVIATIONS.items()],
    "talkgroup_codes": [{"kind": "discipline", "code": k, "name": v} for k, v in TALKGROUP_DISCIPLINES.items()]
    + [{"kind": "region", "code": k, "name": v} for k, v in TALKGROUP_REGIONS.items()],
}
# The value columns of each default row by its key, to mark rows on the Data tab as "default"
_DEFAULT_INDEX = {
    "tts_replacements": {r["pattern"]: (r["replacement"],) for r in DEFAULT_TTS_REPLACEMENTS},
    "abbreviations": {r["abbreviation"]: (r["full_text"],) for r in DEFAULTS["abbreviations"]},
    "city_abbreviations": {r["abbreviation"]: (r["city"],) for r in DEFAULTS["city_abbreviations"]},
    "talkgroup_codes": {(r["kind"], r["code"]): (r["name"],) for r in DEFAULTS["talkgroup_codes"]},
}

# ------------------------------------------------------------------------ patterns

TOKEN = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ&]+(?:-[0-9A-Za-zÀ-ÖØ-öø-ÿ&]+)*")
# Letters with dots in between: "t.h.v.", "o.a.", "O.V.D."
DOTTED = re.compile(r"(?<![\w.])(?:[A-Za-z]{1,3}\.)+[A-Za-z]{1,3}\.?(?![\w])")
# A time: "00:00", "7:45", "23:59:30"; with a dot only when "uur" follows ("14.05 uur")
TIME = re.compile(
    r"(?<![\d:.,])([01]?\d|2[0-3]):([0-5]\d)(?::([0-5]\d))?(?![\d:])"
    r"|(?<![\d:.,])([01]?\d|2[0-3])\.([0-5]\d)\s*(uur)\b",
    re.IGNORECASE,
)
POSTCODE = re.compile(r"\b(\d{4})\s?([A-Z]{2})\b")
DECIMAL = re.compile(r"(?<![\d.,])(\d{1,3})[.,](\d)(?![\d]|[.,]\d)")
LETTERS_DIGITS = re.compile(r"([A-Z]+)(\d+)")
TALKGROUP = re.compile(r"([A-Z])([A-Z]{2})-(\d{1,4})")
LEADING_PRIO = re.compile(r"\s*[A-Za-z]\s?\d\b")


def spell(token: str) -> str:
    """Letter by letter and digit by digit: "BRT-01" -> "B R T 0 1"."""
    return " ".join(c for c in token if c.isalnum())


def number(digits: str, in_code: bool = False) -> str:
    """A number as the voice should say it: as a number, or digit by digit when it is a
    reference (5 or more digits), starts with 0, or is part of a code."""
    if len(digits) > 1 and (in_code or len(digits) >= 5 or digits.startswith("0")):
        return spell(digits)
    return digits


def clean_meaning(text: str) -> str:
    """An abbreviation's meaning as spoken: "Adembescherming(svoertuig)" -> one word,
    an explanation in brackets ("Tankwagen (BRW)") left out."""
    spoken = re.sub(r"(\w)\(([^)\s]*)\)", r"\1\2", text)
    spoken = re.sub(r"\s*\([^)]*\)", "", spoken)
    spoken = re.sub(r"\s+", " ", spoken).strip()
    return spoken or text.strip()


def _code_like(token: str) -> bool:
    """A code is in capitals ("OMS", "BRT-01") or has at least two ("OvD"); "Brand" or "hmp" is a word."""
    letters = [c for c in token if c.isalpha()]
    if not letters:
        return False
    upper = sum(c.isupper() for c in letters)
    return upper == len(letters) or upper >= 2


def is_default(table: str, row: dict) -> bool:
    """Whether a row is a built-in default as it was added (not changed by you)."""
    index = _DEFAULT_INDEX.get(table, {})
    if table == "tts_replacements":
        return index.get(row.get("pattern")) == (row.get("replacement"),)
    if table == "abbreviations":
        return index.get(row.get("abbreviation")) == (row.get("full_text"),)
    if table == "city_abbreviations":
        return index.get(row.get("abbreviation")) == (row.get("city"),)
    if table == "talkgroup_codes":
        return index.get((row.get("kind"), row.get("code"))) == (row.get("name"),)
    return False


class Speech:
    """Turns incident texts into spoken text with the tables in the database."""

    def __init__(self, database: Database):
        self.db = database
        self._rules: list[tuple[re.Pattern, str, str]] = []
        self._abbreviations: dict[str, str] = {}
        self._cities: dict[str, str] = {}
        self._disciplines: dict[str, str] = {}
        self._regions: dict[str, str] = {}
        self.seed()

    def seed(self) -> None:
        """Add the defaults (only rows never added before) and load everything."""
        added = self.db.seed_speech(DEFAULTS)
        if any(added.values()):
            logger.info("Added speech defaults: " + ", ".join(f"{n} {t}" for t, n in added.items() if n))
        self.reload()

    def reload(self) -> None:
        """Load the tables again (after a change on the Data tab): no restart needed."""
        rules = []
        for row in self.db.speech_rows("tts_replacements"):
            if not (row["replacement"] or "").strip():
                logger.warning(f"Speech rule {row['pattern']!r} has no replacement and would delete text: not used")
                continue
            try:
                rules.append((re.compile(row["pattern"], re.IGNORECASE), row["replacement"], row["pattern"]))
            except re.error as e:
                logger.warning(f"Speech rule {row['pattern']!r} is not a valid pattern ({e}): not used")
        self._rules = rules
        self._abbreviations = self.db.abbreviation_map()
        self._cities = {r["abbreviation"]: r["city"] for r in self.db.speech_rows("city_abbreviations")}
        groups = self.db.speech_rows("talkgroup_codes")
        self._disciplines = {r["code"]: r["name"] for r in groups if r["kind"] == "discipline"}
        self._regions = {r["code"]: r["name"] for r in groups if r["kind"] == "region"}

    # ----------------------------------------------------------------- lookup

    def meaning(self, code: str) -> str | None:
        """What an abbreviation is spoken as, or None when it is spelled out: not in the
        list, or a meaning with "/" (two meanings, e.g. "Mobiele Communicatie / Commando Unit")."""
        text = self._abbreviations.get(code.upper())
        if text is None or "/" in text:
            return None
        return clean_meaning(text)

    def talkgroup(self, token: str) -> str | None:
        """ "BRT-01" -> "gespreksgroep Brandweer Rotterdam 0 1", None for an unknown code."""
        m = TALKGROUP.fullmatch(token.upper())
        if not m:
            return None
        discipline, region = self._disciplines.get(m.group(1)), self._regions.get(m.group(2))
        if not discipline or not region:
            return None
        return f"gespreksgroep {discipline} {region} {number(m.group(3), in_code=True)}"

    def _dotted(self, token: str) -> tuple[str, str]:
        """(spoken, kind) for letters with dots: "t.h.v." -> "ter hoogte van".

        Looked up with its dots ("T.H.V.", also without the last dot). Only one in capitals
        ("O.V.D.") is also looked up without dots: a lowercase "t.h.v." is a Dutch shortening,
        not the code THV (Technische Hulpverlening). Unknown ones are spelled out.
        """
        key = token.upper()
        for candidate in (key, key if key.endswith(".") else key + ".", key.rstrip(".")):
            if "." in candidate and (meaning := self.meaning(candidate)):
                return meaning, "abbreviation"
        if token.isupper() and (meaning := self.meaning(key.replace(".", ""))):
            return meaning, "abbreviation"
        return spell(token), "spelled"

    def _token(self, token: str) -> tuple[str, str] | None:
        """(spoken, kind) for a word that is spoken differently, None when it stays as it is."""
        if len(token) == 1:
            return None
        if token.isdigit():
            spoken = number(token)
            return (spoken, "number") if spoken != token else None
        if not _code_like(token):
            return None
        key = token.upper()
        if key in self._abbreviations:
            meaning = self.meaning(key)
            return (meaning, "abbreviation") if meaning else (spell(token), "uncertain")
        if group := self.talkgroup(key):
            return group, "talkgroup"
        if key in self._cities:
            return self._cities[key], "place"
        m = LETTERS_DIGITS.fullmatch(key)
        if m:
            letters, digits = m.groups()
            meaning = self.meaning(letters) if len(letters) > 1 else None
            spoken = f"{meaning or spell(letters)} {number(digits)}"
            return spoken, "abbreviation" if meaning else "spelled"
        if "-" in token:
            parts = []
            for part in token.split("-"):
                if part.isdigit():
                    parts.append(number(part, in_code=True))
                else:
                    result = self._token(part)
                    parts.append(result[0] if result else part)
            return " ".join(parts), "code"
        if key.isalpha() and len(key) >= 5:
            # A word in capitals: lowercase, so the voice reads it as a word
            return token.lower(), "word"
        return spell(token), "spelled"

    # ------------------------------------------------------------------ speak

    def explain(self, body: str | None, prio: str | None = None) -> dict:
        """The spoken text and each change made to get it: {text, spoken, steps}."""
        text = (body or "").strip()
        steps: list[dict] = []
        # The priority from the incident when the text doesn't start with one
        prio = (prio or "").strip()
        if prio and re.fullmatch(r"[A-Za-z]\s?\d", prio) and not LEADING_PRIO.match(text):
            text = f"{prio.upper()}. {text}" if text else prio.upper()
            steps.append({"kind": "priority", "from": "", "to": prio.upper(), "note": "priority of the incident"})

        for pattern, replacement, source in self._rules:

            def apply(m: re.Match, replacement=replacement, source=source) -> str:
                try:
                    out = m.expand(replacement)
                except (re.error, IndexError):
                    return m.group(0)
                if not out.strip():
                    return m.group(0)
                if out != m.group(0):
                    steps.append({"kind": "rule", "from": m.group(0).strip(), "to": out.strip(), "note": source})
                # Keep words apart: "bon123" -> "bonnummer 123"
                return f" {out} "

            text = pattern.sub(apply, text)

        def dotted(m: re.Match) -> str:
            token = m.group(0)
            spoken, kind = self._dotted(token)
            steps.append({"kind": kind, "from": token, "to": spoken, "note": ""})
            return f" {spoken} "

        def clock(m: re.Match) -> str:
            # "00:00" -> "0 uur 0" (nul uur nul), "14:05" -> "14 uur 5": how a Dutch time is
            # said, and never digits with a stray colon
            if m.group(1) is not None:
                hour, minute, second = m.group(1), m.group(2), m.group(3)
            else:
                hour, minute, second = m.group(4), m.group(5), None
            out = f"{int(hour)} uur {int(minute)}"
            if second is not None:
                out += f" en {int(second)} seconden"
            steps.append({"kind": "time", "from": m.group(0), "to": out, "note": "time"})
            return out

        def postcode(m: re.Match) -> str:
            out = f"{spell(m.group(1))} {spell(m.group(2))}"
            steps.append({"kind": "postcode", "from": m.group(0), "to": out, "note": "postal code"})
            return out

        def decimal(m: re.Match) -> str:
            out = f"{m.group(1)} komma {m.group(2)}"
            steps.append({"kind": "number", "from": m.group(0), "to": out, "note": "decimal number"})
            return out

        text = DOTTED.sub(dotted, text)
        text = TIME.sub(clock, text)
        text = POSTCODE.sub(postcode, text)
        text = DECIMAL.sub(decimal, text)

        def word(m: re.Match) -> str:
            token = m.group(0)
            result = self._token(token)
            if not result or result[0] == token:
                return token
            spoken, kind = result
            steps.append({"kind": kind, "from": token, "to": spoken, "note": ""})
            return spoken

        text = TOKEN.sub(word, text)
        text = re.sub(r"\s+([.,:;!?)])", r"\1", text)
        text = re.sub(r"\s+", " ", text).strip()
        return {"text": body or "", "spoken": text, "steps": steps}

    def speak(self, body: str | None, prio: str | None = None) -> str:
        return self.explain(body, prio)["spoken"]
