"""1.4.17: the built-in lists are CSV files in src/defaults/, read only to fill the database."""

import csv
import re
from pathlib import Path

import pytest

from src import default_data
from src.database import Database

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "meldkamer.sqlite3"))
    yield d
    d.close()


def _forget_defaults_marker(db):
    """A database from 1.4.16 or older has no record of which files were seeded."""
    db.cursor.execute("DELETE FROM speech_seeded WHERE kind = 'defaults'")
    db._commit()


@pytest.mark.parametrize(
    "text",
    [
        # A place code, abbreviations, talk group names, an ignore text, a vehicle type code,
        # region names, a TTS rule, an incident type, an address word, a discipline meaning
        "AARLRL",
        "Adembescherming",
        "Personeel- en materieelvoertuig",
        "Zeeland-West-Brabant",
        "Multidisciplinaire samenwerking",
        "*MOB*",
        '"TS-HV"',
        "Noord- en Oost-Gelderland",
        "hectometerpaal",
        "Liftopsluiting",
        "ambulancepost",
        "hoogcomplexe zorgvraag",
    ],
)
def test_no_lookup_lists_in_python_source(text):
    hits = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if text in p.read_text(encoding="utf-8")]
    assert not hits, f"{text!r} in {hits}"


def test_every_default_file_has_the_table_columns(db):
    for table in Database.DEFAULT_TABLES:
        path = default_data.DIR / f"{table}.csv"
        with path.open(encoding="utf-8", newline="") as f:
            header = next(csv.reader(f))
        spec = db._SEED_SPECS[table]
        assert set(spec["cols"]) <= set(header), table
        assert db.cursor.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == len(default_data.read(table)), table


def test_files_are_not_read_again_after_the_first_fill(db, monkeypatch):
    def fail(name):
        raise AssertionError(f"read {name}")

    monkeypatch.setattr(default_data, "read", fail)
    assert db.seed_all_defaults() == 0
    Database(db.db_path).close()


def test_deleted_defaults_stay_deleted_on_update(tmp_path):
    """A database of the previous version (tracked tables) with defaults the user deleted."""
    path = str(tmp_path / "meldkamer.sqlite3")
    db = Database(path)
    for table, _key in (
        ("talkgroup_codes", "code"),
        ("city_abbreviations", "abbreviation"),
        ("incident_rules", "kind"),
    ):
        row = db.list_lookup(table)["items"][0]
        db.delete_lookup(table, row["id"])
    gone = db.list_abbreviations(search="GHOR")["items"][0]
    db.delete_abbreviation(gone["id"])
    counts = {t: db.cursor.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in Database.DEFAULT_TABLES}
    _forget_defaults_marker(db)
    db.close()

    db = Database(path)
    assert {t: db.cursor.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in Database.DEFAULT_TABLES} == counts
    db.close()


def test_a_new_default_row_in_a_new_version_is_added(tmp_path, monkeypatch):
    path = str(tmp_path / "meldkamer.sqlite3")
    Database(path).close()
    real = default_data.read

    def with_new_row(name):
        rows = real(name)
        if name == "vehicle_regions":
            rows = [*rows, {"prefix": "26", "region": "Testregio"}]
        return rows

    monkeypatch.setattr(default_data, "read", with_new_row)
    monkeypatch.setattr(default_data, "signature", lambda: "next-version")
    db = Database(path)
    assert any(r["prefix"] == "26" for r in db.list_lookup("vehicle_regions", per_page=100)["items"])
    db.close()


def test_import_list_uses_the_shipped_file(db):
    from src import abbreviation_import

    row = db.list_abbreviations(search="GHOR")["items"][0]
    db.delete_abbreviation(row["id"])
    abbreviation_import.import_abbreviations(db, replace=True)
    assert db.list_abbreviations(search="GHOR")["items"]
    assert re.search(r"defaults[/\\]abbreviations\.csv$", str(abbreviation_import.LIST))
