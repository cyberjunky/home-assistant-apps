"""Changing the thermostat: only fixed commands with checked numbers, and only when allowed."""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import control
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

    monitor._query_all = refresh
    monitor._poll_fast = refresh
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
