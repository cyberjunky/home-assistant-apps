"""MQTT publishing with Home Assistant discovery.

The Toon becomes a device in Home Assistant. Its whole state is published as one retained JSON
message on "<base_topic>/<serial>/state", at most once per PUBLISH_INTERVAL, and the entities pick
their value from it. Entities become unavailable while the Toon can't be reached, except for the
"Connected" sensor, which is what tells you it can't.

The thermostat is also a climate entity. Unless the app is read-only, Home Assistant sends its
changes to "<base_topic>/<serial>/set/<setpoint|preset|mode>", and they go through the same
checked commands as the Dashboard's controls (control.py).
"""

import asyncio
import json
import logging

import asyncssh
import paho.mqtt.client as mqtt

from .config import APP_VERSION, Config
from .control import CommandRefused
from .ha_client import HAClient
from .metrics import PUBLISHED
from .monitor import ToonMonitor, ToonNotConnected

logger = logging.getLogger(__name__)

PUBLISH_INTERVAL = 5
# Home Assistant's preset names for the Toon's activeState 0 to 3
CLIMATE_PRESETS = ("comfort", "home", "sleep", "away")
# Mode "auto" follows the week program; "heat" holds the setpoint (the program off)
CLIMATE_MODES = ("auto", "heat")


def climate_change(command: str, payload: str) -> dict:
    """What a climate command from Home Assistant asks the thermostat. Raises ValueError."""
    payload = payload.strip()
    if command == "setpoint":
        return {"setpoint": float(payload)}
    if command == "preset" and payload in CLIMATE_PRESETS:
        return {"preset": CLIMATE_PRESETS.index(payload)}
    if command == "mode" and payload in CLIMATE_MODES:
        return {"program": payload == "auto"}
    raise ValueError(f"unknown climate command {command} {payload!r}")


def climate_config(node: str, base: str, serial: str, device: dict, origin: dict, controls: bool) -> dict:
    """The thermostat as a climate entity, read from the same state message as the sensors."""
    state = f"{base}/{serial}/state"
    presets = "{" + ", ".join(f"{i}: '{name}'" for i, name in enumerate(CLIMATE_PRESETS)) + "}"
    config = {
        "name": "Thermostat",
        "unique_id": f"{node}_thermostat",
        "device": device,
        "origin": origin,
        "availability_mode": "all",
        "availability": [{"topic": f"{base}/status"}, {"topic": f"{base}/{serial}/link"}],
        "current_temperature_topic": state,
        "current_temperature_template": "{{ value_json.room_temperature }}",
        "temperature_state_topic": state,
        "temperature_state_template": "{{ value_json.setpoint }}",
        "mode_state_topic": state,
        "mode_state_template": "{{ 'heat' if (value_json.program_state | int(1)) == 0 else 'auto' }}",
        "modes": list(CLIMATE_MODES),
        "action_topic": state,
        "action_template": "{{ 'heating' if value_json.burner_state in ('heating', 'preheating') else 'idle' }}",
        "preset_mode_state_topic": state,
        "preset_mode_value_template": f"{{{{ {presets}.get(value_json.active_state | int(-1), 'none') }}}}",
        "preset_modes": list(CLIMATE_PRESETS),
        "min_temp": 6,
        "max_temp": 30,
        "temp_step": 0.5,
        "precision": 0.1,
        "temperature_unit": "C",
    }
    if controls:
        config["temperature_command_topic"] = f"{base}/{serial}/set/setpoint"
        config["preset_mode_command_topic"] = f"{base}/{serial}/set/preset"
        config["mode_command_topic"] = f"{base}/{serial}/set/mode"
    return config


def state_payload(monitor: ToonMonitor) -> dict:
    """The state as published. Metrics without a value are left out and become "unknown"."""
    payload = {}
    for metric in PUBLISHED:
        value = monitor.values.get(metric.key)
        if value is not None:
            payload[metric.key] = ("ON" if value else "OFF") if metric.binary else value
    return payload


def discovery_messages(
    serial: str, version: str, prefix: str, base: str, controls: bool = True
) -> list[tuple[str, dict]]:
    """Topic and payload of every entity."""
    node = "toon_" + "".join(c if c.isalnum() else "_" for c in serial).lower()
    state_topic = f"{base}/{serial}/state"
    device = {
        "identifiers": [node],
        "name": "Toon",
        "manufacturer": "Quby",
        "model": "Toon",
        "serial_number": serial,
        "sw_version": version,
    }
    common = {
        "state_topic": state_topic,
        "device": device,
        "origin": {"name": "Toon app", "sw_version": APP_VERSION},
        "availability_mode": "all",
        "availability": [{"topic": f"{base}/status"}, {"topic": f"{base}/{serial}/link"}],
    }
    messages = []
    for metric in PUBLISHED:
        config = {
            **common,
            "name": metric.name,
            "unique_id": f"{node}_{metric.key}",
            "value_template": f"{{{{ value_json.{metric.key} | default('unknown') }}}}",
        }
        if metric.device_class:
            config["device_class"] = metric.device_class
        if metric.unit:
            config["unit_of_measurement"] = metric.unit
        if metric.state_class and not metric.binary:
            config["state_class"] = metric.state_class
        if metric.icon:
            config["icon"] = metric.icon
        if metric.diagnostic:
            config["entity_category"] = "diagnostic"
        if not metric.enabled:
            config["enabled_by_default"] = False
        component = "binary_sensor" if metric.binary else "sensor"
        messages.append((f"{prefix}/{component}/{node}/{metric.key}/config", config))
    messages.append(
        (
            f"{prefix}/binary_sensor/{node}/connected/config",
            {
                "name": "Connected",
                "unique_id": f"{node}_connected",
                "device_class": "connectivity",
                "entity_category": "diagnostic",
                "state_topic": f"{base}/{serial}/link",
                "payload_on": "online",
                "payload_off": "offline",
                "availability_topic": f"{base}/status",
                "device": device,
                "origin": common["origin"],
            },
        )
    )
    messages.append(
        (
            f"{prefix}/climate/{node}/thermostat/config",
            climate_config(node, base, serial, device, common["origin"], controls),
        )
    )
    return messages


class MqttPublisher:
    """Publishes the Toon to an MQTT broker."""

    def __init__(self, config: Config, ha_client: HAClient, monitor: ToonMonitor):
        self.config = config
        self.ha_client = ha_client
        self.monitor = monitor
        self.base = config.mqtt_base_topic
        self.status_topic = f"{self.base}/status"
        self.connected = False
        self.broker = ""
        self._client: mqtt.Client | None = None
        self._published = ""
        self._link = ""
        self._discovered = ""
        self._loop: asyncio.AbstractEventLoop | None = None
        self._commands: set[asyncio.Task] = set()

    async def run(self) -> None:
        """Connect and publish until cancelled. Returns right away when MQTT is off or has no broker."""
        if not self.config.mqtt_enabled:
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

        self._loop = asyncio.get_running_loop()
        self.broker = f"{host}:{port}"
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"app-toon-{self.base}")
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
                    self._publish()
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
        if not self.config.read_only:
            client.subscribe(f"{self.base}/+/set/+", qos=1)
        # Publish everything again: the broker may have lost its retained messages
        self._published = self._link = self._discovered = ""
        self.connected = True

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if self.connected:
            logger.warning(f"MQTT: disconnected from {self.broker}: {reason_code}")
        self.connected = False

    def _on_message(self, client, userdata, message) -> None:
        # A retained command would be carried out again on every reconnect: only take live ones
        if message.retain or self._loop is None:
            return
        parts = message.topic.split("/")
        if len(parts) < 3 or parts[-2] != "set" or parts[-3] != self.monitor.info.get("serialNumber"):
            return
        payload = message.payload.decode("utf-8", "replace")
        self._loop.call_soon_threadsafe(self._start_command, parts[-1], payload)

    def _start_command(self, command: str, payload: str) -> None:
        task = asyncio.create_task(self._command(command, payload), name="climate-command")
        self._commands.add(task)
        task.add_done_callback(self._commands.discard)

    async def _command(self, command: str, payload: str) -> None:
        """Carry out a change from Home Assistant's climate entity. Its result shows in the state."""
        if self.config.read_only:
            return
        try:
            await self.monitor.change(climate_change(command, payload))
        except (ValueError, ToonNotConnected, CommandRefused, OSError, TimeoutError, asyncssh.Error) as e:
            logger.warning(f"Home Assistant's thermostat change ({command} {payload!r}) didn't go through: {e}")
            return
        # Show the result soon, not only after the next interval
        await asyncio.sleep(1)
        if self.connected:
            self._publish()

    def _publish(self) -> None:
        serial = self.monitor.info.get("serialNumber")
        if not serial:
            return
        version = self.monitor.info.get("vers", "")
        if self._discovered != f"{serial}/{version}":
            self._discovered = f"{serial}/{version}"
            messages = discovery_messages(
                serial, version, self.config.mqtt_discovery_prefix, self.base, not self.config.read_only
            )
            for topic, config in messages:
                self._client.publish(topic, json.dumps(config), qos=1, retain=True)

        link = "online" if self.monitor.connected else "offline"
        if link != self._link:
            self._client.publish(f"{self.base}/{serial}/link", link, qos=1, retain=True)
            self._link = link

        payload = json.dumps(state_payload(self.monitor), sort_keys=True)
        if payload != self._published:
            self._client.publish(f"{self.base}/{serial}/state", payload, qos=0, retain=True)
            self._published = payload
