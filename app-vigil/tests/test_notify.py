"""Alerts reach Home Assistant: the event, the persistent notification and the notify action."""

from aiohttp import web
from aiohttp.test_utils import TestServer

from src import rules
from src.config import Config


async def test_firing_and_resolving_notify_home_assistant(monkeypatch):
    calls = []

    async def record(request):
        calls.append((request.path, request.headers.get("Authorization"), await request.json()))
        return web.json_response([])

    app = web.Application()
    app.router.add_post("/core/api/{tail:.*}", record)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    monkeypatch.setattr(rules, "CORE_API_URL", f"http://127.0.0.1:{server.port}/core/api")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "abc")
    home_assistant = rules.HomeAssistant(Config(notify_service="notify.mobile_app_phone"))
    rule = {"id": 3, "name": "Errors", "severity": "warning", "notify": True}
    await home_assistant.alert(rule, "firing", 34.0, "Errors: 34 (> 20)")
    await home_assistant.alert(rule, "resolved", 2.0, "Errors: resolved (2)")
    paths = [c[0] for c in calls]
    assert paths == [
        "/core/api/events/vigil_alert",
        "/core/api/services/persistent_notification/create",
        "/core/api/services/notify/mobile_app_phone",
        "/core/api/events/vigil_alert",
        "/core/api/services/persistent_notification/dismiss",
        "/core/api/services/notify/mobile_app_phone",
    ]
    assert all(c[1] == "Bearer abc" for c in calls)
    assert calls[0][2] == {
        "rule": 3,
        "name": "Errors",
        "state": "firing",
        "value": 34.0,
        "severity": "warning",
        "message": "Errors: 34 (> 20)",
    }
    assert calls[1][2]["notification_id"] == "vigil_rule_3"
    # Without notify only the event
    calls.clear()
    await home_assistant.alert({**rule, "notify": False}, "firing", 1.0, "x")
    assert [c[0] for c in calls] == ["/core/api/events/vigil_alert"]
    await server.close()


async def test_alerts_go_to_any_action_such_as_telegram(monkeypatch):
    calls = []

    async def record(request):
        calls.append((request.path, await request.json()))
        return web.json_response([])

    app = web.Application()
    app.router.add_post("/core/api/{tail:.*}", record)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    monkeypatch.setattr(rules, "CORE_API_URL", f"http://127.0.0.1:{server.port}/core/api")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "abc")
    home_assistant = rules.HomeAssistant(Config(notify_service="telegram_bot.send_message"))
    await home_assistant.alert(
        {"id": 4, "name": "Disk", "severity": "critical", "notify": True}, "firing", 0.9, "Disk: 90 %"
    )
    assert calls[-1] == (
        "/core/api/services/telegram_bot/send_message",
        {"title": "Vigil: Disk", "message": "Disk: 90 %"},
    )
    await server.close()
