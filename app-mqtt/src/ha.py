"""Home Assistant's MQTT integration, through the Supervisor's proxy to Home Assistant's API.

Home Assistant offers a broker that announces itself (discovery) only while MQTT isn't set up. To
move from another broker (such as the Mosquitto app), the MQTT integration is reconfigured: the
same flow as its Reconfigure button, with this broker's address and the homeassistant user.
"""

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

CORE_API = os.environ.get("MQTT_CORE_API", "http://supervisor/core/api")


class HAError(Exception):
    pass


def form_values(schema: list[dict]) -> dict[str, Any]:
    """The values a flow's form suggests (its current settings), sections included."""
    out: dict[str, Any] = {}
    for field in schema or []:
        name = field.get("name")
        if not name:
            continue
        if field.get("type") == "expandable":
            out[name] = form_values(field.get("schema") or [])
            continue
        description = field.get("description") or {}
        if "suggested_value" in description:
            out[name] = description["suggested_value"]
        elif "default" in field:
            out[name] = field["default"]
    return out


class HomeAssistant:
    def __init__(self) -> None:
        self.token = os.environ.get("SUPERVISOR_TOKEN", "")
        self._session: aiohttp.ClientSession | None = None

    @property
    def available(self) -> bool:
        return bool(self.token)

    async def _call(self, method: str, path: str, body: dict | None = None) -> Any:
        if not self.token:
            raise HAError("No Supervisor token: the app runs under Home Assistant")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
        try:
            async with self._session.request(
                method, CORE_API + path, json=body, headers={"Authorization": f"Bearer {self.token}"}
            ) as resp:
                text = await resp.text()
                if resp.status >= 400:
                    raise HAError(f"Home Assistant answered {resp.status}: {text[:300]}")
                return await resp.json(content_type=None) if text else None
        except aiohttp.ClientError as e:
            raise HAError(f"Cannot reach Home Assistant: {e}") from e

    async def mqtt_entry(self) -> dict | None:
        """The MQTT integration's config entry, or None when MQTT isn't set up."""
        entries = await self._call("GET", "/config/config_entries/entry?domain=mqtt")
        entries = [e for e in entries or [] if e.get("domain") == "mqtt"]
        return entries[0] if entries else None

    async def reconfigure_mqtt(self, host: str, port: int, username: str, password: str) -> str:
        """Point the MQTT integration at this broker. Returns what Home Assistant said."""
        entry = await self.mqtt_entry()
        if entry is None:
            raise HAError("MQTT isn't set up in Home Assistant yet: add it from the notification it shows for this app")
        flow = await self._call(
            "POST",
            "/config/config_entries/flow",
            {"handler": "mqtt", "entry_id": entry["entry_id"], "show_advanced_options": False},
        )
        flow_id = flow.get("flow_id")
        if flow.get("type") != "form" or not flow_id:
            raise HAError(f"Home Assistant didn't open the MQTT settings: {flow.get('reason') or flow.get('type')}")
        values = form_values(flow.get("data_schema") or [])
        values.update(broker=host, port=port, username=username, password=password)
        try:
            result = await self._call("POST", f"/config/config_entries/flow/{flow_id}", values)
        except HAError:
            # Leave no half-done flow behind
            await self._call("DELETE", f"/config/config_entries/flow/{flow_id}")
            raise
        if result.get("type") == "abort" and result.get("reason") == "reconfigure_successful":
            return "Home Assistant now uses this broker"
        if result.get("type") == "form":
            await self._call("DELETE", f"/config/config_entries/flow/{flow_id}")
            errors = result.get("errors") or {}
            raise HAError(
                f"Home Assistant didn't accept the settings: {', '.join(f'{k}: {v}' for k, v in errors.items()) or 'unknown error'}"
            )
        raise HAError(f"Home Assistant answered: {result.get('reason') or result.get('type')}")

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
