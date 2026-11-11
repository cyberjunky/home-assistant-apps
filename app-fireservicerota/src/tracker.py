"""Follows the FireServiceRota integration's entities in Home Assistant and stores what they show.

The data flow is Home Assistant only:
- the entity registry (websocket) says which entities the integration created;
- state_changed events (websocket) bring every change live;
- the recorder history (REST) fills the database at start and after a lost connection;
- the response switch is switched through Home Assistant's switch.turn_on / turn_off.
"""

import asyncio
import contextlib
import logging
import math
from datetime import timedelta
from typing import Any

from .config import Config
from .database import Database, response_outcome
from .ha_client import HAClient, HAClientError, HAWebSocket
from .speech import Speech
from .timeutil import norm_ts, now_ts, parse_ts, utc_now

logger = logging.getLogger(__name__)

PLATFORM = "fireservicerota"
EVENT_TYPE = "fireservicerota_app_incident"
KINDS = ("incidents", "duty", "response")
DOMAINS = {"incidents": "sensor", "duty": "binary_sensor", "response": "switch"}
# unique_id suffixes and translation keys the integration gives its entities
UNIQUE_SUFFIX = {"incidents": "_Incidents", "duty": "_Duty", "response": "_Response"}
TRANSLATION_KEY = {"incidents": "incidents", "duty": "duty", "response": "incident_response"}
# A new incident older than this (e.g. restored after a restart) is stored, not announced
ANNOUNCE_MAX_AGE = timedelta(hours=1)
HISTORY_CHUNK = timedelta(days=3)
# Overlap when filling a gap after a lost connection; duplicates are skipped anyway
GAP_MARGIN = timedelta(minutes=5)
NO_STATE = ("unknown", "unavailable", "")


def distance_km(home: dict | None, lat: Any, lon: Any) -> float | None:
    if not home or lat is None or lon is None:
        return None
    try:
        lat1, lon1, lat2, lon2 = map(math.radians, (home["latitude"], home["longitude"], float(lat), float(lon)))
    except (TypeError, ValueError, KeyError):
        return None
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(6371.0 * 2 * math.asin(math.sqrt(a)), 1)


class Tracker:
    def __init__(self, config: Config, database: Database, ha: HAClient):
        self.config = config
        self.db = database
        self.ha = ha
        # The spoken text of incidents, for the announcements and the dashboard's browser speech
        self.speech = Speech(database)
        self.ws = HAWebSocket(ha, self._on_connected)
        # entity ids per kind; from the options or the entity registry
        self.entities: dict[str, str] = {}
        self.entity_source = ""
        self.discovery_error = ""
        self.config_entries: list[str] = []
        # Latest Home Assistant state object per kind
        self.states: dict[str, dict | None] = dict.fromkeys(KINDS)
        self.home: dict | None = None
        self.ha_version = ""
        self.dashboard_path = ""
        self.backfill = {"running": False, "done": False, "from": None, "imported": 0, "error": "", "finished_at": None}
        self.version = 0
        self._changed = asyncio.Event()
        self._announcements: asyncio.Queue[str] = asyncio.Queue(maxsize=10)
        self._tasks: list[asyncio.Task] = []
        self._running: set[asyncio.Task] = set()
        self._backfill_lock = asyncio.Lock()
        self.last_event_at: str | None = None
        self.last_command: dict | None = None
        self._connected_before = False

    # ---------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        if not self.ha.available:
            logger.error("No SUPERVISOR_TOKEN: the app can't reach Home Assistant")
            self.discovery_error = "No Home Assistant API access (SUPERVISOR_TOKEN missing)"
            return
        slug = await self.ha.get_addon_slug()
        if slug:
            self.dashboard_path = f"/hassio/ingress/{slug}"
        self._tasks.append(asyncio.create_task(self.ws.run()))
        if self.config.announce_enabled:
            self._tasks.append(asyncio.create_task(self._announce_worker()))
        self._tasks.append(asyncio.create_task(self._tick()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    def background(self, coro) -> None:
        """Run a coroutine without waiting for it, keeping a reference until it's done."""
        task = asyncio.get_running_loop().create_task(coro)
        self._running.add(task)
        task.add_done_callback(self._running.discard)

    def changed(self) -> None:
        self.version += 1
        self._changed.set()

    async def wait_changed(self, version: int, timeout: float) -> int:
        """Wait until the version moves past `version` (or the timeout); returns the current version."""
        if self.version == version:
            self._changed.clear()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._changed.wait(), timeout)
        return self.version

    async def _tick(self) -> None:
        """The response window and quiet hours change with time alone: let the dashboards know. While
        sensors are missing it also looks for them again, so installing the integration needs no restart."""
        ticks = 0
        while True:
            await asyncio.sleep(30)
            ticks += 1
            if ticks % 2 == 0 and self.ws.connected and self.sensors_missing():
                await self.rediscover()
            self.changed()

    def sensors_missing(self) -> bool:
        return any(kind not in self.entities for kind in KINDS)

    async def rediscover(self) -> None:
        """Look for the integration's entities again and start following any that turned up."""
        before = dict(self.entities)
        try:
            await self._discover()
        except Exception:
            logger.exception("Looking for the FireServiceRota entities again failed")
            return
        new = {kind: eid for kind, eid in self.entities.items() if before.get(kind) != eid}
        for kind, entity_id in new.items():
            try:
                state = await self.ha.get_state(entity_id)
            except HAClientError as e:
                logger.warning(f"Could not read {entity_id}: {e}")
                continue
            if state:
                self._process(kind, state, source="live", notify=False)
        if new:
            logger.info("Found the FireServiceRota entities: " + ", ".join(new.values()))
            self.background(self.run_backfill())

    # ------------------------------------------------------- connection setup

    async def _on_connected(self) -> None:
        try:
            ha_config = await self.ha.get_config()
            self.ha_version = ha_config.get("version", "")
            if ha_config.get("latitude") is not None and ha_config.get("longitude") is not None:
                self.home = {
                    "latitude": ha_config["latitude"],
                    "longitude": ha_config["longitude"],
                    "name": ha_config.get("location_name") or "Home",
                }
        except HAClientError as e:
            logger.warning(f"Could not read Home Assistant's configuration: {e}")
        await self._discover()
        await self.ws.subscribe_events("state_changed", self._on_state_changed)
        # Changes during a disconnect: the current states, then the recorder for what's in between
        for kind, entity_id in self.entities.items():
            try:
                state = await self.ha.get_state(entity_id)
            except HAClientError as e:
                logger.warning(f"Could not read {entity_id}: {e}")
                continue
            if state:
                # After a lost connection, an incident that came in meanwhile still gets its event and
                # announcement; at the first start the incident already showing is only stored
                self._process(kind, state, source="live", notify=self._connected_before)
        self._connected_before = True
        self.changed()
        self.background(self.run_backfill())

    async def _discover(self) -> None:
        overrides = {
            "incidents": self.config.incidents_entity,
            "duty": self.config.duty_entity,
            "response": self.config.response_entity,
        }
        found: dict[str, str] = {}
        self.discovery_error = ""
        try:
            registry = await self.ws.call({"type": "config/entity_registry/list"})
        except HAClientError as e:
            registry = []
            self.discovery_error = f"Could not read the entity registry: {e}"
            logger.warning(self.discovery_error)
        entries = [e for e in registry or [] if e.get("platform") == PLATFORM and not e.get("disabled_by")]
        self.config_entries = sorted({e.get("config_entry_id") or "" for e in entries})
        if len(self.config_entries) > 1:
            logger.warning(
                f"{len(self.config_entries)} FireServiceRota accounts found; following the first one. "
                "Set the entity options to follow another."
            )
        first_entry = self.config_entries[0] if self.config_entries else None
        for kind in KINDS:
            for e in entries:
                entity_id = e.get("entity_id", "")
                if e.get("config_entry_id", "") != (first_entry or "") or not entity_id.startswith(DOMAINS[kind] + "."):
                    continue
                if e.get("translation_key") == TRANSLATION_KEY[kind] or str(e.get("unique_id", "")).endswith(
                    UNIQUE_SUFFIX[kind]
                ):
                    found[kind] = entity_id
                    break
        for kind, entity_id in overrides.items():
            if entity_id:
                found[kind] = entity_id
        if found != self.entities:
            logger.info("Following " + ", ".join(f"{k}: {v}" for k, v in found.items()) if found else "No entities")
        self.entities = found
        self.entity_source = "options" if any(overrides.values()) else "registry"
        missing = [k for k in KINDS if k not in found]
        if missing and not self.discovery_error:
            self.discovery_error = (
                "FireServiceRota integration not found in Home Assistant"
                if not entries and not any(overrides.values())
                else f"Entity not found: {', '.join(missing)}"
            )

    def _kind_of(self, entity_id: str) -> str | None:
        for kind, eid in self.entities.items():
            if eid == entity_id:
                return kind
        return None

    def _on_state_changed(self, event: dict) -> None:
        data = event.get("data") or {}
        kind = self._kind_of(data.get("entity_id", ""))
        if not kind:
            return
        new_state = data.get("new_state")
        if not new_state:
            return
        self.last_event_at = now_ts()
        self._process(kind, new_state, source="live", notify=True)
        self.changed()

    # ------------------------------------------------------------- processing

    def _process(self, kind: str, state: dict, source: str, notify: bool) -> bool:
        """Store one Home Assistant state of one of the entities; True when something new was stored."""
        ts = norm_ts(state.get("last_updated") or state.get("last_changed")) or now_ts()
        attrs = state.get("attributes") or {}
        value = state.get("state", "")
        if source == "live":
            current = self.states.get(kind)
            if not current or (norm_ts(current.get("last_updated")) or "") <= ts:
                self.states[kind] = state
        if kind == "incidents":
            if attrs.get("id") in (None, ""):
                return False
            body = None if value in NO_STATE else value
            new, updated = self.db.store_incident(attrs, body, ts, source)
            if notify and (new or updated):
                self._incident_out(str(attrs["id"]), new)
            return new or updated
        if kind == "response":
            return self.db.store_response(self.db.incident_at(ts), value, attrs, ts)
        if kind == "duty":
            return self.db.store_duty(value, attrs, ts)
        return False

    # ----------------------------------------------------------- history import

    async def run_backfill(self, days: int | None = None) -> None:
        """Import the recorder history: everything since history_days on first start, else the gap since the last state."""
        if not self.entities:
            return
        async with self._backfill_lock:
            end = utc_now()
            latest = parse_ts(self.db.latest_ts())
            done_before = self.db.get_meta("backfill_days")
            want = self.config.history_days if days is None else days
            if days is None and latest and done_before is not None and int(done_before) >= want:
                start = latest - GAP_MARGIN
            else:
                start = end - timedelta(days=want)
            if want <= 0 and days is None and not latest:
                self.backfill.update(done=True)
                return
            self.backfill.update(running=True, error="", **{"from": start.isoformat()})
            self.changed()
            imported = 0
            ids = list(self.entities.values())
            try:
                chunk_start = start
                while chunk_start < end:
                    chunk_end = min(chunk_start + HISTORY_CHUNK, end)
                    lists = await self.ha.get_history(ids, chunk_start, chunk_end)
                    states = [s for lst in lists for s in lst if isinstance(s, dict)]
                    states.sort(key=lambda s: norm_ts(s.get("last_updated")) or "")
                    for s in states:
                        kind = self._kind_of(s.get("entity_id", ""))
                        if kind and self._process(kind, s, source="history", notify=False):
                            imported += 1
                    chunk_start = chunk_end
                    self.backfill["imported"] = imported
                    await asyncio.sleep(0)
                if days is not None or done_before is None or int(done_before) < want:
                    self.db.set_meta("backfill_days", str(max(want, int(done_before or 0))))
                self.backfill.update(done=True, finished_at=now_ts())
                if imported:
                    logger.info(
                        f"Imported {imported} state(s) from Home Assistant's history since {start:%Y-%m-%d %H:%M}"
                    )
            except HAClientError as e:
                self.backfill["error"] = str(e)
                logger.warning(f"History import failed: {e}")
            finally:
                self.backfill.update(running=False, imported=imported)
                self.changed()

    # -------------------------------------------------------------- responding

    def respond_check(self, incident: dict | None) -> str:
        """Why you can't respond right now, or "" when you can."""
        if "response" not in self.entities:
            return "The incident response switch was not found in Home Assistant"
        if not self.ws.connected:
            return "No connection to Home Assistant"
        switch = self.states.get("response")
        duty = self.states.get("duty")
        if duty and duty.get("state") != "on":
            return "You are not on duty"
        if not switch or switch.get("state") == "unavailable":
            return "The response switch is unavailable (not on duty)"
        if not incident:
            return "There is no incident"
        until = parse_ts(incident.get("can_respond_until"))
        if until and until < utc_now():
            return "The response window has closed"
        return ""

    async def respond(self, action: str) -> None:
        """Acknowledge (switch on) or reject (switch off) the current incident through Home Assistant."""
        incident = self.current_incident()
        reason = self.respond_check(incident)
        if reason:
            raise ValueError(reason)
        service = "turn_on" if action == "acknowledge" else "turn_off"
        entity_id = self.entities["response"]
        logger.info(f"Responding '{action}' to incident {incident['id']} via switch.{service} on {entity_id}")
        self.last_command = {"action": action, "incident_id": incident["id"], "at": now_ts(), "error": ""}
        try:
            await self.ha.call_service("switch", service, {"entity_id": entity_id})
        except HAClientError as e:
            self.last_command["error"] = str(e)
            raise
        finally:
            self.changed()

    # -------------------------------------------------- events and announcements

    def _incident_out(self, incident_id: str, new: bool) -> None:
        incident = self.db.get_incident(incident_id)
        if not incident:
            return
        created = parse_ts(incident.get("created_at"))
        if created and utc_now() - created > ANNOUNCE_MAX_AGE:
            return
        if self.config.ha_events:
            data = {
                k: incident.get(k)
                for k in (
                    "id",
                    "body",
                    "prio",
                    "type",
                    "trigger",
                    "state",
                    "responder_mode",
                    "created_at",
                    "can_respond_until",
                    "latitude",
                    "longitude",
                    "address_type",
                    "formatted_address",
                    "message_to_speech_url",
                    "task_ids",
                )
            }
            data["update"] = not new
            data["spoken"] = (
                self.speech.speak(incident.get("body"), incident.get("prio")) if incident.get("body") else ""
            )
            data["distance_km"] = distance_km(self.home, incident.get("latitude"), incident.get("longitude"))
            if self.dashboard_path:
                data["dashboard_url"] = f"{self.dashboard_path}#incident={incident_id}"
            self.background(self.ha.fire_event(EVENT_TYPE, data))
        if new and self.config.announce_enabled and incident.get("body"):
            if self.config.quiet_now():
                logger.debug(f"Quiet hours ({self.config.announce_quiet_hours}): not announcing incident {incident_id}")
                return
            text = self.speech.speak(incident.get("body"), incident.get("prio"))
            try:
                self._announcements.put_nowait(text)
            except asyncio.QueueFull:
                logger.warning("Too many announcements queued, skipping one")

    async def _announce_worker(self) -> None:
        while True:
            text = await self._announcements.get()
            data = {
                "entity_id": self.config.announce_tts_entity,
                "media_player_entity_id": self.config.announce_media_players,
                "message": text,
            }
            if self.config.announce_language:
                data["language"] = self.config.announce_language
            try:
                await self.ha.call_service("tts", "speak", data)
            except HAClientError as e:
                logger.error(f"Announcement failed: {e}")

    # ---------------------------------------------------------------- snapshot

    def enrich(self, incident: dict) -> dict:
        incident["distance_km"] = distance_km(self.home, incident.get("latitude"), incident.get("longitude"))
        # What the announcement and the browser speech say (the text itself stays in body)
        incident["spoken"] = (
            self.speech.speak(incident.get("body"), incident.get("prio")) if incident.get("body") else ""
        )
        return incident

    def current_incident(self) -> dict | None:
        """The incident the sensor shows now (else the newest stored one)."""
        state = self.states.get("incidents")
        incident_id = ((state or {}).get("attributes") or {}).get("id")
        incident = self.db.get_incident(str(incident_id)) if incident_id not in (None, "") else None
        if incident is None:
            items = self.db.list_incidents(limit=1)["items"]
            incident = self.db.get_incident(items[0]["id"]) if items else None
        return self.enrich(incident) if incident else None

    def snapshot(self) -> dict:
        incident = self.current_incident()
        switch = self.states.get("response") or {}
        duty = self.states.get("duty") or {}
        switch_attrs = switch.get("attributes") or {}
        live_outcome = None
        if incident and switch:
            # The switch's own answer, unless it still shows the previous incident's
            live_outcome = response_outcome(
                incident,
                [
                    {
                        "state": switch.get("state"),
                        **{
                            k: switch_attrs.get(k)
                            for k in ("status", "responded_at", "reported_status", "arrived_at_station")
                        },
                        "ts": switch.get("last_updated"),
                    }
                ],
            )
        response = (live_outcome if live_outcome and live_outcome["status"] != "none" else None) or (
            incident["response"] if incident else None
        )
        return {
            "incident": incident,
            "response": response,
            "can_respond": self.respond_check(incident),
            "switch": {
                "entity_id": self.entities.get("response"),
                "state": switch.get("state"),
                "attributes": switch_attrs,
                "last_changed": switch.get("last_changed"),
            },
            "duty": {
                "entity_id": self.entities.get("duty"),
                "state": duty.get("state"),
                "attributes": duty.get("attributes") or {},
                "last_changed": duty.get("last_changed"),
            },
            "last_command": self.last_command,
        }

    def status(self) -> dict:
        entities = {}
        for kind in KINDS:
            state = self.states.get(kind) or {}
            entities[kind] = {
                "entity_id": self.entities.get(kind),
                "state": (state.get("state") or "")[:80] if state else None,
                "last_changed": state.get("last_changed"),
                "last_updated": state.get("last_updated"),
            }
        return {
            "connected": self.ws.connected,
            "ws_error": self.ws.last_error,
            "reconnects": max(0, self.ws.reconnects),
            "ha_version": self.ha_version,
            "entities": entities,
            "entity_source": self.entity_source,
            "config_entries": len(self.config_entries),
            "discovery_error": self.discovery_error,
            "home": self.home,
            "backfill": self.backfill,
            "history_days": self.config.history_days,
            "ha_events": self.config.ha_events,
            "dashboard_path": self.dashboard_path,
            "announce": {
                "enabled": self.config.announce_enabled,
                "tts_entity": self.config.announce_tts_entity,
                "media_players": self.config.announce_media_players,
            },
            "quiet_hours": self.config.announce_quiet_hours,
            "quiet_now": self.config.quiet_now(),
            "last_event_at": self.last_event_at,
        }
