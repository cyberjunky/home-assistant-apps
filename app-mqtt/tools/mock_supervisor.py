"""A stand-in for the Supervisor, to run the app's image outside Home Assistant (development only).

Run it on a Docker network under the name "supervisor" and start the image on that network with
SUPERVISOR_TOKEN=test. It answers what bashio and the dashboard ask: the options, the time zone,
discovery, the mqtt service, the logins of Home Assistant users (user "ron", password "secret"),
and Home Assistant's MQTT config entry with its reconfigure flow. GET /_calls shows what it got.

    python3 -m tools.mock_supervisor [options.json]
"""

import json
import sys

from aiohttp import web

CALLS: list[dict] = []
OPTIONS = {
    "logins": [{"username": "tasmota", "password": "tasmota-pass"}],
    "log_dest": [],
    "log_type": [],
    "require_certificate": False,
    "certfile": "fullchain.pem",
    "keyfile": "privkey.pem",
    "customize": {"active": False, "folder": "mosquitto"},
    "keep_days": 30,
    "capture_payloads": True,
}
ENTRY = {"entry_id": "mqtt1", "domain": "mqtt", "title": "core-mosquitto", "state": "loaded"}


def ok(data=None) -> web.Response:
    return web.json_response({"result": "ok", "data": data if data is not None else {}})


async def record(request: web.Request) -> dict:
    body = await request.text()
    call = {"method": request.method, "path": request.path_qs, "body": body[:2000]}
    CALLS.append(call)
    return call


async def options(request: web.Request) -> web.Response:
    await record(request)
    return ok(OPTIONS)


async def info(request: web.Request) -> web.Response:
    await record(request)
    return ok({"timezone": "Europe/Amsterdam", "hostname": "homeassistant"})


async def anything(request: web.Request) -> web.Response:
    await record(request)
    return ok({"uuid": "x"})


async def auth(request: web.Request) -> web.Response:
    call = await record(request)
    try:
        body = json.loads(call["body"])
    except ValueError:
        from urllib.parse import parse_qs

        body = {k: v[0] for k, v in parse_qs(call["body"]).items()}
    if body.get("username") == "ron" and body.get("password") == "secret":
        return ok()
    return web.json_response({"result": "error", "message": "Username or password is wrong"}, status=401)


async def entries(request: web.Request) -> web.Response:
    await record(request)
    return web.json_response([ENTRY])


async def flow_start(request: web.Request) -> web.Response:
    await record(request)
    return web.json_response(
        {
            "type": "form",
            "flow_id": "flow1",
            "step_id": "broker",
            "data_schema": [
                {"name": "broker", "type": "string", "required": True, "description": {"suggested_value": "core-mosquitto"}},
                {"name": "port", "type": "integer", "required": True, "default": 1883},
                {"name": "protocol", "type": "select", "default": "3.1.1"},
                {"name": "username", "type": "string", "description": {"suggested_value": "homeassistant"}},
                {"name": "password", "type": "string", "description": {"suggested_value": "**PWD_NOT_CHANGED**"}},
                {"name": "other_settings", "type": "expandable", "schema": [{"name": "keepalive", "default": 60}]},
            ],
            "errors": {},
        }
    )


async def flow_step(request: web.Request) -> web.Response:
    await record(request)
    ENTRY["title"] = "switched"
    return web.json_response({"type": "abort", "flow_id": "flow1", "reason": "reconfigure_successful"})


async def calls(request: web.Request) -> web.Response:
    return web.json_response(CALLS)


def main() -> None:
    if len(sys.argv) > 1:
        OPTIONS.update(json.loads(open(sys.argv[1]).read()))
    app = web.Application()
    app.router.add_get("/addons/self/options/config", options)
    app.router.add_get("/info", info)
    app.router.add_post("/discovery", anything)
    app.router.add_post("/services/mqtt", anything)
    app.router.add_delete("/services/mqtt", anything)
    app.router.add_post("/auth", auth)
    app.router.add_get("/core/api/config/config_entries/entry", entries)
    app.router.add_post("/core/api/config/config_entries/flow", flow_start)
    app.router.add_post("/core/api/config/config_entries/flow/{flow_id}", flow_step)
    app.router.add_delete("/core/api/config/config_entries/flow/{flow_id}", anything)
    app.router.add_get("/_calls", calls)
    app.router.add_route("*", "/{tail:.*}", anything)
    web.run_app(app, port=80, print=None)


if __name__ == "__main__":
    main()
