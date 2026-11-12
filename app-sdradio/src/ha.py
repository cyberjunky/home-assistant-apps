"""Home Assistant: the radio's state as sensors, and an event for every transmission on a stored channel.

Both are switched on on the Settings page and go through the Supervisor's proxy to the core API.
The modes add an event for every pager message, and sensors for the devices picked in Sensors.
"""

import logging
import os
import re
import time

import aiohttp

logger = logging.getLogger(__name__)

CORE_API_URL = "http://supervisor/core/api"
# Sensor updates at most this often; the signal level changes all the time
SENSOR_INTERVAL = 5.0

# rtl_433 fields as Home Assistant sensors: (name, unit, device class)
SENSOR_FIELDS = {
    "temperature_C": ("Temperature", "°C", "temperature"),
    "temperature_F": ("Temperature", "°F", "temperature"),
    "humidity": ("Humidity", "%", "humidity"),
    "moisture": ("Moisture", "%", "moisture"),
    "pressure_hPa": ("Pressure", "hPa", "pressure"),
    "pressure_kPa": ("Pressure", "kPa", "pressure"),
    "wind_avg_km_h": ("Wind speed", "km/h", "wind_speed"),
    "wind_max_km_h": ("Wind gust", "km/h", "wind_speed"),
    "wind_avg_m_s": ("Wind speed", "m/s", "wind_speed"),
    "wind_max_m_s": ("Wind gust", "m/s", "wind_speed"),
    "wind_dir_deg": ("Wind direction", "°", None),
    "rain_mm": ("Rain", "mm", "precipitation"),
    "rain_rate_mm_h": ("Rain rate", "mm/h", "precipitation_intensity"),
    "light_lux": ("Light", "lx", "illuminance"),
    "uv": ("UV index", None, None),
    "power_W": ("Power", "W", "power"),
    "energy_kWh": ("Energy", "kWh", "energy"),
    "current_A": ("Current", "A", "current"),
    "voltage_V": ("Voltage", "V", "voltage"),
}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


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
        rds = state.get("rds") or {}
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
                    "station": rds.get("ps") or None,
                    "radio_text": rds.get("rt") or None,
                    "artist": rds.get("artist") or None,
                    "title": rds.get("title") or None,
                    "programme_type": rds.get("pty") or None,
                    "power": "off" if state.get("power") is False else "on",
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

    async def fire_pager(self, item: dict) -> None:
        await self._post(
            "events/sdradio_pager",
            {k: item.get(k) for k in ("protocol", "address", "type", "text", "frequency")}
            | {"capcode": item.get("key")},
        )

    async def publish_sensor(self, item: dict) -> None:
        """One rtl_433 reading of a device picked in Sensors, as sensor.sdradio_<device>_<field>."""
        device = _slug(item["key"])
        label = " ".join(str(x) for x in (item.get("model"), item.get("id"), item.get("channel")) if x is not None)
        values = item.get("values") or {}
        for field, value in values.items():
            if field == "battery_ok":
                entity, state, attributes = f"sensor.sdradio_{device}_battery", "ok" if value else "low", {
                    "friendly_name": f"{label} battery", "icon": "mdi:battery"}  # fmt: skip
            elif isinstance(value, int | float) and not isinstance(value, bool):
                name, unit, device_class = SENSOR_FIELDS.get(field, (field.replace("_", " "), None, None))
                entity, state = f"sensor.sdradio_{device}_{_slug(field)}", str(value)
                attributes = {"friendly_name": f"{label} {name.lower()}", "state_class": "measurement"}
                if unit:
                    attributes["unit_of_measurement"] = unit
                if device_class:
                    attributes["device_class"] = device_class
            else:
                continue
            attributes["device"] = item["key"]
            key = (state, tuple(sorted((k, str(v)) for k, v in attributes.items())))
            if self._last_states.get(entity) == key:
                continue
            if await self._post(f"states/{entity}", {"state": state, "attributes": attributes}):
                self._last_states[entity] = key

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
