"""A town with its province in the message: "Velp NB", or an abbreviation such as VELPNB or RIJSZH."""

import asyncio
from types import SimpleNamespace

import pytest

from src import bag_import
from src.database import Database
from src.parser import Parser
from src.processor import MessageProcessor

VELP_GLD = (51.995, 5.975)
VELP_NB = (51.748, 5.714)
RIJSWIJK_ZH = (52.036, 4.325)
RIJSWIJK_GLD = (51.958, 5.355)
STEENVOORDELAAN = (52.041, 4.318)

# {province: {woonplaats: (lat, lon, code, [(street, postcode, lat, lon)])}}
BAG = {
    "Gelderland": {
        "Velp": (*VELP_GLD, "1001", [("Hoofdstraat", "6881", 51.997, 5.977), ("Kerkstraat", "6881", 51.994, 5.973)]),
        "Rijswijk": (*RIJSWIJK_GLD, "1002", [("Lingedijk", "4119", 51.957, 5.354)]),
    },
    "Noord-Brabant": {
        "Velp": (*VELP_NB, "2001", [("Graafsebaan", "5363", 51.745, 5.710)]),
    },
    "Zuid-Holland": {
        "Rijswijk": (*RIJSWIJK_ZH, "3001", [("Steenvoordelaan", "2284", *STEENVOORDELAAN)]),
        "Katwijk": (52.200, 4.416, "3002", [("Asterstraat", "2221", 52.196, 4.410)]),
    },
}


@pytest.fixture
def db(tmp_path, monkeypatch):
    def woonplaatsen(province):
        return {name: (lat, lon, [code]) for name, (lat, lon, code, _) in BAG[province].items()}

    def streets(woonplaats, codes):
        for places in BAG.values():
            place = places.get(woonplaats)
            if place and place[2] in codes:
                return [{"street": s, "postcode": pc, "latitude": la, "longitude": lo} for s, pc, la, lo in place[3]]
        return []

    monkeypatch.setattr(bag_import, "_fetch_woonplaatsen_for_province", woonplaatsen)
    monkeypatch.setattr(bag_import, "_fetch_streets_for_woonplaats", streets)
    database = Database(str(tmp_path / "meldkamer.sqlite3"))
    for province in BAG:
        assert bag_import.try_start_import(province)
        bag_import.import_province_data(province, database)
    return database


def _locate(db, body, region=""):
    """Parse a message from a capcode of the region (none: an unknown capcode) and place it."""
    db.cursor.execute("DELETE FROM capcodes")
    if region:
        db.add_capcode("000100001", "Ambulance", region)
    msg = Parser(db).parse(f"FLEX|2026-11-11 10:00:00|1600/2/K/A|10|000100001|ALN|{body}")
    stub = SimpleNamespace(database=db, geocoder=SimpleNamespace(enabled=False))
    asyncio.run(MessageProcessor._locate(stub, msg))
    return msg


def test_split_province_code():
    from src.message_fields import split_province_code

    assert split_province_code("Velp NB") == ("Velp", "Noord-Brabant")
    assert split_province_code("Hoorn Fr") == ("Hoorn", "Friesland")
    assert split_province_code("Beek L") == ("Beek", "Limburg")
    assert split_province_code("Beek Montferland") == ("Beek Montferland", "")
    assert split_province_code("Velp") == ("Velp", "")


@pytest.mark.parametrize(
    "body,expected",
    [
        ("A2 Velp NB 184820", VELP_NB),  # no region: without the code, Velp Gelderland (most streets)
        ("A2 Velp GE 184821", VELP_GLD),
        ("P 2 BR container VELPNB 12345", VELP_NB),
        ("P 2 BR container VELPGE 12345", VELP_GLD),
    ],
)
def test_province_code_picks_the_town(db, body, expected):
    msg = _locate(db, body)
    assert msg.city == "Velp"
    assert (msg.latitude, msg.longitude) == expected


def test_abbreviation_with_street(db):
    # Live 1.4.11: city "Rijswijk ZH", street found, no position at all
    msg = _locate(db, "A1 Steenvoordelaan RIJSZH : 15115", "Haaglanden")
    assert (msg.city, msg.street, msg.location_accuracy) == ("Rijswijk", "Steenvoordelaan", "street")
    assert (msg.latitude, msg.longitude) == STEENVOORDELAAN
    # Katwijk exists once: the code only drops from the name
    msg = _locate(db, "A2 Asterstraat KATWZH Directe inzet 16180")
    assert (msg.city, msg.street, msg.latitude) == ("Katwijk", "Asterstraat", 52.196)


def test_code_in_text_wins_over_region(db):
    # The text names the province; the capcode's region may be a neighbouring one
    msg = _locate(db, "A2 Velp NB 184820", "Gelderland Midden")
    assert (msg.latitude, msg.longitude) == VELP_NB


def test_without_code_unchanged(db):
    assert (_locate(db, "A2 Velp 184820").latitude, _locate(db, "A2 Velp 184820").longitude) == VELP_GLD
    msg = _locate(db, "A2 Velp 184820", "Brabant Noord")
    assert (msg.latitude, msg.longitude) == VELP_NB
