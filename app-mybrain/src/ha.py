"""Home Assistant, reached through the Supervisor: the share events from the companion app, the AI
Task entities for sorting, and a sensor with the size of the inbox."""

import asyncio
import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

# MYBRAIN_SUPERVISOR_URL points the app at the development stand-in (see tools/mock_ha.py)
SUPERVISOR_URL = os.environ.get("MYBRAIN_SUPERVISOR_URL", "http://supervisor").rstrip("/")
CORE_API_URL = f"{SUPERVISOR_URL}/core/api"
CORE_WS_URL = SUPERVISOR_URL.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/core/websocket"
TIMEOUT = aiohttp.ClientTimeout(total=120, connect=10)
SHARE_EVENT = "mobile_app.share"


class HAError(Exception):
    """Home Assistant can't be reached, or refused a request."""


class HA:
    """Talks to Home Assistant Core."""

    def __init__(self):
        self.token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None
        self.connected = False
        self.version = ""
        self.devices: dict[str, str] = {}

    @property
    def available(self) -> bool:
        return bool(self.token) or SUPERVISOR_URL != "http://supervisor"

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"Authorization": f"Bearer {self.token}"}, timeout=TIMEOUT)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def core(self, method: str, path: str, payload: Any = None) -> Any:
        """Core's REST API. Raises HAError on anything but a 2xx answer."""
        try:
            session = await self.session()
            kwargs: dict[str, Any] = {"json": payload} if payload is not None else {}
            async with session.request(method, f"{CORE_API_URL}{path}", **kwargs) as response:
                body = await response.json() if response.content_type == "application/json" else await response.text()
                status = response.status
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as e:
            raise HAError(f"{method} {path}: {e or type(e).__name__}") from e
        if not 200 <= status < 300:
            message = body.get("message") if isinstance(body, dict) else str(body)[:300]
            raise HAError(f"{method} {path}: {message or status}")
        return body

    async def ai_task_entities(self) -> list[dict]:
        """The AI Task entities Home Assistant has: [{entity_id, name}]."""
        states = await self.core("GET", "/states")
        return [
            {"entity_id": s["entity_id"], "name": (s.get("attributes") or {}).get("friendly_name") or s["entity_id"]}
            for s in states or []
            if isinstance(s, dict) and str(s.get("entity_id", "")).startswith("ai_task.")
        ]

    async def generate_data(self, entity_id: str, instructions: str, structure: dict) -> dict:
        """Ask an AI Task entity for structured data. Returns the data it filled in."""
        payload: dict[str, Any] = {"task_name": "MyBrain sort", "instructions": instructions, "structure": structure}
        if entity_id:
            payload["entity_id"] = entity_id
        answer = await self.core("POST", "/services/ai_task/generate_data?return_response", payload)
        data = ((answer or {}).get("service_response") or {}).get("data") if isinstance(answer, dict) else None
        if not isinstance(data, dict):
            raise HAError("the AI Task gave no structured answer")
        return data

    async def calendar_events(self, entity_id: str, start: str, end: str) -> list[dict]:
        """A calendar's events between two moments (REST, with each event's uid)."""
        events = await self.core("GET", f"/calendars/{entity_id}?start={quote(start)}&end={quote(end)}")
        return [e for e in events or [] if isinstance(e, dict)]

    async def entity_exists(self, entity_id: str) -> bool:
        try:
            await self.core("GET", f"/states/{entity_id}")
        except HAError:
            return False
        return True

    async def commands(self, calls: list[dict]) -> list[str]:
        """Websocket commands on one connection, in order. Returns an error text per failed call."""
        errors: list[str] = []
        if not calls:
            return errors
        session = await self.session()
        try:
            async with session.ws_connect(CORE_WS_URL, heartbeat=30, max_msg_size=0) as ws:
                await ws.receive_json(timeout=30)
                await ws.send_json({"type": "auth", "access_token": self.token})
                if (await ws.receive_json(timeout=30)).get("type") != "auth_ok":
                    raise HAError("Home Assistant refused the app's token")
                for number, call in enumerate(calls, start=1):
                    await ws.send_json({"id": number, **call})
                    while True:
                        answer = await ws.receive_json(timeout=60)
                        if answer.get("id") == number and answer.get("type") == "result":
                            break
                    if not answer.get("success"):
                        error = answer.get("error") or {}
                        errors.append(f"{call['type']}: {error.get('message') or error.get('code') or 'refused'}")
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError, TypeError) as e:
            raise HAError(f"websocket: {e or type(e).__name__}") from e
        return errors

    async def set_state(self, entity_id: str, state: Any, attributes: dict) -> None:
        await self.core("POST", f"/states/{entity_id}", {"state": state, "attributes": attributes})

    async def listen(self, on_share: Callable[[dict], Awaitable[None]]) -> None:
        """Receive share events until cancelled, connecting again whenever the connection drops."""
        delay = 5
        while True:
            try:
                await self._listen_once(on_share)
                delay = 5
            except asyncio.CancelledError:
                raise
            except (HAError, aiohttp.ClientError, TimeoutError, OSError, ValueError, TypeError) as e:
                logger.warning(f"Share events: {e or type(e).__name__}; trying again in {delay} s")
            self.connected = False
            await asyncio.sleep(delay)
            delay = min(delay * 2, 120)

    async def _listen_once(self, on_share: Callable[[dict], Awaitable[None]]) -> None:
        session = await self.session()
        async with session.ws_connect(CORE_WS_URL, heartbeat=30, max_msg_size=0) as ws:
            first = await ws.receive_json(timeout=30)
            if first.get("type") != "auth_required":
                raise HAError(f"unexpected first message: {first.get('type')}")
            await ws.send_json({"type": "auth", "access_token": self.token})
            answer = await ws.receive_json(timeout=30)
            if answer.get("type") != "auth_ok":
                raise HAError(f"Home Assistant refused the app's token: {answer.get('message', answer.get('type'))}")
            self.version = answer.get("ha_version", "")
            await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": SHARE_EVENT})
            await ws.send_json({"id": 2, "type": "config/device_registry/list"})
            self.connected = True
            logger.info(f"Listening for shares from the Home Assistant app (Home Assistant {self.version})")
            async for message in ws:
                if message.type != aiohttp.WSMsgType.TEXT:
                    if message.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                        break
                    continue
                data = message.json()
                if data.get("type") == "result" and data.get("id") == 1 and not data.get("success"):
                    raise HAError(f"subscribing refused: {(data.get('error') or {}).get('message')}")
                if data.get("type") == "result" and data.get("id") == 2 and data.get("success"):
                    self.devices = {
                        d["id"]: d.get("name_by_user") or d.get("name") or ""
                        for d in data.get("result") or []
                        if isinstance(d, dict) and d.get("id")
                    }
                if data.get("type") == "event":
                    event = data.get("event") or {}
                    if event.get("event_type") == SHARE_EVENT:
                        try:
                            await on_share(event.get("data") or {})
                        except Exception:
                            # One share that can't be saved must not stop the ones after it
                            logger.exception(f"A share could not be saved: {event.get('data')!r}"[:500])
            raise HAError("the connection closed")
