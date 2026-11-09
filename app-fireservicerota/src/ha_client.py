"""Home Assistant client: the Core REST API and websocket API through the Supervisor proxy.

This is the app's only connection to the outside world. All FireServiceRota data comes
from the integration's entities in Home Assistant; the app never talks to
FireServiceRota's (or Brandweerrooster's) own servers.
"""

import asyncio
import contextlib
import itertools
import logging
import os
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any
from urllib.parse import quote

import aiohttp

logger = logging.getLogger(__name__)

# FSR_SUPERVISOR_URL points the app at the development mock (see tools/mock_ha.py)
SUPERVISOR_URL = os.environ.get("FSR_SUPERVISOR_URL", "http://supervisor").rstrip("/")
CORE_API_URL = f"{SUPERVISOR_URL}/core/api"
CORE_WS_URL = SUPERVISOR_URL.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/core/websocket"


class HAClientError(Exception):
    """A Home Assistant API request failed (unreachable, auth or server error)."""


class HAAuthError(HAClientError):
    """Home Assistant rejected the token."""


class HAClient:
    """Talks to Home Assistant Core (through the Supervisor proxy) and to the Supervisor itself."""

    def __init__(self):
        self._token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None

    @property
    def available(self) -> bool:
        """Whether a Supervisor token is available (it is, inside the app container)."""
        return bool(self._token)

    @property
    def token(self) -> str:
        return self._token

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._token}"},
                # tts.speak waits for the speech to be generated
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def _request(
        self, method: str, url: str, payload: Any = None, timeout: float | None = None
    ) -> tuple[int, Any]:
        """Send a request and return (status, parsed JSON body or text)."""
        try:
            session = await self.session()
            kwargs: dict[str, Any] = {"json": payload} if payload is not None else {}
            if timeout:
                kwargs["timeout"] = aiohttp.ClientTimeout(total=timeout)
            async with session.request(method, url, **kwargs) as resp:
                if resp.content_type == "application/json":
                    return resp.status, await resp.json()
                return resp.status, await resp.text()
        except Exception as e:
            raise HAClientError(f"{type(e).__name__}: {e}") from e

    async def _core(self, method: str, path: str, payload: Any = None, timeout: float | None = None) -> Any:
        """Call the Core REST API, raising HAClientError on anything but a 2xx answer."""
        status, body = await self._request(method, f"{CORE_API_URL}{path}", payload, timeout)
        if status == 401:
            raise HAAuthError(f"HA API refused the token for {path}")
        if not 200 <= status < 300:
            raise HAClientError(f"HA API error {status} for {path}: {str(body)[:200]}")
        return body

    async def get_state(self, entity_id: str) -> dict[str, Any] | None:
        """The current state of an entity, None when it doesn't exist."""
        status, body = await self._request("GET", f"{CORE_API_URL}/states/{entity_id}")
        if status == 404:
            return None
        if status != 200:
            raise HAClientError(f"HA API error {status} for {entity_id}: {str(body)[:200]}")
        return body

    async def get_config(self) -> dict[str, Any]:
        """Home Assistant's configuration: home location, time zone, version."""
        return await self._core("GET", "/config")

    async def get_history(self, entity_ids: list[str], start: datetime, end: datetime) -> list[list[dict]]:
        """Recorder history of the entities between start and end, every change (attributes too)."""
        path = (
            f"/history/period/{quote(start.isoformat())}"
            f"?filter_entity_id={','.join(entity_ids)}"
            f"&end_time={quote(end.isoformat())}"
            # Otherwise changes of attributes only (reported_status, arrived_at_station) are left out
            "&significant_changes_only=0"
        )
        body = await self._core("GET", path, timeout=120)
        return body if isinstance(body, list) else []

    async def fire_event(self, event_type: str, data: dict[str, Any]) -> bool:
        """Fire an event on the Home Assistant event bus. Returns False (and logs) on failure."""
        try:
            await self._core("POST", f"/events/{event_type}", data)
        except HAClientError as e:
            logger.error(f"Failed to fire {event_type}: {e}")
            return False
        return True

    async def call_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        """Call a Home Assistant service; raises HAClientError on failure."""
        await self._core("POST", f"/services/{domain}/{service}", data)

    async def get_addon_slug(self) -> str | None:
        """This app's slug from the Supervisor (e.g. "1a2b3c4d_app-fireservicerota"), None when unavailable."""
        try:
            status, body = await self._request("GET", f"{SUPERVISOR_URL}/addons/self/info")
        except HAClientError:
            return None
        if status != 200 or not isinstance(body, dict):
            return None
        return (body.get("data") or {}).get("slug") or None

    async def get_map_tile(self, z: int, x: int, y: int) -> bytes:
        """An OpenStreetMap raster tile from Home Assistant's map_tiles proxy."""
        try:
            session = await self.session()
            async with session.get(f"{CORE_API_URL}/map_tiles/raster/{z}/{x}/{y}.png") as resp:
                status, body = resp.status, await resp.read()
        except Exception as e:
            raise HAClientError(f"{type(e).__name__}: {e}") from e
        if status != 200:
            raise HAClientError(f"HA API error {status} for map tile {z}/{x}/{y}")
        return body

    async def close(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None


EventHandler = Callable[[dict], None]


class HAWebSocket:
    """One websocket connection to Home Assistant at a time, reconnecting with backoff.

    on_connected runs after every (re)connect, with the connection ready for call()
    and subscribe_events(); events are handed to the handler given to subscribe_events.
    """

    def __init__(self, client: HAClient, on_connected: Callable[[], Awaitable[None]]):
        self.client = client
        self.on_connected = on_connected
        self.connected = False
        self.last_error = ""
        self.connected_since: float | None = None
        self.reconnects = 0
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._ids = itertools.count(1)
        self._pending: dict[int, asyncio.Future] = {}
        self._handlers: dict[int, EventHandler] = {}

    async def run(self) -> None:
        """Keep a connection up until cancelled."""
        backoff = 2
        while True:
            try:
                await self._session()
                backoff = 2
                self.last_error = "Connection closed"
                logger.warning(f"Home Assistant websocket closed, reconnecting in {backoff}s")
            except asyncio.CancelledError:
                raise
            except HAAuthError as e:
                self.last_error = str(e)
                logger.error(f"Home Assistant websocket: {e}")
                backoff = 60
            except Exception as e:
                self.last_error = f"{type(e).__name__}: {e}"
                logger.warning(f"Home Assistant websocket: {self.last_error}, reconnecting in {backoff}s")
            self.connected = False
            self.connected_since = None
            self.reconnects += 1
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _session(self) -> None:
        session = await self.client.session()
        async with session.ws_connect(
            CORE_WS_URL, heartbeat=30, max_msg_size=0, timeout=aiohttp.ClientWSTimeout(ws_close=10)
        ) as ws:
            first = await ws.receive_json(timeout=30)
            if first.get("type") != "auth_required":
                raise HAClientError(f"Unexpected first message: {first.get('type')}")
            await ws.send_json({"type": "auth", "access_token": self.client.token})
            answer = await ws.receive_json(timeout=30)
            if answer.get("type") != "auth_ok":
                raise HAAuthError(f"Authentication failed: {answer.get('message', answer.get('type'))}")
            self._ws = ws
            self._handlers.clear()
            reader = asyncio.create_task(self._read(ws))
            try:
                self.connected = True
                self.connected_since = asyncio.get_running_loop().time()
                self.last_error = ""
                logger.info(f"Connected to Home Assistant {answer.get('ha_version', '')} over the websocket")
                await self.on_connected()
                await reader
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await reader
                self._ws = None
                for future in self._pending.values():
                    if not future.done():
                        future.set_exception(HAClientError("Websocket closed"))
                self._pending.clear()

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                continue
            try:
                data = msg.json()
            except ValueError:
                continue
            # Home Assistant may bundle several messages in one frame
            for item in data if isinstance(data, list) else [data]:
                self._dispatch(item)

    def _dispatch(self, item: dict) -> None:
        msg_id = item.get("id")
        if item.get("type") == "result":
            future = self._pending.pop(msg_id, None)
            if future and not future.done():
                if item.get("success"):
                    future.set_result(item.get("result"))
                else:
                    error = item.get("error") or {}
                    future.set_exception(HAClientError(f"{error.get('code')}: {error.get('message')}"))
        elif item.get("type") == "event":
            handler = self._handlers.get(msg_id)
            if handler:
                try:
                    handler(item.get("event") or {})
                except Exception:
                    logger.exception("Event handler failed")

    async def call(self, message: dict, timeout: float = 30) -> Any:
        """Send a command and wait for its result."""
        if self._ws is None:
            raise HAClientError("Not connected")
        msg_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        await self._ws.send_json({**message, "id": msg_id})
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(msg_id, None)

    async def subscribe_events(self, event_type: str, handler: EventHandler) -> None:
        if self._ws is None:
            raise HAClientError("Not connected")
        msg_id = next(self._ids)
        future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = future
        # Registered before sending, so no event can slip in before the handler is there
        self._handlers[msg_id] = handler
        await self._ws.send_json({"id": msg_id, "type": "subscribe_events", "event_type": event_type})
        try:
            await asyncio.wait_for(future, 30)
        except Exception:
            self._handlers.pop(msg_id, None)
            raise
        finally:
            self._pending.pop(msg_id, None)
