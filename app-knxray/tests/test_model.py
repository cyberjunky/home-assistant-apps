"""Telegrams, the project and Home Assistant's configuration as plain data."""

from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import GroupAddress, IndividualAddress, Telegram, apci

from src.model import Project, build_entities, expected_dpt
from src.telegrams import decode, from_ha, from_xknx
from tools import installation as house
from tools.mock_ha import MockHA, telegram_dict


def test_decode_reads_a_payload_as_its_type():
    assert decode("9.001", "0c1a") == ("21", 21.0, "°C")
    assert decode("5.001", "80") == ("50", 50.0, "%")
    # A switch reads as a word and charts as a number
    assert decode("1.001", "01") == ("on", 1.0, None)
    assert decode("20.102", "01")[0] == "comfort"
    # A bare main type reads like its first sub type
    assert decode("9", "0c1a")[1] == 21.0


def test_decode_refuses_what_does_not_fit():
    assert decode("9.001", "01") is None  # one byte for a two-byte float
    assert decode("9.001", "01", bits=True) is None  # a bit for a float
    assert decode("1.001", "0c1a", bits=False) is None
    assert decode(None, "01") is None
    assert decode("9.001", "") is None
    assert decode("999.999", "01") is None


def test_from_xknx_takes_group_telegrams_only():
    write = Telegram(
        destination_address=GroupAddress("3/0/1"),
        source_address=IndividualAddress("1.1.20"),
        payload=apci.GroupValueWrite(DPTArray((0x0C, 0x1A))),
    )
    t = from_xknx(write, "9.001")
    assert (t["src"], t["dst"], t["type"], t["raw"], t["text"], t["num"], t["unit"]) == (
        "1.1.20",
        "3/0/1",
        "write",
        "0c1a",
        "21",
        21.0,
        "°C",
    )
    assert t["dir"] == "out" and t["via"] == "bus" and t["bits"] is False
    read = from_xknx(Telegram(destination_address=GroupAddress("3/0/1"), payload=apci.GroupValueRead()), "9.001")
    assert read["type"] == "read" and read["raw"] == "" and read["text"] is None
    bit = from_xknx(
        Telegram(destination_address=GroupAddress("1/0/1"), payload=apci.GroupValueResponse(DPTBinary(1))), None
    )
    assert bit["type"] == "response" and bit["raw"] == "01" and bit["bits"] is True and bit["text"] is None
    other = Telegram(destination_address=IndividualAddress("1.1.1"), payload=apci.DeviceDescriptorRead(descriptor=0))
    assert from_xknx(other, None) is None


def test_from_ha_takes_the_integrations_telegram():
    project = house.knxproject()
    t = from_ha(telegram_dict("1.1.20", "3/0/1", "write", bytes([0x0C, 0x1A]), project))
    assert (t["src"], t["dst"], t["type"], t["raw"], t["num"], t["unit"], t["dpt"]) == (
        "1.1.20",
        "3/0/1",
        "write",
        "0c1a",
        21.0,
        "°C",
        "9.001",
    )
    assert t["via"] == "ha" and t["dir"] == "in"
    switch = from_ha(telegram_dict(house.HA_ADDRESS, "1/0/1", "write", 1, project))
    assert switch["dir"] == "out" and switch["raw"] == "01" and switch["bits"] is True and switch["num"] == 1.0
    read = from_ha(telegram_dict(house.HA_ADDRESS, "1/1/1", "read", None, project))
    assert read["type"] == "read" and read["raw"] == ""
    assert from_ha({"telegramtype": "Other", "destination": "1/0/1"}) is None
    assert from_ha({"telegramtype": "GroupValueWrite"}) is None


def test_project_links_objects_to_addresses():
    project = Project.from_knxproject(house.knxproject())
    assert project.loaded and len(project.group_addresses) == len(house.GROUPS)
    hall = project.group_addresses["1/1/6"]
    assert hall["dpt"] == "1.011" and hall["range"] == "Lighting › Status"
    assert sorted(hall["devices"]) == ["1.1.1", "1.1.21"]
    assert (hall["senders"], hall["listeners"], hall["answerers"]) == (2, 0, 2)
    # An object listens on all its addresses and sends on the first
    central = project.group_addresses["0/0/1"]
    assert central["listeners"] == 7 and central["senders"] == 1
    assert project.group_addresses["3/0/9"]["dpt"] is None
    device = project.devices["1.1.10"]
    assert (device["maker"], device["line"], device["medium"], device["room"]) == (
        "MDT",
        "1.1",
        "TP",
        "House › Living room",
    )
    assert project.name_of("1.1.10") == "MDT Glass push button living room"
    assert project.name_of("1/0/1") == "Living room ceiling switch"
    assert project.dpt_of("9/9/9") is None
    # What ETS still has to download, by its own letters
    assert project.devices["1.1.11"]["pending"] == ["Grp"] and project.devices["1.2.10"]["pending"] == ["Par"]
    assert project.devices["1.1.1"]["pending"] == []
    # No type on the address: the one its object has (a single object, so they agree)
    assert project.group_addresses["3/0/9"]["dpt"] is None and project.dpt_of("3/0/9") == "1.001"


def test_no_project_is_an_empty_project():
    assert not Project.from_knxproject(None).loaded
    assert not Project.from_knxproject({"info": {}}).loaded


def test_expected_dpt_from_the_role_of_an_address():
    assert expected_dpt("light", "ga_switch", None) == "1"
    assert expected_dpt("light", "brightness_state_address", None) == "5"
    assert expected_dpt("cover", "position_address", None) == "5"
    assert expected_dpt("climate", "temperature_address", None) == "9"
    assert expected_dpt("climate", "operation_mode_address", None) == "20"
    assert expected_dpt("climate", "on_off_address", None) == "1"
    # What the entity says itself wins
    assert expected_dpt("sensor", "state_address", "temperature") == "9.001"
    assert expected_dpt("sensor", "ga_sensor", "5.001") == "5.001"
    # Not to be told from the role
    assert expected_dpt("sensor", "state_address", None) is None
    assert expected_dpt("light", "color_temperature_address", None) is None


def _entities():
    ha = MockHA(simulate=False)
    states = {s["entity_id"]: s for s in ha.states()}
    registry = [e for e in ha.registry() if e["platform"] == "knx"]
    return build_entities(registry, states, ha.by_group(), {"entities": house.HA_UI}, house.HA_YAML)


def test_entities_get_their_addresses_and_roles():
    entities, stray = _entities()
    by_id = {e["entity_id"]: e for e in entities}
    assert len(entities) == 20
    light = by_id["light.living_room_ceiling"]
    assert light["source"] == "ui"
    assert [(a["ga"], a["role"], a["use"]) for a in light["addresses"]] == [
        ("1/0/1", "ga_switch", "write"),
        ("1/1/1", "ga_switch", "state"),
        ("1/0/3", "ga_brightness", "write"),
        ("1/1/3", "ga_brightness", "state"),
    ]
    cover = by_id["cover.living_room_blind"]
    assert cover["source"] == "yaml" and cover["name"] == "Living room blind"
    assert {a["role"]: a["use"] for a in cover["addresses"]}["position_state_address"] == "state"
    climate = {a["role"]: a for a in by_id["climate.living_room"]["addresses"]}
    # The measured temperature is listened to, although its key does not say "state"
    assert climate["temperature_address"]["use"] == "state" and climate["temperature_address"]["expects"] == "9"
    assert by_id["sensor.outside_temperature"]["addresses"][0]["expects"] == "9.001"
    assert by_id["sensor.wind_speed"]["addresses"][0]["expects"] == "5.001"
    assert sorted(e["entity_id"] for e in stray) == ["light.attic", "switch.old_doorbell"]
