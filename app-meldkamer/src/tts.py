"""Text-to-speech: turn a P2000 message into a speakable sentence.

The same spoken text is used for Home Assistant sensors, MQTT, speaker
announcements and the dashboard's browser speech.
"""

import logging
import re

from .database import Database
from .message import P2000Message
from .talkgroups import TALKGROUP

logger = logging.getLogger(__name__)

# The replacement rules are the tts_replacements table (built-in rows in
# src/defaults/tts_replacements.csv, applied case-insensitively in id order). Talk groups
# (BRT-01) are not rules: speak() decodes them from the talkgroup_codes table.


def _spell(text: str) -> str:
    """Letter by letter and digit by digit: "BXX-01" -> "B X X 0 1"."""
    return " ".join(c for c in text if c.isalnum())


# B1/B2 mean "Brandweer ... spoed" for fire, but an unrelated, always
# non-urgent "besteld vervoer" code for ambulance - consumed before the rules
# run, so the generic fire-meaning rule never sees a B1/B2 on an ambulance message
_AMBULANCE_B_CODE = re.compile(r"\bB\s?[12]\b", re.IGNORECASE)
# A1/A2/B1/B2 already speak "Ambulance ... spoed"; collapse a following bare
# "Ambulance" (e.g. from the AMBU rule) so it isn't said twice
_DOUBLE_AMBULANCE = re.compile(r"\bAmbulance\b((?:\s+(?:met|zonder)\s+spoed)?)\s+Ambulance\b", re.IGNORECASE)
# Same for Brand - e.g. "(Zeer gr. BR) BR bijeenkomst" both expand to "Brand"
_DOUBLE_BRAND = re.compile(r"\bBrand\b(\W{0,3})Brand\b\s*", re.IGNORECASE)


class SpeechRules:
    """TTS replacement rules from the database, applied to messages."""

    def __init__(self, database: Database):
        self.database = database
        self._rules: list[dict] = []
        self.seed()

    def seed(self) -> None:
        """Fill the tables from src/defaults/ when this version hasn't yet (after a restore or
        reset), and load the rules."""
        self.database.seed_all_defaults()
        self.database.load_lookups()
        self.reload()

    def reload(self) -> None:
        """Reload the enabled rules from the database (after edits in the web UI)."""
        self._rules = self.database.get_all_tts_replacements()
        logger.info(f"Loaded {len(self._rules)} TTS replacement(s)")

    def speak(self, msg: P2000Message) -> str:
        """Return the spoken form of a message.

        Case-insensitive, like the patterns are edited in the web UI (an inline
        (?i) can't be relied on, so the flag is always applied).
        """
        text = msg.speakable_body or msg.body
        if "ambulance" in msg.discipline.lower():
            text = _AMBULANCE_B_CODE.sub("Ambulance besteld vervoer", text)
        for rule in self._rules:
            try:
                text = re.sub(rule["pattern"], rule["replacement"], text, flags=re.IGNORECASE)
            except re.error as e:
                logger.warning(f"Invalid TTS regex pattern '{rule['pattern']}': {e}")
        text = TALKGROUP.sub(self._talkgroup, text)
        text = _DOUBLE_AMBULANCE.sub(r"Ambulance\1", text)
        return _DOUBLE_BRAND.sub(r"Brand\1", text).strip()

    def _talkgroup(self, m: re.Match) -> str:
        """ "BRT-01" -> "gespreksgroep Brandweer Rotterdam 0 1" (the number digit by digit);
        letters that aren't in the talkgroup_codes table are spelled out, never guessed."""
        group = self.database.decode_talkgroup(m.group(0))
        if not group:
            return _spell(m.group(0))
        discipline, region, number = group
        return f"gespreksgroep {discipline} {region} {_spell(number)}"
