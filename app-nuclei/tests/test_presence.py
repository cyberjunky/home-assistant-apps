import asyncio
import socket

import pytest

from src.config import Config
from src.database import Database
from src.presence import PresenceMonitor, is_online, tcp_answers


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    yield d
    d.close()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


async def test_open_port_means_online():
    server = await asyncio.start_server(lambda r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    async with server:
        assert await tcp_answers("127.0.0.1", port)


async def test_refused_connection_still_means_online():
    # Nothing listens, but the host replied with a reset, so it is there
    assert await tcp_answers("127.0.0.1", free_port())


async def test_silence_means_offline():
    async def silent(host, port):
        return False

    async def no_ping(host):
        return False

    assert not await is_online("10.0.0.99", [22], tcp=silent, ping=no_ping)


async def test_ping_is_the_fallback():
    async def silent(host, port):
        return False

    async def answers(host):
        return True

    assert await is_online("10.0.0.99", [], tcp=silent, ping=answers)


async def test_known_ports_are_tried_first_and_the_count_is_capped():
    tried = []

    async def tcp(host, port):
        tried.append(port)
        return False

    async def no_ping(host):
        return False

    await is_online("h", [8080, 9000], tcp=tcp, ping=no_ping)
    assert tried[:2] == [8080, 9000] and len(tried) == 4


def test_presence_flips_are_timed(db):
    did = db.record_device("10.0.0.5", None, None, [], [])
    assert db.device(did)["online"] is None
    db.set_presence(did, True)
    first = db.device(did)
    assert first["online"] == 1 and first["status_changed_at"]
    # Still online: the "since" time must not move
    db._db.execute("UPDATE devices SET status_changed_at = 1000 WHERE id = ?", (did,))
    db.set_presence(did, True)
    assert db.device(did)["status_changed_at"] == 1000
    db.set_presence(did, False)
    gone = db.device(did)
    assert gone["online"] == 0 and gone["status_changed_at"] > 1000 and gone["last_online"] == first["last_online"]


def test_a_scan_finding_ports_marks_the_device_online(db):
    did = db.record_device("10.0.0.5", None, None, [], [22])
    assert db.device(did)["online"] == 1
    db.set_presence(did, False)
    db.record_device("10.0.0.5", None, None, [], [22])
    assert db.device(did)["online"] == 1


async def test_only_authorised_enabled_objects_are_checked(db):
    ok = db.add_object("10.0.0.5", "ip", "", None, authorised=True)
    unauth = db.add_object("10.0.0.6", "ip", "", None, authorised=False)
    off = db.add_object("10.0.0.7", "ip", "", None, authorised=True)
    db.update_object(off, {"enabled": 0})
    for host, oid in (("10.0.0.5", ok), ("10.0.0.6", unauth), ("10.0.0.7", off)):
        db.record_device(host, oid, None, [], [])
    probed = []

    async def check(host, ports):
        probed.append(host)
        return True

    assert await PresenceMonitor(db, 5, check=check).check_all() == 1
    assert probed == ["10.0.0.5"]


async def test_check_all_records_each_result(db):
    oid = db.add_object("10.0.0.0/30", "cidr", "", None, authorised=True)
    up = db.record_device("10.0.0.1", oid, None, [], [])
    down = db.record_device("10.0.0.2", oid, None, [], [])

    async def check(host, ports):
        return host == "10.0.0.1"

    await PresenceMonitor(db, 5, check=check).check_all()
    assert db.device(up)["online"] == 1 and db.device(down)["online"] == 0
    summary = db.summary()
    assert summary["devices_online"] == 1 and summary["devices_total"] == 2


def test_option_is_read_and_bounded():
    assert Config.from_options({}).presence_interval_minutes == 5
    assert Config.from_options({"presence_interval_minutes": 0}).presence_interval_minutes == 0
    assert Config.from_options({"presence_interval_minutes": 99999}).presence_interval_minutes == 1440
