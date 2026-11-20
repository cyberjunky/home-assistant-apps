"""The live picture of Home Assistant: every entity's state, its area, and who is listening."""

import asyncio
import hashlib
import json
import logging
import time

from .ha_client import HAWebSocket

logger = logging.getLogger(__name__)

# Attributes too big to send to every tablet on every change (camera tokens stay out as well)
MAX_ATTRIBUTE_BYTES = 2048
SKIP_ATTRIBUTES = {"access_token", "entity_picture_local"}


def picture_mark(path: str) -> str:
    """What the browser gets instead of a picture's address: it changes when the picture does."""
    return hashlib.sha1(path.encode()).hexdigest()[:10]


def slim(state: dict) -> dict:
    """A state as the dashboard gets it: state, attributes and when it last changed."""
    attributes = {}
    for key, value in (state.get("attributes") or {}).items():
        if key in SKIP_ATTRIBUTES:
            continue
        if isinstance(value, (list, dict)) and len(json.dumps(value)) > MAX_ATTRIBUTE_BYTES:
            continue
        if key == "entity_picture":
            # The address carries a token for Home Assistant: it stays in the app (see Hub.pictures)
            value = picture_mark(str(value))
        attributes[key] = value
    return {"s": state.get("state"), "a": attributes, "c": state.get("last_changed")}


class Hub:
    def __init__(self, ws: HAWebSocket):
        self.ws = ws
        self.states: dict[str, dict] = {}
        # entity_id -> the address of its picture in Home Assistant (an album cover, a person's photo)
        self.pictures: dict[str, str] = {}
        # entity_id -> area name, and the entities Home Assistant hides
        self.areas: dict[str, str] = {}
        self.hidden: set[str] = set()
        self.loaded_at: float | None = None
        self.error = ""
        self._listeners: set[asyncio.Queue] = set()

    async def connected(self) -> None:
        """After every (re)connect: read everything and follow the changes."""
        try:
            await self.ws.subscribe_events("state_changed", self._on_state)
            states = await self.ws.call({"type": "get_states"})
            self.states = {s["entity_id"]: slim(s) for s in states}
            self.pictures = {
                s["entity_id"]: s["attributes"]["entity_picture"]
                for s in states
                if (s.get("attributes") or {}).get("entity_picture")
            }
            await self._load_areas()
            await self.ws.subscribe_events("entity_registry_updated", self._on_registry)
            await self.ws.subscribe_events("area_registry_updated", self._on_registry)
            self.loaded_at = time.time()
            self.error = ""
            logger.info(f"{len(self.states)} entities from Home Assistant")
            self.publish({"type": "reload"})
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            logger.exception("Could not read Home Assistant")

    async def _load_areas(self) -> None:
        areas = {a["area_id"]: a["name"] for a in await self.ws.call({"type": "config/area_registry/list"})}
        devices = {d["id"]: d.get("area_id") for d in await self.ws.call({"type": "config/device_registry/list"})}
        self.areas, self.hidden = {}, set()
        for entry in await self.ws.call({"type": "config/entity_registry/list"}):
            area = entry.get("area_id") or devices.get(entry.get("device_id"))
            if area in areas:
                self.areas[entry["entity_id"]] = areas[area]
            if entry.get("hidden_by") or entry.get("disabled_by"):
                self.hidden.add(entry["entity_id"])

    def _on_registry(self, _event: dict) -> None:
        task = asyncio.ensure_future(self._reload_areas())
        task.add_done_callback(lambda t: t.cancelled() or t.exception())

    async def _reload_areas(self) -> None:
        await self._load_areas()
        self.publish({"type": "reload"})

    def _on_state(self, event: dict) -> None:
        data = event.get("data") or {}
        entity_id, new = data.get("entity_id"), data.get("new_state")
        if not entity_id:
            return
        picture = ((new or {}).get("attributes") or {}).get("entity_picture")
        if picture:
            self.pictures[entity_id] = picture
        else:
            self.pictures.pop(entity_id, None)
        if new is None:
            self.states.pop(entity_id, None)
            self.publish({"type": "state", "id": entity_id, "state": None})
            return
        state = slim(new)
        self.states[entity_id] = state
        self.publish({"type": "state", "id": entity_id, "state": state})

    # ------------------------------------------------------------------
    # The tablets that are watching
    # ------------------------------------------------------------------
    def listen(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._listeners.add(queue)
        return queue

    def leave(self, queue: asyncio.Queue) -> None:
        self._listeners.discard(queue)

    def publish(self, message: dict) -> None:
        for queue in list(self._listeners):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # A tablet that can't keep up starts over with a fresh picture
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"type": "reload"})
