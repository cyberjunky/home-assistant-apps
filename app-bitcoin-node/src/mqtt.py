"""MQTT publishing with Home Assistant discovery.

Bitcoin Core, LND and Tor each become a device in Home Assistant, the last two only when they are
set up. A device's state is one retained JSON message on "<base_topic>/<device>/state", published
when it changed, at most once per PUBLISH_INTERVAL; the sensors pick their value from it. They
become unavailable while the service can't be reached, except "Connected", which tells you so.

What happens (a new block, a forwarded payment, a channel going offline) goes to
"<base_topic>/<device>/event" and to an event entity, for automations. Nothing is taken from MQTT:
Home Assistant can't change the node through this app.

Left out on purpose: addresses, keys and block hashes, and an entity per peer or per channel
(they come and go, and would leave dead entities behind).
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime

import paho.mqtt.client as mqtt

from .config import APP_VERSION, Config, host_of
from .ha_client import HAClient
from .lightning import day_start
from .node import Node

logger = logging.getLogger(__name__)

PUBLISH_INTERVAL = 10

# key, name, unit, icon, device_class, state_class, diagnostic
BITCOIN_SENSORS = (
    ("block_height", "Block height", None, "mdi:cube-outline", None, "measurement", False),
    ("last_block", "Last block", None, "mdi:clock-outline", "timestamp", None, False),
    ("sync_progress", "Sync progress", "%", "mdi:progress-check", None, "measurement", False),
    ("chain_size", "Chain on disk", "GB", "mdi:harddisk", "data_size", "measurement", False),
    ("hashrate", "Network hashrate", "EH/s", "mdi:pickaxe", None, "measurement", False),
    ("difficulty", "Difficulty", None, "mdi:speedometer", None, "measurement", False),
    ("mempool_transactions", "Mempool transactions", None, "mdi:tray-full", None, "measurement", False),
    ("mempool_size", "Mempool size", "MB", "mdi:tray-full", "data_size", "measurement", False),
    ("mempool_memory", "Mempool memory", "MB", "mdi:memory", "data_size", "measurement", False),
    ("fee_next_block", "Fee next block", "sat/vB", "mdi:cash-fast", None, "measurement", False),
    ("fee_1_hour", "Fee within 1 hour", "sat/vB", "mdi:cash-clock", None, "measurement", False),
    ("fee_1_day", "Fee within 1 day", "sat/vB", "mdi:cash", None, "measurement", False),
    ("peers", "Peers", None, "mdi:lan", None, "measurement", False),
    ("peers_inbound", "Peers inbound", None, "mdi:lan-connect", None, "measurement", False),
    ("peers_outbound", "Peers outbound", None, "mdi:lan-connect", None, "measurement", False),
    ("received", "Received", "GB", "mdi:download", "data_size", "total_increasing", False),
    ("sent", "Sent", "GB", "mdi:upload", "data_size", "total_increasing", False),
    ("difficulty_change", "Expected difficulty change", "%", "mdi:chart-line", None, "measurement", False),
    ("blocks_to_adjustment", "Blocks to difficulty adjustment", None, "mdi:counter", None, "measurement", False),
    ("blocks_to_halving", "Blocks to halving", None, "mdi:counter", None, "measurement", False),
    ("version", "Bitcoin Core version", None, "mdi:information-outline", None, None, True),
)
LIGHTNING_SENSORS = (
    ("total_funds", "Total funds", "sat", "mdi:safe", None, "measurement", False),
    ("onchain_balance", "On-chain balance", "sat", "mdi:wallet", None, "measurement", False),
    ("can_send", "Can send", "sat", "mdi:arrow-up-bold-circle-outline", None, "measurement", False),
    ("can_receive", "Can receive", "sat", "mdi:arrow-down-bold-circle-outline", None, "measurement", False),
    ("channels_active", "Channels active", None, "mdi:lightning-bolt", None, "measurement", False),
    ("channels_inactive", "Channels inactive", None, "mdi:lightning-bolt-outline", None, "measurement", False),
    ("channels_pending", "Channels pending", None, "mdi:timer-sand", None, "measurement", False),
    ("fees_today", "Routing fees today", "sat", "mdi:cash-plus", None, "measurement", False),
    ("fees_7_days", "Routing fees 7 days", "sat", "mdi:cash-plus", None, "measurement", False),
    ("fees_30_days", "Routing fees 30 days", "sat", "mdi:cash-plus", None, "measurement", False),
    ("forwards_today", "Payments forwarded today", None, "mdi:swap-horizontal", None, "measurement", False),
    ("alias", "Alias", None, "mdi:tag-outline", None, None, True),
    ("version", "LND version", None, "mdi:information-outline", None, None, True),
)
TOR_SENSORS = (
    ("bootstrap", "Bootstrap", "%", "mdi:progress-check", None, "measurement", False),
    ("circuits", "Circuits", None, "mdi:transit-connection-variant", None, "measurement", False),
    ("received", "Received", "GB", "mdi:download", "data_size", "total_increasing", False),
    ("sent", "Sent", "GB", "mdi:upload", "data_size", "total_increasing", False),
    ("version", "Tor version", None, "mdi:information-outline", None, None, True),
)
# key, name, device_class, icon: on/off values in the same state message
BITCOIN_BINARY = (
    ("synced", "In sync", None, "mdi:check-network-outline"),
    ("onion", "Onion address", None, "mdi:incognito"),
)
LIGHTNING_BINARY = (
    ("synced_chain", "In sync with the chain", None, "mdi:link-variant"),
    ("synced_graph", "In sync with the graph", None, "mdi:graph-outline"),
    ("channels_problem", "Channel offline", "problem", None),
    ("backup_problem", "Channel backup incomplete", "problem", None),
)
EVENT_TYPES = {
    "bitcoin": ("block", "synced", "node"),
    "lightning": (
        "forward",
        "received",
        "payment",
        "channel_opened",
        "channel_closed",
        "channel_offline",
        "channel_online",
        "backup",
        "node",
    ),
}
DEVICES = {
    "bitcoin": ("Bitcoin Core", "Bitcoin Core", BITCOIN_SENSORS, BITCOIN_BINARY, "Node events"),
    "lightning": ("Lightning", "LND", LIGHTNING_SENSORS, LIGHTNING_BINARY, "Lightning events"),
    "tor": ("Tor", "Tor", TOR_SENSORS, (), None),
}


def _round(value, digits: int = 2, scale: float = 1):
    try:
        return round(float(value) / scale, digits)
    except (TypeError, ValueError):
        return None


def bitcoin_payload(node: Node, now: float) -> dict:
    btc = node.bitcoin
    status, mempool, net = btc.node_status(now), btc.mempool_status(), btc.net_status()
    if "blocks" not in status:
        return {}
    last = status.get("last_block_time")
    values = {
        "block_height": status["blocks"],
        "last_block": datetime.fromtimestamp(last, UTC).isoformat() if last else None,
        "sync_progress": _round((status.get("progress") or 0) * 100, 2),
        "chain_size": _round(status.get("size_on_disk"), 2, 1e9),
        "hashrate": _round(status.get("hashrate"), 1, 1e18),
        "difficulty": status.get("difficulty"),
        "mempool_transactions": mempool["tx"],
        "mempool_size": mempool["vsize"],
        "mempool_memory": _round(mempool["usage"], 1, 1e6),
        "fee_next_block": mempool["fees"].get("next"),
        "fee_1_hour": mempool["fees"].get("hour"),
        "fee_1_day": mempool["fees"].get("day"),
        "peers": net["peers"],
        "peers_inbound": net["in"],
        "peers_outbound": net["out"],
        "received": _round(net["recv_total"], 3, 1e9),
        "sent": _round(net["sent_total"], 3, 1e9),
        "difficulty_change": _round(status["retarget"]["change"] * 100, 2),
        "blocks_to_adjustment": status["retarget"]["blocks_left"],
        "blocks_to_halving": status["halving"]["blocks_left"],
        "version": status.get("version"),
        "synced": status["synced"],
        "onion": btc.onion_address() is not None,
    }
    return {key: value for key, value in values.items() if value is not None}


def lightning_payload(node: Node, now: float) -> dict:
    ln = node.lightning
    status = ln.status()
    if not status.get("pubkey"):
        return {}
    channels = status["channels"]
    today = day_start(now)
    backup = status["backup"]
    return {
        "total_funds": status["onchain"]["confirmed"] + channels["local"] + channels["pending_local"],
        "onchain_balance": status["onchain"]["confirmed"],
        "can_send": channels["local"],
        "can_receive": channels["remote"],
        "channels_active": channels["active"],
        "channels_inactive": channels["inactive"],
        "channels_pending": channels["pending"],
        "fees_today": status["fees"]["day"],
        "fees_7_days": status["fees"]["week"],
        "fees_30_days": status["fees"]["month"],
        "forwards_today": sum(1 for f in ln.forwards if f["ts"] >= today),
        "alias": status["alias"],
        "version": status["version"],
        "synced_chain": status["synced_chain"],
        "synced_graph": status["synced_graph"],
        "channels_problem": channels["inactive"] > 0,
        "backup_problem": bool(backup.get("error")) or backup.get("complete") is False,
    }


def tor_payload(node: Node, now: float) -> dict:
    tor = node.tor
    values = {
        "bootstrap": tor.bootstrap,
        "circuits": len(tor.circuits),
        "received": _round(tor.read, 3, 1e9),
        "sent": _round(tor.written, 3, 1e9),
        "version": tor.version or None,
    }
    return {key: value for key, value in values.items() if value is not None}


PAYLOADS = {"bitcoin": bitcoin_payload, "lightning": lightning_payload, "tor": tor_payload}


def device_info(node: Node, key: str) -> dict:
    name, manufacturer, *_ = DEVICES[key]
    client = getattr(node, key)
    version = client.version if key != "bitcoin" else node.bitcoin.node_status(0).get("version", "")
    model = {
        "bitcoin": node.bitcoin.chain.get("chain", ""),
        "lightning": node.lightning.info.get("alias", ""),
        "tor": "",
    }[key]
    host = {
        "bitcoin": host_of(node.config.bitcoin_url),
        "lightning": host_of(node.config.lnd_url),
        "tor": node.config.tor_control,
    }[key]
    return {
        "identifiers": [f"bitcoin_node_{key}"],
        "name": name,
        "manufacturer": manufacturer,
        "model": f"{model} · {host}" if model else host,
        "sw_version": version or "",
    }


def discovery_messages(node: Node, key: str, prefix: str, base: str) -> list[tuple[str, dict]]:
    """Topic and payload of every entity of one device."""
    _, _, sensors, binaries, events_name = DEVICES[key]
    node_id = f"bitcoin_node_{key}"
    device = device_info(node, key)
    origin = {"name": "Bitcoin Node", "sw_version": APP_VERSION}
    availability = [{"topic": f"{base}/status"}, {"topic": f"{base}/{key}/link"}]
    common = {"device": device, "origin": origin, "availability_mode": "all", "availability": availability}
    messages = []
    for name_key, name, unit, icon, device_class, state_class, diagnostic in sensors:
        entity = {
            "name": name,
            "unique_id": f"{node_id}_{name_key}",
            "state_topic": f"{base}/{key}/state",
            "value_template": f"{{{{ value_json.{name_key} | default('unknown') }}}}",
            "icon": icon,
            **common,
        }
        if unit:
            entity["unit_of_measurement"] = unit
        if device_class:
            entity["device_class"] = device_class
        if state_class:
            entity["state_class"] = state_class
        if diagnostic:
            entity["entity_category"] = "diagnostic"
        messages.append((f"{prefix}/sensor/{node_id}/{name_key}/config", entity))
    for name_key, name, device_class, icon in binaries:
        entity = {
            "name": name,
            "unique_id": f"{node_id}_{name_key}",
            "state_topic": f"{base}/{key}/state",
            "value_template": f"{{{{ 'ON' if value_json.{name_key} else 'OFF' }}}}",
            **common,
        }
        if device_class:
            entity["device_class"] = device_class
        if icon:
            entity["icon"] = icon
        messages.append((f"{prefix}/binary_sensor/{node_id}/{name_key}/config", entity))
    messages.append(
        (
            f"{prefix}/binary_sensor/{node_id}/connected/config",
            {
                "name": "Connected",
                "unique_id": f"{node_id}_connected",
                "device_class": "connectivity",
                "entity_category": "diagnostic",
                "state_topic": f"{base}/{key}/link",
                "payload_on": "online",
                "payload_off": "offline",
                "availability_topic": f"{base}/status",
                "device": device,
                "origin": origin,
            },
        )
    )
    if events_name:
        messages.append(
            (
                f"{prefix}/event/{node_id}/events/config",
                {
                    "name": events_name,
                    "unique_id": f"{node_id}_events",
                    "state_topic": f"{base}/{key}/event",
                    "event_types": list(EVENT_TYPES[key]),
                    "icon": "mdi:bell-ring-outline",
                    "availability_topic": f"{base}/status",
                    "device": device,
                    "origin": origin,
                },
            )
        )
    return messages


def event_payload(event: dict, extra: dict) -> dict:
    """An event as Home Assistant's event entity wants it: event_type plus attributes."""
    payload = {"event_type": event["type"], "text": event["text"]}
    payload.update({k: v for k, v in extra.items() if v not in (None, "")})
    payload["time"] = int(event["ts"])
    return payload


class MqttPublisher:
    """Publishes the node to an MQTT broker."""

    def __init__(self, config: Config, ha_client: HAClient, node: Node):
        self.config = config
        self.ha_client = ha_client
        self.node = node
        self.base = config.mqtt_base_topic
        self.status_topic = f"{self.base}/status"
        self.connected = False
        self.broker = ""
        self.devices = [key for key in DEVICES if getattr(node, key).configured]
        self._client: mqtt.Client | None = None
        self._published: dict[str, str] = {}
        self._link: dict[str, str] = {}
        self._discovered: dict[str, str] = {}
        node.listeners.append(self._on_event)

    async def run(self) -> None:
        """Connect and publish until cancelled. Returns right away when MQTT is off or has no broker."""
        if not self.config.mqtt_enabled or not self.devices:
            return
        host, port = self.config.mqtt_host, self.config.mqtt_port
        user, password = self.config.mqtt_user, self.config.mqtt_password
        if not host and self.ha_client.available:
            service = await self.ha_client.get_mqtt_service()
            if service:
                host, port = service["host"], int(service.get("port") or 1883)
                user, password = service.get("username") or "", service.get("password") or ""
        if not host:
            logger.info(
                "MQTT: not publishing. Install and start the Mosquitto app, or set mqtt.host"
                " (and user/password) in the app configuration, then restart"
            )
            return

        self.broker = f"{host}:{port}"
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"bitcoin-node-{self.base}")
        if user:
            client.username_pw_set(user, password or None)
        client.will_set(self.status_topic, "offline", qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.reconnect_delay_set(min_delay=5, max_delay=300)
        self._client = client
        try:
            client.connect_async(host, port, keepalive=60)
            client.loop_start()
        except (OSError, ValueError) as e:
            logger.error(f"MQTT: cannot connect to {self.broker}: {e}")
            return

        try:
            while True:
                await asyncio.sleep(PUBLISH_INTERVAL)
                if self.connected:
                    for key in self.devices:
                        self._publish(key)
        finally:
            if self.connected:
                client.publish(self.status_topic, "offline", qos=1, retain=True).wait_for_publish(timeout=2)
                self.connected = False
            client.loop_stop()
            client.disconnect()

    # paho calls these from its network thread; they only touch simple attributes
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.error(f"MQTT: broker {self.broker} refused the connection: {reason_code}")
            return
        logger.info(f"MQTT: connected to {self.broker}")
        client.publish(self.status_topic, "online", qos=1, retain=True)
        # Publish everything again: the broker may have lost its retained messages
        self._published, self._link, self._discovered = {}, {}, {}
        self.connected = True

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if self.connected:
            logger.warning(f"MQTT: disconnected from {self.broker}: {reason_code}")
        self.connected = False

    def _on_event(self, event: dict, extra: dict) -> None:
        key = event["source"]
        if self.connected and self._client and self._discovered.get(key) and event["type"] in EVENT_TYPES.get(key, ()):
            self._client.publish(f"{self.base}/{key}/event", json.dumps(event_payload(event, extra)), qos=1)

    def _publish(self, key: str) -> None:
        client = getattr(self.node, key)
        payload = PAYLOADS[key](self.node, time.time())
        if not payload:
            # Until the first answer there is nothing to name the device by
            if self._link.get(key) != "offline":
                self._client.publish(f"{self.base}/{key}/link", "offline", qos=1, retain=True)
                self._link[key] = "offline"
            return
        marker = json.dumps(device_info(self.node, key), sort_keys=True)
        if self._discovered.get(key) != marker:
            for topic, message in discovery_messages(self.node, key, self.config.mqtt_discovery_prefix, self.base):
                self._client.publish(topic, json.dumps(message), qos=1, retain=True)
            self._discovered[key] = marker

        link = "online" if client.connected else "offline"
        if link != self._link.get(key):
            self._client.publish(f"{self.base}/{key}/link", link, qos=1, retain=True)
            self._link[key] = link

        text = json.dumps(payload, sort_keys=True)
        if text != self._published.get(key):
            self._client.publish(f"{self.base}/{key}/state", text, qos=0, retain=True)
            self._published[key] = text
