"""When the dashboard may ask for a donation. The answer is kept here, not in the browser,
so it holds on every device and survives clearing the browser's data."""

import json
import logging
import os
import time
from pathlib import Path

from aiohttp import web

_LOGGER = logging.getLogger(__name__)

DAY = 86400
FIRST_ASK_AFTER_DAYS = 14
# "Maybe later" waits longer each time, and stays at the last value.
LATER_DAYS = (30, 60, 90)
ACTIONS = ("later", "never", "donated")


class DonatePrompt:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _load(self) -> dict | None:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return state if isinstance(state, dict) and isinstance(state.get("next_at"), int | float) else None

    def _save(self, state: dict) -> None:
        tmp = self.path.with_name(self.path.name + ".tmp")
        try:
            tmp.write_text(json.dumps(state), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as err:
            _LOGGER.debug("Could not save %s: %s", self.path, err)

    def should_show(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        state = self._load()
        if state is None:
            # The first visit starts the clock: nobody is asked on first use.
            self._save({"next_at": now + FIRST_ASK_AFTER_DAYS * DAY, "later_count": 0, "done": False})
            return False
        return not state.get("done") and now >= state["next_at"]

    def answer(self, action: str, now: float | None = None) -> None:
        now = time.time() if now is None else now
        state = self._load() or {"next_at": now, "later_count": 0, "done": False}
        if action == "later":
            count = int(state.get("later_count", 0))
            state["next_at"] = now + LATER_DAYS[min(count, len(LATER_DAYS) - 1)] * DAY
            state["later_count"] = count + 1
        else:
            state["done"] = True
        self._save(state)

    def add_routes(self, app: web.Application) -> None:
        app.router.add_get("/api/donate", self._handle_get)
        app.router.add_post("/api/donate", self._handle_post)

    async def _handle_get(self, _request: web.Request) -> web.Response:
        return web.json_response({"show": self.should_show()})

    async def _handle_post(self, request: web.Request) -> web.Response:
        try:
            action = (await request.json()).get("action")
        except (ValueError, AttributeError):
            action = None
        if action not in ACTIONS:
            return web.json_response({"error": "Unknown action"}, status=400)
        self.answer(action)
        return web.json_response({"ok": True})
