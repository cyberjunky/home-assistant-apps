"""MQTT publishing with Home Assistant discovery.

Each bot becomes a device in Home Assistant. Its state is published as one retained JSON message on
"<base_topic>/<bot>/state", at most once per PUBLISH_INTERVAL, and the sensors pick their value from
it. They become unavailable while the bot can't be reached, except for "Connected", which is what
tells you it can't.

Each event from the bot's WebSocket (an entry, an exit with its profit, a warning) goes to
"<base_topic>/<bot>/event" and to an event entity, "Trade events", for automations. Nothing is
taken from MQTT: Home Assistant can't change a bot through this app.
"""

import asyncio
import json
import logging

import paho.mqtt.client as mqtt

from .bot import EVENT_TYPES, BotClient
from .config import APP_VERSION, Config
from .ha_client import HAClient

logger = logging.getLogger(__name__)

PUBLISH_INTERVAL = 10

# key, name, unit ("stake" is the bot's stake currency), icon, state_class, diagnostic
SENSORS = (
    ("state", "State", None, "mdi:robot", None, False),
    ("balance", "Balance", "stake", "mdi:wallet", "measurement", False),
    ("profit", "Profit", "stake", "mdi:cash-plus", "measurement", False),
    ("profit_pct", "Profit percentage", "%", "mdi:percent", "measurement", False),
    ("profit_closed", "Closed trades profit", "stake", "mdi:cash-check", "measurement", False),
    ("open_profit", "Open trades profit", "stake", "mdi:cash-clock", "measurement", False),
    ("today_profit", "Profit today", "stake", "mdi:calendar-today", "measurement", False),
    ("open_trades", "Open trades", None, "mdi:swap-horizontal", "measurement", False),
    ("winrate", "Win rate", "%", "mdi:trophy-outline", "measurement", False),
    ("closed_trades", "Closed trades", None, "mdi:counter", "total_increasing", False),
    ("drawdown", "Current drawdown", "%", "mdi:chart-line-variant", "measurement", False),
    ("mode", "Mode", None, "mdi:flask-outline", None, True),
    ("strategy", "Strategy", None, "mdi:strategy", None, True),
    ("version", "Freqtrade version", None, "mdi:information-outline", None, True),
)


def _round(value, digits: int = 8):
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def state_payload(bot: BotClient) -> dict:
    """The bot's state as published. Values that aren't known yet are left out and become "unknown"."""
    summary = bot.summary()
    config, balance, profit = summary["config"], summary["balance"], summary["profit"]
    today = summary["today"] or {}
    values = {
        "state": config.get("state"),
        "balance": _round(balance.get("total_bot")),
        "profit": _round(profit.get("profit_all_coin")),
        "profit_pct": _round((profit.get("profit_all_ratio") or 0) * 100, 2)
        if profit.get("profit_all_ratio") is not None
        else None,
        "profit_closed": _round(profit.get("profit_closed_coin")),
        "open_profit": _round(summary["open_profit"]),
        "today_profit": _round(today.get("abs_profit")),
        "open_trades": (summary["count"] or {}).get("current"),
        "winrate": _round((profit.get("winrate") or 0) * 100, 1) if profit.get("winrate") is not None else None,
        "closed_trades": profit.get("closed_trade_count"),
        "drawdown": _round((profit.get("current_drawdown") or 0) * 100, 2)
        if profit.get("current_drawdown") is not None
        else None,
        "mode": None if config.get("dry_run") is None else ("dry-run" if config["dry_run"] else "live"),
        "strategy": config.get("strategy"),
        "version": config.get("version"),
    }
    return {key: value for key, value in values.items() if value is not None}


def discovery_messages(bot: BotClient, prefix: str, base: str) -> list[tuple[str, dict]]:
    """Topic and payload of every entity of one bot."""
    node = f"freqtrade_{bot.slug}"
    config = bot.data.get("config") or {}
    stake = config.get("stake_currency") or ""
    device = {
        "identifiers": [node],
        "name": f"Freqtrade {bot.name}",
        "manufacturer": "Freqtrade",
        "model": config.get("strategy") or "Trading bot",
        "sw_version": config.get("version") or "",
        "configuration_url": bot.config.url,
    }
    origin = {"name": "Freqtrade app", "sw_version": APP_VERSION}
    availability = [{"topic": f"{base}/status"}, {"topic": f"{base}/{bot.slug}/link"}]
    messages = []
    for key, name, unit, icon, state_class, diagnostic in SENSORS:
        entity = {
            "name": name,
            "unique_id": f"{node}_{key}",
            "state_topic": f"{base}/{bot.slug}/state",
            "value_template": f"{{{{ value_json.{key} | default('unknown') }}}}",
            "device": device,
            "origin": origin,
            "availability_mode": "all",
            "availability": availability,
            "icon": icon,
        }
        if unit:
            entity["unit_of_measurement"] = stake if unit == "stake" else unit
            if unit == "stake" and not stake:
                continue
        if state_class:
            entity["state_class"] = state_class
        if diagnostic:
            entity["entity_category"] = "diagnostic"
        messages.append((f"{prefix}/sensor/{node}/{key}/config", entity))
    messages.append(
        (
            f"{prefix}/binary_sensor/{node}/connected/config",
            {
                "name": "Connected",
                "unique_id": f"{node}_connected",
                "device_class": "connectivity",
                "entity_category": "diagnostic",
                "state_topic": f"{base}/{bot.slug}/link",
                "payload_on": "online",
                "payload_off": "offline",
                "availability_topic": f"{base}/status",
                "device": device,
                "origin": origin,
            },
        )
    )
    messages.append(
        (
            f"{prefix}/event/{node}/events/config",
            {
                "name": "Trade events",
                "unique_id": f"{node}_events",
                "state_topic": f"{base}/{bot.slug}/event",
                "event_types": list(EVENT_TYPES),
                "icon": "mdi:bell-ring-outline",
                "availability_topic": f"{base}/status",
                "device": device,
                "origin": origin,
            },
        )
    )
    return messages


def event_payload(event: dict) -> dict:
    """An event as Home Assistant's event entity wants it: event_type plus attributes."""
    payload = {
        "event_type": event["type"],
        **{k: v for k, v in event.items() if k not in ("type", "ts") and v not in (None, "")},
    }
    payload["time"] = int(event["ts"])
    return payload


class MqttPublisher:
    """Publishes the bots to an MQTT broker."""

    def __init__(self, config: Config, ha_client: HAClient, bots: list[BotClient]):
        self.config = config
        self.ha_client = ha_client
        self.bots = bots
        self.base = config.mqtt_base_topic
        self.status_topic = f"{self.base}/status"
        self.connected = False
        self.broker = ""
        self._client: mqtt.Client | None = None
        self._published: dict[str, str] = {}
        self._link: dict[str, str] = {}
        self._discovered: dict[str, str] = {}
        for bot in bots:
            bot.listeners.append(self._on_event)

    async def run(self) -> None:
        """Connect and publish until cancelled. Returns right away when MQTT is off or has no broker."""
        if not self.config.mqtt_enabled or not self.bots:
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
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"app-freqtrade-{self.base}")
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
                    for bot in self.bots:
                        self._publish(bot)
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

    def _on_event(self, bot: BotClient, event: dict) -> None:
        if self.connected and self._client and self._discovered.get(bot.slug):
            self._client.publish(f"{self.base}/{bot.slug}/event", json.dumps(event_payload(event)), qos=1)

    def _publish(self, bot: BotClient) -> None:
        config = bot.data.get("config") or {}
        if not config:
            # Until the first answer the stake currency (the sensors' unit) isn't known
            if self._link.get(bot.slug) != "offline":
                self._client.publish(f"{self.base}/{bot.slug}/link", "offline", qos=1, retain=True)
                self._link[bot.slug] = "offline"
            return
        key = f"{config.get('stake_currency')}/{config.get('version')}/{config.get('strategy')}"
        if self._discovered.get(bot.slug) != key:
            for topic, payload in discovery_messages(bot, self.config.mqtt_discovery_prefix, self.base):
                self._client.publish(topic, json.dumps(payload), qos=1, retain=True)
            self._discovered[bot.slug] = key

        link = "online" if bot.connected else "offline"
        if link != self._link.get(bot.slug):
            self._client.publish(f"{self.base}/{bot.slug}/link", link, qos=1, retain=True)
            self._link[bot.slug] = link

        payload = json.dumps(state_payload(bot), sort_keys=True)
        if payload != self._published.get(bot.slug):
            self._client.publish(f"{self.base}/{bot.slug}/state", payload, qos=0, retain=True)
            self._published[bot.slug] = payload
