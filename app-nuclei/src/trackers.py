"""Home Assistant's device trackers: which devices are home right now, and at which address.

The scanner only ever looks at trackers Home Assistant reports as home, with an IP address in a private,
loopback or link-local range. That is the ownership gate: a device on your own network. A tracker with a
public address, or none, is listed but never scanned.
"""

import ipaddress
import logging

import aiohttp

from .config import SUPERVISOR_URL

logger = logging.getLogger(__name__)

# Trackers name their address differently: ip (most router integrations), ip_address, host
IP_ATTRIBUTES = ("ip", "ip_address", "host")


class HAError(Exception):
    """Home Assistant could not be asked for its trackers."""


def scannable(ip: str | None) -> bool:
    """Whether an address is on your own network: private, loopback or link-local."""
    if not ip:
        return False
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def tracker_ip(attributes: dict) -> str | None:
    for key in IP_ATTRIBUTES:
        value = attributes.get(key)
        if isinstance(value, str) and value.strip():
            try:
                return str(ipaddress.ip_address(value.strip()))
            except ValueError:
                continue
    return None


def parse_states(states: list) -> list[dict]:
    """The device trackers among Home Assistant's states, as {entity_id, name, ip, mac, online}."""
    out = []
    for state in states:
        entity_id = state.get("entity_id") if isinstance(state, dict) else None
        if not isinstance(entity_id, str) or not entity_id.startswith("device_tracker."):
            continue
        attributes = state.get("attributes") or {}
        out.append(
            {
                "entity_id": entity_id,
                "name": str(attributes.get("friendly_name") or entity_id.split(".", 1)[1]),
                "ip": tracker_ip(attributes),
                "mac": str(attributes.get("mac") or "").lower() or None,
                "online": state.get("state") == "home",
            }
        )
    return out


class Trackers:
    """Reads the device trackers from Home Assistant's REST API through the Supervisor proxy."""

    def __init__(self, token: str, url: str = SUPERVISOR_URL):
        self._token = token
        self._url = f"{url}/core/api/states"
        self._session: aiohttp.ClientSession | None = None

    async def fetch(self) -> list[dict]:
        if not self._token:
            raise HAError("no Supervisor token; the app needs homeassistant_api")
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"Authorization": f"Bearer {self._token}"}, timeout=aiohttp.ClientTimeout(total=30)
            )
        try:
            async with self._session.get(self._url) as resp:
                if resp.status != 200:
                    raise HAError(f"Home Assistant answered {resp.status}")
                return parse_states(await resp.json())
        except aiohttp.ClientError as e:
            raise HAError(f"{type(e).__name__}: {e}") from e
        except TimeoutError as e:
            raise HAError("Home Assistant did not answer in time") from e

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
