"""The factory lists: CSV files in src/defaults/, one per table, in the Data tab's CSV format.

They are only read to fill the database (see the Database class): at the first start of
a version, and never again after that. At runtime the database tables are the only source.

- <table>.csv: the built-in rows of a table (UTF-8, header row = the table's columns).
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
