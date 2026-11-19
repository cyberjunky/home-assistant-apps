"""What Home Assistant itself knows about its custom integrations: how long each took to set up, what each
wrote to the log, and what the Marketplace installed. Asked over Core's WebSocket API, read-only."""

import logging
import re

import aiohttp

from .config import CORE_WEBSOCKET_URL

logger = logging.getLogger(__name__)

LOGGER_DOMAIN = re.compile(r"custom_components[./]([a-z0-9_]+)")
MAX_MESSAGE_BYTES = 64 * 1024 * 1024  # the Marketplace lists every repository of the catalog


class HomeAssistantError(Exception):
    """Home Assistant could not be asked."""


class HomeAssistant:
    def __init__(self, token: str, url: str = CORE_WEBSOCKET_URL):
        self._token = token
        self._url = url

    async def snapshot(self, session: aiohttp.ClientSession) -> dict:
        """{setup: {domain: seconds}, log: {domain: {errors, warnings}}, marketplace: {domain: {...}} or None}.
        A part Home Assistant does not answer is left empty."""
        if not self._token:
            raise HomeAssistantError("no token; the app needs homeassistant_api")
        out: dict = {"setup": {}, "log": {}, "marketplace": None}
        try:
            async with session.ws_connect(self._url, max_msg_size=MAX_MESSAGE_BYTES) as ws:
                await ws.receive_json(timeout=15)
                await ws.send_json({"type": "auth", "access_token": self._token})
                if (await ws.receive_json(timeout=15)).get("type") != "auth_ok":
                    raise HomeAssistantError("Home Assistant refused the app's token")

                async def ask(number: int, kind: str):
                    await ws.send_json({"id": number, "type": kind})
                    while True:
                        answer = await ws.receive_json(timeout=60)
                        if answer.get("id") == number and answer.get("type") == "result":
                            return answer.get("result") if answer.get("success") else None

                for row in await ask(1, "integration/setup_info") or []:
                    if isinstance(row, dict) and isinstance(row.get("seconds"), int | float):
                        out["setup"][str(row.get("domain"))] = float(row["seconds"])
                for entry in await ask(2, "system_log/list") or []:
                    if not isinstance(entry, dict):
                        continue
                    source = entry.get("source") or []
                    where = f"{entry.get('name') or ''} {source[0] if source else ''}"
                    match = LOGGER_DOMAIN.search(where)
                    if not match:
                        continue
                    counts = out["log"].setdefault(match.group(1), {"errors": 0, "warnings": 0})
                    level = str(entry.get("level") or "").upper()
                    key = "errors" if level in ("ERROR", "CRITICAL") else "warnings" if level == "WARNING" else None
                    if key:
                        counts[key] += int(entry.get("count") or 1)
                repositories = await ask(3, "marketplace/repositories/list")
                if isinstance(repositories, list):
                    out["marketplace"] = {
                        str(r["domain"]): {
                            key: r.get(key)
                            for key in (
                                "full_name",
                                "custom",
                                "installed_version",
                                "available_version",
                                "pending_upgrade",
                            )
                        }
                        for r in repositories
                        if isinstance(r, dict)
                        and r.get("installed")
                        and r.get("category") == "integration"
                        and r.get("domain")
                    }
        except (aiohttp.ClientError, TimeoutError, TypeError, ValueError) as e:
            raise HomeAssistantError(f"{type(e).__name__}: {e}") from e
        return out
