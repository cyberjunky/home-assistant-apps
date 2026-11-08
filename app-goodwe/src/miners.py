"""The surplus loads (bitcoin miners, or anything else switched on by the surplus signal).

The app doesn't switch them: a Home Assistant automation does, on the *Solar surplus*
binary sensor. This module only knows whether they run and how much they draw, so the
dashboard, the statistics and Home Assistant can show it:

- With `miners.entity` set, it reads that Home Assistant entity. A switch, input_boolean
  or binary_sensor says on/off and the miners count at their rated `power_w`; a power
  sensor (W or kW) gives the actual draw, and they count as running above a small floor.
- Without an entity, the miners are taken to run whenever the surplus signal is on (your
  automation follows it), at their rated `power_w`.

Every poll the state is stamped into the inverter data ("miners_on", "miners_w"), so it is
stored with the snapshots and integrated into hours and energy per day like the surplus.
"""

import asyncio
import logging
import time

from .config import Config
from .events import EventBus
from .ha_client import HAClient, HAClientError
from .inverter import InverterLink

logger = logging.getLogger(__name__)

ON_STATES = {"on", "true", "home", "open", "running", "mining", "active"}
OFF_STATES = {"off", "false", "not_home", "closed", "idle", "stopped", "standby"}
# A power sensor counts as "running" above this share of the rated power (and at least RUNNING_MIN_W)
RUNNING_SHARE = 0.05
RUNNING_MIN_W = 20
# Report the entity as unreadable only after this long without a good reading
STALE_SECONDS = 300


class Miners:
    def __init__(self, config: Config, ha_client: HAClient, link: InverterLink, events: EventBus):
        self.name = config.miners_name
        self.power_w = config.miners_power_w
        self.entity = config.miners_entity
        self.poll_seconds = max(config.poll_seconds, 10)
        self.ha_client = ha_client
        self.events = events
        self.on: bool | None = None
        self.watts: float | None = None
        self.since: int | None = None  # when they last started or stopped
        self.error: str | None = None
        self._entity_on: bool | None = None
        self._entity_w: float | None = None
        self._entity_at = 0.0
        # After the controller's listener (which stamps "surplus"), before the recorder's
        link.listeners.append(self.on_data)

    @property
    def enabled(self) -> bool:
        return self.power_w > 0 or bool(self.entity)

    @property
    def source(self) -> str:
        return f"entity {self.entity}" if self.entity else "the surplus signal"

    def state(self) -> dict:
        return {
            "enabled": self.enabled,
            "name": self.name,
            "power_w": self.power_w,
            "entity": self.entity or None,
            "source": "entity" if self.entity else "surplus",
            "on": self.on,
            "watts": None if self.watts is None else round(self.watts),
            "since": self.since,
            "error": self.error,
        }

    # ------------------------------------------------------------------
    def on_data(self, data: dict) -> None:
        if not self.enabled:
            return
        if self.entity:
            fresh = time.monotonic() - self._entity_at < STALE_SECONDS
            on = self._entity_on if fresh else None
            watts = self._entity_w if fresh else None
        else:
            on = bool(data.get("surplus"))
            watts = None
        if on is not None and watts is None:
            watts = float(self.power_w) if on else 0.0
        self._set(on, watts)
        if on is not None:
            data["miners_on"] = 1 if on else 0
            data["miners_w"] = round(watts or 0)

    def _set(self, on: bool | None, watts: float | None) -> None:
        self.watts = watts
        if on is None or on == self.on:
            if on is not None:
                self.on = on
            return
        was, self.on = self.on, on
        now = int(time.time())
        if was is not None:  # not on the first reading after start
            if on:
                self.events.emit(
                    "miners_started",
                    f"{self.name} started (≈ {round(watts or 0)} W, from {self.source})",
                    watts=round(watts or 0),
                )
            else:
                ran = now - self.since if self.since else None
                ran_text = f" after {ran // 3600} h {ran % 3600 // 60} min" if ran else ""
                self.events.emit("miners_stopped", f"{self.name} stopped{ran_text}", seconds=ran)
        self.since = now

    # ------------------------------------------------------------------
    async def run(self) -> None:
        """Read the entity every poll. Without an entity there is nothing to fetch."""
        if not self.enabled:
            return
        logger.info(
            f"{self.name}: {self.power_w} W rated, running state from {self.source}"
            if self.power_w
            else f"{self.name}: running state from {self.source}"
        )
        if not self.entity:
            return
        if not self.ha_client.available:
            self.error = "Home Assistant API not available"
            logger.warning(f"{self.name}: cannot read {self.entity} ({self.error})")
            return
        while True:
            try:
                self._parse(await self.ha_client.get_state(self.entity))
                self._entity_at = time.monotonic()
                self.error = None
            except (HAClientError, ValueError) as e:
                if self.error != str(e):
                    logger.warning(f"{self.name}: cannot read {self.entity}: {e}")
                self.error = str(e)
            await asyncio.sleep(self.poll_seconds)

    def _parse(self, body: dict) -> None:
        state = str((body or {}).get("state", "")).strip().lower()
        if state in ("unavailable", "unknown", ""):
            raise ValueError(f"{self.entity} is {state or 'empty'}")
        if state in ON_STATES or state in OFF_STATES:
            self._entity_on, self._entity_w = state in ON_STATES, None
            return
        try:
            watts = float(state)
        except ValueError:
            raise ValueError(f"{self.entity} has state {state!r}: expected on/off or a power in W or kW") from None
        unit = str(((body or {}).get("attributes") or {}).get("unit_of_measurement") or "W").strip()
        if unit.lower() == "kw":
            watts *= 1000
        watts = max(watts, 0.0)
        self._entity_w = watts
        self._entity_on = watts >= max(RUNNING_MIN_W, self.power_w * RUNNING_SHARE)
