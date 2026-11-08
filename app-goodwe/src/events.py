"""Events: what happened, kept in the database, shown in the dashboard and published to MQTT.

Every notable thing the add-on does or notices goes through one bus, so the
dashboard's event list, the MQTT event entity (for Home Assistant automations,
e.g. a Telegram message) and the log all see the same thing.
"""

import json
import logging
import time
from collections import deque
from collections.abc import Callable

logger = logging.getLogger(__name__)

# Every event type the add-on can raise, announced to Home Assistant as the event entity's types.
# kind: (description)
EVENT_TYPES = {
    "inverter_online": "Inverter reachable again",
    "inverter_offline": "Inverter not answering",
    "inverter_write": "A command was sent to the inverter",
    "inverter_write_failed": "A command to the inverter failed",
    "hold_applied": "Battery discharge held (solar expected)",
    "hold_released": "Battery discharge released",
    "hold_violation": "Battery discharging although held",
    "hold_violation_recovered": "Battery hold honoured again",
    "zero_export_set": "Export limit set to 0 W",
    "export_violation": "Exporting although zero export is on",
    "export_violation_recovered": "Export stopped again",
    "write_budget_exhausted": "Daily inverter write budget used up",
    "work_mode_changed": "Inverter work mode changed (General, Off-grid, Backup, Eco)",
    "work_mode_not_general": "Inverter not in General mode: auto-hold paused",
    "fault_detected": "Inverter reports a fault",
    "fault_cleared": "Inverter fault cleared",
    "grid_outage": "Grid lost (off-grid or backup mode)",
    "grid_restored": "Grid back",
    "battery_full": "Battery reached 100%",
    "battery_low": "Battery low",
    "surplus_started": "Solar surplus available (battery full)",
    "surplus_ended": "Solar surplus gone",
}

Listener = Callable[[dict], None]


class EventBus:
    """Raises events: logs them, stores them and hands them to the listeners (MQTT, dashboard)."""

    def __init__(self, database):
        self.database = database
        self.listeners: list[Listener] = []
        self.recent: deque = deque(maxlen=200)
        self.seq = 0

    def emit(self, kind: str, message: str, level: str = "info", **data) -> dict:
        if kind not in EVENT_TYPES:
            raise ValueError(f"Unknown event type {kind}")
        event = {"ts": int(time.time()), "type": kind, "level": level, "message": message, "data": data}
        logger.log(logging.WARNING if level in ("warning", "error") else logging.INFO, f"[{kind}] {message}")
        try:
            self.database.insert_event(event)
        except Exception as e:  # a full disk must not stop the controller
            logger.warning(f"Event not stored: {e}")
        self.recent.appendleft(event)
        self.seq += 1
        for listener in self.listeners:
            try:
                listener(event)
            except Exception as e:
                logger.warning(f"Event listener failed: {e}")
        return event

    @staticmethod
    def as_json(event: dict) -> str:
        return json.dumps(event, default=str)
