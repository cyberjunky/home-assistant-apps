"""MQTT publishing with Home Assistant discovery: the sensors, the Mining switch and the Restart button.

Each miner becomes a device in Home Assistant with the entities the Bitaxe integration makes, under the
same names, so with the integration removed they get the same entity ids. Its state goes out as one
retained JSON message on "<base_topic>/<miner>/state", and each entity picks its value from it. The
entities become unavailable while the miner can't be reached, except "Online", which tells you it can't.

The Mining switch and the Restart button are the only way in: their commands come back on
"<base_topic>/<miner>/mining/set" (ON or OFF) and "<base_topic>/<miner>/restart/set".

A "Lottery" device has the numbers for all miners together.
"""

import asyncio
import json
import logging

import paho.mqtt.client as mqtt

from .config import APP_VERSION, Config
from .firmware import repo_for
from .ha_client import HAClient
from .miner import Miner, MinerError, chips
from .odds import format_difficulty
from .stats import miner_odds, totals

logger = logging.getLogger(__name__)

PUBLISH_INTERVAL = 5
DIAG = "diagnostic"
GH = "GH/s"

# key, name, unit, device_class, state_class, icon, entity_category, display precision
SENSORS = (
    ("hashrate", "Hash rate", GH, None, "measurement", "mdi:speedometer", None, 0),
    ("hashrate_1m", "Hash rate (1m avg)", GH, None, "measurement", "mdi:speedometer", None, 0),
    ("hashrate_10m", "Hash rate (10m avg)", GH, None, "measurement", "mdi:speedometer", None, 0),
    ("hashrate_1h", "Hash rate (1h avg)", GH, None, "measurement", "mdi:speedometer", None, 0),
    ("hashrate_1d", "Hash rate (1d avg)", GH, None, "measurement", "mdi:speedometer", None, 0),
    ("expected_hashrate", "Expected hash rate", GH, None, "measurement", "mdi:speedometer-medium", None, 0),
    ("power", "Power", "W", "power", "measurement", None, None, 1),
    ("max_power", "Max power limit", "W", "power", "measurement", None, DIAG, 0),
    ("voltage", "Input voltage", "mV", "voltage", "measurement", None, None, 0),
    ("nominal_voltage", "Nominal voltage", "V", "voltage", "measurement", None, DIAG, 0),
    ("current", "Current", "A", "current", "measurement", None, None, 2),
    ("core_voltage", "Core voltage target", "mV", "voltage", "measurement", None, None, 0),
    ("core_voltage_actual", "Core voltage actual", "mV", "voltage", "measurement", None, None, 0),
    ("temp", "ASIC temperature", "°C", "temperature", "measurement", None, None, 1),
    ("temp2", "ASIC temperature 2", "°C", "temperature", "measurement", None, None, 1),
    ("vr_temp", "VR temperature", "°C", "temperature", "measurement", None, None, 1),
    ("vr_temp_int", "VR internal temperature", "°C", "temperature", "measurement", None, None, 1),
    ("target_temp", "Target temperature", "°C", "temperature", "measurement", None, DIAG, 0),
    ("frequency", "ASIC frequency", "MHz", "frequency", "measurement", None, None, 0),
    ("core_count", "ASIC core count", None, None, "measurement", "mdi:chip", DIAG, 0),
    ("error_rate", "Error rate", "%", None, "measurement", "mdi:alert-circle-outline", None, 2),
    ("efficiency", "Efficiency", "J/TH", None, "measurement", "mdi:lightning-bolt-outline", None, 1),
    ("best_diff", "Best difficulty (all time)", None, None, "total", "mdi:trophy", None, 0),
    ("best_diff_formatted", "Best difficulty (all time) formatted", None, None, None, "mdi:trophy", None, None),
    ("best_session_diff", "Best difficulty (session)", None, None, "measurement", "mdi:trophy-outline", None, 0),
    (
        "best_session_diff_formatted",
        "Best difficulty (session) formatted",
        None,
        None,
        None,
        "mdi:trophy-outline",
        None,
        None,
    ),
    ("pool_diff", "Pool difficulty", None, None, "measurement", "mdi:target", None, 0),
    ("network_diff", "Network difficulty", None, None, "measurement", "mdi:bitcoin", None, 0),
    ("block_height", "Block height", None, None, "total", "mdi:cube-outline", None, 0),
    ("shares_accepted", "Shares accepted", None, None, "total_increasing", "mdi:check-circle-outline", None, 0),
    ("shares_rejected", "Shares rejected", None, None, "total_increasing", "mdi:close-circle-outline", None, 0),
    ("blocks_found", "Blocks found", None, None, "total_increasing", "mdi:cube-send", None, 0),
    ("total_blocks_found", "Total blocks found", None, None, "total_increasing", "mdi:cube-send", None, 0),
    ("fan_speed", "Fan speed", "%", None, "measurement", "mdi:fan", None, 0),
    ("fan_rpm", "Fan RPM", "RPM", None, "measurement", "mdi:fan", None, 0),
    ("fan2_rpm", "Fan 2 RPM", "RPM", None, "measurement", "mdi:fan", None, 0),
    ("fan2_speed", "Fan 2 speed", "%", None, "measurement", "mdi:fan", None, 0),
    ("wifi_rssi", "WiFi signal", "dBm", "signal_strength", "measurement", None, DIAG, 0),
    ("pool_response", "Pool response time", "ms", "duration", "measurement", None, None, 0),
    ("pool_ping", "Pool ping", "ms", "duration", "measurement", None, None, 0),
    ("ping_loss", "Pool ping loss", "%", None, "measurement", "mdi:lan-disconnect", None, 1),
    ("uptime", "Uptime", "s", "duration", "total_increasing", None, DIAG, 0),
    ("free_heap", "Free memory", "B", "data_size", "measurement", None, DIAG, 0),
    ("reset_reason", "Last reset reason", None, None, None, "mdi:restart-alert", DIAG, None),
    ("duplicate_nonces", "Duplicate HW nonces", None, None, "total_increasing", "mdi:content-duplicate", DIAG, 0),
    ("pool", "Pool", None, None, None, "mdi:pool", DIAG, None),
    ("energy", "Energy", "kWh", "energy", "total_increasing", None, None, 3),
    # The lottery, worked out by this app
    ("day_chance", "Block chance today", "%", None, "measurement", "mdi:ticket-percent-outline", None, 6),
    ("years", "Years to a block", "years", None, "measurement", "mdi:timer-sand", None, 0),
    ("best_vs_network", "Best share against network", "%", None, "measurement", "mdi:bullseye-arrow", None, 6),
    ("luck", "Luck", None, None, "measurement", "mdi:clover", None, 2),
)

# key, name, device_class, entity_category
BINARY_SENSORS = (
    ("pool_connected", "Pool connected", "connectivity", None),
    ("using_fallback", "Using fallback pool", None, None),
    ("overheat", "Overheat mode", "problem", None),
    ("auto_fan", "Auto fan speed", None, DIAG),
    ("overclock", "Overclock", None, DIAG),
)

# The Lottery device: key, name, unit, icon, precision
TOTALS = (
    ("hashrate", "Hash rate", GH, "mdi:speedometer", 0),
    ("power", "Power", "W", None, 1),
    ("day_chance", "Block chance today", "%", "mdi:ticket-percent-outline", 6),
    ("year_chance", "Block chance this year", "%", "mdi:ticket-percent", 4),
    ("years", "Years to a block", "years", "mdi:timer-sand", 0),
    ("best", "Best share ever", None, "mdi:trophy", 0),
    ("luck", "Luck", None, "mdi:clover", 2),
    ("mining", "Miners mining", None, "mdi:pickaxe", 0),
    ("network_diff", "Network difficulty", None, "mdi:bitcoin", 0),
    ("block_height", "Block height", None, "mdi:cube-outline", 0),
    ("prize", "Prize", "BTC", "mdi:bitcoin", 4),
)


def _round(value, digits):
    return None if value is None else round(value, digits)


def miner_payload(miner: Miner, difficulty: float | None) -> dict:
    """The miner's state as published. Values that aren't known are left out."""
    v = dict(miner.values)
    for key in ("pool_connected", "using_fallback", "overheat", "auto_fan", "overclock", "mining"):
        if key in v:
            v[key] = "ON" if v[key] else "OFF"
    for key in ("best_diff", "best_session_diff"):
        if v.get(key) is not None:
            v[f"{key}_formatted"] = format_difficulty(v[key])
    for key in ("pool_user", "model", "board", "asic_model", "version", "hostname", "mac"):
        v.pop(key, None)
    v["energy"] = round(miner.energy_kwh, 4)
    odds = miner_odds(miner, difficulty)
    for key, digits in (("day_chance", 10), ("best_vs_network", 10), ("luck", 3)):
        value = odds.get(key)
        if value is not None:
            v[key] = round(value * 100, digits) if key == "day_chance" else round(value, digits)
    if odds.get("years") is not None:
        v["years"] = round(odds["years"])
    for i, chip in enumerate(chips(miner.info or miner.snapshot), 1):
        for field, value in chip.items():
            if value is not None:
                v[f"asic{i}_{field}"] = value
    return {key: value for key, value in v.items() if value is not None}


def totals_payload(miners: list[Miner], network) -> dict:
    status = network.status()
    t = totals(miners, status["difficulty"])
    out = {
        "hashrate": round(t["hashrate"] / 1e9, 1),
        "power": round(t["power"], 1),
        "day_chance": _round(t["day_chance"] and t["day_chance"] * 100, 10),
        "year_chance": _round(t["year_chance"] and t["year_chance"] * 100, 8),
        "years": _round(t["years"], 0),
        "best": t["best"],
        "luck": _round(t["luck"], 3),
        "mining": t["mining"],
        "network_diff": status["difficulty"],
        "block_height": status["height"],
        "prize": _round(status["prize"], 8),
    }
    return {key: value for key, value in out.items() if value is not None}


def _chip_entities(miner: Miner) -> list[tuple]:
    out = []
    for i, chip in enumerate(chips(miner.info or miner.snapshot), 1):
        if "hashrate" in chip:
            out.append(
                (f"asic{i}_hashrate", f"ASIC {i} hash rate", GH, None, "measurement", "mdi:speedometer", None, 0)
            )
        if "error_hashrate" in chip:
            out.append(
                (
                    f"asic{i}_error_hashrate",
                    f"ASIC {i} error hash rate",
                    GH,
                    None,
                    "measurement",
                    "mdi:alert-circle-outline",
                    None,
                    1,
                )
            )
        if "errors" in chip:
            out.append(
                (
                    f"asic{i}_errors",
                    f"ASIC {i} errors",
                    None,
                    None,
                    "total_increasing",
                    "mdi:alert-circle-outline",
                    None,
                    0,
                )
            )
        if "temp" in chip:
            out.append((f"asic{i}_temp", f"ASIC {i} temperature", "°C", "temperature", "measurement", None, None, 1))
    return out


def discovery_messages(miner: Miner, prefix: str, base: str, firmware=None) -> dict[str, dict]:
    """Config topic and payload of every entity of one miner."""
    node = f"lottery_{miner.slug}"
    data = miner.info or miner.snapshot
    v = miner.values
    payload_keys = set(miner_payload(miner, None)) | {"energy", "day_chance", "years", "best_vs_network", "luck"}
    model = " ".join(x for x in (v.get("model") or v.get("board"), v.get("asic_model")) if x) or "AxeOS miner"
    device = {
        "identifiers": [node],
        "name": miner.name,
        "model": model,
        "sw_version": v.get("version") or "",
        "configuration_url": miner.config.url,
        "via_device": "lottery",
    }
    if v.get("mac"):
        device["connections"] = [["mac", v["mac"].lower()]]
    if v.get("model"):
        device["manufacturer"] = "NerdQAxe"
    elif str(v.get("board") or "").isdigit():
        device["manufacturer"] = "Bitaxe"
    origin = {
        "name": "Lottery",
        "sw_version": APP_VERSION,
        "support_url": "https://github.com/cyberjunky/home-assistant-apps",
    }
    state_topic = f"{base}/{miner.slug}/state"
    availability = {
        "availability_mode": "all",
        "availability": [{"topic": f"{base}/status"}, {"topic": f"{base}/{miner.slug}/link"}],
    }
    messages: dict[str, dict] = {}

    for key, name, unit, device_class, state_class, icon, category, precision in (*SENSORS, *_chip_entities(miner)):
        if key not in payload_keys:
            continue
        entity = {
            "name": name,
            "unique_id": f"{node}_{key}",
            "state_topic": state_topic,
            "value_template": f"{{{{ value_json.{key} | default('None') }}}}",
            "device": device,
            "origin": origin,
            **availability,
        }
        for field, value in (
            ("unit_of_measurement", unit),
            ("device_class", device_class),
            ("state_class", state_class),
            ("icon", icon),
            ("entity_category", category),
            ("suggested_display_precision", precision),
        ):
            if value is not None:
                entity[field] = value
        messages[f"{prefix}/sensor/{node}/{key}/config"] = entity

    for key, name, device_class, category in BINARY_SENSORS:
        if key not in v:
            continue
        entity = {
            "name": name,
            "unique_id": f"{node}_{key}",
            "state_topic": state_topic,
            "value_template": f"{{{{ value_json.{key} | default('None') }}}}",
            "device": device,
            "origin": origin,
            **availability,
        }
        if device_class:
            entity["device_class"] = device_class
        if category:
            entity["entity_category"] = category
        messages[f"{prefix}/binary_sensor/{node}/{key}/config"] = entity

    messages[f"{prefix}/binary_sensor/{node}/online/config"] = {
        "name": "Online",
        "unique_id": f"{node}_online",
        "device_class": "connectivity",
        "entity_category": DIAG,
        "state_topic": f"{base}/{miner.slug}/link",
        "payload_on": "online",
        "payload_off": "offline",
        "availability_topic": f"{base}/status",
        "device": device,
        "origin": origin,
    }
    if miner.pause_mode:
        messages[f"{prefix}/switch/{node}/mining/config"] = {
            "name": "Mining",
            "unique_id": f"{node}_mining",
            "icon": "mdi:pickaxe",
            "state_topic": state_topic,
            "value_template": "{{ value_json.mining | default('None') }}",
            "command_topic": f"{base}/{miner.slug}/mining/set",
            "payload_on": "ON",
            "payload_off": "OFF",
            "device": device,
            "origin": origin,
            **availability,
        }
    messages[f"{prefix}/button/{node}/restart/config"] = {
        "name": "Restart",
        "unique_id": f"{node}_restart",
        "device_class": "restart",
        "entity_category": "config",
        "command_topic": f"{base}/{miner.slug}/restart/set",
        "payload_press": "PRESS",
        "device": device,
        "origin": origin,
        **availability,
    }
    if firmware is not None and firmware.enabled and repo_for(data) and v.get("version"):
        messages[f"{prefix}/update/{node}/firmware/config"] = {
            "name": "Firmware",
            "unique_id": f"{node}_firmware",
            "device_class": "firmware",
            "state_topic": f"{base}/{miner.slug}/firmware",
            "device": device,
            "origin": origin,
            **availability,
        }
    return messages


def totals_discovery(prefix: str, base: str) -> dict[str, dict]:
    device = {
        "identifiers": ["lottery"],
        "name": "Lottery",
        "manufacturer": "cyberjunky",
        "model": "Solo mining odds",
        "sw_version": APP_VERSION,
    }
    origin = {"name": "Lottery", "sw_version": APP_VERSION}
    messages = {}
    for key, name, unit, icon, precision in TOTALS:
        entity = {
            "name": name,
            "unique_id": f"lottery_total_{key}",
            "state_topic": f"{base}/total/state",
            "value_template": f"{{{{ value_json.{key} | default('None') }}}}",
            "availability_topic": f"{base}/status",
            "state_class": "measurement",
            "device": device,
            "origin": origin,
            "suggested_display_precision": precision,
        }
        if unit:
            entity["unit_of_measurement"] = unit
        if key == "power":
            entity["device_class"] = "power"
        if icon:
            entity["icon"] = icon
        messages[f"{prefix}/sensor/lottery/{key}/config"] = entity
    return messages


def firmware_payload(miner: Miner, firmware) -> dict:
    installed = miner.values.get("version")
    release = firmware.latest(repo_for(miner.info or miner.snapshot)) if firmware else None
    payload = {"installed_version": installed, "latest_version": release["version"] if release else installed}
    if release:
        payload["release_url"] = release["url"]
        payload["release_summary"] = release["notes"][:250]
        payload["title"] = repo_for(miner.info or miner.snapshot).split("/")[1]
    return payload


class MqttPublisher:
    """Publishes the miners to an MQTT broker, and takes the Mining switch and Restart commands."""

    def __init__(self, config: Config, ha_client: HAClient, miners: list[Miner], network, firmware=None):
        self.config = config
        self.ha_client = ha_client
        self.miners = {miner.slug: miner for miner in miners}
        self.network = network
        self.firmware = firmware
        self.base = config.mqtt_base_topic
        self.prefix = config.mqtt_discovery_prefix
        self.status_topic = f"{self.base}/status"
        self.connected = False
        self.broker = ""
        self.session = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: mqtt.Client | None = None
        self._reset()

    def _reset(self) -> None:
        self._published: dict[str, str] = {}
        # Per miner (and "total"): the config topics published, to clear the ones that go away
        self._discovered: dict[str, dict[str, str]] = {}

    async def run(self, session) -> None:
        """Connect and publish until cancelled. Returns right away when MQTT is off or has no broker."""
        if not self.config.mqtt_enabled:
            return
        self.session = session
        self._loop = asyncio.get_running_loop()
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
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"lottery-{self.base}")
        if user:
            client.username_pw_set(user, password or None)
        client.will_set(self.status_topic, "offline", qos=1, retain=True)
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
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
                    self.publish_all()
        finally:
            if self.connected:
                client.publish(self.status_topic, "offline", qos=1, retain=True).wait_for_publish(timeout=2)
                self.connected = False
            client.loop_stop()
            client.disconnect()

    # paho calls these from its network thread
    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            logger.error(f"MQTT: broker {self.broker} refused the connection: {reason_code}")
            return
        logger.info(f"MQTT: connected to {self.broker}")
        client.publish(self.status_topic, "online", qos=1, retain=True)
        client.subscribe([(f"{self.base}/+/mining/set", 1), (f"{self.base}/+/restart/set", 1)])
        # Publish everything again: the broker may have lost its retained messages
        self._reset()
        self.connected = True

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if self.connected:
            logger.warning(f"MQTT: disconnected from {self.broker}: {reason_code}")
        self.connected = False

    def _on_message(self, client, userdata, message) -> None:
        parts = message.topic[len(self.base) + 1 :].split("/")
        payload = message.payload.decode(errors="replace").strip()
        if len(parts) != 3 or message.retain or self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.command(parts[0], parts[1], payload), self._loop)

    async def command(self, slug: str, kind: str, payload: str) -> None:
        """A press of the Mining switch or the Restart button in Home Assistant."""
        miner = self.miners.get(slug)
        if miner is None or self.session is None:
            return
        try:
            if kind == "mining" and payload in ("ON", "OFF"):
                await miner.set_mining(self.session, payload == "ON")
            elif kind == "restart" and payload == "PRESS":
                await miner.restart(self.session)
        except MinerError as e:
            logger.warning(f"{miner.name}: {kind} {payload} from Home Assistant failed: {e}")

    # --- publishing ----------------------------------------------------
    def _send(self, topic: str, payload: str, retain: bool = True, qos: int = 1) -> None:
        if self._published.get(topic) != payload:
            self._client.publish(topic, payload, qos=qos, retain=retain)
            self._published[topic] = payload

    def _discover(self, key: str, messages: dict[str, dict]) -> None:
        wanted = {topic: json.dumps(payload, sort_keys=True) for topic, payload in messages.items()}
        before = self._discovered.get(key, {})
        for topic in before.keys() - wanted.keys():
            self._client.publish(topic, "", qos=1, retain=True)
        for topic, payload in wanted.items():
            if before.get(topic) != payload:
                self._client.publish(topic, payload, qos=1, retain=True)
        self._discovered[key] = wanted

    def publish_all(self) -> None:
        difficulty = self.network.difficulty()
        self._discover("total", totals_discovery(self.prefix, self.base))
        self._send(
            f"{self.base}/total/state",
            json.dumps(totals_payload(list(self.miners.values()), self.network), sort_keys=True),
            qos=0,
        )
        for miner in self.miners.values():
            if not (miner.info or miner.snapshot):
                # Never seen: what it reports, and so which entities it has, isn't known yet
                self._send(f"{self.base}/{miner.slug}/link", "offline")
                continue
            self._discover(miner.slug, discovery_messages(miner, self.prefix, self.base, self.firmware))
            self._send(f"{self.base}/{miner.slug}/link", "online" if miner.connected else "offline")
            self._send(
                f"{self.base}/{miner.slug}/state", json.dumps(miner_payload(miner, difficulty), sort_keys=True), qos=0
            )
            if self.firmware is not None and self.firmware.enabled and repo_for(miner.info or miner.snapshot):
                self._send(
                    f"{self.base}/{miner.slug}/firmware",
                    json.dumps(firmware_payload(miner, self.firmware), sort_keys=True),
                )
