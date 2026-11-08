"""Sensors from Home Assistant: an outside temperature and any others named in the options.

They are read from Home Assistant, stored like the Toon's own values and shown on the dashboard.
They aren't published back over MQTT: Home Assistant already has them.
"""

import asyncio
import logging
import re
import time

logger = logging.getLogger(__name__)

POLL_SECONDS = 60
# A reading older than this no longer counts as the current value
STALE_SECONDS = 900
OUTSIDE_KEY = "outside_temperature"
_NOT_A_READING = ("unavailable", "unknown", "none", "")


def sensor_key(name: str, taken: set[str]) -> str:
    """A storage key from a name. It must not change, or the sensor's history is left behind."""
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "sensor"
    key = f"ext_{slug}"
    number = 2
    while key in taken:
        key = f"ext_{slug}_{number}"
        number += 1
    return key


class ExternalSensors:
    def __init__(self, config, database, client):
        self.database = database
        self.client = client
        self.sensors: list[dict] = []
        taken: set[str] = set()
        if config.outside_temperature:
            self.sensors.append(self._sensor(OUTSIDE_KEY, "Outside", config.outside_temperature))
            taken.add(OUTSIDE_KEY)
        for item in config.external_sensors:
            key = sensor_key(item["name"], taken)
            taken.add(key)
            self.sensors.append(self._sensor(key, item["name"], item["entity"]))

    @staticmethod
    def _sensor(key: str, name: str, entity: str) -> dict:
        return {"key": key, "name": name, "entity": entity, "value": None, "unit": None, "updated": None, "error": None}

    @property
    def keys(self) -> set[str]:
        return {sensor["key"] for sensor in self.sensors}

    def names(self) -> dict[str, str]:
        return {sensor["key"]: sensor["name"] for sensor in self.sensors}

    def status(self, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        result = []
        for sensor in self.sensors:
            fresh = sensor["updated"] is not None and now - sensor["updated"] <= STALE_SECONDS
            result.append({**sensor, "value": sensor["value"] if fresh else None})
        return result

    async def poll(self, now: float | None = None) -> None:
        for sensor in self.sensors:
            try:
                state = await self.client.get_state(sensor["entity"])
            except Exception as e:
                sensor["error"] = f"could not read it: {e}"
                continue
            if state is None:
                sensor["error"] = "not found in Home Assistant"
                continue
            raw = str(state.get("state", "")).strip()
            try:
                if raw.lower() in _NOT_A_READING:
                    raise ValueError
                value = float(raw)
            except ValueError:
                sensor["error"] = f"no number: {raw or 'empty'}"
                continue
            at = time.time() if now is None else now
            sensor.update(value=value, updated=at, error=None)
            sensor["unit"] = (state.get("attributes") or {}).get("unit_of_measurement")
            self.database.record(sensor["key"], at, value)

    async def run(self) -> None:
        if not self.sensors:
            return
        if not self.client.available:
            logger.warning("External sensors: no connection to Home Assistant, so they can't be read")
            for sensor in self.sensors:
                sensor["error"] = "no connection to Home Assistant"
            return
        logger.info(f"External sensors: reading {len(self.sensors)} from Home Assistant")
        while True:
            await self.poll()
            await asyncio.sleep(POLL_SECONDS)
