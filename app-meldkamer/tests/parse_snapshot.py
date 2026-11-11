"""Parse a fixed set of realistic messages and return everything derived from them.

tests/test_parse_snapshot.py compares this with tests/data/parse_snapshot.json, made with
1.4.15's hard-coded lists before they moved into database tables (1.4.16): with the default
data the output must stay identical. Run `python -m tests.parse_snapshot > tests/data/parse_snapshot.json`
only to record a deliberate change.
"""

import asyncio
import json
import sys
import tempfile
from types import SimpleNamespace

from src import bag_import, message_fields, vehicle_types
from src.database import Database
from src.parser import Parser, city_shortname_tags, is_street_name_word, looks_like_street
from src.processor import MessageProcessor
from src.tts import SpeechRules

# {province: {woonplaats: (lat, lon, code, [(street, postcode, lat, lon)])}}
BAG = {
    "Gelderland": {
        "Velp": (
            51.995,
            5.975,
            "1001",
            [("Hoofdstraat", "6881", 51.997, 5.977), ("Kerkstraat", "6881", 51.994, 5.973)],
        ),
        "Rijswijk": (51.958, 5.355, "1002", [("Lingedijk", "4119", 51.957, 5.354)]),
        "Arnhem": (51.985, 5.899, "1003", [("Jansbinnensingel", "6811", 51.983, 5.910)]),
    },
    "Noord-Brabant": {
        "Velp": (51.748, 5.714, "2001", [("Graafsebaan", "5363", 51.745, 5.710)]),
        "Eindhoven": (51.441, 5.469, "2002", [("Stratumseind", "5611", 51.437, 5.481)]),
    },
    "Zuid-Holland": {
        "Rijswijk": (52.036, 4.325, "3001", [("Steenvoordelaan", "2284", 52.041, 4.318)]),
        "Katwijk": (52.200, 4.416, "3002", [("Asterstraat", "2221", 52.196, 4.410)]),
        "Rotterdam": (
            51.922,
            4.479,
            "3003",
            [("Coolsingel", "3011", 51.920, 4.478), ("Burgemeester van Walsumweg", "3011", 51.921, 4.490)],
        ),
        "'s-Gravenhage": (
            52.070,
            4.300,
            "3004",
            [("Frankenslag", "2582", 52.089, 4.282), ("Laan van Meerdervoort", "2517", 52.078, 4.290)],
        ),
        "Sliedrecht": (51.821, 4.776, "3005", [("Kerkbuurt", "3361", 51.823, 4.775)]),
    },
    "Utrecht": {
        "Utrecht": (
            52.091,
            5.121,
            "4001",
            [("Biltstraat", "3572", 52.093, 5.134), ("Oudegracht", "3511", 52.089, 5.119)],
        ),
    },
    "Noord-Holland": {
        "Hoorn": (52.643, 5.059, "5001", [("Kerkstraat", "1621", 52.642, 5.060)]),
        "Amsterdam": (52.373, 4.893, "5002", [("Damrak", "1012", 52.375, 4.896)]),
    },
}

CAPCODES = [
    ("000100001", "Ambulance", "Haaglanden"),
    ("000200001", "Brandweer", "Rotterdam-Rijnmond"),
    ("000300001", "Brandweer", "Gelderland Midden"),
    ("000400001", "Ambulance", "Brabant Zuid-Oost"),
    ("000500001", "Politie", "Limburg Zuid"),
    ("000600001", "Brandweer", "Fryslân"),
]

MESSAGES = [
    ("000200001", "P 1 BRT-01 Brand woning Coolsingel Rotterdam 123456"),
    ("000200001", "P 2 BRT-02 OMS brandmelding Coolsingel Rotterdam (Directe inzet: ja) bon 170616"),
    ("000200001", "P 1 BRT-03 VKO letsel Burgemeester van Walsumweg Rotterdam TS 17-1731 HV 17-1761"),
    ("000200001", "P 2 BRT-01 Stank/hind. lucht Coolsingel ROTTDM 172131"),
    ("000100001", "A1 Steenvoordelaan RIJSZH : 15115"),
    ("000100001", "A2 Asterstraat KATWZH Directe inzet 16180"),
    ("000100001", "A1 (DIA: ja) AMBU 17106 Frankenslag 2582 SGRAVH bon 12345 Rit 54321"),
    ("000100001", "A2 Laan van Meerdervoort SGRAVH 15124 rit: 112233"),
    ("000100001", "B1 17123 Rit 98765 ziekenhuis Haaglanden"),
    ("000100001", "B2 Rit 12345 Frankenslag 's-Gravenhage"),
    ("000300001", "P 2 Assistentie ambulance Jansbinnensingel Arnhem 073211"),
    ("000300001", "P 1 BON-01 Woningbrand Hoofdstraat Velp 073131"),
    ("000300001", "A2 Velp NB 184820"),
    ("000300001", "A2 Velp GE 184821"),
    ("000300001", "P 2 BR container VELPNB 12345"),
    ("", "A2 Velp 184820"),
    ("000400001", "A1 Reanimatie AED Stratumseind Eindhoven 22111"),
    ("000400001", "P 1 BOB-02 Liftopsluiting Stratumseind EINDHV 201234"),
    ("000500001", "Pol: aanrijding letsel Damrak Amsterdam"),
    ("000600001", "P 1 BNN-01 Ongeval wegvervoer Stationsstraat Scheemda 013693 012933"),
    ("000200001", "P 1 BRT-01 (GRIP 1) Brand bedrijfspand Coolsingel Rotterdam"),
    ("000200001", "P 2 Dier in problemen kat in boom Biltstraat Utrecht"),
    ("000200001", "P 2 Stormschade boom op weg Oudegracht UTRECH"),
    ("000200001", "P 1 Waterongeval drenkeling Kerkbuurt Sliedrecht SLIEDR"),
    ("000200001", "P 2 Gaslucht Oudegracht Utrecht OVD-B 09-1191"),
    ("000200001", "P 1 BMD-01 BR woning Biltstraat Utrecht AL 09-1351 TS 09-1131"),
    ("000200001", "P 1 BAD-04 Brand Damrak Amsterdam 131234"),
    ("000200001", "P 2 BNH-914 Nacontrole Kerkstraat Hoorn"),
    ("000200001", "TESTOPROEP MOB"),
    ("000200001", "P 3 Dienstverlening wateroverlast Kerkstraat Hoorn NH"),
    ("000100001", "A2 Rit 4433 ambu 16-107 Frankenslag 's-Gravenhage"),
    ("000200001", "PRIO 1 Binnenbrand Coolsingel Rotterdam OvD 17-1891"),
    ("000200001", "P 1 Hulpverlening Rijksweg A2 hmp 67.4 Utrecht"),
    ("000200001", "P 1 Brand t.h.v. Kerkstraat Velp i.v.m. rook"),
    ("000200001", "P 2 Melding ambulance post Coolsingel Rotterdam"),
    ("000200001", "P 1 BR woning Dorpsstraat Hoorn"),
    ("000200001", "P 1 BR woning Kerkstraat Velp"),
]

WORDS = ["Burgemeester", "van", "Ambulance", "Woningbrand", "BR", "2e", "J.P.", "Kerkstraat", "Prio", "Melding"]
REGIONS = [
    "Gelderland Midden",
    "Brabant Zuid-Oost",
    "Limburg Zuid",
    "Zuid-Limburg",
    "Fryslân",
    "Friesland",
    "Veiligheidsregio Haaglanden",
    "Noord- en Oost-Gelderland",
    "Hollands-Midden",
    "Onbekend",
]
VEHICLES = ["17-135", "09-1131", "25-201", "99-100", "06"]


def build_database(path: str) -> Database:
    def woonplaatsen(province):
        return {name: (lat, lon, [code]) for name, (lat, lon, code, _) in BAG[province].items()}

    def streets(woonplaats, codes):
        for places in BAG.values():
            place = places.get(woonplaats)
            if place and place[2] in codes:
                return [{"street": s, "postcode": pc, "latitude": la, "longitude": lo} for s, pc, la, lo in place[3]]
        return []

    bag_import._fetch_woonplaatsen_for_province = woonplaatsen
    bag_import._fetch_streets_for_woonplaats = streets
    db = Database(path)
    for province in BAG:
        bag_import.try_start_import(province)
        bag_import.import_province_data(province, db)
    for capcode, discipline, region in CAPCODES:
        db.add_capcode(capcode, discipline, region)
    from src import abbreviation_import

    abbreviation_import.import_abbreviations(db, replace=False)
    return db


def snapshot(path: str) -> dict:
    db = build_database(path)
    speech = SpeechRules(db)
    parser = Parser(db)
    out: dict = {"messages": [], "words": {}, "regions": {}, "vehicles": {}}
    for capcode, body in MESSAGES:
        msg = parser.parse(f"FLEX|2026-11-11 10:00:00|1600/2/K/A|10|{capcode or '000999999'}|ALN|{body}")
        if msg is None:
            out["messages"].append({"body": body, "parsed": None})
            continue
        stub = SimpleNamespace(database=db, geocoder=SimpleNamespace(enabled=False))
        asyncio.run(MessageProcessor._locate(stub, msg))
        d = msg.to_dict()
        d.pop("timestamp", None)
        d.pop("tts", None)
        d["spoken"] = speech.speak(msg)
        d["province_found"] = msg.province
        d["tags"] = db.find_abbreviations_in_text(body, msg.discipline) + city_shortname_tags(body, db)
        d["derived"] = message_fields.derived_fields(body, db.decode_talkgroup)
        d["units"] = vehicle_types.message_units(body, msg.discipline)
        d["named"] = vehicle_types.named_vehicles(body)
        d["vehicle"] = db.resolve_vehicle(body, msg.discipline)
        d["fire"] = vehicle_types.is_fire_message(body, msg.discipline)
        d["classify_text"] = vehicle_types.classify_text(body)
        out["messages"].append(json.loads(json.dumps(d, default=str)))
    for w in WORDS:
        out["words"][w] = [is_street_name_word(w), looks_like_street(w), message_fields.is_incident_word(w)]
    for r in REGIONS:
        out["regions"][r] = message_fields.province_of_region(r)
    for v in VEHICLES:
        out["vehicles"][v] = message_fields.region_of_vehicle(v)
    out["incident_types"] = message_fields.incident_types()
    out["vehicle_icons"] = {c: vehicle_types.icon_for(c) for c in ("ambulance", "politie", "x")}
    db.close()
    return out


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as tmp:
        json.dump(snapshot(f"{tmp}/m.sqlite3"), sys.stdout, indent=1, ensure_ascii=False, sort_keys=True)
