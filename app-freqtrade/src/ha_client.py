"""The Supervisor's REST API: where to find the MQTT broker."""

import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

SUPERVISOR_URL = "http://supervisor"


class HAClient:
    """Talks to the Supervisor when the app runs under it."""

    def __init__(self):
        self._token = os.environ.get("SUPERVISOR_TOKEN", "")

    @property
    def available(self) -> bool:
        return bool(self._token)

    async def get_mqtt_service(self) -> dict | None:
        """The broker Home Assistant uses, or None when there is none."""
        try:
            async with (
                aiohttp.ClientSession(
                    headers={"Authorization": f"Bearer {self._token}"},
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as session,
                session.get(f"{SUPERVISOR_URL}/services/mqtt") as response,
            ):
                if response.status != 200:
                    return None
                return (await response.json()).get("data")
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning(f"MQTT: could not ask the Supervisor for the broker: {e}")
            return None
