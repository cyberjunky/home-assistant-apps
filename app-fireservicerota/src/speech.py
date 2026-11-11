"""The spoken text of an incident: the alarm text made readable for a text-to-speech voice.

Ported from the Meldkamer app (its tts.py and abbreviation list), with stricter rules
because firefighters act on what they hear:

- nothing is left out: every word, code and number of the alarm text is spoken, as
  itself or as its meaning;
- nothing is guessed: a code is only spoken as words when its meaning is certain;
  otherwise it is spelled out ("BRT-01" -> "B R T 0 1");
- numbers are spoken exactly ("hmp 67.4" -> "hectometerpaal 67 komma 4", reference
  numbers digit by digit).

The steps, in order (explain() returns each change it made):
1. replacement rules (regex, editable on the Data tab, applied in order);
2. shortenings with dots ("t.h.v."), times ("00:00" -> "0 uur 0"), postal codes and
   decimal numbers;
3. each word: abbreviations (editable), place-name abbreviations, letter codes with
   numbers, then unknown codes spelled out and long numbers digit by digit.

The same text goes to Home Assistant's tts.speak and to the dashboard's browser speech.
"""

import logging
import re

from .abbreviation_import import ABBREVIATIONS
from .city_abbreviations import ABBREVIATION_TO_CITY
from .database import Database

logger = logging.getLogger(__name__)

# Added to the database once (see Database.seed_speech): your changes are kept and a
# default you delete stays deleted. Applied case-insensitively, top to bottom.
# A replacement may never be empty: a rule translates, it doesn't delete.
DEFAULT_TTS_REPLACEMENTS = [
    # Priority at the start of the alarm text ("P 1 ...", "P1 ...", "A1 ..."). Only at the
    # start: elsewhere "A1" or "A2" is a motorway (handled by the road rule below)
    {"pattern": r"^\s*P\s?([1-5])\b", "replacement": r"Prio \1"},
    {"pattern": r"^\s*A\s?1\b", "replacement": "Ambulance met spoed"},
    {"pattern": r"^\s*A\s?2\b", "replacement": "Ambulance zonder spoed"},
    # Road numbers: "A2" or "N209" is spoken as a letter and a number
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

# Codes from the abbreviation list whose listed meaning is not certain in a FireServiceRota
# text; spelled out until you give them your own meaning on the Data tab:
# - priority codes: handled at the start of the text; elsewhere A1/A2 is a road, and B1/B2
#   mean different things for fire and ambulance
# - Meldkamer talk groups (BRT-01 etc.): the list maps them to a region, which is not
#   known to be right for a Brandweerrooster text
# - HV: the list says "Hulpverleningsvoertuig", but in an alarm text it is often the
#   incident class "Hulpverlening"
# Codes whose listed meaning has a "/" (two meanings, e.g. "AC") are spelled out too.
UNCERTAIN_CODES = frozenset(
    {"A1", "A2", "B1", "B2", "P1", "P2", "P3"}
    | {"BDH", "BNN", "BON", "BNH", "BLB", "BOB", "BAD", "BMD", "BRT", "BZB"}
    | {"HV"}
)
SHIPPED = {code.upper(): text for code, text in ABBREVIATIONS.items()}

# Short Dutch words, so a text in capitals ("BR WONING VAN DE ...") doesn't get them spelled
COMMON_WORDS = frozenset(
    [
        "AAN",
        "AF",
        "ALS",
        "BIJ",
        "DAN",
        "DAT",
        "DE",
        "DIE",
        "DIT",
        "EEN",
        "EN",
        "ER",
        "HET",
        "HIJ",
        "IN",
        "IS",
        "JA",
        "MET",
        "NA",
        "NAAR",
        "NEE",
        "NIET",
        "NOG",
        "OF",
        "OM",
        "ONDER",
        "OP",
        "OVER",
        "PER",
        "TE",
        "TER",
        "TOT",
        "UIT",
        "VAN",
        "VIA",
        "VOOR",
        "WEG",
        "ZIJ",
    ]
)

# Standard Dutch shortenings written with dots, not in the P2000 list; added to the
# abbreviations (stored as "T.H.V.") like the list, so you can change or delete them
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


class Speech:
    """Turns incident texts into spoken text with the rules and abbreviations in the database."""

    def __init__(self, database: Database):
        self.db = database
        self._rules: list[tuple[re.Pattern, str, str]] = []
        self._abbreviations: dict[str, str] = {}
        self.seed()

    def seed(self) -> None:
        """Add the built-in list and rules (only ones never added before) and load everything."""
        abbr, rules = self.db.seed_speech({**ABBREVIATIONS, **DUTCH_ABBREVIATIONS}, DEFAULT_TTS_REPLACEMENTS)
        if abbr or rules:
            logger.info(f"Added {abbr} abbreviation(s) and {rules} speech rule(s) to the database")
        self.reload()

    def reload(self) -> None:
        """Load the enabled rules and the abbreviations again (after a change on the Data tab)."""
        rules = []
        for row in self.db.enabled_rules():
            if not (row["replacement"] or "").strip():
                logger.warning(f"Speech rule {row['pattern']!r} has no replacement and would delete text: not used")
                continue
            try:
                rules.append((re.compile(row["pattern"], re.IGNORECASE), row["replacement"], row["pattern"]))
            except re.error as e:
                logger.warning(f"Speech rule {row['pattern']!r} is not a valid pattern ({e}): not used")
        self._rules = rules
        self._abbreviations = self.db.abbreviation_map()

    # ----------------------------------------------------------------- lookup

    def meaning(self, code: str) -> str | None:
        """What an abbreviation is spoken as, or None when it is spelled out (not in the
        list, or its listed meaning is not certain here and you didn't change it)."""
        key = code.upper()
        text = self._abbreviations.get(key)
        if text is None:
            return None
        if text == SHIPPED.get(key) and (key in UNCERTAIN_CODES or "/" in text):
            return None
        return clean_meaning(text)

    def _dotted(self, token: str) -> tuple[str, str]:
        """(spoken, kind) for letters with dots: "t.h.v." -> "ter hoogte van".

        Looked up with its dots ("T.H.V.", also without the last dot). Only one in capitals
        ("O.V.D.") is also looked up without dots: a lowercase "t.h.v." is a Dutch shortening,
        not the code THV (Technische Hulpverlening). Unknown ones are spelled out.
        """
        key = token.upper()
        for candidate in (key, key if key.endswith(".") else key + ".", key.rstrip(".")):
            if candidate in self._abbreviations and "." in candidate:
                meaning = self.meaning(candidate)
                if meaning:
                    return meaning, "abbreviation"
        if token.isupper():
            bare = key.replace(".", "")
            meaning = self.meaning(bare) if bare in self._abbreviations else None
            if meaning:
                return meaning, "abbreviation"
        return spell(token), "spelled"

    def _token(self, token: str, shouting: bool) -> tuple[str, str] | None:
        """(spoken, kind) for a word that is spoken differently, None when it stays as it is."""
        if len(token) == 1:
            return None
        if token.isdigit():
            spoken = number(token)
            return (spoken, "number") if spoken != token else None
        if not _code_like(token):
            return None
        key = token.upper()
        if shouting and key in COMMON_WORDS:
            return token.lower(), "word"
        if key in self._abbreviations:
            meaning = self.meaning(key)
            if meaning:
                return meaning, "abbreviation"
            return spell(token), "uncertain" if key in SHIPPED else "spelled"
        if key in ABBREVIATION_TO_CITY:
            return ABBREVIATION_TO_CITY[key], "place"
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
                    result = self._token(part, shouting)
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
        original = text

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

        def postcode(m: re.Match) -> str:
            out = f"{spell(m.group(1))} {spell(m.group(2))}"
            steps.append({"kind": "postcode", "from": m.group(0), "to": out, "note": "postal code"})
            return out

        def decimal(m: re.Match) -> str:
            out = f"{m.group(1)} komma {m.group(2)}"
            steps.append({"kind": "number", "from": m.group(0), "to": out, "note": "decimal number"})
            return out

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

        text = DOTTED.sub(dotted, text)
        text = TIME.sub(clock, text)
        text = POSTCODE.sub(postcode, text)
        text = DECIMAL.sub(decimal, text)

        shouting = any(c.isalpha() for c in original) and not any(c.islower() for c in original)

        def word(m: re.Match) -> str:
            token = m.group(0)
            result = self._token(token, shouting)
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
