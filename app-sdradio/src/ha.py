"""Home Assistant: the radio's state as sensors, and an event for every transmission on a stored channel.

Both are switched on on the Settings page and go through the Supervisor's proxy to the core API.
"""

import logging
import os
import time

import aiohttp

logger = logging.getLogger(__name__)

CORE_API_URL = "http://supervisor/core/api"
# Sensor updates at most this often; the signal level changes all the time
SENSOR_INTERVAL = 5.0


class HomeAssistant:
    def __init__(self):
        self.token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None
        self._last_sent = 0.0
        self._last_states: dict[str, tuple] = {}
        self._warned = False

    @property
    def available(self) -> bool:
        return bool(self.token)

    async def _post(self, path: str, payload: dict) -> bool:
        if not self.token:
            return False
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self.token}"}, timeout=aiohttp.ClientTimeout(total=10)
            )
        try:
            async with self._session.post(f"{CORE_API_URL}/{path}", json=payload) as response:
                if response.status >= 300:
                    raise aiohttp.ClientResponseError(
                        response.request_info, (), status=response.status, message=await response.text()
                    )
            self._warned = False
            return True
        except (aiohttp.ClientError, TimeoutError) as e:
            if not self._warned:
                logger.warning(f"Home Assistant did not take the update: {e}")
                self._warned = True
            return False

    async def publish_state(self, state: dict) -> None:
        """Called with every radio state; sends the sensors that changed, at most every few seconds."""
        now = time.monotonic()
        if now - self._last_sent < SENSOR_INTERVAL:
            return
        self._last_sent = now
        channel = state.get("channel") or {}
        scan = state.get("scan")
        sensors = {
            "sensor.sdradio_frequency": (
                f"{state['frequency'] / 1e6:.4f}",
                {
                    "friendly_name": "SDRadio frequency",
                    "unit_of_measurement": "MHz",
                    "icon": "mdi:radio-tower",
                    "mode": state["mode"].upper(),
                    "band": state.get("band"),
                    "channel": channel.get("name"),
                    "channel_number": channel.get("number"),
                },
            ),
            "sensor.sdradio_signal": (
                f"{state['level']:.0f}",
                {
                    "friendly_name": "SDRadio signal",
                    "unit_of_measurement": "dBFS",
                    "icon": "mdi:signal",
                    "state_class": "measurement",
                    "busy": state.get("busy", False),
                },
            ),
            "sensor.sdradio_scan": (
                ("holding" if scan["hold"] else "scanning") if scan else "off",
                {
                    "friendly_name": "SDRadio scan",
                    "icon": "mdi:radar",
                    "scan": scan["kind"] if scan else None,
                    "range": scan["label"] if scan else None,
                },
            ),
        }
        for entity_id, (value, attributes) in sensors.items():
            key = (value, tuple(sorted((k, str(v)) for k, v in attributes.items())))
            if self._last_states.get(entity_id) == key:
                continue
            if await self._post(f"states/{entity_id}", {"state": value, "attributes": attributes}):
                self._last_states[entity_id] = key

    async def fire_hit(self, hit: dict) -> None:
        channel = hit.get("channel") or {}
        await self._post(
            "events/sdradio_signal",
            {
                "frequency": hit["frequency"],
                "frequency_mhz": round(hit["frequency"] / 1e6, 6),
                "mode": hit["mode"],
                "channel": channel.get("name"),
                "channel_number": channel.get("number"),
                "favourite": bool(channel.get("favourite")),
                "duration": round(hit["duration"], 1),
                "peak": round(hit["peak"], 1),
            },
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
