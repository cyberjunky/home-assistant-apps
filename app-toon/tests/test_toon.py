"""Tests against a fake bus; nothing here touches a real Toon."""

import asyncio
import time

import pytest

from src.boxtalk import BoxTalkClient, BoxTalkError, leaf_values, parse_devices, parse_head
from src.config import Config
from src.database import Database
from src.history import ARCHIVES, parse_rows, rrd_arguments
from src.metrics import BINDINGS, METRIC_BY_KEY, convert, device_matches
from src.monitor import ToonMonitor, classify_outage, parse_system
from src.mqtt import discovery_messages, state_payload

TH = "00000000-0000-4000-8000-000000000001"
DISCOVERY = (
    f'<discovery nts="ssdp:alive"><device uuid="{TH}" type="urn:schemas-hcb-hae-com:device:subdevice" '
    'intAddr="thermostatSettings" version="1"><service type="ThermostatInfo" version="(null)"></service></device>'
    '<device uuid="aaa" type="urn:schemas-hcb-hae-com:device:HAE_METER_v2_1" intAddr="3.1" version="1">'
    '<service type="GasFlowMeter" version="1"></service></device></discovery>'
)


class FakeWriter:
    def __init__(self):
        self.sent = []

    def write(self, data):
        self.sent.append(data)

    def close(self):
        pass


def bus():
    """A client on a stream the test feeds."""
    reader = asyncio.StreamReader()
    return reader, FakeWriter()


def query_response(variable, value):
    return (
        f'<query class="response" uuid="{TH}" destuuid="x" serviceid="s">\n'
        f'    <u:QueryStateVariableResponse xmlns:u="u"><{variable}>{value}</{variable}>\n'
        "    </u:QueryStateVariableResponse>\n</query>\0"
    ).encode()


def test_parse_discovery():
    devices = parse_devices(DISCOVERY)
    assert [d.address for d in devices] == ["thermostatSettings", "3.1"]
    assert devices[0].services == ["ThermostatInfo"]
    assert devices[1].type == "HAE_METER_v2_1"


def test_parse_head_and_leaves():
    xml = f'<notify uuid="{TH}" serviceid="urn:x"> <CurrentTemperature>19.77</CurrentTemperature> </notify>'
    tag, attrs = parse_head(xml)
    assert tag == "notify"
    assert attrs["uuid"] == TH
    assert leaf_values(xml) == {"CurrentTemperature": "19.77"}


async def test_query_and_notify_over_split_frames():
    reader, writer = bus()
    notified = []
    client = BoxTalkClient(reader, writer, lambda uuid, values: notified.append((uuid, values)))
    task = asyncio.create_task(client.read_forever())
    # The banner is not XML and must be ignored; a frame may arrive in pieces
    reader.feed_data(b"Home Control Box -- BoxTalk Hub Ready.\n\0")
    reader.feed_data(f'<notify uuid="{TH}"><Current'.encode())
    reader.feed_data(b"Temperature>19.77</CurrentTemperature></notify>\0")
    pending = asyncio.create_task(client.query(TH, "ThermostatInfo", "BoilerChPressure"))
    await asyncio.sleep(0)
    reader.feed_data(query_response("BoilerChPressure", "0.89"))
    assert await pending == "0.89"
    assert notified == [(TH, {"CurrentTemperature": "19.77"})]
    assert b"<varName>BoilerChPressure</varName>" in writer.sent[-1]
    assert writer.sent[-1].endswith(b"\0")
    task.cancel()


async def test_query_of_unknown_variable_is_none():
    reader, writer = bus()
    client = BoxTalkClient(reader, writer)
    task = asyncio.create_task(client.read_forever())
    pending = asyncio.create_task(client.query(TH, "ThermostatInfo", "Nope"))
    await asyncio.sleep(0)
    reader.feed_data(query_response("queryError", "Unknown state-var"))
    assert await pending is None
    task.cancel()


async def test_only_read_only_actions_can_be_sent():
    reader, writer = bus()
    client = BoxTalkClient(reader, writer)
    for name in ("SetSetpoint", "ChangeSchemeState", "RevertToFactoryDefaults", "SetBoilerType", "TestBoilerType"):
        with pytest.raises(ValueError):
            await client.action("cn:happ_thermstat", "Thermostat", name)
    assert writer.sent == []


async def test_action_sends_escaped_arguments_and_returns_values():
    reader, writer = bus()
    client = BoxTalkClient(reader, writer)
    task = asyncio.create_task(client.read_forever())
    pending = asyncio.create_task(client.action("cn:hcb_rrd", "specific1", "GetRraData", {"rra": "a<b"}))
    await asyncio.sleep(0)
    assert b"<rra>a&lt;b</rra>" in writer.sent[-1]
    reader.feed_data(
        b'<action class="response" uuid="cn:hcb_rrd"><u:GetRraDataResponse xmlns:u="u">'
        b'<rraData>{ "1": 2 }</rraData><result>ok</result></u:GetRraDataResponse></action>\0'
    )
    answer = await pending
    assert answer["rraData"] == '{ "1": 2 }'
    assert answer["result"] == "ok"
    task.cancel()


async def test_lost_connection_ends_reader_and_pending_request():
    reader, writer = bus()
    client = BoxTalkClient(reader, writer)
    task = asyncio.create_task(client.read_forever())
    pending = asyncio.create_task(client.query(TH, "ThermostatInfo", "CurrentTemperature", timeout=5))
    await asyncio.sleep(0)
    reader.feed_eof()
    with pytest.raises(BoxTalkError):
        await task
    with pytest.raises(BoxTalkError):
        await pending


async def test_request_times_out_without_answer():
    reader, writer = bus()
    client = BoxTalkClient(reader, writer)
    with pytest.raises(TimeoutError):
        await client.query(TH, "ThermostatInfo", "CurrentTemperature", timeout=0.01)


def test_convert_rejects_what_is_not_a_value():
    assert convert(METRIC_BY_KEY["burner_starts"], "65535") is None
    assert convert(METRIC_BY_KEY["gas_flow"], "NaN") is None
    assert convert(METRIC_BY_KEY["gas_flow"], "39.00") == 0.039
    assert convert(METRIC_BY_KEY["energy_import_low"], "1677228") == 1677.228
    assert convert(METRIC_BY_KEY["next_setpoint"], "2100.00") == 21.0
    assert convert(METRIC_BY_KEY["room_temperature"], None) is None
    assert convert(METRIC_BY_KEY["burner_state"], "heating") == "heating"


def test_every_binding_has_a_metric_and_meter_types_match():
    assert all(b.metric in METRIC_BY_KEY for b in BINDINGS)
    assert device_matches(r"HAE_METER_v\d+_1", "3.1", "HAE_METER_v2_1")
    assert not device_matches(r"HAE_METER_v\d+", "3.1", "HAE_METER_v2_1")


def test_outage_classification():
    assert classify_outage(120, 200) == "reboot"
    assert classify_outage(600, 80000) == "network"
    assert classify_outage(600, None) == "unknown"


def test_parse_system_output():
    output = (
        "78731.21 1000.00\n0.86 0.5 0.4 1/135 23888\nMemFree:  2940 kB\nCached:  38328 kB\nNow: 1794134794\n"
        "VmRSS:\t   67516 kB\nWeb: VmRSS:\t 2276 kB\nBusThreads: Threads:\t36\nBusFds: 22\n"
        "Wifi: wlan0: 0000   76.   78.    0.  0 0 0 0 0 0\n"
        " wlan0:       0 3380618    0 4448    0     0   0  0  0   91060\n"
    )
    values = parse_system(output)
    assert values["uptime_seconds"] == 78731.21
    assert values["load"] == 0.86
    assert values["memory_free"] == 2.9
    assert values["memory_gui"] == 65.9
    assert values["memory_web"] == 2.2
    assert values["bus_threads"] == 36
    assert values["bus_fds"] == 22
    assert values["toon_time"] == 1794134794
    assert values["wifi_signal"] == 78
    assert values["wifi_rx_dropped"] == 4448


def test_parse_system_survives_garbage():
    assert parse_system("") == {}
    assert parse_system("nonsense\nmore\nWeb:\nWifi: x\n") == {}


def test_history_request_and_rows():
    archive = next(a for a in ARCHIVES if a.logger == "gas_quantity" and a.rra == "5yrhours")
    from zoneinfo import ZoneInfo

    # 1640995200 is 2022-01-01 00:00 UTC, which is 01:00 in the Netherlands
    arguments = rrd_arguments(archive, 1640995200, 1640998800, ZoneInfo("Europe/Amsterdam"))
    assert arguments["from"] == "01-01-2022 01:00:00"
    assert arguments["to"] == "01-01-2022 02:00:00"
    assert (arguments["loggerName"], arguments["rra"]) == ("gas_quantity", "5yrhours")
    rows = parse_rows(archive, {"1640995200": 8063676, "1640998800": None, "result": "error"})
    assert rows == [("gas_total", 1640995200, 8063.676)]
    assert parse_rows(archive, {"1640995200": 1000}, shift=-60)[0][1] == 1640995140


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "t.db")
    yield db
    db.close()


def test_samples_rollup_and_retention(database):
    now = int(time.time())
    hour = now // 3600 * 3600 - 3600
    database.record("room_temperature", hour + 10, 19.0)
    database.record("room_temperature", hour + 20, 21.0)
    database.roll_up(hour)
    assert database.history(["room_temperature"], hour, now)["room_temperature"]
    row = database._db.execute("SELECT avg, min, max, last FROM hourly").fetchone()
    assert row == (20.0, 19.0, 21.0, 21.0)
    database.record("room_temperature", now - 200 * 86400, 5.0)
    database.prune(90)
    assert database.counts()["samples"] == 2
    assert database.history(["room_temperature"], now - 300 * 86400, now)["room_temperature"]


def test_live_values_win_over_history(database):
    database.record("setpoint", 1000, 20.0)
    database.flush()
    database.add_samples([("setpoint", 1000, 5.0), ("setpoint", 1060, 21.0)])
    rows = database._db.execute("SELECT ts, value FROM samples ORDER BY ts").fetchall()
    assert rows == [(1000, 20.0), (1060, 21.0)]


def test_outage_stays_single_while_retrying(database):
    database.open_outage(1000, "first", 3.0, 67.0)
    database.open_outage(1060, "retry", None, None)
    assert database.current_outage()["start_ts"] == 1000
    database.close_outage(1200, "network", 80000)
    outages = database.outages()
    assert len(outages) == 1
    assert (outages[0]["kind"], outages[0]["end_ts"], outages[0]["memory_gui"]) == ("network", 1200, 67.0)
    assert database.current_outage() is None


def make_monitor(database):
    config = Config(ssh_host="toon", ssh_password="x")
    return ToonMonitor(config, database, link=None)


def test_monitor_stores_changes_and_heartbeats_only(database):
    monitor = make_monitor(database)
    monitor.set_value("setpoint", "19.50", now=1000)
    monitor.set_value("setpoint", "19.50", now=1010)
    monitor.set_value("setpoint", "19.50", now=2000)
    monitor.set_value("setpoint", "20.00", now=2010)
    database.flush()
    rows = database._db.execute("SELECT ts FROM samples WHERE metric = 'setpoint' ORDER BY ts").fetchall()
    assert [r[0] for r in rows] == [1000, 2000, 2010]


def test_monitor_ignores_nonsense(database):
    monitor = make_monitor(database)
    monitor.set_value("gas_flow", "0.039e3")
    monitor.set_value("gas_flow", "NaN")
    assert monitor.values["gas_flow"] == 0.039
    monitor.set_value("no_such_metric", "1")
    assert "no_such_metric" not in monitor.values


def test_app_restart_closes_a_stale_outage(database):
    database.open_outage(1000, "app was stopped", None, None)
    make_monitor(database)
    assert database.current_outage() is None
    assert database.outages()[0]["kind"] == "app_stopped"


def test_mqtt_payload_and_discovery(database):
    monitor = make_monitor(database)
    monitor.set_value("preheating", "1")
    monitor.set_value("boiler_temperature", "53.59")
    payload = state_payload(monitor)
    assert payload == {"preheating": "ON", "boiler_temperature": 53.59}
    messages = dict(discovery_messages("00-00-000-000", "6.3.30", "homeassistant", "toon"))
    temperature = messages["homeassistant/sensor/toon_00_00_000_000/boiler_temperature/config"]
    assert temperature["unit_of_measurement"] == "°C"
    assert temperature["unique_id"] == "toon_00_00_000_000_boiler_temperature"
    assert "homeassistant/binary_sensor/toon_00_00_000_000/preheating/config" in messages
    connected = messages["homeassistant/binary_sensor/toon_00_00_000_000/connected/config"]
    assert connected["availability_topic"] == "toon/status"
    assert connected["state_topic"] == "toon/00-00-000-000/link"
