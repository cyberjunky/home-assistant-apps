"""The database helper refuses every request it can't trust, before it does anything."""

import stat
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import helper
from src.helper import Helper, TaskError, check_database, dump_path, make_app


@pytest.mark.parametrize("name", ["homeassistant_copy", "restored", "Ab_1"])
def test_good_database_names(name):
    assert check_database(name, new=True) == name


@pytest.mark.parametrize(
    "name", ["", "1abc", "a b", 'x"; DROP DATABASE y; --', "a-b", "x" * 64, None, 5, "postgres", "template1"]
)
def test_bad_database_names(name):
    with pytest.raises(TaskError):
        check_database(name)


def test_the_chronicle_database_is_never_a_target():
    check_database("homeassistant")  # may be dumped
    with pytest.raises(TaskError):
        check_database("homeassistant", new=True)  # never restored over, renamed or removed


def test_dump_files_stay_in_the_backup_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(helper, "BACKUP_DIR", tmp_path)
    assert dump_path("homeassistant-20261113-223020.dump") == tmp_path / "homeassistant-20261113-223020.dump"
    for bad in ["../x.dump", "/etc/passwd", ".hidden.dump", "a/b.dump", "x.sql", "..dump", "x..dump", None, ""]:
        with pytest.raises(TaskError):
            dump_path(bad)


@pytest.fixture
async def client():
    async with TestClient(TestServer(make_app(Helper()))) as c:
        yield c


@pytest.mark.parametrize(
    ("path", "body", "message"),
    [
        ("/dump", {"database": "x; rm -rf /"}, "database name"),
        ("/restore", {"file": "../../etc/passwd", "database": "x"}, "file name"),
        ("/restore", {"file": "missing.dump", "database": "x"}, "no such backup"),
        ("/use", {"database": "homeassistant"}, "Not allowed"),
        ("/drop", {"database": "postgres"}, "Not allowed"),
        (
            "/copy",
            {"host": "-oProxyCommand=x", "username": "u", "password": "p", "database": "d", "target": "t"},
            "host",
        ),
        (
            "/copy",
            {"host": "db", "port": 99999, "username": "u", "password": "p", "database": "d", "target": "t"},
            "port",
        ),
        ("/copy", {"host": "db", "username": "-U", "password": "p", "database": "d", "target": "t"}, "user"),
        ("/copy", {"host": "db", "username": "u", "password": "a\nb", "database": "d", "target": "t"}, "password"),
        (
            "/copy",
            {"host": "db", "username": "u", "password": "p", "database": "--help", "target": "t"},
            "database name",
        ),
        (
            "/copy",
            {"host": "db", "username": "u", "password": "p", "database": "d", "target": "t", "drop_tables": "public.x"},
            "drop_tables",
        ),
    ],
)
async def test_requests_are_checked(client, tmp_path, monkeypatch, path, body, message):
    monkeypatch.setattr(helper, "BACKUP_DIR", tmp_path)
    response = await client.post(path, json=body)
    assert response.status == 400
    assert message.lower() in (await response.json())["error"].lower()


async def test_not_json_is_refused(client):
    response = await client.post("/dump", data="database=homeassistant")
    assert response.status == 400


async def test_password_file_escapes_and_is_private():
    path = Path((await Helper()._password_env("pa:ss\\word"))["PGPASSFILE"])
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert path.read_text() == "*:*:*:*:pa\\:ss\\\\word\n"
    finally:
        path.unlink()
