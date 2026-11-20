import asyncio
import json

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from src.config import BotConfig, Config
from src.discovery import DiscoveryService, identity
from src.discovery_settings import DiscoveryConfig, parse_discovery, parse_ports

CONFIG = {
    "bot_name": "Example",
    "strategy": "Trend",
    "dry_run": True,
    "exchange": "kraken",
    "trading_mode": "spot",
    "stake_currency": "USD",
}


@pytest.mark.parametrize("ports", ["", "0", "65536", "90-80", "1-4097", "1-2-3", "abc"])
def test_reject_bad_port_ranges(ports):
    with pytest.raises(ValueError):
        parse_ports(ports)


def test_ports_are_bounded_and_deduplicated():
    assert parse_ports("8080,9000-9002,9001") == (8080, 9000, 9001, 9002)


@pytest.mark.parametrize("host", ["example.org", "10.0.0.0/24", "0.0.0.0", "224.0.0.1", ""])
def test_reject_non_unicast_ip(host):
    with pytest.raises(ValueError):
        parse_discovery({"enabled": True, "host": host, "username": "u", "password": "p"})


def test_disabled_discovery_needs_no_credentials():
    assert not Config.from_options({}).discovery.enabled
    assert not parse_discovery({"enabled": False, "host": "invalid"}).enabled


def test_discovery_can_use_own_login_without_shared_credentials():
    cfg = parse_discovery({"enabled": True, "host": "::1", "username": "u", "password": "secret"})
    assert cfg.url(8080) == "http://[::1]:8080"
    assert "secret" not in repr(cfg)


def test_optional_shared_login_and_partial_override():
    options = {"enabled": True, "host": "127.0.0.1"}
    shared = {"username": "u", "password": "p", "ws_token": "token"}
    assert parse_discovery(options, shared).password == "p"
    assert parse_discovery(options, shared).ws_token == "token"
    with pytest.raises(ValueError):
        parse_discovery({**options, "username": "other"}, shared)


class FakeClient:
    def __init__(self, config, *_):
        self.config = config
        self.slug = config.slug
        self.data = {}
        self.connected = self.ws_connected = True
        self.error = ""

    async def run(self):
        await asyncio.Event().wait()


@pytest.fixture
async def service(tmp_path, monkeypatch):
    monkeypatch.setattr("src.discovery.BotClient", FakeClient)
    settings = DiscoveryConfig(True, "127.0.0.1", (8080,), username="user", password="secret")
    registered = []
    svc = DiscoveryService(settings, tmp_path / "discovery.json", 30, None, [], registered.append)
    svc.registered = registered
    yield svc
    for task in svc.tasks.values():
        task.cancel()
    await asyncio.gather(*svc.tasks.values(), return_exceptions=True)


def record(config=None, port=8080):
    return {"url": f"http://127.0.0.1:{port}", "port": port, "config": config or CONFIG}


async def test_new_bot_is_added_once_and_registry_excludes_secrets(service):
    await service.accept(record())
    await service.accept(record())
    assert len(service.registered) == 1
    assert len(service.tasks) == 1
    service.save()
    assert "secret" not in service.path.read_text()
    assert "password" not in service.path.read_text()
    assert "secret" not in json.dumps(service.status())


async def test_changed_identity_retires_old_client(service):
    await service.accept(record())
    first = service.registered[0]
    await service.accept(record({**CONFIG, "strategy": "Different"}))
    assert len(service.registered) == 2
    assert not first.connected and not first.ws_connected
    assert first.slug not in service.tasks
    assert service.records[first.slug]["retired"]


async def test_restore_retains_offline_known_bot_and_ignores_out_of_scope(service):
    good = record()
    foreign = record(port=9000)
    service.path.write_text(
        json.dumps({identity(good["url"], CONFIG): good, identity(foreign["url"], CONFIG): foreign})
    )
    await service.restore()
    assert len(service.registered) == 1
    assert service.registered[0].config.url == good["url"]
    # Re-scanning the same endpoint keeps the stable history key.
    await service.accept(good)
    assert len(service.registered) == 1


async def test_corrupt_registry_does_not_destroy_history(service):
    service.path.write_text("broken JSON")
    await service.restore()
    assert "could not be read" in service.error
    assert service.path.read_text() == "broken JSON"


async def test_scan_adds_new_bot_and_keeps_it_after_server_stops(service):
    app = web.Application()

    async def ping(request):
        assert "Authorization" not in request.headers
        return web.json_response({"status": "pong"})

    async def config(request):
        assert request.headers["Authorization"] == aiohttp.BasicAuth("user", "secret").encode()
        return web.json_response(CONFIG)

    app.router.add_get("/api/v1/ping", ping)
    app.router.add_get("/api/v1/show_config", config)
    server = TestServer(app)
    await server.start_server()
    service.settings.ports = (server.port,)
    try:
        await service.scan_once()
        assert service.last_found == 1
        assert len(service.registered) == 1
    finally:
        await server.close()
    await service.scan_once()
    assert service.last_found == 0
    assert len(service.records) == 1  # no deletion on outage


async def test_redirects_do_not_receive_credentials(service):
    app = web.Application()
    seen = []

    async def ping(request):
        raise web.HTTPFound("/outside")

    async def outside(request):
        seen.append(request.headers.get("Authorization"))
        return web.json_response({"status": "pong"})

    app.router.add_get("/api/v1/ping", ping)
    app.router.add_get("/outside", outside)
    async with TestServer(app) as server:
        service.settings.ports = (server.port,)
        await service.scan_once()
    assert not seen
    assert not service.records


async def test_manual_endpoint_is_not_probed(service):
    service.manual_urls = {"http://127.0.0.1:8080"}
    assert await service.probe(None, 8080, asyncio.Semaphore(1)) is None


async def test_unauthenticated_or_non_freqtrade_service_is_not_added(service):
    app = web.Application()

    async def ping(request):
        return web.json_response({"status": "pong"})

    async def config(request):
        return web.Response(status=401)

    app.router.add_get("/api/v1/ping", ping)
    app.router.add_get("/api/v1/show_config", config)
    async with TestServer(app) as server:
        service.settings.ports = (server.port,)
        await service.scan_once()
    assert not service.records


def test_manual_configuration_remains_supported():
    cfg = Config.from_options(
        {"bots": [{"name": "Manual", "url": "http://127.0.0.1:8080", "username": "u", "password": "p"}]}
    )
    assert isinstance(cfg.bots[0], BotConfig)


async def test_full_registry_does_not_retire_existing_client(service, monkeypatch):
    await service.accept(record())
    first = service.registered[0]
    monkeypatch.setattr("src.discovery.MAX_BOTS", 1)
    await service.accept(record({**CONFIG, "strategy": "Replacement"}))
    assert first.connected
    assert not service.records[first.slug]["retired"]
    assert first.slug in service.tasks
    assert len(service.registered) == 1
    assert "limit" in service.error


def test_thousand_port_server_range_is_supported():
    ports = parse_ports("9000-9999")
    assert len(ports) == 1000
    assert ports[0] == 9000 and ports[-1] == 9999


def test_multiple_ranges_cannot_exceed_total_limit():
    with pytest.raises(ValueError):
        parse_ports("1-3000,4000-6000")
