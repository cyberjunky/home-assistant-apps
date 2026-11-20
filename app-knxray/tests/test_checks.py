"""The checks: the made-up installation of tools/installation.py has the mistakes, the checks find them."""

import time

from src.bus import ReadOnlyCEMIHandler, allowed, mask_text
from src.checks import CATALOG, run_checks
from src.model import HAConfig, Project, build_entities
from src.monitor import Monitor
from src.telegrams import from_ha
from tools import installation as house
from tools.mock_ha import MockHA, telegram_dict


def scene(telegrams=(), reach=None, connected=True, project=True, at=1000.0, **options):
    """The installation, what Home Assistant has, and some telegrams: the findings by code."""
    data = house.knxproject()
    mock = MockHA(simulate=False)
    ha = HAConfig(present=True, loaded=True, connection={"connected": connected, "current_address": house.HA_ADDRESS})
    ha.entities, ha.stray_entities = build_entities(
        [e for e in mock.registry() if e["platform"] == "knx"],
        {s["entity_id"]: s for s in mock.states()},
        mock.by_group(),
        {"entities": house.HA_UI},
        house.HA_YAML,
    )
    ha.stray_devices = [{"id": "dev_old", "name": "Old garden shed"}]
    ha.exposes = {"sensor.living_room_temperature": ["3/0/8"]}
    monitor = Monitor()
    ts = at
    for source, address, kind, payload in telegrams:
        ts += 0.03
        t = from_ha(telegram_dict(source, address, kind, payload, data))
        t["ts"] = ts
        monitor.add(t)
    monitor.tick(ts + 10)
    found = run_checks(Project.from_knxproject(data if project else None), ha, monitor, reach or {}, **options)
    by_code: dict[str, list[dict]] = {}
    for finding in found:
        by_code.setdefault(finding["code"], []).append(finding)
    return by_code, found


def subjects(by_code, code):
    return sorted(f["subject"] for f in by_code.get(code, []))


def test_every_finding_is_in_the_catalog_and_worst_comes_first():
    by_code, found = scene(house.Simulation().startup())
    assert set(by_code) <= set(CATALOG)
    order = [{"error": 0, "warning": 1, "info": 2}[f["severity"]] for f in found]
    assert order == sorted(order)
    assert len({f["id"] for f in found}) == len(found)
    for severity, _category, title, fix in CATALOG.values():
        assert severity in ("error", "warning", "info") and title and fix.endswith(".")


def test_the_project_alone():
    by_code, _found = scene()
    assert subjects(by_code, "ga_no_dpt") == ["3/0/9"]
    assert subjects(by_code, "ga_unused") == ["6/0/10", "6/0/9"]
    assert subjects(by_code, "dpt_conflict") == ["3/0/6"]
    assert subjects(by_code, "device_no_links") == ["1.1.30"]
    # The time is sent and nobody listens; Home Assistant listening counts as a listener
    assert "0/0/2" in subjects(by_code, "ga_no_listener") and "4/0/1" not in subjects(by_code, "ga_no_listener")
    assert "1/0/9" in subjects(by_code, "ga_no_sender")
    assert not any(code.startswith(("read_un", "read_multi", "ga_unknown", "src_", "payload")) for code in by_code)
    # What the project alone gives away
    assert subjects(by_code, "read_flag_twice") == ["1/1/6"]
    assert subjects(by_code, "line_no_supply") == ["1.2"] and subjects(by_code, "line_no_coupler") == ["1.1", "1.2"]
    assert "line_crowded" not in by_code


def test_home_assistant_against_the_project():
    by_code, _found = scene()
    assert subjects(by_code, "ha_ga_not_in_project") == [
        "light.garden_lights|1/0/19",
        "sensor.living_room_temperature|3/0/8",
    ]
    assert subjects(by_code, "ha_dpt_mismatch") == ["sensor.wind_speed|4/0/2"]
    assert "DPT 9.005" in by_code["ha_dpt_mismatch"][0]["detail"]
    assert subjects(by_code, "ha_state_no_answer") == [
        "light.bedroom_ceiling|1/1/7",
        "sensor.bedroom_temperature|3/0/6",
    ]
    assert subjects(by_code, "ha_unavailable") == ["light.garden_lights"]
    assert subjects(by_code, "ha_stray_entity") == ["light.attic", "switch.old_doorbell"]
    assert subjects(by_code, "ha_stray_device") == ["dev_old"]
    assert subjects(by_code, "ha_duplicate") == ["light.kitchen_ceiling_old"]
    assert "ha_write_no_listener" not in by_code and "ha_missing" not in by_code


def test_home_assistant_itself():
    by_code, _found = scene(connected=False)
    assert "ha_disconnected" in by_code and "ha_unavailable" not in by_code
    by_code, _found = scene(project=False)
    assert "ha_no_project" in by_code
    # Without a project nothing can be held against it
    assert not any(code in by_code for code in ("ha_ga_not_in_project", "ga_unknown", "ga_no_dpt"))
    missing = run_checks(Project(), HAConfig(error="The KNX integration is not set up."), Monitor(), {})
    assert [f["code"] for f in missing] == ["ha_missing"]


def test_what_the_bus_shows():
    sim = house.Simulation()
    telegrams = [
        *sim.startup(),
        ("1.2.11", "3/0/6", "write", bytes([20])),  # one byte on a two-byte float
        ("1.1.99", "7/3/1", "write", 1),  # a stranger, to an address nobody knows
        ("1.1.11", "1/0/8", "write", 1),  # a push button that is not linked there
        ("15.15.255", "1/0/1", "write", 1),  # never programmed
        (house.HA_ADDRESS, "1/0/8", "write", 1),  # Home Assistant is not a stranger
    ]
    by_code, _found = scene(telegrams)
    assert subjects(by_code, "payload_mismatch") == ["3/0/6"]
    assert "1 byte" in by_code["payload_mismatch"][0]["detail"]
    assert subjects(by_code, "ga_unknown") == ["7/3/1"]
    assert subjects(by_code, "src_unknown") == ["1.1.99"]
    assert subjects(by_code, "src_unprogrammed") == ["15.15.255"]
    assert subjects(by_code, "foreign_sender") == ["1/0/8"]
    assert (
        "1.1.11" in by_code["foreign_sender"][0]["detail"]
        and house.HA_ADDRESS not in by_code["foreign_sender"][0]["detail"]
    )
    assert subjects(by_code, "read_unanswered") == ["1/1/7"]
    assert subjects(by_code, "read_multi") == ["1/1/6"]


def test_busy_addresses_and_devices():
    wind = [("1.2.10", "4/0/2", "write", house.float16(3.0 + i % 2)) for i in range(120)]
    by_code, _found = scene(wind, chatty_per_minute=30)
    assert subjects(by_code, "chatty_ga") == ["4/0/2"] and subjects(by_code, "chatty_src") == ["1.2.10"]
    assert "bus_load" not in by_code  # long ago: the load is about the last hour
    by_code, _found = scene(wind, at=time.time() - 30)
    assert subjects(by_code, "bus_load") == ["load"]  # 33 a second in this test
    by_code, _found = scene(wind, chatty_per_minute=200)
    assert "chatty_ga" not in by_code
    same = [("1.1.10", "1/0/1", "write", 1)] * 15
    by_code, _found = scene(same)
    assert subjects(by_code, "repeats") == ["1/0/1"]


def test_devices_that_were_asked():
    reach = {
        "1.1.12": {"reachable": False},
        "1.1.11": {"reachable": True, "progmode": True},
        "1.2.1": {"reachable": True, "load_state": "unloaded"},
        "1.1.1": {"reachable": True, "load_state": "loaded", "progmode": False},
        "1.1.99": {"reachable": True, "mask": "07B0"},
        "1.1.200": {"reachable": False},
    }
    by_code, _found = scene(reach=reach)
    assert subjects(by_code, "device_unreachable") == ["1.1.12"]
    assert subjects(by_code, "device_progmode") == ["1.1.11"]
    assert subjects(by_code, "device_unloaded") == ["1.2.1"]
    assert subjects(by_code, "device_not_in_project") == ["1.1.99"]


def test_silent_devices_and_an_address_used_twice():
    by_code, _found = scene([("1.1.20", "3/0/1", "write", house.float16(21.0))], observed=7200.0)
    silent = subjects(by_code, "device_silent")
    assert "1.2.10" in silent and "1.1.20" not in silent and "1.1.30" not in silent
    by_code, _found = scene(observed=600.0)
    assert "device_silent" not in by_code
    by_code, _found = scene(own_addresses=("1.1.3",))
    assert subjects(by_code, "address_conflict") == ["1.1.3"]


def test_only_reads_leave_the_apps_connection():
    from xknx.dpt import DPTBinary
    from xknx.telegram import GroupAddress, IndividualAddress, Telegram, apci, tpci

    def group(payload):
        return Telegram(destination_address=GroupAddress("1/0/1"), payload=payload)

    def device(control, payload=None):
        return Telegram(destination_address=IndividualAddress("1.1.1"), tpci=control, payload=payload)

    assert allowed(group(apci.GroupValueRead()))
    assert not allowed(group(apci.GroupValueWrite(DPTBinary(1))))
    assert not allowed(group(apci.GroupValueResponse(DPTBinary(1))))
    assert allowed(device(tpci.TConnect())) and allowed(device(tpci.TDisconnect())) and allowed(device(tpci.TAck(0)))
    assert allowed(device(tpci.TDataConnected(0), apci.DeviceDescriptorRead(descriptor=0)))
    assert allowed(
        device(tpci.TDataConnected(0), apci.PropertyValueRead(object_index=0, property_id=11, count=1, start_index=1))
    )
    for refused in (
        apci.Restart(),
        apci.MemoryWrite(address=0x100, count=1, data=b"\x00"),
        apci.PropertyValueWrite(object_index=0, property_id=54, count=1, start_index=1, data=b"\x01"),
        apci.IndividualAddressWrite(address=IndividualAddress("1.1.5")),
    ):
        assert not allowed(device(tpci.TDataConnected(0), refused)), refused
    assert issubclass(ReadOnlyCEMIHandler, object) and ReadOnlyCEMIHandler.send_telegram.__qualname__.startswith(
        "ReadOnly"
    )
    assert mask_text(0x07B0) == "07B0 (TP, System B)" and mask_text(0x091A) == "091A (TP, IP router)"
    assert mask_text(0x27B0) == "27B0 (RF, System B)" and mask_text(0xFFFF) == "FFFF"


def test_what_a_device_holds_and_frames_sent_again():
    held = ["0/0/1", "1/0/4", "1/0/5", "1/0/8"]  # the kitchen push button, programmed from an older project
    by_code, _found = scene(reach={"1.1.11": {"reachable": True, "group_addresses": held}})
    assert subjects(by_code, "device_table_differs") == ["1.1.11"]
    assert "not linked in the project: 1/0/8" in by_code["device_table_differs"][0]["detail"]
    same = scene(reach={"1.1.11": {"reachable": True, "group_addresses": ["0/0/1", "1/0/4", "1/0/5"]}})[0]
    assert "device_table_differs" not in same
    clash = scene(reach={"1.1.3": {"reachable": True, "address_clash": True, "stopped": True}})[0]
    assert subjects(clash, "device_address_clash") == ["1.1.3"] and subjects(clash, "device_stopped") == ["1.1.3"]
    tables = scene(
        reach={"1.1.3": {"reachable": True, "objects": [{"type": "Association table", "load_state": "error"}]}}
    )[0]
    assert (
        subjects(tables, "device_unloaded") == ["1.1.3"]
        and "Association table: error" in tables["device_unloaded"][0]["detail"]
    )
    # As a real device has it: an interface program nobody loaded is no fault
    spare = [{"type": "Application", "load_state": "loaded"}, {"type": "Interface program", "load_state": "unloaded"}]
    assert "device_unloaded" not in scene(reach={"1.1.3": {"reachable": True, "objects": spare}})[0]

    from src.model import HAConfig, Project
    from src.monitor import Monitor

    monitor = Monitor()
    for i in range(6):
        monitor.add({"ts": 50.0 + i, "dir": "in", "src": "1.1.10", "dst": "1/0/1", "type": "write", "raw": "01", "bits": True,
                     "text": "on", "num": 1.0, "unit": None, "dpt": "1.001", "rep": i % 2 == 0, "hops": 1})  # fmt: skip
    found = run_checks(Project.from_knxproject(house.knxproject()), HAConfig(present=True, loaded=True), monitor, {})
    codes = {f["code"]: f for f in found}
    assert codes["resent"]["subject"] == "1.1.10" and "3 of its 6" in codes["resent"]["detail"]
    assert codes["hops_exhausted"]["subject"] == "1.1.10"


def test_objects_read_from_a_device_and_couplers():
    project = Project.from_knxproject(house.knxproject())

    def held(device, change=None):
        out = []
        for co in (project.objects[o] for o in project.devices[device]["objects"]):
            item = {
                "number": co["number"] + 1,
                "flags": dict(co["flags"]),
                "group_addresses": list(co["group_addresses"]),
            }
            if change:
                change(co["number"], item)
            out.append(item)
        return out

    # The same as the project, counted from 1: nothing to say
    assert (
        "device_objects_differ" not in scene(reach={"1.1.20": {"reachable": True, "held_objects": held("1.1.20")}})[0]
    )

    def lost_write_flag(number, item):
        if number == 1:
            item["flags"]["write"] = False
        if number == 4:
            item["group_addresses"].append("3/0/7")

    by_code = scene(reach={"1.1.20": {"reachable": True, "held_objects": held("1.1.20", lost_write_flag)}})[0]
    detail = by_code["device_objects_differ"][0]["detail"]
    assert (
        "object 1 (Setpoint Setpoint) flags W differ" in detail
        and "object 4" in detail
        and "also linked to 3/0/7" in detail
    )

    # Tables that fit nothing were read wrongly: no finding
    nonsense = [{"number": n, "flags": {}, "group_addresses": ["9/9/9"]} for n in range(1, 6)]
    assert "device_objects_differ" not in scene(reach={"1.1.20": {"reachable": True, "held_objects": nonsense}})[0]

    router = {
        "main_groups_low": "filters",
        "main_groups_high": "filters",
        "sub_groups_low": "passes all",
        "sub_groups_high": "passes all",
    }
    assert subjects(scene(reach={"1.0.1": {"reachable": True, "router": router}})[0], "coupler_open") == ["1.0.1"]
    router["main_groups_high"] = "blocks all"
    blocked = scene(reach={"1.0.1": {"reachable": True, "router": router}})[0]
    assert subjects(blocked, "coupler_blocks") == ["1.0.1"] and "coupler_open" not in blocked


def test_what_bus_monitor_mode_counted():
    quiet = {
        "frames": 500,
        "data": 250,
        "repeated": 1,
        "acked": 249,
        "nak": 0,
        "busy": 0,
        "unanswered": 0,
        "errors": 0,
        "bad_checksum": 0,
    }
    assert not any(code.startswith("wire_") for code in scene(wire=quiet)[0])
    noisy = {**quiet, "repeated": 30, "busy": 2, "unanswered": 9, "errors": 3, "unanswered_to": ["7/3/1 (9)"]}
    by_code = scene(wire=noisy)[0]
    assert {"wire_errors", "wire_repeats", "wire_refused", "wire_unanswered"} <= set(by_code)
    assert "7/3/1 (9)" in by_code["wire_unanswered"][0]["detail"] and by_code["wire_errors"][0]["severity"] == "error"
