"""The spoken text of incidents: rules, abbreviations, numbers, and the speech tables in the database."""

import re
import sqlite3

import pytest

from src.database import Database
from src.speech import DEFAULT_TTS_REPLACEMENTS, Speech
from tools.mock_ha import KINDS, STREETS


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "f.db"))
    yield d
    d.close()


@pytest.fixture
def speech(db):
    return Speech(db)


# (alarm text, incident prio, spoken text)
EXAMPLES = [
    ("P 1 BRT-01 Brand woning Biltstraat 45 Utrecht", "p1", "Prio 1 B R T 0 1 Brand woning Biltstraat 45 Utrecht"),
    (
        "P 2 BRT-02 Automatische brandmelding Kanaalweg 12 Utrecht",
        "p2",
        "Prio 2 B R T 0 2 Automatische brandmelding Kanaalweg 12 Utrecht",
    ),
    (
        "P 1 Hulpverlening ongeval wegvervoer Rijksweg A2 hmp 67.4 Utrecht",
        "p1",
        "Prio 1 Hulpverlening ongeval wegvervoer Rijksweg A 2 hectometerpaal 67 komma 4 Utrecht",
    ),
    ("A1 Reanimatie Dorpsstraat 8 Bilt", "a1", "Ambulance met spoed Reanimatie Dorpsstraat 8 Bilt"),
    (
        "P 2 OMS brandmelding Ziekenhuis (Directe inzet: ja) bon 123456",
        "p2",
        "Prio 2 Openbaar Meldsysteem brandmelding Ziekenhuis (Directe inzet: ja) bonnummer 1 2 3 4 5 6",
    ),
    (
        "P 1 BDH-03 BR woning Laan van Meerdervoort 12 SGRAVH 159331",
        None,
        "Prio 1 B D H 0 3 Brand woning Laan van Meerdervoort 12 Den Haag 1 5 9 3 3 1",
    ),
    (
        "P 2 TS VKO N209 OVD-B 3511 AX Utrecht DP4 (DIA: ja) rit: 12345",
        None,
        "Prio 2 Tankautospuit Verkeersongeval N 209 Officier van Dienst Brandweer 3 5 1 1 A X Utrecht "
        "Dienstpost 4 (Directe Inzet Ambulance: ja) ritnummer 1 2 3 4 5",
    ),
    ("P 1 BR WONING VAN DE DORPSSTRAAT UTRECH", None, "Prio 1 Brand woning van de dorpsstraat Utrecht"),
    ("Brand woning", "p1", "Prio 1. Brand woning"),
    (
        "Ass. ambulance ivm reanimatie evt. AED",
        "p2",
        "Prio 2. assistentie ambulance in verband met reanimatie eventueel A E D",
    ),
    # A code in the list with two meanings ("AC"), HV (vehicle or incident class) and an unknown code
    ("P 2 HV AC XQZ Stationsplein 1", None, "Prio 2 H V A C X Q Z Stationsplein 1"),
    # Times: never digits with a stray colon
    (
        "Test proefalarm 00:00 GRIP 1",
        None,
        "Test proefalarm 0 uur 0 Gecoördineerde Regionale Incidentenbestrijdingsprocedure 1",
    ),
    (
        "Melding om 7:45 en 23:59:30, aanvang 14.05 uur",
        None,
        "Melding om 7 uur 45 en 23 uur 59 en 30 seconden, aanvang 14 uur 5",
    ),
    ("P 1 BDH-03 Brand St.Jansstraat 3 om 08:05", None, "Prio 1 B D H 0 3 Brand St.Jansstraat 3 om 8 uur 5"),
    # Shortenings with dots
    (
        "P 1 Brand t.h.v. Rijksweg A2 hmp 45,2 i.v.m. rook z.s.m. ter plaatse",
        None,
        "Prio 1 Brand ter hoogte van Rijksweg A 2 hectometerpaal 45 komma 2 in verband met rook "
        "zo snel mogelijk ter plaatse",
    ),
    (
        "P 2 o.a. i.p.v. m.b.t. n.a.v. x.y.z. O.V.D. NL-Alert 23-bis DHG",
        None,
        "Prio 2 onder andere in plaats van met betrekking tot naar aanleiding van x y z Officier van Dienst "
        "N L Alert 23-bis D H G",
    ),
    # t.h.v. is not the code THV (Technische Hulpverlening); "n.v.t" without the last dot
    ("THV t.h.v. n.v.t", None, "Technische Hulpverlening ter hoogte van niet van toepassing"),
]


@pytest.mark.parametrize(("text", "prio", "spoken"), EXAMPLES)
def test_examples(speech, text, prio, spoken):
    assert speech.speak(text, prio) == spoken


def mock_texts() -> list[tuple[str, str]]:
    """Every alarm text the mock makes (each kind on each street)."""
    out = []
    for prio, _kind, template in KINDS:
        for street, _lat, _lon in STREETS:
            out.append((template.format(street=street.split(",")[0] + " " + street.split(" ")[-1]), prio))
    return out


ALNUM = re.compile(r"[0-9A-Za-zÀ-ÿ]+")


@pytest.mark.parametrize(("text", "prio"), mock_texts() + [(t, p) for t, p, _ in EXAMPLES])
def test_nothing_is_left_out(speech, text, prio):
    """Every word and number of the text is spoken: as itself, or as the translation of a step
    that took it in. No step translates to nothing."""
    result = speech.explain(text, prio)
    spoken = result["spoken"].lower()
    assert spoken
    covered = set()
    for step in result["steps"]:
        assert step["to"].strip(), step
        covered |= {t.lower() for t in ALNUM.findall(step["from"])}
    spoken_words = set(ALNUM.findall(spoken))
    for token in ALNUM.findall(text):
        t = token.lower()
        assert t in spoken_words or t in covered or " ".join(t) in spoken, (token, result)


def test_mock_texts_have_no_motorway_turned_ambulance(speech):
    assert "Ambulance" not in speech.speak("P 1 Hulpverlening ongeval wegvervoer Rijksweg A2 hmp 67.4 Utrecht", "p1")


def test_no_default_rule_deletes_text():
    for rule in DEFAULT_TTS_REPLACEMENTS:
        assert rule["replacement"].strip(), rule
        re.compile(rule["pattern"])


def test_an_empty_rule_is_not_used(db, speech):
    # A Meldkamer-style rule that drops reference numbers
    db.conn.execute("INSERT INTO tts_replacements (pattern, replacement) VALUES (?, '')", (r"\s*\bbon:?\s*\d+",))
    db.conn.commit()
    speech.reload()
    assert "1 2 3 4 5 6" in speech.speak("P 1 Brand bon 123456")
    with pytest.raises(ValueError):
        db.save_rule(r"\bfoo\b", "  ")


def test_a_meaning_you_give_is_spoken(db, speech):
    assert speech.speak("P 1 BRT-01 Brand") == "Prio 1 B R T 0 1 Brand"
    db.save_abbreviation("BRT-01", "Brandweer Rotterdam-Rijnmond kanaal 1")
    speech.reload()
    assert speech.speak("P 1 BRT-01 Brand") == "Prio 1 Brandweer Rotterdam-Rijnmond kanaal 1 Brand"
    # Changing the listed meaning of an uncertain code makes it certain
    row = db.conn.execute("SELECT id FROM abbreviations WHERE abbreviation = 'HV'").fetchone()
    db.save_abbreviation("HV", "hulpverlening", row["id"])
    speech.reload()
    assert speech.speak("P 2 HV Kat in boom") == "Prio 2 hulpverlening Kat in boom"


def test_explain_lists_the_changes(speech):
    steps = speech.explain("P 1 BRT-01 hmp 67.4", None)["steps"]
    kinds = {s["kind"] for s in steps}
    assert {"rule", "code", "number"} <= kinds


# ------------------------------------------------------------------ database


OLD_SCHEMA = """
CREATE TABLE incidents (id TEXT PRIMARY KEY, body TEXT, prio TEXT, type TEXT, trigger TEXT, state TEXT,
    responder_mode TEXT, created_at TEXT, can_respond_until TEXT, latitude REAL, longitude REAL,
    address_type TEXT, formatted_address TEXT, message_to_speech_url TEXT, task_ids TEXT, attributes TEXT,
    first_seen TEXT NOT NULL, last_seen TEXT NOT NULL, source TEXT NOT NULL);
CREATE TABLE incident_updates (ts TEXT PRIMARY KEY, incident_id TEXT NOT NULL, trigger TEXT, state TEXT,
    body TEXT, attributes TEXT);
CREATE TABLE responses (ts TEXT PRIMARY KEY, incident_id TEXT, state TEXT NOT NULL, status TEXT,
    responded_at TEXT, reported_status TEXT, arrived_at_station TEXT, attributes TEXT);
CREATE TABLE duty (ts TEXT PRIMARY KEY, state TEXT NOT NULL, start_time TEXT, end_time TEXT, available INTEGER,
    active INTEGER, assigned_function TEXT, attributes TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
INSERT INTO meta VALUES ('schema_version', '1'), ('backfill_days', '30');
INSERT INTO incidents (id, body, prio, first_seen, last_seen, source)
    VALUES ('42', 'P 1 BRT-01 Brand woning', 'p1', '2026-11-01T10:00:00+00:00', '2026-11-01T10:00:00+00:00', 'live');
INSERT INTO responses (ts, incident_id, state, status) VALUES ('2026-11-01T10:01:00+00:00', '42', 'on', 'acknowledged');
INSERT INTO duty (ts, state) VALUES ('2026-11-01T08:00:00+00:00', 'on');
"""


def test_an_older_database_keeps_its_data(tmp_path):
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.commit()
    conn.close()

    db = Database(path)
    speech = Speech(db)
    assert db.get_meta("schema_version") == "2"
    assert db.get_meta("backfill_days") == "30"
    incident = db.get_incident("42")
    assert incident["body"] == "P 1 BRT-01 Brand woning"
    assert incident["response"]["status"] == "acknowledged"
    assert db.counts()["duty"] == 1
    counts = db.speech_counts()
    assert counts["abbreviations"] > 300
    assert counts["tts_replacements"] == len(DEFAULT_TTS_REPLACEMENTS)
    assert speech.speak(incident["body"], incident["prio"]) == "Prio 1 B R T 0 1 Brand woning"
    db.close()


def test_your_changes_survive_a_restart(tmp_path):
    path = str(tmp_path / "f.db")
    db = Database(path)
    Speech(db)
    ts = db.conn.execute("SELECT id FROM abbreviations WHERE abbreviation = 'TS'").fetchone()["id"]
    db.save_abbreviation("TS", "tankautospuit van de kazerne", ts)
    gone = db.conn.execute("SELECT id FROM abbreviations WHERE abbreviation = 'VKO'").fetchone()["id"]
    db.delete_speech("abbreviations", gone)
    hmp = db.conn.execute("SELECT id FROM tts_replacements WHERE pattern = ?", (r"\bhmp\b\.?",)).fetchone()["id"]
    db.save_rule(r"\bhmp\b\.?", "hectometerpaaltje", True, hmp)
    road = db.conn.execute("SELECT id FROM tts_replacements WHERE pattern = ?", (r"\b([AN])(\d{1,3})\b",)).fetchone()
    db.delete_speech("tts_replacements", road["id"])
    db.save_abbreviation("XQZ", "eigen code")
    db.close()

    db = Database(path)
    speech = Speech(db)
    assert (
        speech.speak("TS VKO hmp 3.2 N209 XQZ")
        == "tankautospuit van de kazerne V K O hectometerpaaltje 3 komma 2 N 209 eigen code"
    )
    db.close()


def test_reset_keeps_the_speech_tables(db, speech):
    db.save_abbreviation("XQZ", "eigen code")
    db.reset()
    assert db.abbreviation_map()["XQZ"] == "eigen code"


def test_csv_roundtrip_and_meldkamer_format(db, speech):
    cols, _rows = db.speech_csv("abbreviations")
    assert cols == ["abbreviation", "full_text"]
    cols, _rules = db.speech_csv("tts_replacements")
    assert cols == ["pattern", "replacement", "enabled"]

    # A Meldkamer export: semicolons work too; an empty replacement is skipped
    meldkamer = "pattern,replacement,enabled\n\\bBRT\\-[0-9]+,Brandweer Rotterdam,1\n\\s*\\bbon:?\\s*\\d+,,1\n"
    result = db.import_speech_csv("tts_replacements", meldkamer)
    assert result["imported"] == 1
    assert len(result["skipped"]) == 1
    result = db.import_speech_csv("abbreviations", "abbreviation;full_text\nxqz;eigen code\n")
    assert result["imported"] == 1
    assert db.abbreviation_map()["XQZ"] == "eigen code"
    with pytest.raises(ValueError):
        db.import_speech_csv("abbreviations", "code,text\nA,B\n")
    with pytest.raises(ValueError):
        db.import_speech_csv("tts_replacements", "pattern,replacement\n([,x\n")


def test_import_list_restores_a_meaning(db, speech):
    from src import abbreviation_import

    row = db.conn.execute("SELECT id FROM abbreviations WHERE abbreviation = 'TS'").fetchone()["id"]
    db.save_abbreviation("TS", "iets anders", row)
    db.save_abbreviation("XQZ", "eigen code")
    abbreviation_import.import_abbreviations(db, replace=True)
    m = db.abbreviation_map()
    assert m["TS"] == "Tankautospuit"
    assert m["XQZ"] == "eigen code"


@pytest.mark.parametrize("text", ["00:00", "7:45", "23:59", "08:05:09", "14.05 uur", "om 00:00 uur"])
def test_a_time_never_keeps_a_colon_between_digits(speech, text):
    spoken = speech.speak(text)
    assert "uur" in spoken
    assert not re.search(r"\d\s*[:.]\s*\d", spoken), spoken


def test_dotted_shortenings_are_editable_abbreviations(db, speech):
    assert db.abbreviation_map()["T.H.V."] == "ter hoogte van"
    row = db.conn.execute("SELECT id FROM abbreviations WHERE abbreviation = 'T.H.V.'").fetchone()["id"]
    db.save_abbreviation("t.h.v.", "ter hoogte van de", row)
    speech.reload()
    assert speech.speak("Brand t.h.v. de brug") == "Brand ter hoogte van de de brug"
    db.delete_speech("abbreviations", row)
    speech.reload()
    # Not in the list any more: spelled out, not dropped
    assert speech.speak("Brand t.h.v. de brug") == "Brand t h v de brug"
