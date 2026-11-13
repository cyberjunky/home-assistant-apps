"""Home Assistant and Supervisor REST API client."""

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"
CORE_API_URL = f"{SUPERVISOR_URL}/core/api"


class HAClientError(Exception):
    """Home Assistant API request failed (unreachable, auth or server error)."""


class HAClient:
    """Talks to Home Assistant Core (through the Supervisor proxy) and to the Supervisor itself."""

    def __init__(self):
        self._token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None

    @property
    def available(self) -> bool:
        """Whether the app runs under the Supervisor."""
        return bool(self._token)

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def _get(self, url: str) -> Any:
        """GET a JSON resource, raising HAClientError on anything but a 200 answer."""
        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                if resp.status != 200:
                    # The Supervisor explains itself in a short JSON message
                    detail = (await resp.text())[:200].strip()
                    raise HAClientError(f"HTTP {resp.status} for {url}: {detail}")
                return await resp.json()
        except HAClientError:
            raise
        except Exception as e:
            raise HAClientError(f"{type(e).__name__}: {e}") from e

    async def get_home_location(self) -> tuple[float, float] | None:
        """Home Assistant's home coordinates (for the solar forecast), None when unavailable."""
        try:
            body = await self._get(f"{CORE_API_URL}/config")
            return float(body["latitude"]), float(body["longitude"])
        except (HAClientError, KeyError, TypeError, ValueError) as e:
            logger.info(f"Home location not available from Home Assistant ({e})")
            return None

    async def get_state(self, entity_id: str) -> dict:
        """One entity's state object ({"state": ..., "attributes": {...}}); raises HAClientError."""
        body = await self._get(f"{CORE_API_URL}/states/{entity_id}")
        if not isinstance(body, dict):
            raise HAClientError(f"Unexpected answer for {entity_id}")
        return body

    async def call_service(self, domain: str, service: str, entity_id: str) -> None:
        """Call a service on one entity (switch.turn_on, ...); raises HAClientError."""
        url = f"{CORE_API_URL}/services/{domain}/{service}"
        try:
            session = await self._get_session()
            async with session.post(url, json={"entity_id": entity_id}) as resp:
                if resp.status != 200:
                    detail = (await resp.text())[:200].strip()
                    raise HAClientError(f"HTTP {resp.status} for {domain}.{service}: {detail}")
        except HAClientError:
            raise
        except Exception as e:
            raise HAClientError(f"{type(e).__name__}: {e}") from e

    async def get_mqtt_service(self) -> dict | None:
        """The broker of Home Assistant's MQTT service (Mosquitto broker app), None when there is none."""
        try:
            body = await self._get(f"{SUPERVISOR_URL}/services/mqtt")
        except HAClientError as e:
            # Usually: no app provides the MQTT service (Mosquitto not installed or not running)
            logger.info(f"MQTT: Home Assistant has no MQTT broker to offer ({e})")
            return None
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, dict) or not data.get("host"):
            logger.info("MQTT: Home Assistant's MQTT service gave no broker address")
            return None
        return data

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
