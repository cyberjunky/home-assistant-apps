"""Changing the thermostat: only fixed commands with checked numbers, and only when allowed."""

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import control
from src.boxtalk import BoxTalkError, Device
from src.config import Config
from src.control import CommandRefused, check_answer, plan
from src.database import Database
from src.monitor import ToonMonitor
from src.webui import WebUI

OK = b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n{"result":"ok"}'


def test_setpoint_command():
    description, commands = plan({"setpoint": 20.5}, {})
    assert commands == ["/happ_thermstat?action=setSetpoint&Setpoint=2050"]
    assert "20.5" in description
    assert plan({"setpoint": 20.3}, {})[1] == ["/happ_thermstat?action=setSetpoint&Setpoint=2050"]


def test_setpoint_at_a_preset_activates_that_preset():
    values = {"preset_comfort": 21.0, "preset_home": 19.5, "preset_sleep": 16.5, "preset_away": 16.0}
    commands = plan({"setpoint": 19.5}, values)[1]
    assert commands == [
        "/happ_thermstat?action=setSetpoint&Setpoint=1950",
        "/happ_thermstat?action=changeSchemeState&state=2&temperatureState=1",
    ]
    # Two presets with the same temperature leave open which is meant
    assert len(plan({"setpoint": 19.5}, {**values, "preset_away": 19.5})[1]) == 1


def test_preset_and_program_commands():
    assert plan({"preset": 2}, {})[1] == ["/happ_thermstat?action=changeSchemeState&state=2&temperatureState=2"]
    assert plan({"program": True}, {})[1] == ["/happ_thermstat?action=changeSchemeState&state=1"]
    assert plan({"program": False}, {})[1] == ["/happ_thermstat?action=changeSchemeState&state=0"]
    assert plan({"program": False}, {"active_state": 4})[1] == [
        "/happ_thermstat?action=changeSchemeState&state=0&temperatureState=1"
    ]


@pytest.mark.parametrize(
    "body",
    [
        {},
        [],
        {"setpoint": 5.9},
        {"setpoint": 30.5},
        {"setpoint": "21"},
        {"setpoint": True},
        {"setpoint": float("nan")},
        {"setpoint": 21, "preset": 1},
        {"preset": 4},
        {"preset": True},
        {"preset": "1&state=8"},
        {"program": 1},
        {"action": "RevertToFactoryDefaults"},
    ],
)
def test_anything_else_is_refused(body):
    with pytest.raises(ValueError):
        plan(body, {})


def test_answers():
    check_answer(OK)
    for raw in (
        b"",
        b"HTTP/1.0 500 Oops\r\n\r\n{}",
        b"HTTP/1.0 200 OK\r\n\r\nnot json",
        b"HTTP/1.0 200 OK\r\n\r\n[]",
        b'HTTP/1.0 200 OK\r\n\r\n{"success":false}',
    ):
        with pytest.raises(CommandRefused):
            check_answer(raw)


class FakeSession:
    def __init__(self, answer=OK):
        self.answer = answer
        self.sent = []

    async def web_get(self, path):
        self.sent.append(path)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


class FakeMqtt:
    connected = False
    broker = None


def make(tmp_path, **options):
    config = Config.from_options({"ssh": {"host": "toon", "password": "x"}, **options})
    database = Database(tmp_path / "toon.db")
    monitor = ToonMonitor(config, database, None)
    calls = []

    async def refresh(_bus):
        calls.append(True)

    monitor._read_after_change = refresh
    return monitor, WebUI(0, database, monitor, FakeMqtt()), database, calls


def connect(monitor, session):
    monitor.connected = True
    monitor._session = session
    monitor._bus = object()


async def post(ui, body, content_type="application/json"):
    import json

    async with TestClient(TestServer(ui.app)) as client:
        response = await client.post("/api/control", data=json.dumps(body), headers={"Content-Type": content_type})
        return response.status, await response.json()


async def test_a_change_is_sent_and_the_state_read_back(tmp_path):
    monitor, ui, database, calls = make(tmp_path)
    session = FakeSession()
    connect(monitor, session)
    assert (await post(ui, {"preset": 1}))[0] == 200
    assert session.sent == [control.preset_command(1)]
    await asyncio.sleep(0)
    assert calls
    database.close()


async def test_read_only_sends_nothing(tmp_path):
    monitor, ui, database, _ = make(tmp_path, read_only=True)
    session = FakeSession()
    connect(monitor, session)
    assert (await post(ui, {"preset": 1}))[0] == 403
    assert session.sent == []
    database.close()


async def test_refusals_reach_the_page(tmp_path):
    monitor, ui, database, _ = make(tmp_path)
    assert (await post(ui, {"preset": 1}))[0] == 503  # not connected
    session = FakeSession(b"HTTP/1.0 500 Oops\r\n\r\n{}")
    connect(monitor, session)
    status, answer = await post(ui, {"preset": 1})
    assert status == 502
    assert "500" in answer["error"]
    assert (await post(ui, {"setpoint": 99}))[0] == 400
    assert (await post(ui, {"preset": 1}, "text/plain"))[0] == 415
    session.answer = OSError("gone")
    assert (await post(ui, {"preset": 1}))[0] == 502
    database.close()


async def test_under_home_assistant_only_ingress_may_change(tmp_path, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    monitor, ui, database, _ = make(tmp_path)
    session = FakeSession()
    connect(monitor, session)
    assert (await post(ui, {"preset": 1}))[0] == 403
    assert session.sent == []
    database.close()


async def test_web_request_goes_through_the_ssh_session(monkeypatch):
    """The request itself, over a real SSH connection like the one to a Toon."""
    import asyncio

    import asyncssh

    from src import toon_link

    seen = []

    async def web_server(reader, writer):
        request = await reader.readuntil(b"\r\n\r\n")
        seen.append(request.decode())
        writer.write(OK)
        writer.write_eof()

    class Server(asyncssh.SSHServer):
        def begin_auth(self, username):
            return False

        def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
            seen.append((dest_host, dest_port))
            return web_server

    server = await asyncssh.listen(
        "127.0.0.1", 0, server_factory=Server, server_host_keys=[asyncssh.generate_private_key("ssh-ed25519")]
    )
    port = server.sockets[0].getsockname()[1]
    connection = await asyncssh.connect("127.0.0.1", port, known_hosts=None, username="x", client_keys=None)
    try:
        raw = await asyncio.wait_for(toon_link.ToonSession(connection).web_get("/happ_thermstat?action=x"), 10)
    finally:
        connection.close()
        server.close()
    check_answer(raw)
    assert seen[0] == ("127.0.0.1", toon_link.WEB_PORT)
    assert seen[1].startswith("GET /happ_thermstat?action=x HTTP/1.0\r\n")


async def test_the_answer_does_not_wait_for_reading_the_state_back(tmp_path):
    monitor, ui, database, _ = make(tmp_path)
    connect(monitor, FakeSession())
    release = asyncio.Event()
    finished = []

    async def slow(_bus):
        await release.wait()
        finished.append(True)

    monitor._read_after_change = slow
    status, _ = await asyncio.wait_for(post(ui, {"preset": 1}), 5)
    assert status == 200
    assert not finished
    release.set()
    await asyncio.sleep(0.01)
    assert finished
    database.close()


async def test_reading_back_only_asks_for_what_a_change_affects(tmp_path):
    config = Config.from_options({"ssh": {"host": "toon", "password": "x"}})
    database = Database(tmp_path / "toon.db")
    monitor = ToonMonitor(config, database, None)
    asked = []

    class Bus:
        async def query(self, _uuid, _service, variable):
            asked.append(variable)
            return "2100"

    monitor._devices_of = lambda _bus, _binding: [Device("u", "t", "a")]

    async def poll(_bus):
        asked.append("poll")

    monitor._poll_fast = poll
    await monitor._read_after_change(Bus())
    assert "CurrentSetpoint" in asked
    assert "ProgramState" in asked
    assert "CurrentBoilerTemperature" not in asked
    assert asked[-1] == "poll"

    # A lost connection during this is no reason to fail anything
    class Lost:
        async def query(self, *_):
            raise BoxTalkError("gone")

    await monitor._read_after_change(Lost())
    database.close()


# Home Assistant's climate entity over MQTT


def test_climate_commands_map_to_the_dashboard_changes():
    from src.mqtt import climate_change

    assert climate_change("setpoint", "20.5") == {"setpoint": 20.5}
    assert climate_change("preset", "sleep") == {"preset": 2}
    assert climate_change("mode", "auto") == {"program": True}
    assert climate_change("mode", "heat") == {"program": False}
    for command, payload in (("preset", "eco"), ("mode", "off"), ("fan", "on"), ("setpoint", "warm")):
        with pytest.raises(ValueError):
            climate_change(command, payload)


def test_climate_discovery_and_read_only():
    from src.mqtt import discovery_messages

    topic = "homeassistant/climate/toon_00_00_000_000/thermostat/config"
    climate = dict(discovery_messages("00-00-000-000", "6.3.30", "homeassistant", "toon"))[topic]
    assert climate["temperature_command_topic"] == "toon/00-00-000-000/set/setpoint"
    assert climate["preset_modes"] == ["comfort", "home", "sleep", "away"]
    assert climate["current_temperature_topic"] == "toon/00-00-000-000/state"
    read_only = dict(discovery_messages("00-00-000-000", "6.3.30", "homeassistant", "toon", controls=False))[topic]
    assert not any(key.endswith("command_topic") for key in read_only)


def test_climate_templates_render():
    """The templates Home Assistant will run, with Jinja as it would."""
    jinja2 = pytest.importorskip("jinja2")
    from src.mqtt import discovery_messages

    climate = dict(discovery_messages("s", "v", "homeassistant", "toon"))[
        "homeassistant/climate/toon_s/thermostat/config"
    ]
    env = jinja2.Environment()
    env.filters["int"] = lambda value, default=0: int(float(value)) if value not in (None, "") else default

    def render(key, **state):
        return env.from_string(climate[key]).render(value_json=state).strip()

    assert render("preset_mode_value_template", active_state=2.0) == "sleep"
    assert render("preset_mode_value_template", active_state=4.0) == "none"
    assert render("mode_state_template", program_state=0.0) == "heat"
    assert render("mode_state_template", program_state=2.0) == "auto"
    assert render("action_template", burner_state="preheating") == "heating"
    assert render("action_template", burner_state="hot_water") == "idle"
    assert render("current_temperature_template", room_temperature=18.9) == "18.9"


class FakeMessage:
    def __init__(self, topic, payload, retain=False):
        self.topic, self.payload, self.retain = topic, payload.encode(), retain


async def test_climate_command_goes_to_the_toon(tmp_path):
    from src.mqtt import MqttPublisher

    monitor, _ui, _database, _calls = make(tmp_path)
    monitor.info["serialNumber"] = "00-00-000-000"
    session = FakeSession()
    connect(monitor, session)
    publisher = MqttPublisher(monitor.config, None, monitor)
    publisher._loop = asyncio.get_running_loop()
    publisher._on_message(None, None, FakeMessage("toon/00-00-000-000/set/setpoint", "20.5"))
    # A retained command, or one for another Toon, is ignored
    publisher._on_message(None, None, FakeMessage("toon/00-00-000-000/set/preset", "sleep", retain=True))
    publisher._on_message(None, None, FakeMessage("toon/11-11-111-111/set/preset", "sleep"))
    for _ in range(50):
        await asyncio.sleep(0.02)
        if session.sent:
            break
    await asyncio.sleep(0.1)
    assert session.sent == ["/happ_thermstat?action=setSetpoint&Setpoint=2050"]


async def test_read_only_takes_no_climate_commands(tmp_path):
    from src.mqtt import MqttPublisher

    monitor, _ui, _database, _calls = make(tmp_path, read_only=True)
    monitor.info["serialNumber"] = "s"
    session = FakeSession()
    connect(monitor, session)
    publisher = MqttPublisher(monitor.config, None, monitor)
    await publisher._command("setpoint", "20")
    assert session.sent == []
