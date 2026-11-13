"""The miners (surplus loads): switched on and off with the solar surplus, one by one.

Each miner has a Home Assistant switch that starts and stops it, a power sensor with what it
draws, and a rated power (optional with a sensor: the draw is learned from it). The app reads
them every poll and, for the miners on *auto*, switches them:

- **Start** the next miner (in the order of the configuration) while the surplus signal is on
  and the estimated surplus covers that miner's draw (its rated power, or the draw learned from
  its sensor; the surplus margin while neither is known).
- **Stop** the miner that started last once the house is no longer covered by the panels: it
  imports more than *off when importing* from the grid, or drains the battery, for the
  debounce time. Then the next one, as long as that lasts.

That is the hysteresis: a miner starts only when the surplus carries it, and stops only when
the house draws from the grid or the battery, not when the surplus estimate merely dips. One
switch per debounce period (the sensors and the inverter settle in between), and a miner that
was stopped stays off for at least MIN_OFF_SECONDS.

A miner on *manual* (dashboard) is left alone: only read, shown and counted. A miner without
a switch is read only; one with neither switch nor sensor is taken to run whenever the surplus
signal is on, at its rated power.

Every poll the state is stamped into the inverter data: the totals ("miners_on", "miners_w")
and each miner ("miner1_on", "miner1_w", ...), so it is stored with the snapshots and
integrated into hours and energy per day.
"""

import asyncio
import json
import logging
import time
from pathlib import Path

from .config import Config, Miner
from .events import EventBus
from .ha_client import HAClient, HAClientError
from .inverter import InverterLink

logger = logging.getLogger(__name__)

ON_STATES = {"on", "true", "home", "open", "running", "mining", "active"}
OFF_STATES = {"off", "false", "not_home", "closed", "idle", "stopped", "standby"}
# A power sensor counts as "running" above this share of the miner's draw (and at least RUNNING_MIN_W)
RUNNING_SHARE = 0.05
RUNNING_MIN_W = 20
# Report an entity as unreadable only after this long without a good reading
STALE_SECONDS = 300
# A miner that was stopped stays off at least this long
MIN_OFF_SECONDS = 300
# The shortest pause between two switch actions, whatever the debounce is set to
MIN_STEP_SECONDS = 60


def _fmt_duration(seconds: int) -> str:
    return f"{seconds // 3600} h {seconds % 3600 // 60} min"


class MinerUnit:
    """One miner: its configuration, what its entities say, and what the app did with it."""

    def __init__(self, index: int, cfg: Miner):
        self.index = index  # 1-based, the key in the snapshots ("miner1_w")
        self.cfg = cfg
        self.auto = True
        self.learned_w: float | None = None
        self.switch_on: bool | None = None
        self.sensor_w: float | None = None
        self._switch_at = 0.0
        self._sensor_at = 0.0
        self.on: bool | None = None  # running
        self.watts: float | None = None
        self.since: int | None = None  # when it last started or stopped (epoch)
        self.error: str | None = None
        self.started_mono = 0.0  # when the app (or anyone) last switched it on, for the stop order
        self.stopped_mono = -1e9

    @property
    def name(self) -> str:
        return self.cfg.name

    @property
    def expected_w(self) -> float | None:
        """What it draws while running: the rated power, else what its sensor taught."""
        return float(self.cfg.power_w) if self.cfg.power_w else self.learned_w

    @property
    def controllable(self) -> bool:
        return bool(self.cfg.switch)

    def fresh(self, at: float) -> bool:
        return time.monotonic() - at < STALE_SECONDS

    def reading(self, surplus: bool) -> tuple[bool | None, float | None]:
        """Running or not, and the draw, from what is known now."""
        if self.cfg.sensor:
            watts = self.sensor_w if self.fresh(self._sensor_at) else None
            if watts is None:
                return None, None
            # A switch that is off means not mining, whatever the sensor still says: it may lag a
            # poll or two, and a miner whose switch only stops the mining keeps drawing some power
            if self.cfg.switch and self.switch_on is False and self.fresh(self._switch_at):
                return False, watts
            floor = max(RUNNING_MIN_W, (self.expected_w or 0) * RUNNING_SHARE)
            return watts >= floor, watts
        if self.cfg.switch:
            on = self.switch_on if self.fresh(self._switch_at) else None
            if on is None:
                return None, None
            return on, float(self.cfg.power_w) if on else 0.0
        return surplus, float(self.cfg.power_w) if surplus else 0.0

    def state(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "switch": self.cfg.switch or None,
            "sensor": self.cfg.sensor or None,
            "power_w": self.cfg.power_w or None,
            "learned_w": None if self.learned_w is None else round(self.learned_w),
            "expected_w": None if self.expected_w is None else round(self.expected_w),
            "auto": self.auto if self.controllable else None,
            "switch_on": self.switch_on,
            "on": self.on,
            "watts": None if self.watts is None else round(self.watts),
            "since": self.since,
            "error": self.error,
        }


class Miners:
    def __init__(self, config: Config, ha_client: HAClient, link: InverterLink, events: EventBus, data_dir: Path):
        self.units = [MinerUnit(i + 1, m) for i, m in enumerate(config.miners)]
        self.poll_seconds = max(config.poll_seconds, 10)
        self.ha_client = ha_client
        self.events = events
        self.controller = None  # the battery controller (surplus signal and overload), set by __main__
        self.file = data_dir / "miners.json"
        self.action = ""  # what the app did last, or is waiting for, in words
        self._last_action = -1e9
        self._overload_since: float | None = None
        self._saved_at = 0.0
        self._load()
        # After the controller's listener (which stamps "surplus"), before the recorder's
        link.listeners.append(self.on_data)

    @property
    def enabled(self) -> bool:
        return bool(self.units)

    def unit(self, name: str) -> MinerUnit:
        for u in self.units:
            if u.name == name:
                return u
        raise ValueError(f"No miner named {name!r}")

    # ------------------------------------------------------------------ persistence
    def _load(self) -> None:
        try:
            data = json.loads(self.file.read_text())
        except (OSError, ValueError):
            return
        if not isinstance(data, dict):
            return
        for u in self.units:
            saved = data.get(u.name)
            if isinstance(saved, dict):
                u.auto = bool(saved.get("auto", True))
                learned = saved.get("learned_w")
                u.learned_w = float(learned) if isinstance(learned, (int, float)) and learned > 0 else None

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        data = {u.name: {"auto": u.auto, "learned_w": u.learned_w and round(u.learned_w)} for u in self.units}
        self.file.write_text(json.dumps(data, indent=2))
        self._saved_at = time.monotonic()

    # ------------------------------------------------------------------ state
    def state(self) -> dict:
        on = [u.on for u in self.units]
        watts = [u.watts for u in self.units if u.watts is not None]
        return {
            "enabled": self.enabled,
            "name": "Miners",
            "on": any(on) if any(v is not None for v in on) else None,
            "running": sum(1 for v in on if v),
            "watts": round(sum(watts)) if watts else None,
            "error": next((f"{u.name}: {u.error}" for u in self.units if u.error), None),
            "action": self.action,
            "miners": [u.state() for u in self.units],
        }

    def on_data(self, data: dict) -> None:
        """Every poll: each miner's state, stamped into the data for the recorder."""
        if not self.enabled:
            return
        surplus = bool(data.get("surplus"))
        total_w, any_on, known = 0.0, False, False
        for u in self.units:
            on, watts = u.reading(surplus)
            self._set(u, on, watts)
            if on is None:
                continue
            known = True
            any_on = any_on or on
            total_w += watts or 0
            data[f"miner{u.index}_on"] = 1 if on else 0
            data[f"miner{u.index}_w"] = round(watts or 0)
        if known:
            data["miners_on"] = 1 if any_on else 0
            data["miners_w"] = round(total_w)

    def _set(self, u: MinerUnit, on: bool | None, watts: float | None) -> None:
        u.watts = watts
        if on and u.cfg.sensor and watts:
            # Learn the draw while it runs (a slow average: the start-up peak doesn't count much)
            u.learned_w = watts if u.learned_w is None else u.learned_w * 0.9 + watts * 0.1
            if time.monotonic() - self._saved_at > 600:
                self._save()
        if on is None or on == u.on:
            if on is not None:
                u.on = on
            return
        was, u.on = u.on, on
        now = int(time.time())
        if was is not None:  # not on the first reading after start
            if on:
                self.events.emit("miners_started", f"{u.name} started (≈ {round(watts or 0)} W)", miner=u.name,
                                 watts=round(watts or 0))  # fmt: skip
            else:
                ran = now - u.since if u.since else None
                self.events.emit("miners_stopped", f"{u.name} stopped{f' after {_fmt_duration(ran)}' if ran else ''}",
                                 miner=u.name, seconds=ran)  # fmt: skip
        u.since = now

    # ------------------------------------------------------------------ the loop
    async def run(self) -> None:
        """Read the entities every poll, then switch the miners on auto."""
        if not self.enabled:
            return
        for u in self.units:
            parts = [f"switch {u.cfg.switch}" if u.cfg.switch else "no switch (read only)"]
            if u.cfg.sensor:
                parts.append(f"power from {u.cfg.sensor}")
            if u.cfg.power_w:
                parts.append(f"{u.cfg.power_w} W rated")
            logger.info(f"{u.name}: {', '.join(parts)}{'' if u.auto or not u.controllable else ', manual'}")
        if not self.ha_client.available:
            for u in self.units:
                if u.cfg.switch or u.cfg.sensor:
                    u.error = "Home Assistant API not available"
            logger.warning("Miners: the Home Assistant API is not available, nothing to read or switch")
            return
        while True:
            await self.read()
            try:
                await self.control()
            except Exception as e:  # never let one bad poll end the loop
                logger.exception(f"Miners: control failed: {e}")
            await asyncio.sleep(self.poll_seconds)

    async def read(self) -> None:
        for u in self.units:
            errors = []
            for entity in (u.cfg.switch, u.cfg.sensor):
                if not entity:
                    continue
                try:
                    self._parse(u, entity, await self.ha_client.get_state(entity))
                except (HAClientError, ValueError) as e:
                    errors.append(str(e))
            error = "; ".join(errors) or None
            if error and error != u.error:
                logger.warning(f"{u.name}: {error}")
            u.error = error

    def _parse(self, u: MinerUnit, entity: str, body: dict) -> None:
        state = str((body or {}).get("state", "")).strip().lower()
        if state in ("unavailable", "unknown", ""):
            raise ValueError(f"{entity} is {state or 'empty'}")
        if entity == u.cfg.switch:
            if state not in ON_STATES and state not in OFF_STATES:
                raise ValueError(f"{entity} has state {state!r}: expected on or off")
            u.switch_on, u._switch_at = state in ON_STATES, time.monotonic()
            return
        try:
            watts = float(state)
        except ValueError:
            raise ValueError(f"{entity} has state {state!r}: expected a power in W or kW") from None
        unit = str(((body or {}).get("attributes") or {}).get("unit_of_measurement") or "W").strip()
        if unit.lower() == "kw":
            watts *= 1000
        u.sensor_w, u._sensor_at = max(watts, 0.0), time.monotonic()

    async def control(self) -> None:
        """One step of the automatic switching (see the module's docstring)."""
        c = self.controller
        auto = [u for u in self.units if u.auto and u.controllable and u.switch_on is not None]
        if c is None or not auto:
            self.action = ""
            return
        now = time.monotonic()
        step = max(c.settings.surplus_debounce_s, MIN_STEP_SECONDS)
        running = [u for u in auto if u.switch_on]

        if c.overload:
            if self._overload_since is None:
                self._overload_since = now
            if not running:
                self.action = f"waiting for the solar surplus ({c.surplus_reason or c.overload})"
                return
            last = max(running, key=lambda u: (u.started_mono, u.index))
            wait = max(self._overload_since + step, self._last_action + step) - now
            if wait > 0:
                self.action = f"{c.overload}: stopping {last.name} in {int(wait)} s unless that ends"
                return
            await self._switch(last, False, c.overload)
            self._overload_since = now  # the next one only if it still lasts a full step
            return
        self._overload_since = None

        stopped = [u for u in auto if not u.switch_on]
        if not stopped:
            self.action = "all miners on auto are running"
            return
        if not c.surplus:
            self.action = f"waiting for the solar surplus ({c.surplus_reason})" if c.surplus_reason else ""
            return
        nxt = stopped[0]
        need = nxt.expected_w or c.settings.surplus_margin_w
        if c.surplus_w < need:
            self.action = f"surplus about {c.surplus_w} W, {nxt.name} needs about {round(need)} W"
            return
        wait = max(self._last_action + step, nxt.stopped_mono + MIN_OFF_SECONDS) - now
        if wait > 0:
            self.action = f"starting {nxt.name} in {int(wait)} s"
            return
        await self._switch(nxt, True, f"surplus about {c.surplus_w} W")

    async def _switch(self, u: MinerUnit, on: bool, why: str, source: str = "auto") -> bool:
        domain = u.cfg.switch.split(".", 1)[0]
        service = "turn_on" if on else "turn_off"
        now = time.monotonic()
        self._last_action = now
        try:
            await self.ha_client.call_service(domain, service, u.cfg.switch)
        except HAClientError as e:
            self.action = f"could not switch {u.name} {'on' if on else 'off'}: {e}"
            self.events.emit("miner_switch_failed", f"{u.name}: {u.cfg.switch} {service} failed: {e}",
                             level="warning", miner=u.name)  # fmt: skip
            return False
        u.switch_on = on
        if on:
            u.started_mono = now
        else:
            u.stopped_mono = now
        self.action = f"switched {u.name} {'on' if on else 'off'} ({why})"
        logger.info(f"{u.name}: {u.cfg.switch} {service} ({source}: {why})")
        return True

    # ------------------------------------------------------------------ dashboard
    def set_auto(self, name: str, auto: bool) -> dict:
        u = self.unit(name)
        if not u.controllable:
            raise ValueError(f"{name} has no switch: there is nothing to switch")
        u.auto = bool(auto)
        self._save()
        return u.state()

    async def switch(self, name: str, on: bool) -> dict:
        """Switch a miner by hand (from the dashboard): only a miner on manual."""
        u = self.unit(name)
        if not u.controllable:
            raise ValueError(f"{name} has no switch")
        if u.auto:
            raise ValueError(f"{name} is on auto: switch it to manual first")
        if not await self._switch(u, on, "by hand", source="dashboard"):
            raise ValueError(self.action)
        return u.state()
