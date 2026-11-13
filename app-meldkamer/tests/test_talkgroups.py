"""Talk groups (BRT-01) and place-name codes from database tables: seeded once, editable,
in CSV export/import and in backup/restore."""

import csv
import io
from datetime import UTC, datetime

import pytest

from src import default_data
from src.database import Database
from src.message import P2000Message
from src.parser import Parser, city_shortname_tags
from src.tts import SpeechRules


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "meldkamer.sqlite3"))
    yield d
    d.close()


@pytest.fixture
def speech(db):
    return SpeechRules(db)


def spoken(speech, body: str, discipline: str = "Brandweer") -> str:
    return speech.speak(P2000Message(timestamp=datetime.now(UTC), body=body, raw_message=body, discipline=discipline))


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("BRT-01", "gespreksgroep Brandweer Rotterdam 0 1"),
        ("BAD-04", "gespreksgroep Brandweer Amsterdam 0 4"),
        ("BUT-13", "gespreksgroep Brandweer Utrecht 1 3"),
        ("SNH-1525", "gespreksgroep Multidisciplinaire samenwerking Noord-Holland 1 5 2 5"),
        ("BGV-417", "gespreksgroep Brandweer Gooi en Vechtstreek 4 1 7"),
        ("BNH-914", "gespreksgroep Brandweer Noord-Holland 9 1 4"),
        ("BXX-01", "B X X 0 1"),
    ],
)
def test_talkgroups_are_spoken_from_the_nipv_coding(speech, code, expected):
    assert spoken(speech, f"P 1 {code} Brand woning") == f"Prio 1 {expected} Brand woning"


def test_talkgroup_tag_in_the_message_detail(db):
    tags = {t["abbreviation"]: t["full_text"] for t in db.find_abbreviations_in_text("P 1 BRT-01 Brand woning")}
    assert tags["BRT-01"] == "Gespreksgroep Brandweer Rotterdam 01"


def test_seeded_once_and_your_edits_survive_a_restart(tmp_path):
    path = str(tmp_path / "meldkamer.sqlite3")
    db = Database(path)
    speech = SpeechRules(db)
    assert db.list_lookup("talkgroup_codes")["total"] == len(default_data.read("talkgroup_codes"))
    assert db.list_lookup("city_abbreviations")["total"] > 2000
    rt = db.list_lookup("talkgroup_codes", search="Rotterdam")["items"]
    rt = next(r for r in rt if r["code"] == "RT")
    db.save_lookup("talkgroup_codes", {"kind": "region", "code": "RT", "name": "Rotterdam-Rijnmond"}, rt["id"])
    bd = next(r for r in db.list_lookup("talkgroup_codes", search="Brandweer")["items"] if r["code"] == "B")
    utrech = db.list_lookup("city_abbreviations", search="UTRECH")["items"][0]
    db.delete_lookup("city_abbreviations", utrech["id"])
    hmp_rule = db.list_tts_replacements(search="hind")["items"][0]
    db.delete_tts_replacement(hmp_rule["id"])
    # Used right away, without a restart
    assert spoken(speech, "P 1 BRT-01 Brand") == "Prio 1 gespreksgroep Brandweer Rotterdam-Rijnmond 0 1 Brand"
    assert db.city_from_abbreviation("UTRECH") == ""
    db.close()

    db = Database(path)
    speech = SpeechRules(db)
    assert spoken(speech, "P 1 BRT-01 Brand") == "Prio 1 gespreksgroep Brandweer Rotterdam-Rijnmond 0 1 Brand"
    assert db.city_from_abbreviation("UTRECH") == ""
    assert not db.list_tts_replacements(search="hind")["items"]
    assert any(r["id"] == bd["id"] for r in db.list_lookup("talkgroup_codes", search="Brandweer")["items"])
    db.close()


def test_parser_and_tags_use_the_place_table(db):
    assert db.city_from_abbreviation("SGRAVH") == "'s-Gravenhage"
    db.save_lookup("city_abbreviations", {"abbreviation": "ztest", "city": "Zandtest"})
    assert db.city_from_abbreviation("ZTEST") == "Zandtest"
    assert {"abbreviation": "ZTEST", "full_text": "Zandtest"} in city_shortname_tags("P 2 Brand Dorpsstraat ZTEST", db)
    body = "P 2 BRT-01 Brand Dorpsstraat ZTEST 123456"
    msg = P2000Message(timestamp=datetime.now(UTC), body=body, raw_message=body)
    Parser(db)._extract_address(msg)
    assert msg.city == "Zandtest"
    assert "Zandtest" in msg.speakable_body


def test_talk_group_field_comes_from_the_table(db):
    from src import message_fields

    fields = message_fields.derived_fields("P 1 BRT-01 Brand SNH-1525 BXX-02", db.decode_talkgroup)
    assert fields["talk_group"] == "BRT-01"
    assert fields["talk_group_name"] == "Brandweer Rotterdam 01"
    assert message_fields.derived_fields("P 1 BXX-02 Brand", db.decode_talkgroup)["talk_group"] == ""
    assert message_fields.derived_fields("P 2 BUT-13 Brand", db.decode_talkgroup)["talk_group_name"] == (
        "Brandweer Utrecht 13"
    )


def test_invalid_talkgroup_codes_are_refused(db):
    with pytest.raises(ValueError):
        db.save_lookup("talkgroup_codes", {"kind": "region", "code": "ABC", "name": "x"})
    with pytest.raises(ValueError):
        db.save_lookup("talkgroup_codes", {"kind": "team", "code": "A", "name": "x"})


@pytest.mark.parametrize("table", ["city_abbreviations", "talkgroup_codes"])
def test_csv_export_and_import(tmp_path, db, table):
    exported = db.export_lookup_csv(table)
    other = Database(str(tmp_path / "other.sqlite3"))
    other.clear_table(table)
    result = other.import_lookup_csv(table, exported, replace=True)
    assert result["skipped"] == 0
    assert other.export_lookup_csv(table) == exported
    rows = list(csv.reader(io.StringIO(exported)))
    assert rows[0] == list(Database.LOOKUP_TABLES[table]["cols"])
    other.close()


def test_backup_and_restore(tmp_path, db, speech):
    rt = next(r for r in db.list_lookup("talkgroup_codes", search="Rotterdam")["items"] if r["code"] == "RT")
    db.save_lookup("talkgroup_codes", {"kind": "region", "code": "RT", "name": "Rotterdam-Rijnmond"}, rt["id"])
    db.save_lookup("city_abbreviations", {"abbreviation": "ZTEST", "city": "Zandtest"})
    backup = str(tmp_path / "backup.sqlite3")
    db.backup_to(backup)
    db.save_lookup("talkgroup_codes", {"kind": "region", "code": "RT", "name": "iets anders"}, rt["id"])
    db.delete_lookup("city_abbreviations", db.list_lookup("city_abbreviations", search="ZTEST")["items"][0]["id"])

    assert Database.check_backup(backup) is None
    db.replace_with(backup)
    speech.seed()
    assert spoken(speech, "P 1 BRT-01 Brand") == "Prio 1 gespreksgroep Brandweer Rotterdam-Rijnmond 0 1 Brand"
    assert db.city_from_abbreviation("ZTEST") == "Zandtest"
