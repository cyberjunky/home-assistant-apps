"""Same-named places in different provinces (issue #67): Velp in Gelderland and in Noord-Brabant."""

import asyncio
from types import SimpleNamespace

import pytest

from src import bag_import
from src.database import Database
from src.message_fields import province_of_region
from src.parser import Parser
from src.processor import MessageProcessor

VELP_GLD = (51.995, 5.975)
VELP_NB = (51.748, 5.714)
HOORN_NH = (52.643, 5.059)
HOORN_TS = (53.387, 5.338)  # Hoorn on Terschelling, Friesland

# What PDOK returns per province: {province: {woonplaats: (lat, lon, code, [(street, postcode, lat, lon)])}}
BAG = {
    "Gelderland": {
        "Velp": (
            *VELP_GLD,
            "1001",
            [
                ("Hoofdstraat", "6881", 51.997, 5.977),
                ("Kerkstraat", "6881", 51.994, 5.973),
                ("Arnhemsestraat", "6881", 51.990, 5.960),
            ],
        ),
    },
    "Noord-Brabant": {
        "Velp": (
            *VELP_NB,
            "2001",
            [("Kerkstraat", "5363", 51.749, 5.715), ("Graafsebaan", "5363", 51.745, 5.710)],
        ),
    },
    "Noord-Holland": {
        "Hoorn": (*HOORN_NH, "3001", [("Grote Noord", "1621", 52.642, 5.058)]),
    },
    "Friesland": {
        "Hoorn": (*HOORN_TS, "4001", [("Dorpsstraat", "8896", 53.388, 5.340)]),
    },
}


@pytest.fixture
def fake_pdok(monkeypatch):
    """The BAG import reads BAG above instead of PDOK."""

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


def _import(db, *provinces):
    for province in provinces:
        assert bag_import.try_start_import(province)
        bag_import.import_province_data(province, db)


def _places(db, city):
    rows = db.cursor.execute(
        "SELECT id, province, latitude, longitude FROM places WHERE city = ? ORDER BY province", (city,)
    ).fetchall()
    return {row[1]: dict(row) for row in rows}


def _streets(db, place_id):
    rows = db.cursor.execute(
        "SELECT street, latitude, longitude FROM streets WHERE city_id = ? ORDER BY street", (place_id,)
    ).fetchall()
    return {row[0]: (row[1], row[2]) for row in rows}


def test_import_two_provinces_gives_two_places(tmp_path, fake_pdok):
    db = Database(str(tmp_path / "meldkamer.sqlite3"))
    _import(db, "Noord-Brabant", "Gelderland")

    velp = _places(db, "Velp")
    assert set(velp) == {"Gelderland", "Noord-Brabant"}
    assert (velp["Gelderland"]["latitude"], velp["Gelderland"]["longitude"]) == VELP_GLD
    assert (velp["Noord-Brabant"]["latitude"], velp["Noord-Brabant"]["longitude"]) == VELP_NB
    gld = _streets(db, velp["Gelderland"]["id"])
    nb = _streets(db, velp["Noord-Brabant"]["id"])
    assert set(gld) == {"Hoofdstraat", "Kerkstraat", "Arnhemsestraat"}
    assert set(nb) == {"Kerkstraat", "Graafsebaan"}
    # The same street name in both places, each with its own position
    assert gld["Kerkstraat"] == (51.994, 5.973)
    assert nb["Kerkstraat"] == (51.749, 5.715)
    # Importing again adds nothing
    _import(db, "Gelderland")
    assert len(_places(db, "Velp")) == 2
    assert db.cursor.execute("SELECT COUNT(*) FROM streets").fetchone()[0] == 5
    # The places table of the web UI shows both, with their province
    items = db.list_places(search="Velp")["items"]
    assert sorted((p["city"], p["province"]) for p in items) == [("Velp", "Gelderland"), ("Velp", "Noord-Brabant")]
    names = {s["city_name"] for s in db.list_streets(search="Kerkstraat")["items"]}
    assert names == {"Velp (Gelderland)", "Velp (Noord-Brabant)"}


def test_street_provinces(tmp_path, fake_pdok):
    db = Database(str(tmp_path / "meldkamer.sqlite3"))
    _import(db, "Gelderland", "Noord-Brabant", "Noord-Holland", "Friesland")
    assert db.street_provinces() == {
        "Gelderland": {"streets": 3, "located": 3},
        "Noord-Brabant": {"streets": 2, "located": 2},
        "Noord-Holland": {"streets": 1, "located": 1},
        "Friesland": {"streets": 1, "located": 1},
    }


def test_region_province():
    assert province_of_region("Gelderland Midden") == "Gelderland"
    assert province_of_region("Gelderland-Midden") == "Gelderland"
    assert province_of_region("Brabant Zuid-Oost") == "Noord-Brabant"
    assert province_of_region("Brabant-Zuidoost") == "Noord-Brabant"
    assert province_of_region("Noord-Holland Noord") == "Noord-Holland"
    assert province_of_region("Fryslân") == "Friesland"
    assert province_of_region("Limburg Zuid") == "Limburg"
    assert province_of_region("Zuid-Limburg") == "Limburg"
    assert province_of_region("Landelijk") == ""
    assert province_of_region("") == ""


def _locate(db, body, region=""):
    """Parse a message from a capcode of the region and place it, as the processor does."""
    db.cursor.execute("DELETE FROM capcodes")
    if region:
        db.add_capcode("000100001", "Brandweer", region)
    msg = Parser(db).parse(f"FLEX|2026-11-11 10:00:00|1600/2/K/A|10|000100001|ALN|{body}")
    stub = SimpleNamespace(database=db, geocoder=SimpleNamespace(enabled=False))
    asyncio.run(MessageProcessor._locate(stub, msg))
    return msg


@pytest.fixture
def hoorn_db(tmp_path, fake_pdok):
    db = Database(str(tmp_path / "meldkamer.sqlite3"))
    _import(db, "Noord-Holland", "Friesland")
    return db


def test_street_only_in_one_namesake_wins(hoorn_db):
    msg = _locate(hoorn_db, "P 1 BR woning Dorpsstraat Hoorn")
    assert (msg.street, msg.location_accuracy) == ("Dorpsstraat", "street")
    assert (msg.latitude, msg.longitude) == (53.388, 5.340)


def test_region_picks_the_namesake(hoorn_db):
    # A Noord-Holland capcode: Hoorn NH, which has no Dorpsstraat: the town's position, not Terschelling
    msg = _locate(hoorn_db, "P 1 BR woning Dorpsstraat Hoorn", "Noord-Holland Noord")
    assert msg.city == "Hoorn"
    assert (msg.latitude, msg.longitude) == HOORN_NH
    assert msg.location_accuracy == "city"
    # A Friesland capcode: Terschelling
    msg = _locate(hoorn_db, "P 1 BR woning Dorpsstraat Hoorn", "Friesland")
    assert (msg.latitude, msg.longitude) == (53.388, 5.340)
    # Only the town: the namesake of the region
    assert hoorn_db.get_place_coordinates("Hoorn", "Friesland") == {"latitude": HOORN_TS[0], "longitude": HOORN_TS[1]}
    assert hoorn_db.get_place_coordinates("Hoorn", "Noord-Holland") == {
        "latitude": HOORN_NH[0],
        "longitude": HOORN_NH[1],
    }


def test_same_street_in_both_namesakes(tmp_path, fake_pdok):
    db = Database(str(tmp_path / "meldkamer.sqlite3"))
    _import(db, "Noord-Brabant", "Gelderland")
    msg = _locate(db, "P 1 BR woning Kerkstraat Velp", "Gelderland Midden")
    assert (msg.latitude, msg.longitude) == (51.994, 5.973)
    assert msg.postalcode == "6881"
    msg = _locate(db, "P 1 BR woning Kerkstraat Velp", "Brabant Noord")
    assert (msg.latitude, msg.longitude) == (51.749, 5.715)
    assert msg.postalcode == "5363"
    # No region: the same answer every time (the place with the most streets, Velp Gld)
    assert db.get_street_coordinates("Kerkstraat", "Velp") == {"latitude": 51.994, "longitude": 5.973}


def test_csv_round_trip_keeps_namesakes(tmp_path, fake_pdok):
    db = Database(str(tmp_path / "a.sqlite3"))
    _import(db, "Noord-Brabant", "Gelderland")
    other = Database(str(tmp_path / "b.sqlite3"))
    other.import_places_csv(db.export_places_csv())
    other.import_streets_csv(db.export_streets_csv())
    velp = _places(other, "Velp")
    assert set(_streets(other, velp["Gelderland"]["id"])) == {"Hoofdstraat", "Kerkstraat", "Arnhemsestraat"}
    assert set(_streets(other, velp["Noord-Brabant"]["id"])) == {"Kerkstraat", "Graafsebaan"}
