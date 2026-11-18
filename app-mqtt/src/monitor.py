"""The dashboard's own client on the broker: it sees every message and the broker's $SYS topics.

It subscribes to # and $SYS/# with MQTT 5's "retain as published", so a retained message keeps its
retain flag. Per topic it keeps the count, bytes, rate, the last value and a few values before
it; for the Traffic page the last few thousand messages; for the charts the totals per 5 seconds.

paho runs the connection in its own thread; everything here is guarded by one lock.
"""

import logging
import threading
import time
from collections import deque

import paho.mqtt.client as mqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from paho.mqtt.subscribeoptions import SubscribeOptions

logger = logging.getLogger(__name__)

DISCOVERY_PREFIX = "homeassistant"
MAX_TOPICS = 50000
# The last value of a topic is kept up to this size; discovery configs whole (up to the larger one)
MAX_PAYLOAD = 4096
MAX_CONFIG_PAYLOAD = 65536
# Values kept per topic for its chart, when they are short (numbers, states)
VALUES_PER_TOPIC = 60
MAX_VALUE_SIZE = 64
TRAFFIC_SIZE = 3000
TRAFFIC_PREVIEW = 512
BUCKET = 5
BUCKETS = 15 * 60 // BUCKET
CLIENT_ID = "mqtt-dashboard"


def text_of(payload: bytes, limit: int | None = None) -> tuple[str, bool]:
    """A payload as text, and whether it is binary (then as hex, the first bytes)."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        return payload[:64].hex(" ") + (" …" if len(payload) > 64 else ""), True
    if limit is not None and len(text) > limit:
        text = text[:limit] + "…"
    return text, False


def group_of(topic: str) -> str:
    """The first level of a topic: zigbee2mqtt, homeassistant, tasmota…"""
    return topic.split("/", 1)[0] or "/"


class Topic:
    __slots__ = ("bytes", "count", "first", "last", "payload", "prev", "qos", "rate", "retained", "size", "values")

    def __init__(self, now: float):
        self.count = 0
        self.bytes = 0
        self.first = now
        self.last = now
        self.retained = False
        self.qos = 0
        self.payload: bytes | None = None
        self.size = 0
        self.prev = 0
        self.rate = 0.0
        self.values: deque | None = None

    def row(self, name: str) -> dict:
        text, binary = text_of(self.payload, 160) if self.payload is not None else ("", False)
        return {
            "topic": name,
            "count": self.count,
            "bytes": self.bytes,
            "first": self.first,
            "last": self.last,
            "retained": self.retained,
            "qos": self.qos,
            "size": self.size,
            "rate": round(self.rate, 3),
            "preview": text,
            "binary": binary,
        }


class Monitor:
    def __init__(self, host: str, port: int, username: str, password: str, capture_payloads: bool = True):
        self.host = host
        self.port = port
        self.capture = capture_payloads
        self.lock = threading.Lock()
        self.topics: dict[str, Topic] = {}
        self.sys: dict[str, str] = {}
        self.traffic: deque[dict] = deque(maxlen=TRAFFIC_SIZE)
        self.seq = 0
        # Totals per 5 seconds for the live chart: [ts, messages, bytes]
        self.buckets: deque[list] = deque(maxlen=BUCKETS)
        # For the history (history.py takes and resets them every minute): group -> [messages, bytes]
        self.groups: dict[str, list[int]] = {}
        self.total_messages = 0
        self.total_bytes = 0
        # Topics whose value is kept also without capture_payloads: discovery's availability topics
        self.keep_topics: set[str] = set()
        # Goes up when a discovery config changes, so discovery.py reads them again
        self.config_version = 0
        self.connected = False
        self.error = ""
        self.connected_since: float | None = None
        self.started = time.time()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=CLIENT_ID, protocol=mqtt.MQTTv5)
        self.client.username_pw_set(username, password)
        self.client.reconnect_delay_set(1, 30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------
    def start(self) -> None:
        props = Properties(PacketTypes.CONNECT)
        # A clean start every time: the dashboard doesn't want the messages it missed
        self.client.connect_async(self.host, self.port, keepalive=30, clean_start=True, properties=props)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.disconnect()
        self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason, properties) -> None:
        if reason.is_failure:
            self.error = f"The broker refused the dashboard: {reason}"
            logger.warning(self.error)
            return
        self.connected = True
        self.connected_since = time.time()
        self.error = ""
        options = SubscribeOptions(qos=0, retainAsPublished=True)
        client.subscribe([("#", options), ("$SYS/#", options)])
        logger.info("Following the broker's messages")

    def _on_disconnect(self, client, userdata, flags, reason, properties) -> None:
        self.connected = False
        if reason.is_failure:
            self.error = f"Lost the broker: {reason}"
            logger.warning(self.error)

    # ------------------------------------------------------------------
    # Messages
    # ------------------------------------------------------------------
    def _on_message(self, client, userdata, msg) -> None:
        topic = msg.topic
        payload = msg.payload
        now = time.time()
        if topic.startswith("$SYS/"):
            with self.lock:
                self.sys[topic] = payload[:200].decode("utf-8", "replace")
            return
        size = len(payload)
        with self.lock:
            t = self.topics.get(topic)
            if t is None:
                if len(self.topics) >= MAX_TOPICS:
                    return
                t = self.topics[topic] = Topic(now)
            t.count += 1
            t.bytes += size
            t.last = now
            t.qos = msg.qos
            t.size = size
            is_config = topic.startswith(DISCOVERY_PREFIX + "/") and topic.endswith("/config")
            if msg.retain:
                # An empty retained message deletes the retained one
                t.retained = size > 0
            if is_config:
                t.payload = payload[:MAX_CONFIG_PAYLOAD]
                self.config_version += 1
            elif self.capture or topic in self.keep_topics:
                t.payload = payload[:MAX_PAYLOAD]
                if size <= MAX_VALUE_SIZE:
                    if t.values is None:
                        t.values = deque(maxlen=VALUES_PER_TOPIC)
                    t.values.append((now, payload))
            self.total_messages += 1
            self.total_bytes += size
            g = self.groups.get(group_of(topic))
            if g is None:
                g = self.groups[group_of(topic)] = [0, 0]
            g[0] += 1
            g[1] += size
            bucket = int(now // BUCKET * BUCKET)
            if self.buckets and self.buckets[-1][0] == bucket:
                self.buckets[-1][1] += 1
                self.buckets[-1][2] += size
            else:
                self.buckets.append([bucket, 1, size])
            self.seq += 1
            if self.capture or is_config:
                preview, binary = text_of(payload[: TRAFFIC_PREVIEW * 4], TRAFFIC_PREVIEW)
            else:
                preview, binary = "", False
            self.traffic.append(
                {
                    "seq": self.seq,
                    "ts": now,
                    "topic": topic,
                    "payload": preview,
                    "binary": binary,
                    "size": size,
                    "qos": msg.qos,
                    # Delivered at subscribe time: an old retained message, not traffic now
                    "retain": bool(msg.retain),
                }
            )

    def tick(self, seconds: float) -> None:
        """Every few seconds: the rate of every topic."""
        with self.lock:
            for t in self.topics.values():
                now = (t.count - t.prev) / seconds
                t.rate = now if t.rate == 0 else 0.6 * t.rate + 0.4 * now
                if t.rate < 0.0005:
                    t.rate = 0.0
                t.prev = t.count

    # ------------------------------------------------------------------
    # Reading (from the web server)
    # ------------------------------------------------------------------
    def take_groups(self) -> dict[str, list[int]]:
        with self.lock:
            groups, self.groups = self.groups, {}
        return groups

    def series(self) -> dict:
        """The last 15 minutes per 5 seconds, with the empty buckets filled."""
        with self.lock:
            buckets = [list(b) for b in self.buckets]
        now = int(time.time() // BUCKET * BUCKET)
        by_ts = {b[0]: b for b in buckets}
        xs = list(range(now - (BUCKETS - 1) * BUCKET, now + 1, BUCKET))
        return {
            "bucket": BUCKET,
            "x": xs,
            "messages": [(by_ts[x][1] / BUCKET) if x in by_ts else 0 for x in xs],
            "bytes": [(by_ts[x][2] / BUCKET) if x in by_ts else 0 for x in xs],
        }

    def rate_now(self, seconds: int = 30) -> tuple[float, float]:
        """Messages and bytes per second over the last half minute (the bucket being filled left out)."""
        now = time.time() // BUCKET * BUCKET
        with self.lock:
            recent = [b for b in self.buckets if now - seconds <= b[0] < now]
        return sum(b[1] for b in recent) / seconds, sum(b[2] for b in recent) / seconds

    def topic_rows(self) -> list[dict]:
        with self.lock:
            return [t.row(name) for name, t in self.topics.items()]

    def topic(self, name: str) -> dict | None:
        with self.lock:
            t = self.topics.get(name)
            if t is None:
                return None
            row = t.row(name)
            payload = t.payload
            values = list(t.values or ())
        if payload is not None:
            row["payload"], row["binary"] = text_of(payload)
            row["truncated"] = len(payload) < row["size"]
        row["values"] = [[ts, text_of(v)[0]] for ts, v in values]
        return row

    def payloads(self, prefix: str) -> dict[str, bytes]:
        """The kept values of every topic under a prefix (discovery reads its configs this way)."""
        with self.lock:
            return {
                name: t.payload for name, t in self.topics.items() if name.startswith(prefix) and t.payload is not None
            }

    def values_of(self, names: set[str]) -> dict[str, tuple[str, float]]:
        """The last value and its time of these topics."""
        with self.lock:
            out = {}
            for name in names:
                t = self.topics.get(name)
                if t is not None and t.payload is not None:
                    out[name] = (t.payload[:200].decode("utf-8", "replace"), t.last)
                elif t is not None:
                    out[name] = ("", t.last)
            return out

    def traffic_since(self, seq: int, limit: int = 500) -> list[dict]:
        with self.lock:
            if not self.traffic or self.traffic[-1]["seq"] <= seq:
                return []
            out = [m for m in self.traffic if m["seq"] > seq]
        return out[-limit:]

    def summary(self) -> dict:
        with self.lock:
            retained = sum(1 for t in self.topics.values() if t.retained)
            topics = len(self.topics)
            groups = {}
            for name, t in self.topics.items():
                g = groups.setdefault(group_of(name), [0, 0.0, 0])
                g[0] += 1
                g[1] += t.rate
                g[2] += t.bytes
        rate, byte_rate = self.rate_now()
        return {
            "connected": self.connected,
            "error": self.error,
            "topics": topics,
            "retained": retained,
            "rate": rate,
            "byte_rate": byte_rate,
            "messages": self.total_messages,
            "bytes": self.total_bytes,
            "since": self.started,
            "capture": self.capture,
            "groups": [
                {"group": k, "topics": v[0], "rate": round(v[1], 3), "bytes": v[2]}
                for k, v in sorted(groups.items(), key=lambda kv: -kv[1][1])
            ],
        }

    def sys_values(self) -> dict[str, str]:
        with self.lock:
            return dict(self.sys)

    # ------------------------------------------------------------------
    # Changes (from the dashboard's signed-in ingress only, see webui.py)
    # ------------------------------------------------------------------
    def publish(self, topic: str, payload: str | bytes, qos: int = 0, retain: bool = False) -> None:
        if not self.connected:
            raise RuntimeError("The dashboard is not connected to the broker")
        info = self.client.publish(topic, payload, qos=qos, retain=retain)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            raise RuntimeError(mqtt.error_string(info.rc))

    def retained_matching(self, pattern: str) -> list[str]:
        with self.lock:
            names = [name for name, t in self.topics.items() if t.retained]
        return sorted(n for n in names if mqtt.topic_matches_sub(pattern, n))

    def clear_retained(self, names: list[str]) -> int:
        for name in names:
            self.publish(name, b"", qos=1, retain=True)
        return len(names)
