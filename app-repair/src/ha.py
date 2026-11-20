"""Home Assistant, reached through the Supervisor: Core's REST API, its websocket API, and the
Supervisor's own resolution centre.

Everything the app knows comes from here, and everything it changes goes through here. It never
opens Home Assistant's database or the files under .storage: those belong to a running Home
Assistant, and editing them from outside is how installations get broken.
"""

import contextlib
import itertools
import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# REPAIR_SUPERVISOR_URL points the app at the development stand-in (see tools/mock_ha.py)
SUPERVISOR_URL = os.environ.get("REPAIR_SUPERVISOR_URL", "http://supervisor").rstrip("/")
CORE_API_URL = f"{SUPERVISOR_URL}/core/api"
CORE_WS_URL = SUPERVISOR_URL.replace("http://", "ws://", 1).replace("https://", "wss://", 1) + "/core/websocket"
TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)


class HAError(Exception):
    """Home Assistant can't be reached, or refused a request."""


class Connection:
    """One websocket connection, for the calls of one scan or one fix."""

    def __init__(self, ws: aiohttp.ClientWebSocketResponse, version: str):
        self._ws = ws
        self._ids = itertools.count(1)
        self.version = version

    async def call(self, kind: str, **fields) -> Any:
        """One command and its result. Raises HAError when Home Assistant says no."""
        number = next(self._ids)
        await self._ws.send_json({"id": number, "type": kind, **fields})
        while True:
            try:
                answer = await self._ws.receive_json(timeout=90)
            except (TypeError, ValueError, TimeoutError, aiohttp.ClientError) as e:
                raise HAError(f"{kind}: no answer ({type(e).__name__})") from e
            if answer.get("id") != number or answer.get("type") != "result":
                continue  # an event, or the answer to something else
            if not answer.get("success"):
                error = answer.get("error") or {}
                raise HAError(f"{kind}: {error.get('message') or error.get('code') or 'refused'}")
            return answer.get("result")

    async def ask(self, kind: str, default: Any = None, **fields) -> Any:
        """A command an older Home Assistant may not know: its result, or the default."""
        try:
            return await self.call(kind, **fields)
        except HAError as e:
            logger.debug(f"Home Assistant: {e}")
            return default


class HA:
    """Talks to Home Assistant Core and to the Supervisor."""

    def __init__(self):
        self.token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None

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

    @contextlib.asynccontextmanager
    async def connect(self):
        """A websocket connection, logged in, closed again afterwards."""
        session = await self.session()
        try:
            ws = await session.ws_connect(CORE_WS_URL, heartbeat=30, max_msg_size=0)
        except (aiohttp.ClientError, TimeoutError, OSError) as e:
            raise HAError(f"Home Assistant does not answer: {e or type(e).__name__}") from e
        try:
            try:
                first = await ws.receive_json(timeout=30)
                if first.get("type") != "auth_required":
                    raise HAError(f"unexpected first message: {first.get('type')}")
                await ws.send_json({"type": "auth", "access_token": self.token})
                answer = await ws.receive_json(timeout=30)
            except (TypeError, ValueError, TimeoutError, aiohttp.ClientError) as e:
                raise HAError(f"Home Assistant does not answer: {type(e).__name__}") from e
            if answer.get("type") != "auth_ok":
                raise HAError(f"Home Assistant refused the app's token: {answer.get('message', answer.get('type'))}")
            yield Connection(ws, answer.get("ha_version", ""))
        finally:
            await ws.close()

    async def _request(self, method: str, url: str, payload: Any = None) -> tuple[int, Any]:
        try:
            session = await self.session()
            kwargs: dict[str, Any] = {"json": payload} if payload is not None else {}
            async with session.request(method, url, **kwargs) as response:
                if response.content_type == "application/json":
                    return response.status, await response.json()
                return response.status, await response.text()
        except (aiohttp.ClientError, TimeoutError, OSError, ValueError) as e:
            raise HAError(f"{method} {url.replace(SUPERVISOR_URL, '')}: {e or type(e).__name__}") from e

    async def core(self, method: str, path: str, payload: Any = None) -> Any:
        """Core's REST API. Raises HAError on anything but a 2xx answer."""
        status, body = await self._request(method, f"{CORE_API_URL}{path}", payload)
        if not 200 <= status < 300:
            message = body.get("message") if isinstance(body, dict) else str(body)[:200]
            raise HAError(f"{method} {path}: {message or status}")
        return body

    async def supervisor(self, method: str, path: str, payload: Any = None) -> Any:
        """The Supervisor's API: the data of its answer. Raises HAError when it refuses."""
        status, body = await self._request(method, f"{SUPERVISOR_URL}{path}", payload)
        if not 200 <= status < 300 or not isinstance(body, dict) or body.get("result") == "error":
            message = body.get("message") if isinstance(body, dict) else str(body)[:200]
            raise HAError(f"Supervisor {path}: {message or status}")
        return body.get("data") or {}

    async def service(self, domain: str, service: str, data: dict) -> None:
        await self.core("POST", f"/services/{domain}/{service}", data)
