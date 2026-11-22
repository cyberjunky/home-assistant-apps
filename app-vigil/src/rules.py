"""Alert rules: evaluated every 30 seconds, notifying Home Assistant when they fire and resolve.

A rule's value comes from its kind:
  log_count    matching log lines in the last `window` seconds        query {q, window}
  metric       an aggregate of a metric over the last `window` s      query {name, filter, agg, window}
  absence      seconds since the last matching line or point          query {q} or {name, filter}
  pattern_new  log patterns first seen in the last `window` s         query {window, q?}
  anomaly      |z| of the metric's last step against its usual band   query {name, filter, agg, step}
and it fires when `value <op> threshold` has held for `for_seconds`:
  ok -> pending (true, waiting) -> firing -> ok (false again: resolved)
Firing and resolving each leave a row in vigil.alerts, a vigil_alert event in Home Assistant, a
persistent notification (dismissed again on resolve) and, when set, a message to notify_service.
"""

import asyncio
import logging
import math
import os
import time
from datetime import UTC, datetime

import aiohttp

from .anomaly import last_zscore
from .config import CORE_API_URL, Config
from .database import METRIC_AGGS, Database, is_connection_error
from .query import QueryError, to_where
from .records import LogEntry

logger = logging.getLogger(__name__)

INTERVAL = 30
KINDS = ("log_count", "metric", "absence", "pattern_new", "anomaly")
OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}
SEVERITY_LEVELS = ("info", "warning", "critical")
LOG_SEVERITY = {"info": 6, "warning": 4, "critical": 2}
ABSENCE_LOOKBACK = 7 * 86400
DEFAULT_WINDOW = 300
MAX_WINDOW = 7 * 86400

# What the rule editor offers to start from
TEMPLATES = [
    {
        "name": "Errors from any host > 20 in 5 min",
        "kind": "log_count",
        "query": {"q": "severity:<=err", "window": 300},
        "condition": {"op": ">", "threshold": 20},
        "for_seconds": 0,
        "severity": "warning",
    },
    {
        "name": "Host silent for 10 min",
        "kind": "absence",
        "query": {"q": "host:my-server"},
        "condition": {"op": ">", "threshold": 600},
        "for_seconds": 0,
        "severity": "warning",
    },
    {
        "name": "SSH login",
        "kind": "log_count",
        "query": {"q": "app:sshd Accepted", "window": 60},
        "condition": {"op": ">", "threshold": 0},
        "for_seconds": 0,
        "severity": "info",
    },
    {
        "name": "New log patterns",
        "kind": "pattern_new",
        "query": {"window": 3600},
        "condition": {"op": ">", "threshold": 0},
        "for_seconds": 0,
        "severity": "info",
    },
    {
        "name": "Router CPU > 85 %",
        "kind": "metric",
        "query": {"name": "mktxp_system_cpu_load", "filter": {}, "agg": "avg", "window": 300},
        "condition": {"op": ">", "threshold": 85},
        "for_seconds": 300,
        "severity": "warning",
    },
]


def compare(op: str, value: float | None, threshold: float) -> bool:
    """Whether the condition holds; no value (no data) never fires."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return False
    return OPS[op](value, threshold)


def step(state: str, since: float | None, active: bool, for_seconds: float, now: float):
    """The state machine: (state, since, event) after one evaluation; event is 'firing',
    'resolved' or None."""
    if active:
        if state == "firing":
            return "firing", since, None
        if state == "pending" and since is not None:
            if now - since >= for_seconds:
                return "firing", now, "firing"
            return "pending", since, None
        # ok (or pending without a start): waiting starts now, or fires at once without a wait
        if for_seconds <= 0:
            return "firing", now, "firing"
        return "pending", now, None
    if state == "firing":
        return "ok", now, "resolved"
    if state == "pending":
        return "ok", now, None
    return "ok", since if since is not None else now, None


def _number(value, name: str, low: float | None = None, high: float | None = None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} must be a number") from e
    if not math.isfinite(number) or (low is not None and number < low) or (high is not None and number > high):
        raise ValueError(f"{name} is out of range")
    return number


def _filters(value) -> dict:
    if value in (None, ""):
        return {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError("filter must be an object of label: value texts")
    return value


def validate(body) -> dict:
    """A rule from the editor, checked and normalised; ValueError says what is wrong."""
    if not isinstance(body, dict):
        raise ValueError("Expected a JSON object")
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 200:
        raise ValueError("A rule needs a name (at most 200 characters)")
    kind = body.get("kind")
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    raw = body.get("query") or {}
    if not isinstance(raw, dict):
        raise ValueError("query must be an object")
    query: dict = {}
    if kind in ("log_count", "pattern_new") or (kind == "absence" and "name" not in raw):
        query["q"] = str(raw.get("q") or "")
        try:
            to_where(query["q"])
        except QueryError as e:
            raise ValueError(f"The search is not valid: {e}") from e
    if kind in ("metric", "anomaly") or (kind == "absence" and "name" in raw):
        metric = str(raw.get("name") or "").strip()
        if not metric:
            raise ValueError("A metric rule needs the metric's name")
        query["name"] = metric
        query["filter"] = _filters(raw.get("filter"))
    if kind in ("metric", "anomaly"):
        agg = raw.get("agg") or "avg"
        if agg not in METRIC_AGGS:
            raise ValueError(f"agg must be one of {', '.join(METRIC_AGGS)}")
        query["agg"] = agg
    if kind in ("log_count", "metric", "pattern_new"):
        query["window"] = int(_number(raw.get("window", DEFAULT_WINDOW), "window", 10, MAX_WINDOW))
    if kind == "anomaly":
        query["step"] = int(_number(raw.get("step", DEFAULT_WINDOW), "step", 10, 86400))
    condition = body.get("condition") or {}
    if not isinstance(condition, dict) or condition.get("op") not in OPS:
        raise ValueError(f"condition.op must be one of {' '.join(OPS)}")
    severity = body.get("severity") or "warning"
    if severity not in SEVERITY_LEVELS:
        raise ValueError(f"severity must be one of {', '.join(SEVERITY_LEVELS)}")
    rule_id = body.get("id")
    return {
        "id": int(_number(rule_id, "id", 1)) if rule_id not in (None, "", 0) else None,
        "name": name,
        "kind": kind,
        "query": query,
        "condition": {"op": condition["op"], "threshold": _number(condition.get("threshold"), "threshold")},
        "for_seconds": int(_number(body.get("for_seconds") or 0, "for_seconds", 0, 86400)),
        "severity": severity,
        "enabled": bool(body.get("enabled", True)),
        "notify": bool(body.get("notify", True)),
    }


def _format(value: float | None) -> str:
    if value is None:
        return "no data"
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.3g}"


def alert_message(rule: dict, value: float | None, state: str) -> str:
    condition = rule["condition"]
    if state == "resolved":
        return f"{rule['name']}: resolved ({_format(value)})"
    return f"{rule['name']}: {_format(value)} ({condition['op']} {_format(condition['threshold'])})"


class HomeAssistant:
    """Events, persistent notifications and notify actions through the Supervisor's Core proxy."""

    def __init__(self, config: Config):
        self.config = config
        self.token = os.environ.get("SUPERVISOR_TOKEN")

    async def _post(self, path: str, data: dict) -> None:
        if not self.token:
            logger.debug(f"Not under Home Assistant; skipping {path}")
            return
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with (
                aiohttp.ClientSession(headers={"Authorization": f"Bearer {self.token}"}, timeout=timeout) as session,
                session.post(f"{CORE_API_URL}/{path}", json=data) as response,
            ):
                if response.status >= 300:
                    text = (await response.text()).strip()[:200]
                    logger.warning(f"Home Assistant refused {path}: {response.status} {text}")
        except (aiohttp.ClientError, TimeoutError) as e:
            logger.warning(f"Cannot reach Home Assistant for {path}: {e}")

    async def alert(self, rule: dict, state: str, value: float | None, message: str) -> None:
        await self._post(
            "events/vigil_alert",
            {
                "rule": rule["id"],
                "name": rule["name"],
                "state": state,
                "value": value,
                "severity": rule["severity"],
                "message": message,
            },
        )
        if not rule.get("notify", True):
            return
        notification_id = f"vigil_rule_{rule['id']}"
        if state == "firing":
            await self._post(
                "services/persistent_notification/create",
                {"notification_id": notification_id, "title": f"Vigil: {rule['name']}", "message": message},
            )
        else:
            await self._post("services/persistent_notification/dismiss", {"notification_id": notification_id})
        target = self.config.notify_target
        if target:
            await self._post(f"services/notify/{target}", {"title": f"Vigil: {rule['name']}", "message": message})

    async def dismiss(self, rule_id: int) -> None:
        await self._post("services/persistent_notification/dismiss", {"notification_id": f"vigil_rule_{rule_id}"})


class RuleEngine:
    def __init__(self, database: Database, home_assistant: HomeAssistant, log=None):
        self.database = database
        self.home_assistant = home_assistant
        self.log = log  # writer.put_log: firing and resolving are log lines of Vigil itself too
        self.errors: dict[int, str] = {}
        self._lock = asyncio.Lock()

    async def evaluate(self, rule: dict) -> tuple[float | None, str]:
        """(value, detail) of a rule right now."""
        kind, query = rule["kind"], rule["query"]
        now = time.time()
        if kind == "log_count":
            window = query.get("window", DEFAULT_WINDOW)
            value = await self.database.count_logs(query.get("q", ""), now - window, now + 1)
            return float(value), f"{value} matching lines in the last {window} s"
        if kind == "metric":
            window = query.get("window", DEFAULT_WINDOW)
            value = await self.database.metric_value(
                query["name"], query.get("filter") or {}, query.get("agg", "avg"), window
            )
            return value, f"{query.get('agg', 'avg')} of {query['name']} over {window} s: {_format(value)}"
        if kind == "absence":
            if query.get("name"):
                last = await self.database.last_point(query["name"], query.get("filter") or {}, ABSENCE_LOOKBACK)
            else:
                last = await self.database.last_log(query.get("q", ""), ABSENCE_LOOKBACK)
            if last is None:
                return float(ABSENCE_LOOKBACK), "nothing in the last 7 days"
            value = max(0.0, now - last)
            return round(value, 1), f"last data {value:.0f} s ago"
        if kind == "pattern_new":
            window = query.get("window", DEFAULT_WINDOW)
            value = await self.database.new_patterns(now - window, query.get("q", ""))
            return float(value), f"{value} new patterns in the last {window} s"
        if kind == "anomaly":
            step_seconds = query.get("step", DEFAULT_WINDOW)
            points = await self.database.metric_recent(
                query["name"], query.get("filter") or {}, query.get("agg", "avg"), step_seconds, 7 * 86400 + 3600
            )
            z = last_zscore(points)
            if z is None:
                return None, "too little history for a band"
            return round(z, 3), f"last value {_format(points[-1][1])} is {z:.1f} sigma from usual"
        raise ValueError(f"Unknown rule kind {kind}")

    async def test(self, rule: dict) -> dict:
        value, detail = await self.evaluate(rule)
        condition = rule["condition"]
        return {"value": value, "would_fire": compare(condition["op"], value, condition["threshold"]), "detail": detail}

    async def run(self) -> None:
        while True:
            await asyncio.sleep(INTERVAL)
            if not self.database.connected:
                continue
            try:
                await self.evaluate_all()
            except Exception as e:
                # A lost connection is shown on the status page already; anything else is a bug
                if not is_connection_error(e):
                    logger.exception("The rule engine failed")

    async def evaluate_all(self) -> None:
        async with self._lock:
            for rule in await self.database.rules(enabled_only=True):
                await self._evaluate_one(rule)

    async def _evaluate_one(self, rule: dict) -> None:
        try:
            value, _ = await self.evaluate(rule)
        except Exception as e:
            if is_connection_error(e):
                raise
            self.errors[rule["id"]] = str(e)[:300]
            logger.debug(f"Rule {rule['name']} failed: {e}")
            return
        self.errors.pop(rule["id"], None)
        condition = rule["condition"]
        now = time.time()
        active = compare(condition["op"], value, condition["threshold"])
        state, since, event = step(rule["state"], rule["state_since"], active, rule["for_seconds"], now)
        message = alert_message(rule, value, event) if event else None
        await self.database.set_rule_state(rule["id"], state, since, value, (event, value, message) if event else None)
        if event:
            logger.info(f"Alert {message}")
            if self.log:
                self.log(
                    LogEntry(
                        ts=datetime.now(UTC),
                        source="vigil",
                        host=None,
                        app="vigil",
                        severity=LOG_SEVERITY.get(rule["severity"], 4) if event == "firing" else 6,
                        facility=None,
                        message=f"Alert {event}: {message}",
                        attrs={"rule": rule["id"], "state": event},
                    )
                )
            await self.home_assistant.alert(rule, event, value, message)
