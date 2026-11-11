"""0.2.2 speech defaults: PM, Li/Re after a hectometer marker, OM."""

import pytest

from src.database import Database
from src.speech import DEFAULTS, Speech


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "f.db"))
    yield d
    d.close()


@pytest.fixture
def speech(db):
    return Speech(db)


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("P 2 PM 17-1234", "Prio 2 Personeel- en materieelvoertuig 17-1234"),
        ("P 2 PM-90", "Prio 2 Personeel- en materieelvoertuig 90"),
        ("P 2 PM-VEB", "Prio 2 Personeel- en materieelvoertuig V E B"),
        (
            "P 1 Ongeval A12 Li 45,2",
            "Prio 1 Ongeval A 12 rijbaan links 45 komma 2",
        ),
        (
            "P 1 Ongeval Rijksweg A2 hmp 67.4 Re",
            "Prio 1 Ongeval Rijksweg A 2 hectometerpaal 67 komma 4 rijbaan rechts",
        ),
        ("P 1 Ongeval A2 Re hmp 45,2", "Prio 1 Ongeval A 2 rijbaan rechts hectometerpaal 45 komma 2"),
        # Not after a hectometer marker: as written
        ("P 2 Li Kerkstraat 4 Re", "Prio 2 Li Kerkstraat 4 Re"),
        ("P 2 Assistentie OM Stationsplein", "Prio 2 Assistentie O M Stationsplein"),
        ("P 2 brand om de hoek", "Prio 2 brand om de hoek"),
        # Still spelled out for Ron
        ("P 2 MCU VCK", "Prio 2 M C U V C K"),
    ],
)
def test_spoken(speech, text, spoken):
    assert speech.speak(text) == spoken


def test_pm_updated_on_an_older_install_unless_edited(tmp_path):
    path = str(tmp_path / "old.db")
    db = Database(path)
    # As 0.2.1 left it: everything seeded, PM with the old text, PM-90 edited by the user
    db.seed_speech(DEFAULTS)
    db.conn.execute("UPDATE abbreviations SET full_text = 'Personeel / Materieelwagen' WHERE abbreviation = 'PM'")
    db.conn.execute("UPDATE abbreviations SET full_text = 'eigen tekst' WHERE abbreviation = 'PM-90'")
    db.conn.commit()
    speech = Speech(db)
    meanings = db.abbreviation_map()
    assert meanings["PM"] == "Personeel- en materieelvoertuig"
    assert meanings["PM-90"] == "eigen tekst"
    # Once: an edit after the update is kept at the next start
    db.save_abbreviation("PM", "Personeel / Materieelwagen", db.list_speech("abbreviations", "PM")["items"][0]["id"])
    db.close()
    db = Database(path)
    Speech(db)
    assert db.abbreviation_map()["PM"] == "Personeel / Materieelwagen"
    assert speech
    db.close()


def test_new_rules_reach_an_existing_install(tmp_path):
    path = str(tmp_path / "old.db")
    db = Database(path)
    old_rules = [
        r for r in DEFAULTS["tts_replacements"] if "rijbaan" not in r["replacement"] and r["replacement"] != "O M"
    ]
    db.seed_speech({**DEFAULTS, "tts_replacements": old_rules})
    speech = Speech(db)
    assert speech.speak("A12 Li 45,2") == "A 12 rijbaan links 45 komma 2"
    assert speech.speak("OM") == "O M"
    db.close()
