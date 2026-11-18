"""The dashboard, served through Home Assistant's ingress.

The browser only talks to this app. Publishing, clearing retained messages and pointing Home
Assistant at this broker are only accepted from Home Assistant's signed-in ingress proxy, as JSON.
"""

import asyncio
import json
import logging
import os
import socket
import time
from pathlib import Path

import paho.mqtt.client as mqtt
from aiohttp import web

from . import discovery
from .clients import Clients
from .config import APP_VERSION, BROKER_PORT, DATA_DIR, LISTENERS, Config, system_user
from .donate import DonatePrompt
from .ha import HAError, HomeAssistant
from .history import BUCKETS, History
from .monitor import CLIENT_ID, DISCOVERY_PREFIX, Monitor

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
# Home Assistant's ingress proxy
INGRESS_ADDRESS = "172.30.32.2"
# Home Assistant Core on the Supervisor's network
CORE_ADDRESS = "172.30.32.1"
LIVE_HEARTBEAT = 20
SYSTEM_USERS = ("homeassistant", "addons", "mqtt-dashboard")
MAX_TOPIC_ROWS = 2000


def is_pattern(text: str) -> bool:
    return "+" in text or "#" in text


def valid_filter(text: str) -> bool:
    """An MQTT topic filter: # only as the last level, + and # alone in their level."""
    if not text:
        return False
    levels = text.split("/")
    for i, level in enumerate(levels):
        if "#" in level and (level != "#" or i != len(levels) - 1):
            return False
        if "+" in level and level != "+":
            return False
    return True


def matcher(filter_text: str, query: str):
    """A test for a topic (and its payload): an MQTT filter, and/or text in the topic or payload."""
    filter_text = filter_text.strip()
    query = query.strip().lower()

    def test(topic: str, payload: str = "") -> bool:
        if filter_text and not mqtt.topic_matches_sub(filter_text, topic):
            return False
        return not query or query in topic.lower() or query in payload.lower()

    return test


def tree(rows: list[dict], prefix: str) -> list[dict]:
    """The next level under a prefix: per child its topics, messages, rate, last message and retained ones."""
    depth = len(prefix.split("/")) if prefix else 0
    children: dict[str, dict] = {}
    for r in rows:
        name = r["topic"]
        if prefix and not name.startswith(prefix + "/"):
            continue
        levels = name.split("/")
        if len(levels) <= depth:
            continue
        child = levels[depth]
        c = children.get(child)
        if c is None:
            c = children[child] = {
                "name": child,
                "path": "/".join(levels[: depth + 1]),
                "topics": 0,
                "count": 0,
                "rate": 0.0,
                "last": 0,
                "retained": 0,
                "leaf": None,
            }
        c["topics"] += 1
        c["count"] += r["count"]
        c["rate"] += r["rate"]
        c["last"] = max(c["last"], r["last"])
        c["retained"] += 1 if r["retained"] else 0
        if len(levels) == depth + 1:
            c["leaf"] = r
    out = sorted(children.values(), key=lambda c: c["name"])
    for c in out:
        c["rate"] = round(c["rate"], 3)
        c["has_children"] = c["topics"] > (1 if c["leaf"] else 0)
    return out


class WebUI:
    def __init__(
        self,
        port: int,
        config: Config,
        monitor: Monitor,
        clients: Clients,
        history: History,
        ha: HomeAssistant,
    ):
        self.port = port
        self.config = config
        self.monitor = monitor
        self.clients = clients
        self.history = history
        self.ha = ha
        self.hostname = socket.gethostname()
        self._discovery_cache: tuple[int, float, dict] | None = None
        self._stats_cache: dict[int, tuple[float, dict]] = {}
        self.app = web.Application(client_max_size=1024 * 1024)
        r = self.app.router
        r.add_get("/", self._index)
        r.add_get("/api/status", self._status)
        r.add_get("/api/clients", self._clients)
        r.add_get("/api/events", self._events)
        r.add_get("/api/log", self._log)
        r.add_get("/api/topics", self._topics)
        r.add_get("/api/tree", self._tree)
        r.add_get("/api/topic", self._topic)
        r.add_get("/api/series", self._series)
        r.add_get("/api/discovery", self._discovery)
        r.add_get("/api/stats", self._stats)
        r.add_get("/api/connection", self._connection)
        r.add_get("/api/live", self._live)
        r.add_post("/api/publish", self._publish)
        r.add_post("/api/clear", self._clear)
        r.add_post("/api/ha/switch", self._ha_switch)
        DonatePrompt(DATA_DIR / "donate_prompt.json").add_routes(self.app)
        r.add_static("/static", WEB_DIR)

    async def start(self) -> None:
        runner = web.AppRunner(self.app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "0.0.0.0", self.port).start()
        logger.info(f"Dashboard on port {self.port}")
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()

    async def _index(self, request: web.Request) -> web.Response:
        page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
        return web.Response(text=page, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------
    def ha_client(self) -> dict | None:
        """The connected client that is Home Assistant: it subscribes to the discovery topics."""
        best = None
        for c in self.clients.connected():
            subs = c["subs"]
            discovery_sub = any(s.startswith(DISCOVERY_PREFIX + "/") and s.endswith("/config") for s in subs)
            if discovery_sub and (c["ip"] == CORE_ADDRESS or c["user"] == "homeassistant" or best is None):
                best = c
        return best

    def status(self) -> dict:
        sys = self.monitor.sys_values()
        ha = self.ha_client()
        return {
            "version": APP_VERSION,
            "now": time.time(),
            "monitor": self.monitor.summary(),
            "clients": self.clients.summary(),
            "broker": {
                "version": sys.get("$SYS/broker/version", "").replace("mosquitto version ", "")
                or self.clients.broker_version,
                "uptime": _seconds(sys.get("$SYS/broker/uptime")),
                "clients_total": _int(sys.get("$SYS/broker/clients/total")),
                "clients_max": _int(sys.get("$SYS/broker/clients/maximum")),
                "subscriptions": _int(sys.get("$SYS/broker/subscriptions/count")),
                "retained": _int(sys.get("$SYS/broker/retained messages/count")),
                "stored": _int(sys.get("$SYS/broker/store/messages/count") or sys.get("$SYS/broker/messages/stored")),
                # Mosquitto's load figures are per minute (averaged over a minute): per second here
                "received_rate": _per_second(sys.get("$SYS/broker/load/messages/received/1min")),
                # Mosquitto's load figures are per minute (averaged over a minute): per second here
                "sent_rate": _per_second(sys.get("$SYS/broker/load/messages/sent/1min")),
                # Mosquitto's load figures are per minute (averaged over a minute): per second here
                "bytes_received_rate": _per_second(sys.get("$SYS/broker/load/bytes/received/1min")),
                # Mosquitto's load figures are per minute (averaged over a minute): per second here
                "bytes_sent_rate": _per_second(sys.get("$SYS/broker/load/bytes/sent/1min")),
                "heap": _int(sys.get("$SYS/broker/heap/current")),
                "dropped": _int(sys.get("$SYS/broker/publish/messages/dropped")),
            },
            "ha": {"connected": ha is not None, "client": ha["id"] if ha else "", "user": ha["user"] if ha else ""},
            "log_seq": self.clients.log_seq,
            "config": {
                "keep_days": self.config.keep_days,
                "capture_payloads": self.config.capture_payloads,
                "ssl": self.config.ssl,
            },
        }

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(self.status())

    async def _clients(self, request: web.Request) -> web.Response:
        rows = []
        for c in self.clients.clients.values():
            row = dict(c)
            row["subs"] = [{"topic": t, "qos": q} for t, q in sorted(c["subs"].items())]
            row["listener_name"] = LISTENERS.get(c["listener"] or 0, "")
            row["system"] = c["user"] in SYSTEM_USERS
            row["login"] = (
                "system"
                if c["user"] in SYSTEM_USERS
                else "local"
                if c["user"] in self.config.logins
                else "home assistant"
                if c["user"]
                else "anonymous"
            )
            rows.append(row)
        rows.sort(key=lambda c: (not c["connected"], c["id"].lower()))
        return web.json_response({"clients": rows})

    async def _events(self, request: web.Request) -> web.Response:
        client = request.query.get("client", "")
        events = [e for e in self.clients.events if not client or e["client"] == client]
        return web.json_response({"events": events[-500:][::-1]})

    async def _log(self, request: web.Request) -> web.Response:
        since = _query_int(request, "since", 0)
        query = request.query.get("q", "").lower()
        level = request.query.get("level", "")
        lines = [
            line
            for line in self.clients.log
            if line["seq"] > since
            and (not query or query in line["text"].lower())
            and (not level or line["level"] == level or (level == "warning" and line["level"] == "error"))
        ]
        return web.json_response({"lines": lines[-1000:], "seq": self.clients.log_seq})

    async def _topics(self, request: web.Request) -> web.Response:
        filter_text = request.query.get("filter", "")
        if filter_text and not valid_filter(filter_text):
            return web.json_response({"error": "Not a valid MQTT topic filter"}, status=400)
        test = matcher(filter_text, request.query.get("q", ""))
        only = request.query.get("only", "")
        rows = [r for r in self.monitor.topic_rows() if test(r["topic"], r["preview"])]
        if only == "retained":
            rows = [r for r in rows if r["retained"]]
        elif only == "active":
            rows = [r for r in rows if r["rate"] > 0]
        sort = request.query.get("sort", "topic")
        keys = {
            "topic": lambda r: r["topic"],
            "rate": lambda r: -r["rate"],
            "count": lambda r: -r["count"],
            "bytes": lambda r: -r["bytes"],
            "last": lambda r: -r["last"],
            "size": lambda r: -r["size"],
        }
        rows.sort(key=keys.get(sort, keys["topic"]))
        total = len(rows)
        limit = min(MAX_TOPIC_ROWS, _query_int(request, "limit", 500))
        return web.json_response({"topics": rows[:limit], "total": total})

    async def _tree(self, request: web.Request) -> web.Response:
        prefix = request.query.get("prefix", "").strip("/")
        rows = self.monitor.topic_rows()
        return web.json_response({"prefix": prefix, "children": tree(rows, prefix)})

    async def _topic(self, request: web.Request) -> web.Response:
        row = self.monitor.topic(request.query.get("name", ""))
        if row is None:
            raise web.HTTPNotFound(text="No such topic")
        # Who subscribed to it
        row["subscribers"] = sorted(
            c["id"]
            for c in self.clients.connected()
            if c["id"] != CLIENT_ID
            and any(mqtt.topic_matches_sub(s, row["topic"]) for s in c["subs"] if valid_filter(s))
        )
        return web.json_response(row)

    async def _series(self, request: web.Request) -> web.Response:
        return web.json_response(self.monitor.series())

    def discovery_state(self) -> dict:
        version = self.monitor.config_version
        cached = self._discovery_cache
        if cached and cached[0] == version and time.time() - cached[1] < 5:
            return cached[2]
        payloads = self.monitor.payloads(DISCOVERY_PREFIX + "/")
        items, problems = discovery.entities(payloads)
        self.monitor.keep_topics = discovery.watched_topics(items)
        names = self.monitor.keep_topics | {e["topics"]["state_topic"] for e in items if "state_topic" in e["topics"]}
        values = self.monitor.values_of(names)
        devices = discovery.devices(items, values, self.monitor.started)
        origins: dict[str, int] = {}
        for d in devices:
            origins[d["origin"] or "unknown"] = origins.get(d["origin"] or "unknown", 0) + 1
        result = {
            "prefix": DISCOVERY_PREFIX,
            "devices": devices,
            "problems": problems,
            "entities": len(items),
            "origins": origins,
            "watching_since": self.monitor.started,
        }
        self._discovery_cache = (version, time.time(), result)
        return result

    async def _discovery(self, request: web.Request) -> web.Response:
        return web.json_response(self.discovery_state())

    async def _stats(self, request: web.Request) -> web.Response:
        days = _query_int(request, "days", 1)
        if days not in BUCKETS:
            raise web.HTTPBadRequest(text=f"days must be one of {sorted(BUCKETS)}")
        cached = self._stats_cache.get(days)
        if cached and time.time() - cached[0] < 30:
            return web.json_response(cached[1])
        result = await asyncio.to_thread(self.history.stats, days)
        self._stats_cache[days] = (time.time(), result)
        return web.json_response(result)

    async def _connection(self, request: web.Request) -> web.Response:
        entry = None
        error = ""
        if self.ha.available:
            try:
                entry = await self.ha.mqtt_entry()
            except HAError as e:
                error = str(e)
        return web.json_response(
            {
                "hostname": self.hostname,
                "listeners": [
                    {"port": p, "name": n, "tls": p in (8883, 8884)}
                    for p, n in LISTENERS.items()
                    if p in (1883, 1884) or self.config.ssl
                ],
                "logins": list(self.config.logins),
                "mqtt_entry": {"title": entry.get("title"), "state": entry.get("state")} if entry else None,
                "ha": self.status()["ha"],
                "error": error,
            }
        )

    async def _live(self, request: web.Request) -> web.StreamResponse:
        """Server-sent events: the status every 2 seconds, and with traffic=1 the messages that match."""
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["X-Accel-Buffering"] = "no"
        await response.prepare(request)
        traffic = request.query.get("traffic") == "1"
        filter_text = request.query.get("filter", "")
        test = matcher(filter_text if valid_filter(filter_text) else "", request.query.get("q", ""))
        live_retained = request.query.get("retained") != "0"
        seq = self.monitor.seq
        last_status = 0.0
        try:
            await response.write(f"event: hello\ndata: {json.dumps({'seq': seq})}\n\n".encode())
            while True:
                await asyncio.sleep(0.5)
                if traffic:
                    batch = self.monitor.traffic_since(seq, 1000)
                    if batch:
                        seq = batch[-1]["seq"]
                        shown = [
                            m for m in batch if test(m["topic"], m["payload"]) and (live_retained or not m["retain"])
                        ]
                        if shown:
                            await response.write(
                                f"event: messages\ndata: {json.dumps({'messages': shown[-200:], 'skipped': max(0, len(shown) - 200)})}\n\n".encode()
                            )
                if time.monotonic() - last_status >= 2:
                    last_status = time.monotonic()
                    await response.write(f"event: status\ndata: {json.dumps(self.status())}\n\n".encode())
        except (ConnectionResetError, TimeoutError, asyncio.CancelledError):
            pass
        return response

    # ------------------------------------------------------------------
    # Changes: only from Home Assistant's signed-in ingress, as JSON
    # ------------------------------------------------------------------
    @staticmethod
    async def _change_body(request: web.Request) -> dict:
        # MQTT_DEV is only set for development
        if os.environ.get("SUPERVISOR_TOKEN") and not os.environ.get("MQTT_DEV") and request.remote != INGRESS_ADDRESS:
            raise web.HTTPForbidden(text="Use the sidebar")
        if request.content_type != "application/json":
            raise web.HTTPUnsupportedMediaType(text="Send JSON")
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            raise web.HTTPBadRequest(text="Send a JSON object")
        return body

    async def _publish(self, request: web.Request) -> web.Response:
        body = await self._change_body(request)
        topic = str(body.get("topic") or "")
        if not topic or is_pattern(topic) or topic.startswith("$"):
            return web.json_response({"error": "Give a topic without + or #, not starting with $"}, status=400)
        try:
            qos = int(body.get("qos") or 0)
        except (TypeError, ValueError):
            qos = -1
        if qos not in (0, 1, 2):
            return web.json_response({"error": "QoS is 0, 1 or 2"}, status=400)
        payload = str(body.get("payload") or "")
        try:
            self.monitor.publish(topic, payload.encode(), qos, bool(body.get("retain")))
        except (RuntimeError, ValueError) as e:
            return web.json_response({"error": str(e)}, status=503)
        logger.info(f"Published to {topic} from the dashboard{' (retained)' if body.get('retain') else ''}")
        return web.json_response({"ok": True})

    async def _clear(self, request: web.Request) -> web.Response:
        """Clear retained messages: the given topics, or every retained one matching a filter. dry_run: only count."""
        body = await self._change_body(request)
        if isinstance(body.get("topics"), list):
            names = [str(t) for t in body["topics"] if isinstance(t, str) and t and not is_pattern(t)]
        else:
            pattern = str(body.get("filter") or "")
            if not valid_filter(pattern):
                return web.json_response({"error": "Not a valid MQTT topic filter"}, status=400)
            names = self.monitor.retained_matching(pattern)
        if body.get("dry_run"):
            return web.json_response({"topics": names[:200], "count": len(names)})
        try:
            count = self.monitor.clear_retained(names)
        except RuntimeError as e:
            return web.json_response({"error": str(e)}, status=503)
        logger.info(f"Cleared {count} retained message(s) from the dashboard")
        self._discovery_cache = None
        return web.json_response({"ok": True, "count": count})

    async def _ha_switch(self, request: web.Request) -> web.Response:
        await self._change_body(request)
        try:
            username, password = system_user("homeassistant")
            message = await self.ha.reconfigure_mqtt(self.hostname, BROKER_PORT, username, password)
        except (HAError, OSError, KeyError, ValueError) as e:
            return web.json_response({"error": str(e)}, status=502)
        logger.info("Home Assistant's MQTT integration now uses this broker")
        return web.json_response({"ok": True, "message": message})


def _query_int(request: web.Request, name: str, default: int) -> int:
    try:
        return int(request.query.get(name, default))
    except ValueError:
        return default


def _int(text: str | None) -> int | None:
    try:
        return int(str(text).split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def _float(text: str | None) -> float | None:
    try:
        return float(str(text).split()[0])
    except (TypeError, ValueError, IndexError):
        return None


def _per_second(text: str | None) -> float | None:
    value = _float(text)
    return None if value is None else value / 60


def _seconds(text: str | None) -> int | None:
    """Mosquitto's uptime: "12345 seconds"."""
    return _int(text)
