"""1.4.16: the parser's lookup lists are database tables; built-in lists are seeded once."""

import sqlite3
from datetime import UTC, datetime

import pytest

from src import default_data, message_fields, vehicle_types
from src.database import Database
from src.message import P2000Message
from src.parser import Parser, is_street_name_word, looks_like_street
from src.tts import SpeechRules

NEW_TABLES = [
    "discipline_abbreviations",
    "incident_rules",
    "priority_rules",
    "parser_words",
    "vehicle_regions",
    "region_provinces",
    "vehicle_type_codes",
]


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "meldkamer.sqlite3"))
    yield d
    d.close()


def _id(db, table, **match):
    items = db.list_lookup(table, per_page=500)["items"]
    return next(r["id"] for r in items if all(r[k] == v for k, v in match.items()))


def _parse(db, body):
    msg = P2000Message(timestamp=datetime.now(UTC), body=body, raw_message=body)
    msg.priority = Parser(db)._extract_priority(body)
    return msg


def test_every_table_is_seeded_once(db):
    for table in NEW_TABLES:
        assert db.list_lookup(table)["total"] > 0, table
    counts = {t: db.list_lookup(t)["total"] for t in NEW_TABLES}
    db.seed_all_defaults()
    _new_version(db)
    db.seed_all_defaults()
    assert {t: db.list_lookup(t)["total"] for t in NEW_TABLES} == counts


def _new_version(db):
    """As if a new version starts: its files haven't been seeded yet."""
    db.cursor.execute("DELETE FROM speech_seeded WHERE kind = 'defaults'")
    db._commit()


def test_abbreviations_and_ignore_text_deletions_stay_deleted(tmp_path):
    path = str(tmp_path / "meldkamer.sqlite3")
    db = Database(path)
    db.delete_abbreviation(db.list_abbreviations(search="GHOR")["items"][0]["id"])
    mob = next(r for r in db.list_ignore_text()["items"] if r["pattern"] == "*MOB*")
    db.delete_ignore_text(mob["id"])
    db.close()

    _new_version(db := Database(path))
    db.close()
    db = Database(path)
    assert not [r for r in db.list_abbreviations(search="GHOR")["items"] if r["abbreviation"] == "GHOR"]
    defaults = [r["pattern"] for r in default_data.read("ignore_text")]
    assert [r["pattern"] for r in db.list_ignore_text()["items"]] == [p for p in defaults if p != "*MOB*"]
    db.close()


def test_pm_gets_its_sourced_meaning_unless_edited(tmp_path):
    path = str(tmp_path / "meldkamer.sqlite3")
    db = Database(path)
    # An install from before 1.4.16: the old meanings, PM-90 edited by the user
    db.import_abbreviations(
        [
            {"abbreviation": "PM", "full_text": "Personeel / Materieelwagen"},
            {"abbreviation": "PM-90", "full_text": "eigen tekst"},
        ]
    )
    db.cursor.execute("DELETE FROM speech_seeded WHERE kind = 'migration'")
    db._commit()
    _new_version(db)
    db.seed_all_defaults()
    meanings = {r["abbreviation"]: r["full_text"] for r in db.list_abbreviations(search="PM")["items"]}
    assert meanings["PM"] == "Personeel- en materieelvoertuig"
    assert meanings["PM-90"] == "eigen tekst"
    assert {r["abbreviation"]: r["full_text"] for r in default_data.read("abbreviations")}["PM"] == meanings["PM"]
    db.close()


def test_edits_are_used_right_away_and_survive_a_restart(tmp_path):
    path = str(tmp_path / "meldkamer.sqlite3")
    db = Database(path)
    # A new incident type before the others, a street ending, a vehicle type code, a region
    db.save_lookup(
        "incident_rules", {"position": 1, "kind": "oefening", "label": "Oefening", "pattern": r"\boefening\b"}
    )
    db.save_lookup("parser_words", {"kind": "street_suffix", "word": "Erf"})
    db.save_lookup("vehicle_type_codes", {"code": "WTS", "category": "brandweer_slangenwagen"})
    db.save_lookup("vehicle_regions", {"prefix": "99", "region": "Testregio"})
    db.save_lookup("region_provinces", {"region": "Testregio", "province": "Utrecht"})
    db.save_lookup(
        "priority_rules", {"level": 4, "pattern": r"PRIO\s?4|^P\s?4|\bP\s?5\b"}, _id(db, "priority_rules", level=4)
    )
    db.save_lookup(
        "discipline_abbreviations",
        {"code": "B1", "discipline": "Ambulance", "full_text": "Besteld vervoer, zware zorg"},
        _id(db, "discipline_abbreviations", code="B1"),
    )

    def check():
        assert message_fields.classify_incident("P 2 Oefening brand")[0] == "oefening"
        assert looks_like_street("Kerkerf")
        assert vehicle_types.classify_text("P 1 WTS 09-1234") == "brandweer_slangenwagen"
        assert message_fields.region_of_vehicle("99-100") == "Testregio"
        assert message_fields.province_of_region("Testregio") == "Utrecht"
        assert _parse(db, "P 5 Test").priority == 4
        tags = {t["abbreviation"]: t["full_text"] for t in db.find_abbreviations_in_text("B1 Rit 123", "Ambulance")}
        assert tags["B1"] == "Besteld vervoer, zware zorg"

    check()
    db.close()
    db = Database(path)
    check()
    db.close()


def test_invalid_rows_are_refused(db):
    with pytest.raises(ValueError):
        db.save_lookup("incident_rules", {"position": 1, "kind": "x", "label": "X", "pattern": "("})
    with pytest.raises(ValueError):
        db.save_lookup("priority_rules", {"level": "een", "pattern": "x"})
    with pytest.raises(ValueError):
        db.save_lookup("parser_words", {"kind": "anything", "word": "x"})
    with pytest.raises(ValueError):
        db.save_lookup("vehicle_type_codes", {"code": "XX", "category": "raket"})
    with pytest.raises(ValueError):
        db.save_lookup("vehicle_regions", {"prefix": "A1", "region": "x"})


@pytest.mark.parametrize("table", NEW_TABLES)
def test_csv_round_trip(tmp_path, db, table):
    exported = db.export_lookup_csv(table)
    other = Database(str(tmp_path / "other.sqlite3"))
    other.clear_table(table)
    result = other.import_lookup_csv(table, exported, replace=True)
    assert result["skipped"] == 0
    assert other.export_lookup_csv(table) == exported
    other.close()


def test_backup_and_restore(tmp_path, db):
    db.save_lookup("vehicle_regions", {"prefix": "99", "region": "Testregio"})
    backup = str(tmp_path / "backup.sqlite3")
    db.backup_to(backup)
    db.delete_lookup("vehicle_regions", _id(db, "vehicle_regions", prefix="99"))
    assert message_fields.region_of_vehicle("99-1") == ""
    db.replace_with(backup)
    assert message_fields.region_of_vehicle("99-1") == "Testregio"


def test_restoring_an_old_backup_seeds_the_tables(tmp_path, db):
    old = str(tmp_path / "old.sqlite3")
    Database(old).close()
    conn = sqlite3.connect(old)
    conn.executescript("".join(f"DROP TABLE {t};" for t in NEW_TABLES) + "DELETE FROM speech_seeded;")
    conn.commit()
    conn.close()
    db.replace_with(old)
    assert db.list_lookup("incident_rules")["total"] == len(default_data.read("incident_rules"))
    assert message_fields.classify_incident("P 1 Woningbrand")[0] == "fire"
    assert is_street_name_word("van")


def test_speech_defaults(db):
    speech = SpeechRules(db)

    def say(body):
        return speech.speak(P2000Message(timestamp=datetime.now(UTC), body=body, raw_message=body))

    assert "rijbaan links" in say("P 1 VKO A12 hmp 45,2 Li")
    assert "A 12 rijbaan rechts 45,2" in say("P 1 VKO A12 Re 45,2")
    assert "Li" in say("P 2 Li Kerkstraat")  # not after a hectometer marker: as written
    assert "O M" in say("P 2 assistentie OM Kerkstraat")
    assert " om " in say("P 2 brand om de hoek")
