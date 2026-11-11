"""MQTT publishing with Home Assistant discovery.

Each car becomes a device in Home Assistant with sensors, binary sensors and a
device tracker. The car's whole state is published as one retained JSON
message on "<base_topic>/<vin>/state"; the entities pick their value from it.
"""

import asyncio
import json
import logging

import paho.mqtt.client as mqtt

from .config import ADDON_VERSION, Config
from .ha_client import HAClient
from .monitor import CarMonitor, Garage

logger = logging.getLogger(__name__)

# At most one state message per car in this many seconds (the stream updates every second)
PUBLISH_INTERVAL = 3

# key, name, device class, unit, state class, icon, diagnostic
SENSORS = (
    ("state", "State", None, None, None, "mdi:car-connected", False),
    ("soc", "Battery", "battery", "%", "measurement", None, False),
    ("usable_soc", "Usable battery", "battery", "%", "measurement", None, False),
    ("range_km", "Range", "distance", "km", "measurement", "mdi:map-marker-distance", False),
    ("est_range_km", "Estimated range", "distance", "km", "measurement", "mdi:map-marker-distance", False),
    ("odometer_km", "Odometer", "distance", "km", "total_increasing", "mdi:counter", False),
    ("speed_kmh", "Speed", "speed", "km/h", "measurement", None, False),
    ("power_kw", "Power", "power", "kW", "measurement", None, False),
    ("inside_temp", "Inside temperature", "temperature", "°C", "measurement", None, False),
    ("outside_temp", "Outside temperature", "temperature", "°C", "measurement", None, False),
    ("charging_state", "Charging state", None, None, None, "mdi:ev-station", False),
    ("charger_power_kw", "Charger power", "power", "kW", "measurement", None, False),
    ("charge_energy_added_kwh", "Charge energy added", "energy", "kWh", "total_increasing", None, False),
    ("charge_limit_soc", "Charge limit", None, "%", None, "mdi:battery-charging-80", False),
    ("minutes_to_full", "Time to full charge", "duration", "min", None, None, False),
    ("version", "Software version", None, None, None, "mdi:update", True),
    ("tpms_fl", "Tire pressure front left", "pressure", "bar", "measurement", None, True),
    ("tpms_fr", "Tire pressure front right", "pressure", "bar", "measurement", None, True),
    ("tpms_rl", "Tire pressure rear left", "pressure", "bar", "measurement", None, True),
    ("tpms_rr", "Tire pressure rear right", "pressure", "bar", "measurement", None, True),
    ("ideal_range_km", "Ideal range", "distance", "km", "measurement", "mdi:map-marker-distance", True),
    ("target_temp", "Climate set temperature", "temperature", "°C", None, None, True),
    ("passenger_temp", "Climate set temperature passenger", "temperature", "°C", None, None, True),
    ("fan_status", "Fan speed", None, None, "measurement", "mdi:fan", True),
    ("seat_heater_left", "Seat heater driver", None, None, None, "mdi:car-seat-heater", True),
    ("seat_heater_right", "Seat heater passenger", None, None, None, "mdi:car-seat-heater", True),
    ("seat_heater_rear_left", "Seat heater rear left", None, None, None, "mdi:car-seat-heater", True),
    ("seat_heater_rear_center", "Seat heater rear middle", None, None, None, "mdi:car-seat-heater", True),
    ("seat_heater_rear_right", "Seat heater rear right", None, None, None, "mdi:car-seat-heater", True),
    ("climate_keeper", "Keep climate mode", None, None, None, "mdi:dog", True),
    ("cabin_overheat", "Cabin overheat protection", None, None, None, "mdi:thermometer-alert", True),
    ("charger_voltage", "Charger voltage", "voltage", "V", "measurement", None, True),
    ("charger_current", "Charger current", "current", "A", "measurement", None, True),
    ("charger_phases", "Charger phases", None, None, None, "mdi:sine-wave", True),
    ("pilot_current", "Charger offered current", "current", "A", "measurement", None, True),
    ("charge_current_request", "Requested charge current", "current", "A", None, None, True),
    ("cable", "Charge cable", None, None, None, "mdi:ev-plug-type2", True),
    ("charger_type", "Charger type", None, None, None, "mdi:ev-station", True),
    ("charger_brand", "Charger brand", None, None, None, "mdi:ev-station", True),
    ("charge_port_latch", "Charge port latch", None, None, None, "mdi:lock-outline", True),
    ("scheduled_charging_mode", "Scheduled charging", None, None, None, "mdi:calendar-clock", True),
    ("sunroof_open_pct", "Sunroof open", None, "%", "measurement", "mdi:car-select", True),
)
# key, name, device class, icon. "unlocked" is derived: Home Assistant's lock class is on when unlocked.
BINARY_SENSORS = (
    ("unlocked", "Lock", "lock", None),
    ("plugged_in", "Plugged in", "plug", None),
    ("climate_on", "Climate", None, "mdi:fan"),
    ("auto_conditioning", "Auto climate", None, "mdi:fan-auto"),
    ("sentry", "Sentry mode", None, "mdi:shield-car"),
    ("doors_open", "Doors", "door", None),
    ("windows_open", "Windows", "window", None),
    ("trunk_open", "Trunk", "opening", None),
    ("frunk_open", "Frunk", "opening", None),
    ("user_present", "Occupied", "occupancy", None),
    ("update_available", "Software update", "update", None),
    ("preconditioning", "Preconditioning", None, "mdi:car-defrost-front"),
    ("charge_port_open", "Charge port door", "opening", None),
    ("battery_heater_active", "Battery heater", "heat", None),
    ("front_defrost", "Front defrost", None, "mdi:car-defrost-front"),
    ("rear_defrost", "Rear defrost", None, "mdi:car-defrost-rear"),
    ("steering_wheel_heater", "Steering wheel heater", None, "mdi:steering"),
    ("door_driver_front", "Driver door", "door", None),
    ("door_passenger_front", "Passenger door", "door", None),
    ("door_driver_rear", "Rear door driver side", "door", None),
    ("door_passenger_rear", "Rear door passenger side", "door", None),
    ("window_driver_front", "Driver window", "window", None),
    ("window_passenger_front", "Passenger window", "window", None),
    ("window_driver_rear", "Rear window driver side", "window", None),
    ("window_passenger_rear", "Rear window passenger side", "window", None),
    ("tpms_warning_fl", "Tire warning front left", "problem", None),
    ("tpms_warning_fr", "Tire warning front right", "problem", None),
    ("tpms_warning_rl", "Tire warning rear left", "problem", None),
    ("tpms_warning_rr", "Tire warning rear right", "problem", None),
    ("valet_mode", "Valet mode", None, "mdi:account-tie"),
    ("speed_limit_active", "Speed limit mode", None, "mdi:speedometer-slow"),
)

# Binary sensors that not every car reports: unknown in Home Assistant instead of off
MAYBE_UNKNOWN = ("auto_conditioning",)


def state_payload(view: dict) -> dict:
    """The car's state as published: the monitor's view, flattened for templates."""
    payload = {key: view.get(key) for key, *_ in SENSORS if not key.startswith("tpms_")}
    for wheel, pressure in (view.get("tpms") or {}).items():
        payload[f"tpms_{wheel}"] = pressure
    for key, *_ in BINARY_SENSORS:
        payload[key] = bool(view.get(key))
    for key in MAYBE_UNKNOWN:
        payload[key] = None if view.get(key) is None else bool(view.get(key))
    payload["unlocked"] = view.get("locked") is False
    payload["battery_heater_active"] = bool(view.get("battery_heater") or view.get("battery_heater_on"))
    payload["update_available"] = view.get("update_status") in ("available", "scheduled", "downloading")
    # No timestamps: the message is only sent again when something about the car changed
    payload.update({key: view.get(key) for key in ("lat", "lon", "heading", "elevation_m", "name")})
    return payload


class MqttPublisher:
    """Publishes the cars to an MQTT broker."""

    def __init__(self, config: Config, ha_client: HAClient, garage: Garage):
        self.config = config
        self.ha_client = ha_client
        self.garage = garage
        self.base = config.mqtt_base_topic
        self.status_topic = f"{self.base}/status"
        self.connected = False
        self.broker = ""
        self._client: mqtt.Client | None = None
        self._dirty: set[int] = set()
        self._published: dict[int, str] = {}
        self._discovered: dict[int, tuple] = {}

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
                "MQTT: not publishing. Install and start the Mosquitto add-on, or set mqtt.host"
                " (and user/password) in the add-on configuration, then restart"
            )
            return

        self.broker = f"{host}:{port}"
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"tesla-{self.base}")
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

        self.garage.listeners.append(lambda monitor: self._dirty.add(monitor.car_id))
        self.garage.removal_listeners.append(self._forget)
        self._dirty.update(self.garage.monitors)
        try:
            while True:
                await asyncio.sleep(PUBLISH_INTERVAL)
                if self.connected:
                    self._publish_dirty()
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
        self._discovered.clear()
        self._published.clear()
        self._dirty.update(self.garage.monitors)
        self.connected = True

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if self.connected:
            logger.warning(f"MQTT: disconnected from {self.broker}: {reason_code}")
        self.connected = False

    def _forget(self, car_id: int, vin: str) -> None:
        """A removed car: empty retained messages make its entities disappear from Home Assistant."""
        self._dirty.discard(car_id)
        self._published.pop(car_id, None)
        self._discovered.pop(car_id, None)
        if not vin or not self.connected:
            return
        node = f"tesla_{vin.lower()}"
        prefix = self.config.mqtt_discovery_prefix
        topics = [f"{self.base}/{vin}/state", f"{prefix}/device_tracker/{node}/config"]
        topics += [f"{prefix}/sensor/{node}/{key}/config" for key, *_ in SENSORS]
        topics += [f"{prefix}/binary_sensor/{node}/{key}/config" for key, *_ in BINARY_SENSORS]
        for topic in topics:
            self._client.publish(topic, "", qos=1, retain=True)

    def _publish_dirty(self) -> None:
        dirty, self._dirty = self._dirty, set()
        for car_id in dirty:
            monitor = self.garage.monitors.get(car_id)
            if not monitor or not monitor.vin:
                continue
            view = monitor.view()
            self._discover(monitor, view)
            payload = json.dumps(state_payload(view), sort_keys=True)
            if payload != self._published.get(car_id):
                self._client.publish(f"{self.base}/{monitor.vin}/state", payload, qos=0, retain=True)
                self._published[car_id] = payload

    def _discover(self, monitor: CarMonitor, view: dict) -> None:
        """Announce the car's entities to Home Assistant (again when its name or software changes)."""
        signature = (view.get("name"), view.get("model"), view.get("version"))
        if self._discovered.get(monitor.car_id) == signature:
            return
        self._discovered[monitor.car_id] = signature

        vin = monitor.vin
        node = f"tesla_{vin.lower()}"
        state_topic = f"{self.base}/{vin}/state"
        device = {
            "identifiers": [node],
            "name": view.get("name") or f"Tesla {vin[-6:]}",
            "manufacturer": "Tesla",
            "model": view.get("model") or "Vehicle",
            "serial_number": vin,
            "sw_version": view.get("version") or "",
        }
        common = {
            "state_topic": state_topic,
            "availability_topic": self.status_topic,
            "device": device,
            "origin": {"name": "Tesla app (unofficial)", "sw_version": ADDON_VERSION},
        }
        prefix = self.config.mqtt_discovery_prefix

        for key, name, device_class, unit, state_class, icon, diagnostic in SENSORS:
            config = {
                **common,
                "name": name,
                "unique_id": f"{node}_{key}",
                "value_template": f"{{{{ value_json.{key} }}}}",
            }
            if device_class:
                config["device_class"] = device_class
            if unit:
                config["unit_of_measurement"] = unit
            if state_class:
                config["state_class"] = state_class
            if icon:
                config["icon"] = icon
            if diagnostic:
                config["entity_category"] = "diagnostic"
            self._client.publish(f"{prefix}/sensor/{node}/{key}/config", json.dumps(config), qos=1, retain=True)

        for key, name, device_class, icon in BINARY_SENSORS:
            config = {
                **common,
                "name": name,
                "unique_id": f"{node}_{key}",
                "value_template": (
                    f"{{{{ 'None' if value_json.{key} is none else ('ON' if value_json.{key} else 'OFF') }}}}"
                    if key in MAYBE_UNKNOWN
                    else f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}"
                ),
            }
            if device_class:
                config["device_class"] = device_class
            if icon:
                config["icon"] = icon
            self._client.publish(f"{prefix}/binary_sensor/{node}/{key}/config", json.dumps(config), qos=1, retain=True)

        # No state topic: Home Assistant works out home / not_home / zone from the coordinates
        tracker = {
            "availability_topic": self.status_topic,
            "device": device,
            "origin": common["origin"],
            "name": "Location",
            "unique_id": f"{node}_location",
            "icon": "mdi:car",
            "source_type": "gps",
            "json_attributes_topic": state_topic,
            "json_attributes_template": (
                "{{ {'latitude': value_json.lat, 'longitude': value_json.lon, 'gps_accuracy': 10,"
                " 'heading': value_json.heading} | tojson }}"
            ),
        }
        self._client.publish(f"{prefix}/device_tracker/{node}/config", json.dumps(tracker), qos=1, retain=True)
