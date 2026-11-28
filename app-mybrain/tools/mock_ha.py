"""A stand-in Home Assistant for development: the Supervisor's Core proxy, a websocket that sends
mobile_app.share events, an AI Task entity that answers with a plausible sort, and the states API.

    python3 -m tools.mock_ha            # port 18123
    curl -X POST localhost:18123/mock/share -d '{"url": "https://example.com", "caller": "com.android.chrome"}'

MOCK_NO_AI=1 runs it without an AI Task entity.
"""

import datetime as dt
import json
import os
import re
import uuid

from aiohttp import WSMsgType, web

subscribers: list[tuple[web.WebSocketResponse, int]] = []
states: dict[str, dict] = {}
AI = not os.environ.get("MOCK_NO_AI")
DEVICE = "d3v1c3"
# A Local Calendar named MyBrain (MOCK_NO_CALENDAR=1: none)
CALENDAR = None if os.environ.get("MOCK_NO_CALENDAR") else "calendar.mybrain"
events: dict[str, dict] = {}


def calendar_command(data: dict) -> dict:
    kind = data["type"]
    if data.get("entity_id") != CALENDAR:
        return {"success": False, "error": {"code": "not_found", "message": "Entity not found"}}
    if kind == "calendar/event/create":
        uid = uuid.uuid4().hex
        events[uid] = {"uid": uid, **data["event"]}
    elif kind == "calendar/event/update":
        if data["uid"] not in events:
            return {"success": False, "error": {"code": "failed", "message": "no such event"}}
        events[data["uid"]] = {"uid": data["uid"], **data["event"]}
    elif kind == "calendar/event/delete":
        events.pop(data["uid"], None)
    return {"success": True, "result": None}


def as_api(event: dict) -> dict:
    def when(value: str) -> dict:
        if len(value) == 10:
            return {"date": value}
        return {"dateTime": dt.datetime.fromisoformat(value).astimezone().isoformat()}

    return {
        "uid": event["uid"],
        "summary": event["summary"],
        "description": event.get("description", ""),
        "start": when(event["dtstart"]),
        "end": when(event["dtend"]),
    }


async def websocket(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    await ws.send_json({"type": "auth_required", "ha_version": "2026.11.3"})
    async for message in ws:
        if message.type != WSMsgType.TEXT:
            continue
        data = json.loads(message.data)
        if data.get("type") == "auth":
            await ws.send_json({"type": "auth_ok", "ha_version": "2026.11.3"})
        elif data.get("type") == "subscribe_events":
            subscribers.append((ws, data["id"]))
            await ws.send_json({"id": data["id"], "type": "result", "success": True, "result": None})
        elif str(data.get("type", "")).startswith("calendar/event/"):
            await ws.send_json({"id": data["id"], "type": "result", **calendar_command(data)})
        elif data.get("type") == "config/device_registry/list":
            await ws.send_json(
                {"id": data["id"], "type": "result", "success": True, "result": [{"id": DEVICE, "name": "Pixel 9"}]}
            )
        else:
            await ws.send_json({"id": data.get("id"), "type": "result", "success": False, "error": {"code": "unknown"}})
    return ws


async def share(request: web.Request) -> web.Response:
    data = await request.json()
    data.setdefault("device_id", DEVICE)
    sent = 0
    for ws, sub_id in list(subscribers):
        if ws.closed:
            subscribers.remove((ws, sub_id))
            continue
        await ws.send_json({"id": sub_id, "type": "event", "event": {"event_type": "mobile_app.share", "data": data}})
        sent += 1
    return web.json_response({"sent": sent})


async def get_states(request: web.Request) -> web.Response:
    listed = list(states.values())
    if AI:
        listed.append({"entity_id": "ai_task.mock_ai", "state": "unknown", "attributes": {"friendly_name": "Mock AI"}})
    return web.json_response(listed)


async def get_state(request: web.Request) -> web.Response:
    entity = request.match_info["entity"]
    if entity == CALENDAR:
        return web.json_response({"entity_id": entity, "state": "off", "attributes": {"friendly_name": "MyBrain"}})
    if entity in states:
        return web.json_response(states[entity])
    return web.json_response({"message": "Entity not found."}, status=404)


async def get_calendar(request: web.Request) -> web.Response:
    if request.match_info["entity"] != CALENDAR:
        return web.json_response({"message": "Entity not found"}, status=404)
    return web.json_response([as_api(e) for e in events.values()])


async def set_state(request: web.Request) -> web.Response:
    body = await request.json()
    entity = request.match_info["entity"]
    states[entity] = {"entity_id": entity, **body}
    return web.json_response(states[entity])


GUESSES = [
    (r"home assistant|esphome|zigbee|mqtt|hacs|ai task", "Home Assistant", ["home-assistant", "smart-home"]),
    (r"bitcoin|crypto|mining|bitaxe|freqtrade", "Crypto & Finance", ["bitcoin", "mining"]),
    (r"recipe|recept|pasta|soup|sourdough|risotto|bake|cook", "Recipes & Food", ["recipe", "cooking"]),
    (r"solar|heat pump|garden|diy|energy", "Home & DIY", ["energy", "solar"]),
    (r"python|linux|docker|github|proxmox|rust|server|raspberry|asyncio|self-host|selfhosted", "Tech", ["selfhosted"]),
    (r"lisbon|lofoten|travel|hotel|flight|camping|starship|mars", "Travel", ["travel"]),
    (r"marathon|running|fitness", "Health & Sport", ["running"]),
    (r"rick astley|video|music|movie", "Entertainment", ["music"]),
    (r"gift|mom|anne|birthday", "Personal", ["family"]),
]


async def generate_data(request: web.Request) -> web.Response:
    if not AI:
        return web.json_response({"message": "Service ai_task.generate_data not found"}, status=400)
    body = await request.json()
    text = body.get("instructions", "")
    title = re.search(r"^Title: (.*)$", text, re.MULTILINE)
    # What is known about the item, not the list of existing tags above it
    lower = text.split("Existing tags:", 1)[-1].split("\n", 1)[-1].lower()
    category, tags = "Other", ["misc"]
    # The title says the most; the rest only when the title says nothing
    for where in (title.group(1).lower() if title else "", lower):
        for pattern, cat, cat_tags in GUESSES:
            if re.search(pattern, where):
                category, tags = cat, cat_tags
                break
        if category != "Other":
            break
    purpose = "watch" if "youtube" in lower else "read"
    # A plausible summary: the first sentence of the page's description, else the note
    found = re.search(r"^(?:Description|Their own note): (.*)$", text, re.MULTILINE)
    summary = re.split(r"(?<=[.!?])\s", found.group(1).strip())[0][:220] if found else ""
    if not summary:
        summary = f"About {title.group(1)[:80]}." if title else "A note to self."
    return web.json_response(
        {
            "changed_states": [],
            "service_response": {
                "conversation_id": "x",
                "data": {
                    "category": category,
                    "tags": tags,
                    "purpose": purpose,
                    "priority": "normal",
                    "summary": summary,
                },
            },
        }
    )


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/core/websocket", websocket)
    app.router.add_post("/mock/share", share)
    app.router.add_get("/core/api/states", get_states)
    app.router.add_get("/core/api/states/{entity}", get_state)
    app.router.add_post("/core/api/states/{entity}", set_state)
    app.router.add_get("/core/api/calendars/{entity}", get_calendar)
    app.router.add_post("/core/api/services/ai_task/generate_data", generate_data)
    return app


if __name__ == "__main__":
    web.run_app(make_app(), port=int(os.environ.get("MOCK_PORT", "18123")))
