"""The province_codes table: seeded once, editable, used without a restart, in CSV and backups."""

import pytest

from src.database import Database
from src.message_fields import DEFAULT_PROVINCE_CODES


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "meldkamer.sqlite3"))
    yield d
    d.close()


def _row(db, code):
    return next(r for r in db.list_lookup("province_codes", search=code)["items"] if r["code"] == code)


def test_seeded_once(db):
    assert db.list_lookup("province_codes")["total"] == len(DEFAULT_PROVINCE_CODES)
    assert db.seed_defaults("province_codes", DEFAULT_PROVINCE_CODES) == 0
    assert db.province_for_code("nb") == "Noord-Brabant"
    # L only counts in the place codes ("Beek L"), not as a word after a town
    assert db.province_for_code("L") == ""
    assert db.split_province_code("Beek L") == ("Beek", "Limburg")


def test_edits_are_used_right_away_and_survive_a_restart(tmp_path):
    path = str(tmp_path / "meldkamer.sqlite3")
    db = Database(path)
    nb = _row(db, "NB")["id"]
    db.save_lookup("province_codes", {"code": "NB", "province": "Brabant", "abbreviation_only": "0"}, nb)
    db.delete_lookup("province_codes", _row(db, "GLD")["id"])
    db.save_lookup("province_codes", {"code": "Lb", "province": "Limburg", "abbreviation_only": "1"})
    assert db.province_for_code("NB") == "Brabant"
    assert db.province_for_code("GLD") == ""
    assert db.split_province_code("Velp GLD") == ("Velp GLD", "")
    assert db.province_for_code("LB") == ""
    assert db.split_province_code("Beek LB") == ("Beek", "Limburg")
    db.close()

    db = Database(path)
    assert db.province_for_code("NB") == "Brabant"
    assert db.province_for_code("GLD") == ""
    assert db.split_province_code("Beek LB") == ("Beek", "Limburg")
    db.close()


def test_invalid_rows_are_refused(db):
    with pytest.raises(ValueError):
        db.save_lookup("province_codes", {"code": "N1", "province": "x"})
    with pytest.raises(ValueError):
        db.save_lookup("province_codes", {"code": "NB", "province": ""})


def test_csv_round_trip(tmp_path, db):
    exported = db.export_lookup_csv("province_codes")
    assert exported.splitlines()[0] == "code,province,abbreviation_only"
    other = Database(str(tmp_path / "other.sqlite3"))
    other.clear_table("province_codes")
    result = other.import_lookup_csv("province_codes", exported, replace=True)
    assert result == {"imported": len(DEFAULT_PROVINCE_CODES), "skipped": 0}
    assert other.export_lookup_csv("province_codes") == exported
    assert other.province_for_code("L") == ""
    other.close()


def test_place_code_with_province_is_spoken_in_full(db):
    from datetime import UTC, datetime

    from src.message import P2000Message
    from src.parser import Parser

    db.upsert_place("Velp", "", "Noord-Brabant", 51.748, 5.714)
    db.upsert_place("Velp", "", "Gelderland", 51.995, 5.975)
    body = "P 2 BR container VELPNB 12345"
    msg = P2000Message(timestamp=datetime.now(UTC), body=body, raw_message=body)
    Parser(db)._extract_address(msg)
    assert (msg.city, msg.province) == ("Velp", "Noord-Brabant")
    # Nothing of the text is left out of the spoken form
    assert "Velp Noord-Brabant" in msg.speakable_body


def test_backup_and_restore(tmp_path, db):
    db.save_lookup("province_codes", {"code": "NB", "province": "Brabant"}, _row(db, "NB")["id"])
    backup = str(tmp_path / "backup.sqlite3")
    db.backup_to(backup)
    db.save_lookup("province_codes", {"code": "NB", "province": "iets anders"}, _row(db, "NB")["id"])
    db.replace_with(backup)
    assert db.province_for_code("NB") == "Brabant"
