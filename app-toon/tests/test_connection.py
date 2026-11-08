"""The connection, end to end: a real SSH session with the Toon's old algorithms, to a fake Toon."""

import asyncio
import re
import time

import asyncssh
import pytest

from src import monitor as monitor_module
from src import toon_link
from src.boxtalk import BoxTalkError
from src.config import Config
from src.database import Database
from src.history import ARCHIVES, HistoryImporter
from src.monitor import ToonMonitor
from src.toon_link import LEGACY_ALGORITHMS, CredentialsRejected, HostKeyChanged, ToonLink

PASSWORD = "secret"
# The client insists on ssh-rsa as host key; a server has no such option but offers it for an RSA key
SERVER_ALGORITHMS = {key: value for key, value in LEGACY_ALGORITHMS.items() if key != "server_host_key_algs"}
_ACTION = re.compile(r"<u:(\w+)[ >]")


class FakeToon:
    """An SSH server that answers like a Toon, as far as a session needs."""

    def __init__(self):
        self.uptime = 90000.0
        self.key = asyncssh.generate_private_key("ssh-rsa")
        self.server = None
        self.port = 0

    async def start(self):
        toon = self

        class Server(asyncssh.SSHServer):
            def begin_auth(self, username):
                return True

            def password_auth_supported(self):
                return True

            def validate_password(self, username, password):
                return password == PASSWORD

            def connection_requested(self, dest_host, dest_port, orig_host, orig_port):
                return toon.bus

        self.server = await asyncssh.listen(
            "127.0.0.1",
            0,
            server_factory=Server,
            server_host_keys=[self.key],
            process_factory=self.command,
            **SERVER_ALGORITHMS,
        )
        self.port = self.server.get_port()

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    def command(self, process):
        if process.command == "hostname":
            process.stdout.write("faketoon\n")
        else:
            process.stdout.write(f"{self.uptime} 0\n0.1 0.1 0.1 1/1 1\nMemFree: 2048 kB\nNow: {int(time.time())}\n")
        process.exit(0)

    @staticmethod
    async def bus(reader, writer):
        """Answers every action, so a session doesn't have to wait for time-outs."""
        buffer = b""
        while data := await reader.read(65536):
            buffer += data if isinstance(data, bytes) else data.encode()
            *frames, buffer = buffer.split(b"\0")
            for frame in frames:
                text = frame.decode()
                match = _ACTION.search(text)
                if text.startswith("<action") and match:
                    name = match[1]
                    answer = (
                        f'<action class="response"><u:{name}Response xmlns:u="u"><a>1</a></u:{name}Response></action>\0'
                    )
                    writer.write(answer.encode() if isinstance(data, bytes) else answer)


class Network:
    """Between the app and the Toon. Going dark loses all packets without closing anything, like a dead WiFi link."""

    def __init__(self, port):
        self.port = port
        self.dark = False
        self.server = None
        self.listen_port = 0
        self.writers = []

    async def start(self):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.listen_port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        for writer in self.writers:
            writer.close()

    async def _pump(self, reader, writer, tasks_dead):
        while data := await reader.read(65536):
            if self.dark or tasks_dead["dead"]:
                # A connection that saw the outage stays dead, also when the network is back
                tasks_dead["dead"] = True
                continue
            writer.write(data)
        writer.close()

    async def _client(self, reader, writer):
        self.writers.append(writer)
        state = {"dead": self.dark}
        if self.dark:
            await self._pump(reader, writer, state)
            return
        up_reader, up_writer = await asyncio.open_connection("127.0.0.1", self.port)
        self.writers.append(up_writer)
        await asyncio.gather(self._pump(reader, up_writer, state), self._pump(up_reader, writer, state))


async def until(condition, timeout=20):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        await asyncio.sleep(0.02)


def fast_connection(monkeypatch):
    monkeypatch.setattr(toon_link, "KEEPALIVE_INTERVAL", 0.3)
    monkeypatch.setattr(toon_link, "KEEPALIVE_COUNT", 2)
    monkeypatch.setattr(toon_link, "CONNECT_TIMEOUT", 1)
    monkeypatch.setattr(monitor_module, "RECONNECT_MIN", 0.1)
    monkeypatch.setattr(monitor_module, "RECONNECT_MAX", 0.3)
    monkeypatch.setattr(monitor_module, "DISCOVERY_WAIT", 0)


@pytest.fixture
async def toon():
    fake = FakeToon()
    await fake.start()
    yield fake
    await fake.stop()


@pytest.fixture
def database(tmp_path):
    db = Database(tmp_path / "t.db")
    yield db
    db.close()


def config_for(port, password=PASSWORD, **extra):
    return Config(
        ssh_host="127.0.0.1", ssh_port=port, ssh_username="root", ssh_password=password, import_history=False, **extra
    )


@pytest.mark.parametrize(("uptime_after", "kind"), [(90000.0, "network"), (10.0, "reboot")])
async def test_silent_outage_is_noticed_and_classified(toon, database, tmp_path, monkeypatch, uptime_after, kind):
    fast_connection(monkeypatch)
    network = Network(toon.port)
    await network.start()
    monitor = ToonMonitor(
        config_for(network.listen_port), database, ToonLink(config_for(network.listen_port), tmp_path / "key")
    )
    task = asyncio.create_task(monitor.run())
    try:
        await until(lambda: monitor.connected and monitor.host_name == "faketoon")

        # Nothing closes the connection: only the keepalives can tell it's gone
        network.dark = True
        lost_at = time.monotonic()
        await until(lambda: not monitor.connected)
        assert time.monotonic() - lost_at < 5
        assert database.current_outage() is not None

        # Stays down while the network is, retrying without ending
        await asyncio.sleep(1.5)
        assert not task.done()
        assert not monitor.connected

        toon.uptime = uptime_after
        network.dark = False
        await until(lambda: monitor.connected)
        assert database.current_outage() is None
        assert database.outages()[0]["kind"] == kind
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await network.stop()


async def test_wrong_password_is_reported_as_such(toon, tmp_path):
    with pytest.raises(CredentialsRejected):
        await ToonLink(config_for(toon.port, password="wrong"), tmp_path / "key").connect()


async def test_changed_host_key_is_refused_until_trusted(toon, tmp_path):
    key_file = tmp_path / "key"
    session = await ToonLink(config_for(toon.port), key_file).connect()
    session.close()
    assert key_file.exists()
    remembered = key_file.read_text()

    other = FakeToon()
    await other.start()
    try:
        with pytest.raises(HostKeyChanged):
            await ToonLink(config_for(other.port), key_file).connect()
        assert key_file.read_text() == remembered

        session = await ToonLink(config_for(other.port, trust_new_host_key=True), key_file).connect()
        session.close()
        assert key_file.read_text() != remembered
    finally:
        await other.stop()


async def test_unexpected_error_doesnt_end_the_logging(database, monkeypatch):
    monkeypatch.setattr(monitor_module, "RECONNECT_MIN", 0)

    class Session:
        def close(self):
            pass

    class Link:
        async def connect(self):
            return Session()

    monitor = ToonMonitor(config_for(22), database, Link())
    served = []

    async def serve(session):
        served.append(session)
        if len(served) == 1:
            raise RuntimeError("a bug")
        await asyncio.Event().wait()

    monkeypatch.setattr(monitor, "_serve", serve)
    task = asyncio.create_task(monitor.run())
    try:
        await until(lambda: len(served) == 2)
        assert not task.done()
        assert monitor.last_error == "a bug"
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_failed_import_keeps_the_session(database, monkeypatch):
    monitor = ToonMonitor(Config(ssh_host="x", ssh_password="x", import_history=True), database, None)

    async def fail(bus, host):
        raise BoxTalkError("no answer")

    monkeypatch.setattr(monitor.importer, "import_all", fail)
    task = asyncio.create_task(monitor._history(None, None))
    await asyncio.sleep(0.01)
    assert not task.done()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_history_ahead_of_now_is_skipped(database):
    daily = next(a for a in ARCHIVES if a.logger == "gas_quantity" and a.rra == "10yrdays")
    now = int(time.time())
    rows = [("gas_total", now - 86400, 1.0), ("gas_total", now + 3600, 2.0)]
    assert HistoryImporter(database)._store(daily, rows) == 1
    stored = database.history(["gas_total"], now - 2 * 86400, now + 2 * 86400)["gas_total"]
    assert len(stored) == 1
