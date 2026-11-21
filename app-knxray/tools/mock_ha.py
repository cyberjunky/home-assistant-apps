"""A stand-in Home Assistant for development: the parts of the Supervisor proxy the app uses, answering
for the made-up installation in tools/installation.py.

It serves ``/core/websocket`` (the KNX integration's commands, the registries, the states) and
``/core/api`` (the KNX diagnostics and the knx.read service), and sends the telegrams of the simulated
house to whoever subscribed.
"""

import asyncio
import contextlib
import datetime
import logging
from typing import Any

from aiohttp import WSMsgType, web

from src.telegrams import transcoder_for
from tools import installation as house

logger = logging.getLogger(__name__)

ENTRY_ID = "01KNXMOCKENTRY"


def telegram_dict(source: str, address: str, kind: str, payload: Any, project: dict[str, Any]) -> dict[str, Any]:
    """A telegram the way Home Assistant's KNX integration reports it."""
    ga = project["group_addresses"].get(address)
    dpt = ga["dpt"] if ga else None
    value, unit, name = None, None, None
    if dpt:
        transcoder = transcoder_for(f"{dpt['main']}.{dpt['sub']:03d}" if dpt["sub"] else str(dpt["main"]))
        if transcoder is not None:
            unit, name = transcoder.unit, transcoder.value_type
            if payload is not None:
                from xknx.dpt import DPTArray, DPTBinary

                with contextlib.suppress(Exception):
                    decoded = transcoder.from_knx(DPTBinary(payload) if isinstance(payload, int) else DPTArray(payload))
                    value = (
                        decoded
                        if isinstance(decoded, bool | int | float | str)
                        else getattr(decoded, "name", str(decoded)).lower()
                    )
    device = project["devices"].get(source)
    return {
        "data_secure": False,
        "destination": address,
        "destination_name": ga["name"] if ga else "",
        "direction": "Outgoing" if source == house.HA_ADDRESS else "Incoming",
        "dpt_main": dpt["main"] if dpt else None,
        "dpt_sub": dpt["sub"] if dpt else None,
        "dpt_name": name,
        "payload": payload if payload is None or isinstance(payload, int) else list(payload),
        "source": source,
        "source_name": f"{device['manufacturer_name']} {device['name']}" if device else "",
        "telegramtype": {"write": "GroupValueWrite", "response": "GroupValueResponse", "read": "GroupValueRead"}[kind],
        "timestamp": datetime.datetime.now().astimezone().isoformat(),
        "unit": unit,
        "value": value,
    }


class MockHA:
    def __init__(self, simulate: bool = True):
        self.project = house.knxproject()
        self.simulation = house.Simulation()
        self.simulate = simulate
        self.subscribers: dict[web.WebSocketResponse, int] = {}
        self.removed: set[str] = set()
        # What the integration's own telegram store answers to knx/query_telegrams
        self.stored: list[dict[str, Any]] = []
        self.app = web.Application()
        self.app.router.add_get("/core/websocket", self._websocket)
        self.app.router.add_get("/core/api/diagnostics/config_entry/{entry_id}", self._diagnostics)
        self.app.router.add_post("/core/api/services/knx/read", self._service_read)
        self.app.router.add_get("/info", self._info)

    async def start(self, port: int) -> None:
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        if self.simulate:
            task = asyncio.create_task(self._run())
            task.add_done_callback(lambda t: None if t.cancelled() else t.exception())

    async def _run(self) -> None:
        await asyncio.sleep(3)
        await self.emit(self.simulation.startup())
        while True:
            await asyncio.sleep(0.5)
            await self.emit(self.simulation.step())

    async def emit(self, telegrams: list[tuple[str, str, str, Any]]) -> None:
        for source, address, kind, payload in telegrams:
            event = telegram_dict(source, address, kind, payload, self.project)
            for ws, msg_id in list(self.subscribers.items()):
                with contextlib.suppress(ConnectionError, RuntimeError):
                    await ws.send_json({"id": msg_id, "type": "event", "event": event})
            await asyncio.sleep(0.03)

    # ---- what Home Assistant knows ----

    def registry(self) -> list[dict[str, Any]]:
        entries = [
            {
                "entity_id": e["entity_id"],
                "platform": "knx",
                "unique_id": e["unique_id"],
                "name": None,
                "original_name": e["name"],
                "disabled_by": None,
                "device_id": "dev_light" if e["platform"] == "light" and e["ui"] else None,
            }
            for e in house.ha_entities()
        ]
        entries += [
            {
                "entity_id": entity_id,
                "platform": "knx",
                "unique_id": unique_id,
                "name": None,
                "original_name": name,
                "disabled_by": None,
                "device_id": None,
            }
            for entity_id, _platform, unique_id, name in house.HA_STRAY
        ]
        entries.append({"entity_id": "sun.sun", "platform": "sun", "unique_id": "sun", "device_id": None})
        return [e for e in entries if e["entity_id"] not in self.removed]

    def states(self) -> list[dict[str, Any]]:
        out = []
        for e in house.ha_entities():
            state = {"light": "on", "switch": "off", "binary_sensor": "off", "cover": "open", "climate": "heat"}.get(
                e["platform"], "21.4"
            )
            if e["entity_id"] == "light.garden_lights":
                state = "unavailable"
            out.append({"entity_id": e["entity_id"], "state": state, "attributes": {"friendly_name": e["name"]}})
        for entity_id, _platform, _uid, name in house.HA_STRAY:
            out.append(
                {
                    "entity_id": entity_id,
                    "state": "unavailable",
                    "attributes": {"friendly_name": name, "restored": True},
                }
            )
        return out

    def by_group(self) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for e in house.ha_entities():
            for address in e["addresses"]:
                result.setdefault(address, []).append(
                    {"platform": e["platform"], "unique_id": e["unique_id"], "ui": e["ui"], "disabled": False}
                )
        return result

    def answer(self, message: dict[str, Any]) -> Any:
        kind = message.get("type")
        if kind == "config_entries/get":
            return [{"entry_id": ENTRY_ID, "domain": "knx", "title": "KNX", "state": "loaded"}]
        if kind == "knx/get_base_data":
            info = self.project["info"]
            return {
                "connection_info": {"version": "4.1.1", "connected": True, "current_address": house.HA_ADDRESS},
                "dpt_metadata": {},
                "project_info": {k: info[k] for k in ("name", "last_modified", "tool_version", "xknxproject_version")},
                "supported_platforms": sorted(house.HA_UI),
            }
        if kind == "knx/get_knx_project":
            return self.project
        if kind == "knx/get_entities_by_group":
            return self.by_group()
        if kind == "knx/query_telegrams":
            return {"telegrams": self.stored, "total_count": len(self.stored), "limit_reached": False}
        if kind == "knx/get_expose_groups":
            return {}
        if kind == "config/entity_registry/list":
            return self.registry()
        if kind == "config/device_registry/list":
            devices = [
                {"id": "dev_iface", "name": "KNX Interface", "identifiers": [["knx", house.HA_ADDRESS]]},
                {"id": "dev_light", "name": "Lights", "identifiers": [["knx", "knx_vdev_01LIGHTS"]]},
                {"id": "dev_old", "name": "Old garden shed", "identifiers": [["knx", "knx_vdev_01SHED"]]},
            ]
            return [d for d in devices if d["id"] not in self.removed]
        if kind == "get_states":
            return self.states()
        if kind in ("config/entity_registry/remove", "knx/delete_entity"):
            self.removed.add(str(message.get("entity_id")))
            return None
        if kind in ("config/device_registry/remove", "config/device_registry/remove_config_entry"):
            self.removed.add(str(message.get("device_id")))
            return None
        raise KeyError(kind)

    async def _websocket(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"type": "auth_required"})
        await ws.receive_json()
        await ws.send_json({"type": "auth_ok", "ha_version": "2026.11.1"})
        try:
            async for raw in ws:
                if raw.type != WSMsgType.TEXT:
                    continue
                message = raw.json()
                msg_id = message.get("id")
                if message.get("type") == "knx/subscribe_telegrams":
                    self.subscribers[ws] = msg_id
                    await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": None})
                elif message.get("type") == "subscribe_events":
                    await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": None})
                else:
                    try:
                        result = self.answer(message)
                    except KeyError:
                        await ws.send_json(
                            {
                                "id": msg_id,
                                "type": "result",
                                "success": False,
                                "error": {"code": "unknown_command", "message": "Unknown command."},
                            }
                        )
                        continue
                    await ws.send_json({"id": msg_id, "type": "result", "success": True, "result": result})
        finally:
            self.subscribers.pop(ws, None)
        return ws

    async def _diagnostics(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "data": {
                    "xknx": {"version": "4.1.1", "current_address": house.HA_ADDRESS},
                    "configuration_yaml": house.HA_YAML,
                    "yaml_configuration_error": None,
                    "config_store": {"entities": house.HA_UI, "expose": {}, "time_server": {}},
                }
            }
        )

    async def _service_read(self, request: web.Request) -> web.Response:
        address = str((await request.json()).get("address"))
        answers = [(house.HA_ADDRESS, address, "read", None)]
        answers += [(src, address, "response", payload) for src, payload in self.simulation.answer(address)]
        task = asyncio.create_task(self.emit(answers))
        task.add_done_callback(lambda t: None if t.cancelled() else t.exception())
        return web.json_response([])

    async def _info(self, request: web.Request) -> web.Response:
        return web.json_response({"data": {"timezone": "Europe/Amsterdam"}})
