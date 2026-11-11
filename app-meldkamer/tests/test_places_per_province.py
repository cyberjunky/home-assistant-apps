"""Same-named places in different provinces (issue #67): Velp in Gelderland and in Noord-Brabant."""

import asyncio
import sqlite3
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

# The places and streets tables of Meldkamer 1.4.10: a place is unique on its name alone
SCHEMA_1_4_10 = """
CREATE TABLE places (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    city TEXT UNIQUE NOT NULL,
    abbreviation TEXT,
    province TEXT,
    latitude REAL,
    longitude REAL
);
CREATE TABLE streets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    street TEXT NOT NULL,
    city_id INTEGER,
    postalcode TEXT,
    latitude REAL,
    longitude REAL,
    FOREIGN KEY (city_id) REFERENCES places(id)
);
CREATE INDEX idx_streets_street ON streets(street);
CREATE INDEX idx_streets_city ON streets(city_id);
CREATE INDEX idx_streets_lookup ON streets(street, city_id);
CREATE UNIQUE INDEX idx_streets_unique ON streets(street, city_id);
CREATE TABLE capcodes (
    capcode TEXT PRIMARY KEY, discipline TEXT, region TEXT, location TEXT, description TEXT, remark TEXT
);
CREATE TABLE abbreviations (
    id INTEGER PRIMARY KEY AUTOINCREMENT, abbreviation TEXT UNIQUE NOT NULL, full_text TEXT NOT NULL
);
"""


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
    monkeypatch.setattr(
        bag_import, "_fetch_woonplaats_codes", lambda name, prov: [BAG[prov][name][2]] if name in BAG[prov] else []
    )


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


def _old_database(path, velp_province="Noord-Brabant"):
    """A 1.4.10 database after Import BAG for Noord-Brabant and Gelderland: one Velp with streets of both.

    The place has the province and position of one of them (in 1.4.10: of the province imported last).
    """
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA_1_4_10)
    velp = VELP_NB if velp_province == "Noord-Brabant" else VELP_GLD
    conn.execute("INSERT INTO places VALUES (1, 'Velp', 'VELP', ?, ?, ?)", (velp_province, *velp))
    conn.execute("INSERT INTO places VALUES (2, 'Mijndorp', 'MIJND', NULL, 52.1, 5.1)")  # own place, no province
    conn.execute("INSERT INTO places VALUES (3, 'Hoorn', 'HOORN', 'Noord-Holland', ?, ?)", HOORN_NH)
    streets = [
        ("Kerkstraat", 1, "5363", 51.749, 5.715),  # Velp NB (Velp Gld's Kerkstraat was dropped)
        ("Graafsebaan", 1, "5363", 51.745, 5.710),
        ("Hoofdstraat", 1, "6881", 51.997, 5.977),  # Velp Gld streets on the NB place
        ("Arnhemsestraat", 1, "6881", 51.990, 5.960),
        ("Mijnstraat", 2, "", None, None),  # own street
        ("Grote Noord", 3, "1621", 52.642, 5.058),
        ("Dorpsstraat", 3, "8896", 53.388, 5.340),  # Terschelling's Hoorn on the NH place
        ("Losse weg", None, "", None, None),  # a street without a place
    ]
    conn.executemany(
        "INSERT INTO streets (street, city_id, postalcode, latitude, longitude) VALUES (?, ?, ?, ?, ?)", streets
    )
    conn.execute("INSERT INTO capcodes VALUES ('001234567', 'Brandweer', 'Gelderland Midden', '', 'TS Velp', '')")
    conn.execute("INSERT INTO abbreviations (abbreviation, full_text) VALUES ('MIJNAFK', 'Mijn afkorting')")
    conn.commit()
    conn.close()
    return streets


def test_migration_keeps_every_row(tmp_path, caplog):
    path = str(tmp_path / "meldkamer.sqlite3")
    old_streets = _old_database(path)

    db = Database(path)

    places = db.cursor.execute("SELECT id, city, abbreviation, province, latitude FROM places ORDER BY id").fetchall()
    assert [tuple(row) for row in places] == [
        (1, "Velp", "VELP", "Noord-Brabant", VELP_NB[0]),  # still the merged place, re-import separates it
        (2, "Mijndorp", "MIJND", "", 52.1),
        (3, "Hoorn", "HOORN", "Noord-Holland", HOORN_NH[0]),
    ]
    streets = db.cursor.execute(
        "SELECT street, city_id, postalcode, latitude, longitude FROM streets ORDER BY id"
    ).fetchall()
    assert [tuple(row) for row in streets] == old_streets
    assert db.find_capcode("001234567")["region"] == "Gelderland Midden"
    assert db.cursor.execute("SELECT full_text FROM abbreviations WHERE abbreviation = 'MIJNAFK'").fetchone()[0]
    # Now unique per name and province: a second Velp can be added, a second Velp (NB) can't
    assert db.add_place("Velp", province="Gelderland")
    assert not db.add_place("Velp", province="Noord-Brabant")
    # The merged places are reported
    assert [p["city"] for p in db.mixed_places()] == ["Hoorn", "Velp"]
    assert "Run Import BAG again" in caplog.text

    # Opening it again changes nothing
    db.conn.close()
    db = Database(path)
    assert db.cursor.execute("SELECT COUNT(*) FROM places").fetchone()[0] == 4


@pytest.mark.parametrize(
    ("velp_province", "order"),
    [
        ("Noord-Brabant", ["Gelderland", "Noord-Brabant", "Noord-Holland", "Friesland"]),
        ("Gelderland", ["Gelderland", "Noord-Brabant", "Noord-Holland", "Friesland"]),
        ("Gelderland", ["Friesland", "Noord-Holland", "Noord-Brabant", "Gelderland"]),
    ],
)
def test_reimport_separates_merged_places(tmp_path, fake_pdok, velp_province, order):
    path = str(tmp_path / "meldkamer.sqlite3")
    _old_database(path, velp_province)
    db = Database(path)

    _import(db, *order)

    velp = _places(db, "Velp")
    assert set(velp) == {"Gelderland", "Noord-Brabant"}
    assert velp[velp_province]["id"] == 1  # the existing place keeps its id
    assert (velp["Gelderland"]["latitude"], velp["Gelderland"]["longitude"]) == VELP_GLD
    assert (velp["Noord-Brabant"]["latitude"], velp["Noord-Brabant"]["longitude"]) == VELP_NB
    # Each its own Kerkstraat (1.4.10 kept only one)
    assert _streets(db, velp["Gelderland"]["id"])["Kerkstraat"] == (51.994, 5.973)
    assert _streets(db, velp["Noord-Brabant"]["id"])["Kerkstraat"] == (51.749, 5.715)
    assert set(_streets(db, velp["Gelderland"]["id"])) == {"Hoofdstraat", "Kerkstraat", "Arnhemsestraat"}
    assert set(_streets(db, velp["Noord-Brabant"]["id"])) == {"Kerkstraat", "Graafsebaan"}
    hoorn = _places(db, "Hoorn")
    assert set(_streets(db, hoorn["Noord-Holland"]["id"])) == {"Grote Noord"}
    assert set(_streets(db, hoorn["Friesland"]["id"])) == {"Dorpsstraat"}
    # Own place and street untouched, nothing reported as mixed any more
    assert _streets(db, 2) == {"Mijnstraat": (None, None)}
    assert db.mixed_places() == []


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
