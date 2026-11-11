"""0.2.3: the built-in lists are CSV files in src/defaults/, read only to fill the database."""

import csv
import re
from pathlib import Path

import pytest

from src import default_data
from src.database import SPEECH_TABLES, Database
from src.speech import Speech

SRC = Path(__file__).resolve().parent.parent / "src"


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "f.db"))
    Speech(d)
    yield d
    d.close()


@pytest.mark.parametrize(
    "text",
    # A place code, abbreviations, a dotted shortening, a sourced meaning, talk group names,
    # TTS rules
    [
        "AARLRL",
        "Adviseur Gevaarlijke Stoffen",
        "naar aanleiding van",
        "Calamiteiten Commando Kamer",
        "Personeel- en materieelvoertuig",
        "Multidisciplinaire samenwerking",
        "Zeeland-West-Brabant",
        "rijbaan links",
        "Ambulance met spoed",
        "incidentnummer",
    ],
)
def test_no_lookup_lists_in_python_source(text):
    hits = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if text in p.read_text(encoding="utf-8")]
    assert not hits, f"{text!r} in {hits}"


def test_every_default_file_is_in_the_data_tab_format(db):
    for table, spec in SPEECH_TABLES.items():
        with (default_data.DIR / f"{table}.csv").open(encoding="utf-8", newline="") as f:
            assert next(csv.reader(f)) == list(spec["cols"]), table
        assert db.speech_counts()[table] == len(default_data.read(table)), table


def test_files_are_not_read_again_after_the_first_fill(db, monkeypatch):
    def fail(name):
        raise AssertionError(f"read {name}")

    monkeypatch.setattr(default_data, "read", fail)
    assert db.seed_speech() == {}
    Speech(Database(db.db_path))


def _as_022(db):
    """The database as 0.2.2 left it: no file signature, no remembered default values."""
    db.conn.execute("DELETE FROM speech_seeded WHERE kind = 'defaults'")
    db.conn.execute("UPDATE speech_seeded SET value = NULL")
    db.conn.commit()


def test_deleted_defaults_stay_deleted_on_update(tmp_path):
    path = str(tmp_path / "f.db")
    db = Database(path)
    Speech(db)
    for table in SPEECH_TABLES:
        db.delete_speech(table, db.list_speech(table)["items"][0]["id"])
    counts = db.speech_counts()
    _as_022(db)
    db.close()

    db = Database(path)
    Speech(db)
    assert db.speech_counts() == counts
    db.close()


def test_defaults_are_marked_again_after_the_update(tmp_path):
    path = str(tmp_path / "f.db")
    db = Database(path)
    Speech(db)
    row = db.conn.execute("SELECT id FROM abbreviations WHERE abbreviation = 'TS'").fetchone()
    db.save_abbreviation("TS", "Tankautospuit van de post", row["id"])
    _as_022(db)
    db.close()

    db = Database(path)
    Speech(db)
    rows = {r["abbreviation"]: r for r in db.list_speech("abbreviations", per_page=500)["items"]}
    assert db.is_default("abbreviations", rows["AED"])
    assert not db.is_default("abbreviations", rows["TS"])
    db.close()


def test_a_new_default_row_in_a_new_version_is_added(tmp_path, monkeypatch):
    path = str(tmp_path / "f.db")
    db = Database(path)
    Speech(db)
    db.close()
    real = default_data.read

    def with_new_row(name):
        rows = real(name)
        return [*rows, {"abbreviation": "XQZ", "full_text": "nieuw"}] if name == "abbreviations" else rows

    monkeypatch.setattr(default_data, "read", with_new_row)
    monkeypatch.setattr(default_data, "signature", lambda: "next-version")
    db = Database(path)
    Speech(db)
    assert db.abbreviation_map()["XQZ"] == "nieuw"
    db.close()


def test_import_list_uses_the_shipped_file(db):
    from src import abbreviation_import

    row = db.conn.execute("SELECT id FROM abbreviations WHERE abbreviation = 'T.H.V.'").fetchone()
    db.delete_speech("abbreviations", row["id"])
    abbreviation_import.import_abbreviations(db, replace=True)
    assert db.abbreviation_map()["T.H.V."] == "ter hoogte van"
    assert re.search(r"defaults[/\\]abbreviations\.csv$", str(abbreviation_import.LIST))
