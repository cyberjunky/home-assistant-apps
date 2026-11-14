"""The sidebar page: what it serves, and how it tells whether PBS answers."""

import json
import socket
import urllib.error
import urllib.request

import pytest

from src import panel


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def server():
    srv = panel.start(free_port())
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def get(url: str) -> tuple[int, str, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers["Content-Type"], r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers["Content-Type"], e.read()


def test_page_has_the_version_and_relative_links(server):
    status, kind, body = get(server + "/")
    page = body.decode()
    assert status == 200 and kind.startswith("text/html")
    assert "__VERSION__" not in page
    # Under ingress the page lives below a path: every link must be relative
    assert 'href="static/app.css' in page and 'src="static/app.js' in page
    assert 'href="/' not in page and 'src="/' not in page


def test_static_files_and_nothing_else(server):
    assert get(server + "/static/app.js")[0] == 200
    assert get(server + "/static/app.css")[1].startswith("text/css")
    assert get(server + "/static/../config.py")[0] == 404
    assert get(server + "/static/panel.py")[0] == 404


def test_status_says_pbs_is_down_when_its_port_is_closed(server, monkeypatch):
    monkeypatch.setattr(panel, "PBS_PORT", free_port())
    status, kind, body = get(server + "/api/status")
    assert status == 200 and kind == "application/json"
    assert json.loads(body)["pbs"] is False


def test_pbs_answers_is_false_without_a_listener():
    assert panel.pbs_answers("127.0.0.1", free_port(), 1.0) is False
