"""Home Assistant Core API through the Supervisor: only used for map tiles."""

import os

import aiohttp

CORE_API_URL = "http://supervisor/core/api"


class HAClientError(Exception):
    """A request to Home Assistant failed."""


class HAClient:
    def __init__(self):
        self._token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None

    @property
    def available(self) -> bool:
        """Whether the app runs under the Supervisor."""
        return bool(self._token)

    async def get_map_tile(self, z: int, x: int, y: int) -> bytes:
        """An OpenStreetMap raster tile from Home Assistant's map_tiles proxy."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._token}"}, timeout=aiohttp.ClientTimeout(total=30)
            )
        try:
            async with self._session.get(f"{CORE_API_URL}/map_tiles/raster/{z}/{x}/{y}.png") as resp:
                status, body = resp.status, await resp.read()
        except Exception as e:
            raise HAClientError(f"{type(e).__name__}: {e}") from e
        if status != 200:
            raise HAClientError(f"HA API error {status} for map tile {z}/{x}/{y}")
        return body

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
