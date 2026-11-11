"""The "Import list" button: the built-in abbreviation list into the database.

The list is src/defaults/abbreviations.csv (the public P2000 list from p2000.bommel.net
plus sourced meanings), the same file the abbreviations table is first filled from.
"""

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

LIST = Path(__file__).resolve().parent / "defaults" / "abbreviations.csv"


def import_abbreviations(database, replace: bool = True) -> int:
    """Import the built-in list. replace=True gives codes already in the database the list's
    meaning again; codes you added yourself stay. Returns the number imported."""
    with LIST.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    count = database.import_abbreviations(rows, replace=replace)
    logger.info(f"Imported {count} abbreviations")
    return count
