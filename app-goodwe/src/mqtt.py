"""MQTT publishing with Home Assistant discovery.

The inverter becomes one device with sensors, binary sensors and an event entity. The
state is one retained JSON message on "<base_topic>/state" (sent when something changed,
so about once per poll); the entities pick their value from it. Events go out on
"<base_topic>/event" as they happen, and the event entity turns them into Home Assistant
events for automations (a Telegram message on a hold, a violation, a surplus...).
"""

import asyncio
import json
import logging

import paho.mqtt.client as mqtt

from .config import ADDON_VERSION, MAX_MINERS, Config
from .controller import BatteryController
from .events import EVENT_TYPES, EventBus
from .forecast import Forecast
from .ha_client import HAClient
from .inverter import InverterLink

logger = logging.getLogger(__name__)

PUBLISH_INTERVAL = 2

# key, name, device class, unit, state class, icon, diagnostic
SENSORS = (
    ("pv_power", "Solar power", "power", "W", "measurement", "mdi:solar-power", False),
    ("pv1_power", "PV1 power", "power", "W", "measurement", "mdi:solar-panel", True),
    ("pv2_power", "PV2 power", "power", "W", "measurement", "mdi:solar-panel", True),
    ("pv_today", "Solar today", "energy", "kWh", "total_increasing", None, False),
    ("pv_total", "Solar total", "energy", "kWh", "total_increasing", None, False),
    ("grid_power", "Grid power", "power", "W", "measurement", "mdi:transmission-tower", False),
    ("grid_import_today", "Grid import today", "energy", "kWh", "total_increasing", None, False),
    ("grid_export_today", "Grid export today", "energy", "kWh", "total_increasing", None, False),
    ("grid_import_total", "Grid import total", "energy", "kWh", "total_increasing", None, True),
    ("grid_export_total", "Grid export total", "energy", "kWh", "total_increasing", None, True),
    ("grid_voltage", "Grid voltage", "voltage", "V", "measurement", None, True),
    ("grid_frequency", "Grid frequency", "frequency", "Hz", "measurement", None, True),
    ("battery_power", "Battery power", "power", "W", "measurement", "mdi:battery-charging", False),
    ("battery_soc", "Battery", "battery", "%", "measurement", None, False),
    ("battery_soh", "Battery health", None, "%", "measurement", "mdi:battery-heart-variant", True),
    ("battery_voltage", "Battery voltage", "voltage", "V", "measurement", None, True),
    ("battery_current", "Battery current", "current", "A", "measurement", None, True),
    ("battery_temperature", "Battery temperature", "temperature", "°C", "measurement", None, True),
    ("battery_charged_today", "Battery charged today", "energy", "kWh", "total_increasing", None, False),
    ("battery_discharged_today", "Battery discharged today", "energy", "kWh", "total_increasing", None, False),
    ("battery_charged_total", "Battery charged total", "energy", "kWh", "total_increasing", None, True),
    ("battery_discharged_total", "Battery discharged total", "energy", "kWh", "total_increasing", None, True),
    ("battery_cycles", "Battery cycles", None, "cycles", "total_increasing", "mdi:battery-sync-outline", False),
    ("battery_capacity", "Battery capacity", "energy_storage", "kWh", None, "mdi:battery-high", True),
    ("battery_mode", "Battery mode", None, None, None, "mdi:battery-sync", True),
    ("load_power", "Home power", "power", "W", "measurement", "mdi:home-lightning-bolt", False),
    ("load_today", "Home today", "energy", "kWh", "total_increasing", None, False),
    ("backup_power", "Backup power", "power", "W", "measurement", "mdi:power-plug", True),
    ("inverter_temperature", "Inverter temperature", "temperature", "°C", "measurement", None, True),
    ("work_mode", "Work mode", None, None, None, "mdi:cog", False),
    ("energy_mode", "Energy mode", None, None, None, "mdi:state-machine", True),
    ("grid_mode", "Grid mode", None, None, None, "mdi:transmission-tower", True),
    ("error_codes", "Error codes", None, None, None, "mdi:alert-circle-outline", True),
    ("run_hours", "Run hours", "duration", "h", "total_increasing", None, True),
    ("battery_hold", "Battery hold", None, None, None, "mdi:shield-half-full", False),
    ("dod", "Depth of discharge", None, "%", None, "mdi:battery-arrow-down", True),
    ("hold_decision", "Auto-hold decision", None, None, None, "mdi:brain", True),
    ("surplus_power", "Solar surplus", "power", "W", "measurement", "mdi:white-balance-sunny", False),
    ("surplus_today", "Solar surplus today", "duration", "h", "total_increasing", "mdi:timer-sand", False),
    ("surplus_energy_today", "Solar used during surplus today", "energy", "kWh", "total_increasing", None, False),
    ("curtailed_power", "Curtailed power (estimate)", "power", "W", "measurement", "mdi:solar-panel", True),
    ("curtailed_today", "Curtailed today (estimate)", "energy", "kWh", "total_increasing", None, False),
    ("forecast_today", "Forecast today", "energy", "kWh", None, "mdi:weather-sunny", False),
    ("forecast_tomorrow", "Forecast tomorrow", "energy", "kWh", None, "mdi:weather-sunny", False),
    ("forecast_hour", "Forecast this hour", "energy", "kWh", None, "mdi:weather-sunny", True),
    # Everything else the inverter reports
    ("pv1_voltage", "PV1 voltage", "voltage", "V", "measurement", None, True),
    ("pv1_current", "PV1 current", "current", "A", "measurement", None, True),
    ("pv1_mode", "PV1 mode", None, None, None, "mdi:solar-panel", True),
    ("pv2_voltage", "PV2 voltage", "voltage", "V", "measurement", None, True),
    ("pv2_current", "PV2 current", "current", "A", "measurement", None, True),
    ("pv2_mode", "PV2 mode", None, None, None, "mdi:solar-panel", True),
    ("grid_current", "Grid current", "current", "A", "measurement", None, True),
    ("grid_power_factor", "Meter power factor", "power_factor", None, "measurement", None, True),
    ("grid_status", "Grid status", None, None, None, "mdi:transmission-tower", True),
    ("meter_status", "Meter status", None, None, None, "mdi:meter-electric", True),
    ("backup_voltage", "Backup voltage", "voltage", "V", "measurement", None, True),
    ("backup_current", "Backup current", "current", "A", "measurement", None, True),
    ("backup_frequency", "Backup frequency", "frequency", "Hz", "measurement", None, True),
    ("backup_load_power", "Backup load power", "power", "W", "measurement", "mdi:power-plug", True),
    ("backup_mode", "Backup mode", None, None, None, "mdi:power-plug", True),
    ("inverter_power", "Inverter output", "power", "W", "measurement", "mdi:flash", True),
    ("inverter_total_power", "Inverter total power", "power", "W", "measurement", "mdi:flash", True),
    ("load_total", "Home total", "energy", "kWh", "total_increasing", None, True),
    ("battery_status", "Battery status", None, None, None, "mdi:battery-unknown", True),
    ("battery_charge_limit", "Battery charge limit", "current", "A", "measurement", None, True),
    ("battery_discharge_limit", "Battery discharge limit", "current", "A", "measurement", None, True),
    ("battery_warning", "Battery warning", None, None, None, "mdi:battery-alert", True),
    ("battery_error", "Battery error", None, None, None, "mdi:battery-alert", True),
    ("effective_work_mode", "Effective work mode", None, None, None, "mdi:cog", True),
    ("relay_control", "Relay control", None, None, None, "mdi:electric-switch", True),
    ("diagnose", "Diagnosis", None, None, None, "mdi:stethoscope", True),
    # Settings (read every hour)
    ("export_limit", "Export limit", "power", "W", None, "mdi:transmission-tower-off", True),
    ("soc_protection", "SoC protection", None, "%", None, "mdi:battery-lock", True),
    ("backup_supply", "Backup supply", None, None, None, "mdi:power-plug-battery", True),
    ("charge_voltage", "Charge voltage", "voltage", "V", None, None, True),
    ("charge_current", "Charge current", "current", "A", None, None, True),
    ("discharge_voltage", "Discharge voltage", "voltage", "V", None, None, True),
    ("discharge_current", "Discharge current", "current", "A", None, None, True),
    ("eco_slot_1", "Eco schedule 1", None, None, None, "mdi:calendar-clock", True),
    ("eco_slot_2", "Eco schedule 2", None, None, None, "mdi:calendar-clock", True),
    ("eco_slot_3", "Eco schedule 3", None, None, None, "mdi:calendar-clock", True),
    ("eco_slot_4", "Eco schedule 4", None, None, None, "mdi:calendar-clock", True),
)
# Modes, schedules and settings that only matter outside General mode or with a backup load:
# announced disabled, to switch on in Home Assistant when wanted
OPTIONAL = frozenset(
    (
        "pv1_mode",
        "pv2_mode",
        "meter_status",
        "backup_voltage",
        "backup_current",
        "backup_frequency",
        "backup_load_power",
        "backup_mode",
        "effective_work_mode",
        "relay_control",
        "backup_supply",
        "eco_slot_1",
        "eco_slot_2",
        "eco_slot_3",
        "eco_slot_4",
    )
)
# key, name, device class, icon
BINARY_SENSORS = (
    ("inverter_online", "Inverter connected", "connectivity", None),
    ("solar_surplus", "Solar surplus", None, "mdi:solar-power-variant"),
    ("battery_held", "Battery held", None, "mdi:shield-half-full"),
    ("auto_hold", "Auto-hold", None, "mdi:robot"),
    ("work_mode_problem", "Work mode not General", "problem", None),
    ("zero_export", "Zero export", None, "mdi:transmission-tower-off"),
    ("exporting", "Exporting", None, "mdi:transmission-tower-export"),
    ("fault", "Fault", "problem", None),
)
# The miners (surplus loads): only announced when configured, removed again when not
MINER_SENSORS = (
    ("miners_power", "Miners power", "power", "W", "measurement", "mdi:bitcoin", False),
    ("miners_today", "Miners today", "duration", "h", "total_increasing", "mdi:timer-outline", False),
    ("miners_energy_today", "Miners energy today", "energy", "kWh", "total_increasing", "mdi:bitcoin", False),
)
MINER_BINARY_SENSORS = (("miners_running", "Miners running", "running", "mdi:bitcoin"),)
# Entities of earlier versions, removed from Home Assistant on discovery: (component, key)
REMOVED = (("binary_sensor", "charge_cap_active"),)


def state_payload(data: dict, control: dict, inverter: dict, forecast: dict, settings: dict | None = None) -> dict:
    """The inverter, the controller and the forecast as one flat message for the entities."""
    settings = settings or {}

    def text(key, source=None):
        value = (data if source is None else source).get(key)
        return None if value is None or value == "" else str(value)

    def setting(key, digits=1):
        try:
            return round(float(settings[key]), digits)
        except (KeyError, TypeError, ValueError):
            return None

    def eco(n):
        slot = text(f"eco_mode_{n}", settings)
        switch = settings.get(f"eco_mode_{n}_switch")
        if slot is not None and switch is not None and not int(switch or 0):
            slot += " (group off)"
        return slot

    on_off = {0: "off", 1: "on"}

    def num(key, digits=2):
        value = data.get(key)
        try:
            return round(float(value), digits) if value is not None else None
        except (TypeError, ValueError):
            return None

    hold = None
    if control.get("auto_hold"):
        hold = "held" if control.get("held") else "discharging" if control.get("held") is False else "deciding"
    elif control.get("dod_reported") is not None:
        hold = "held" if int(control["dod_reported"]) == 0 else "discharging"
    fault = str(data.get("error_codes") or "0") not in ("0", "", "None")
    capacity = float(control.get("battery_capacity_kwh") or 0)
    miners = control.get("miners") or {}
    return {
        "pv_power": num("ppv", 0),
        "pv1_power": num("ppv1", 0),
        "pv2_power": num("ppv2", 0),
        "pv_today": num("e_day"),
        "pv_total": num("e_total", 1),
        "grid_power": num("pgrid", 0),
        "grid_import_today": num("e_day_imp"),
        "grid_export_today": num("e_day_exp"),
        "grid_import_total": num("e_total_imp", 1),
        "grid_export_total": num("e_total_exp", 1),
        "grid_voltage": num("vgrid", 1),
        "grid_frequency": num("fgrid"),
        "battery_power": num("pbattery1", 0),
        "battery_soc": num("battery_soc", 0),
        "battery_soh": num("battery_soh", 0),
        "battery_voltage": num("vbattery1", 1),
        "battery_current": num("ibattery1", 1),
        "battery_temperature": num("battery_temperature", 1),
        "battery_charged_today": num("e_bat_charge_day"),
        "battery_discharged_today": num("e_bat_discharge_day"),
        "battery_charged_total": num("e_bat_charge_total", 1),
        "battery_discharged_total": num("e_bat_discharge_total", 1),
        "battery_cycles": round(float(data.get("e_bat_discharge_total") or 0) / capacity, 1) if capacity else None,
        "battery_capacity": capacity or None,
        "battery_mode": data.get("battery_mode_label"),
        "load_power": num("load_ptotal", 0),
        "load_today": num("e_load_day"),
        "backup_power": num("backup_ptotal", 0),
        "inverter_temperature": num("temperature", 1),
        "work_mode": control.get("work_mode_name"),
        "energy_mode": data.get("work_mode_label"),
        "grid_mode": data.get("grid_in_out_label"),
        "error_codes": str(data.get("error_codes") if data.get("error_codes") is not None else ""),
        "run_hours": num("h_total", 0),
        "battery_hold": hold,
        "dod": control.get("dod_reported"),
        "hold_decision": (control.get("decision") or {}).get("text"),
        "surplus_power": control.get("surplus_w"),
        "surplus_today": num("surplus_hours"),
        "surplus_energy_today": num("e_surplus_day"),
        "curtailed_power": control.get("curtailed_w"),
        "curtailed_today": num("e_curtailed_day"),
        "forecast_today": forecast.get("today_kwh"),
        "forecast_tomorrow": forecast.get("tomorrow_kwh"),
        "forecast_hour": forecast.get("hour_kwh"),
        "inverter_online": bool(inverter.get("online")),
        "solar_surplus": bool(control.get("surplus")),
        "battery_held": hold == "held",
        "auto_hold": bool(control.get("auto_hold")),
        "work_mode_problem": control.get("work_mode_general") is False,
        "zero_export": bool(control.get("zero_export")),
        "exporting": (num("pgrid", 0) or 0) < -30,
        "fault": fault,
        "miners_power": miners.get("watts"),
        "miners_today": num("miners_hours"),
        "miners_energy_today": num("e_miners_day"),
        "miners_running": bool(miners.get("on")),
        # Each miner: its power and whether it runs ("miner1_power", "miner1_running", ...)
        **{
            f"miner{m['index']}_{key}": value
            for m in miners.get("miners") or []
            for key, value in (("power", m.get("watts")), ("running", bool(m.get("on"))))
        },
        "pv1_voltage": num("vpv1", 1),
        "pv1_current": num("ipv1", 1),
        "pv1_mode": text("pv1_mode_label"),
        "pv2_voltage": num("vpv2", 1),
        "pv2_current": num("ipv2", 1),
        "pv2_mode": text("pv2_mode_label"),
        "grid_current": num("igrid", 1),
        "grid_power_factor": num("meter_power_factor", 3),
        "grid_status": text("grid_mode_label"),
        "meter_status": text("meter_status"),
        "backup_voltage": num("vload", 1),
        "backup_current": num("iload", 1),
        "backup_frequency": num("fload"),
        "backup_load_power": num("pload", 0),
        "backup_mode": text("load_mode_label"),
        "inverter_power": num("plant_power", 0),
        "inverter_total_power": num("total_power", 0),
        "load_total": num("e_load_total", 1),
        "battery_status": text("battery_status"),
        "battery_charge_limit": num("battery_charge_limit", 0),
        "battery_discharge_limit": num("battery_discharge_limit", 0),
        "battery_warning": text("battery_warning"),
        "battery_error": text("battery_error"),
        "effective_work_mode": text("effective_work_mode"),
        "relay_control": text("effective_relay_control"),
        "diagnose": text("diagnose_result_label") or "none",
        "export_limit": setting("grid_export_limit", 0),
        "soc_protection": setting("battery_soc_protection", 0),
        "backup_supply": on_off.get(int(settings["backup_supply"]))
        if settings.get("backup_supply") is not None
        else None,
        "charge_voltage": setting("charge_v"),
        "charge_current": setting("charge_i", 0),
        "discharge_voltage": setting("discharge_v"),
        "discharge_current": setting("discharge_i", 0),
        "eco_slot_1": eco(1),
        "eco_slot_2": eco(2),
        "eco_slot_3": eco(3),
        "eco_slot_4": eco(4),
    }


class MqttPublisher:
    """Publishes the inverter to an MQTT broker."""

    def __init__(
        self,
        config: Config,
        ha_client: HAClient,
        link: InverterLink,
        controller: BatteryController,
        forecast: Forecast,
        events: EventBus,
    ):
        self.config = config
        self.ha_client = ha_client
        self.link = link
        self.controller = controller
        self.forecast = forecast
        self.events = events
        self.base = config.mqtt_base_topic
        self.status_topic = f"{self.base}/status"
        self.state_topic = f"{self.base}/state"
        self.event_topic = f"{self.base}/event"
        self.connected = False
        self.broker = ""
        self._client: mqtt.Client | None = None
        self._published = ""
        self._discovered = None
        self._pending_events: list[dict] = []
        events.listeners.append(self._on_event)

    def view(self) -> dict:
        return {
            "enabled": bool(self.broker),
            "connected": self.connected,
            "broker": self.broker,
            "state_topic": self.state_topic,
            "event_topic": self.event_topic,
            "status_topic": self.status_topic,
        }

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
                "MQTT: not publishing. Install and start the Mosquitto broker app, or set mqtt.host"
                " (and user/password) in the app configuration, then restart"
            )
            return

        self.broker = f"{host}:{port}"
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"goodwe-{self.base}")
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
        # Publish everything again: the broker may have lost its retained messages
        self._discovered = None
        self._published = ""
        self.connected = True

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        if self.connected:
            logger.warning(f"MQTT: disconnected from {self.broker}: {reason_code}")
        self.connected = False

    def _on_event(self, event: dict) -> None:
        self._pending_events.append(event)

    def _publish(self) -> None:
        inverter = self.link.view()
        if not inverter.get("serial"):
            return
        self._discover(inverter)
        payload = json.dumps(
            state_payload(
                self.link.data, self.controller.state(), inverter, self.forecast.summary(), self.link.settings
            ),
            sort_keys=True,
            default=str,
        )
        if payload != self._published:
            self._client.publish(self.state_topic, payload, qos=0, retain=True)
            self._published = payload
        pending, self._pending_events = self._pending_events, []
        for event in pending:
            body = {
                "event_type": event["type"],
                "message": event["message"],
                "level": event["level"],
                "ts": event["ts"],
            }
            body.update({k: v for k, v in (event.get("data") or {}).items() if k not in body})
            self._client.publish(self.event_topic, json.dumps(body, default=str), qos=1, retain=False)

    def _discover(self, inverter: dict) -> None:
        """Announce the entities to Home Assistant (again when the model or firmware changes)."""
        signature = (inverter.get("model"), inverter.get("serial"), inverter.get("firmware"))
        if self._discovered == signature:
            return
        self._discovered = signature
        serial = str(inverter["serial"])
        node = f"goodwe_{serial.lower()}"
        device = {
            "identifiers": [node],
            "name": f"GoodWe {inverter.get('model') or 'inverter'}",
            "manufacturer": "GoodWe",
            "model": inverter.get("model") or "Inverter",
            "serial_number": serial,
            "sw_version": str(inverter.get("firmware") or ""),
        }
        common = {
            "state_topic": self.state_topic,
            "availability_topic": self.status_topic,
            "device": device,
            "origin": {"name": "GoodWe", "sw_version": ADDON_VERSION},
        }
        prefix = self.config.mqtt_discovery_prefix
        units = self.config.miners
        miners = bool(units)
        sensors = (
            SENSORS
            + (MINER_SENSORS if miners else ())
            + tuple(
                (f"miner{i}_power", f"{m.name} power", "power", "W", "measurement", "mdi:bitcoin", False)
                for i, m in enumerate(units, 1)
            )
        )
        binary_sensors = (
            BINARY_SENSORS
            + (MINER_BINARY_SENSORS if miners else ())
            + tuple(
                (f"miner{i}_running", f"{m.name} running", "running", "mdi:bitcoin") for i, m in enumerate(units, 1)
            )
        )
        removed = (
            REMOVED
            + (
                ()
                if miners
                else tuple(("sensor", s[0]) for s in MINER_SENSORS)
                + tuple(("binary_sensor", s[0]) for s in MINER_BINARY_SENSORS)
            )
            # The miners no longer in the configuration
            + tuple(
                (component, f"miner{i}_{key}")
                for i in range(len(units) + 1, MAX_MINERS + 1)
                for component, key in (("sensor", "power"), ("binary_sensor", "running"))
            )
        )
        for key, name, device_class, unit, state_class, icon, diagnostic in sensors:
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
            if key in OPTIONAL:
                config["enabled_by_default"] = False
            self._client.publish(f"{prefix}/sensor/{node}/{key}/config", json.dumps(config), qos=1, retain=True)
        for key, name, device_class, icon in binary_sensors:
            config = {
                **common,
                "name": name,
                "unique_id": f"{node}_{key}",
                "value_template": f"{{{{ 'ON' if value_json.{key} else 'OFF' }}}}",
            }
            if device_class:
                config["device_class"] = device_class
            if icon:
                config["icon"] = icon
            self._client.publish(f"{prefix}/binary_sensor/{node}/{key}/config", json.dumps(config), qos=1, retain=True)
        for component, key in removed:
            # An empty retained config removes the entity from Home Assistant
            self._client.publish(f"{prefix}/{component}/{node}/{key}/config", "", qos=1, retain=True)
        event = {
            "state_topic": self.event_topic,
            "availability_topic": self.status_topic,
            "device": device,
            "origin": common["origin"],
            "name": "Events",
            "unique_id": f"{node}_events",
            "icon": "mdi:bell-ring-outline",
            "event_types": sorted(EVENT_TYPES),
        }
        self._client.publish(f"{prefix}/event/{node}/events/config", json.dumps(event), qos=1, retain=True)
        logger.info(
            f"MQTT: announced {len(sensors)} sensors, {len(binary_sensors)} binary sensors and the event entity"
        )
