"""The factory lists: CSV files in src/defaults/, one per table, in the Data tab's CSV format.

They are only read to fill the database (Database.seed_all_defaults): at the first start of
a version, and never again after that. At runtime the database tables are the only source.

- <table>.csv: the built-in rows of a table (UTF-8, header row = the table's columns).
- previous.csv: values older versions shipped (table, key, column, value, migration): a row
  still at such a value gets the current value, or is removed when the key is no longer
  shipped. Done once per entry; rows you changed are left alone.
- added_later.csv: keys a table got after older versions filled it without keeping track of
  what they added (table, key, version). Only those are added to such a table; the other
  shipped rows count as offered, so a row you deleted there stays deleted.
"""

import csv
import os
from pathlib import Path

DIR = Path(__file__).resolve().parent / "defaults"
CONFIG = Path(__file__).resolve().parent.parent / "config.yaml"


def read(name: str) -> list[dict[str, str]]:
    """The rows of src/defaults/<name>.csv as dicts; [] when there is no such file."""
    path = DIR / f"{name}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def signature() -> str:
    """Identifies this version's files without reading them: the app version plus each
    file's name and size. Seeding is skipped while it is unchanged."""
    version = "dev"
    try:
        for line in CONFIG.read_text(encoding="utf-8").splitlines():
            if line.startswith("version:"):
                version = line.split(":", 1)[1].strip().strip("\"'")
                break
    except OSError:
        pass
    files = sorted((p.name, os.path.getsize(p)) for p in DIR.glob("*.csv"))
    return version + ";" + ";".join(f"{name}:{size}" for name, size in files)
