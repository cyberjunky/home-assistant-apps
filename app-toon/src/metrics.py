"""Everything this app reads from a Toon: what each value is called and where it comes from."""

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Metric:
    """One value: its name and how Home Assistant should present it."""

    key: str
    name: str
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = "measurement"
    icon: str | None = None
    diagnostic: bool = False
    # Off by default in Home Assistant: rarely useful, or not reported by every boiler
    enabled: bool = True
    scale: float = 1.0
    # A boiler that doesn't support a counter reports this value instead
    unsupported: float | None = None
    binary: bool = False
    text: bool = False
    # Only for the dashboard's own use: no entity in Home Assistant
    published: bool = True


_C = {"unit": "°C", "device_class": "temperature"}
_KWH = {"unit": "kWh", "device_class": "energy", "state_class": "total_increasing", "scale": 0.001}
_W = {"unit": "W", "device_class": "power"}
_DIAG = {"diagnostic": True}

METRICS: tuple[Metric, ...] = (
    # Thermostat
    Metric("room_temperature", "Room temperature", **_C),
    Metric("setpoint", "Setpoint", **_C),
    Metric("next_setpoint", "Next setpoint", scale=0.01, **_C),
    Metric("program_state", "Program state", state_class=None, icon="mdi:calendar-clock", **_DIAG),
    Metric("active_state", "Active preset", state_class=None, icon="mdi:thermostat", **_DIAG),
    Metric("preheating", "Preheating", state_class=None, binary=True, icon="mdi:radiator"),
    Metric("setpoint_by_load_shifting", "Set by load shifting", state_class=None, binary=True, enabled=False, **_DIAG),
    Metric("preset_comfort", "Preset comfort", state_class=None, **_C, **_DIAG),
    Metric("preset_home", "Preset home", state_class=None, **_C, **_DIAG),
    Metric("preset_sleep", "Preset sleep", state_class=None, **_C, **_DIAG),
    Metric("preset_away", "Preset away", state_class=None, **_C, **_DIAG),
    Metric("heating_factor", "Heating factor", icon="mdi:chart-line", enabled=False, **_DIAG),
    Metric("outside_rate", "Outside rate", icon="mdi:chart-line", enabled=False, **_DIAG),
    Metric("long_term_offset", "Long-term offset", icon="mdi:tune", enabled=False, **_DIAG),
    # Boiler
    Metric("burner_code", "Burner state code", state_class=None, published=False),
    Metric("burner_minutes", "Burner minutes", unit="min", state_class="total_increasing", published=False),
    # What the values of this one mean isn't known; it is kept as the Toon logs it
    Metric("boiler_op_mode", "Boiler operating mode", state_class=None, published=False),
    Metric("boiler_temperature", "Boiler supply temperature", **_C),
    Metric("boiler_return_temperature", "Boiler return temperature", **_C),
    Metric("boiler_setpoint", "Boiler setpoint", **_C),
    Metric("boiler_pressure", "Boiler pressure", unit="bar", device_class="pressure"),
    Metric("modulation", "Modulation", unit="%", icon="mdi:fire"),
    Metric("burner_state", "Burner", state_class=None, text=True, icon="mdi:fire"),
    Metric("burner_hours", "Burner hours", unit="h", state_class="total_increasing", icon="mdi:counter"),
    Metric("dhw_burner_hours", "Hot water burner hours", unit="h", state_class="total_increasing", icon="mdi:counter"),
    Metric("failed_burner_starts", "Failed burner starts", state_class="total_increasing", icon="mdi:fire-alert"),
    Metric(
        "burner_starts",
        "Burner starts",
        state_class="total_increasing",
        icon="mdi:counter",
        enabled=False,
        unsupported=65535,
    ),
    Metric(
        "dhw_burner_starts",
        "Hot water burner starts",
        state_class="total_increasing",
        icon="mdi:counter",
        enabled=False,
        unsupported=65535,
    ),
    Metric(
        "pump_starts", "Pump starts", state_class="total_increasing", icon="mdi:pump", enabled=False, unsupported=65535
    ),
    Metric("oem_fault_code", "OEM fault code", state_class=None, icon="mdi:alert-circle-outline", **_DIAG),
    Metric("slave_state", "Boiler status flags", state_class=None, icon="mdi:flag-outline", **_DIAG),
    Metric("opentherm_error", "OpenTherm error", state_class=None, binary=True, icon="mdi:alert", **_DIAG),
    Metric("boiler_fault", "Boiler fault", device_class="problem", state_class=None, binary=True),
    Metric("boiler_module_connected", "Boiler module connected", state_class=None, binary=True, **_DIAG),
    Metric("dhw_enabled", "Hot water enabled", state_class=None, binary=True, icon="mdi:water-boiler"),
    Metric("dhw_setpoint", "Hot water setpoint", state_class=None, **_C),
    Metric("max_heater_temperature", "Maximum boiler temperature", state_class=None, **_C, **_DIAG),
    # Energy; lt and nt are the low and normal tariff
    Metric("power_import", "Power from grid", **_W),
    Metric("power_export", "Power to grid", **_W),
    Metric("energy_import_low", "Energy from grid low tariff", **_KWH),
    Metric("energy_import_normal", "Energy from grid normal tariff", **_KWH),
    Metric("energy_export_low", "Energy to grid low tariff", **_KWH),
    Metric("energy_export_normal", "Energy to grid normal tariff", **_KWH),
    Metric("energy_house_low", "House energy low tariff", enabled=False, **_KWH),
    Metric("energy_house_normal", "House energy normal tariff", enabled=False, **_KWH),
    Metric("low_tariff", "Low tariff active", state_class=None, binary=True, icon="mdi:cash-clock"),
    Metric(
        "gas_total",
        "Gas",
        unit="m³",
        device_class="gas",
        state_class="total_increasing",
        scale=0.001,
    ),
    Metric(
        "gas_flow",
        "Gas flow",
        unit="m³/h",
        device_class="volume_flow_rate",
        scale=0.001,
        icon="mdi:gas-burner",
    ),
    # Not gas_total: this one counts from another start, so only its changes mean something
    Metric(
        "gas_delivered",
        "Gas meter, 5-minute history",
        unit="m³",
        state_class="total_increasing",
        scale=0.001,
        published=False,
    ),
    # Health of the Toon itself
    Metric("meter_adapter_connected", "Meter adapter connected", state_class=None, binary=True, **_DIAG),
    Metric("meter_adapter_health", "Meter adapter health", icon="mdi:heart-pulse", **_DIAG),
    Metric("wifi_signal", "WiFi signal", unit="%", icon="mdi:wifi", **_DIAG),
    Metric("wifi_rx_dropped", "WiFi packets dropped", state_class="total_increasing", icon="mdi:wifi-alert", **_DIAG),
    Metric("clock_offset", "Toon clock offset", unit="s", device_class="duration", icon="mdi:clock-alert", **_DIAG),
    Metric("uptime", "Toon uptime", unit="h", icon="mdi:timer-outline", **_DIAG),
    Metric("memory_free", "Toon memory free", unit="MB", icon="mdi:memory", **_DIAG),
    Metric("memory_cached", "Toon memory cached", unit="MB", icon="mdi:memory", **_DIAG),
    Metric("memory_gui", "Toon screen software memory", unit="MB", icon="mdi:memory", **_DIAG),
    Metric("memory_web", "Toon web server memory", unit="MB", icon="mdi:memory", **_DIAG),
    Metric("bus_threads", "Toon bus threads", icon="mdi:transit-connection-variant", **_DIAG),
    Metric("bus_fds", "Toon bus open files", icon="mdi:transit-connection-variant", **_DIAG),
    Metric("load", "Toon load", icon="mdi:gauge", **_DIAG),
)
PUBLISHED = tuple(metric for metric in METRICS if metric.published)
METRIC_BY_KEY = {metric.key: metric for metric in METRICS}


@dataclass(frozen=True)
class Binding:
    """Where a metric lives on the bus. The device is matched on its address or its type."""

    device: str
    service: str
    variable: str
    metric: str


_TH = ("thermostatSettings", "ThermostatInfo")
_ADAPTER_GAS = r"HAE_METER_v\d+_1"
_ADAPTER = r"HAE_METER_v\d+"

BINDINGS: tuple[Binding, ...] = (
    *(
        Binding(*_TH, variable, metric)
        for variable, metric in (
            ("CurrentTemperature", "room_temperature"),
            ("CurrentSetpoint", "setpoint"),
            ("NextSetpoint", "next_setpoint"),
            ("ProgramState", "program_state"),
            ("IsPreheating", "preheating"),
            ("PresetComfort", "preset_comfort"),
            ("PresetHome", "preset_home"),
            ("PresetSleep", "preset_sleep"),
            ("PresetAway", "preset_away"),
            ("HeatingFactor", "heating_factor"),
            ("OutsideRate", "outside_rate"),
            ("LongTermOffset", "long_term_offset"),
            ("CurrentBoilerTemperature", "boiler_temperature"),
            ("CurrentBoilerReturnTemperature", "boiler_return_temperature"),
            ("CurrentBoilerSetpoint", "boiler_setpoint"),
            ("BoilerChPressure", "boiler_pressure"),
            ("BoilerBurnerHours", "burner_hours"),
            ("BoilerDhwBurnerHours", "dhw_burner_hours"),
            ("BoilerFailedBurnerStarts", "failed_burner_starts"),
            ("BoilerSuccesfulBurnerStarts", "burner_starts"),
            ("BoilerDhwBurnerStarts", "dhw_burner_starts"),
            ("BoilerPumpStarts", "pump_starts"),
            ("OTBoilerOEMFaultCode", "oem_fault_code"),
            ("OTBoilerSlaveState", "slave_state"),
        )
    ),
    Binding("elec_delivered_nt", "ElectricityFlowMeter", "CurrentElectricityFlow", "power_import"),
    Binding("elec_received_nt", "ElectricityFlowMeter", "CurrentElectricityFlow", "power_export"),
    Binding("elec_delivered_lt", "ElectricityQuantityMeter", "CurrentElectricityQuantity", "energy_import_low"),
    Binding("elec_delivered_nt", "ElectricityQuantityMeter", "CurrentElectricityQuantity", "energy_import_normal"),
    Binding("elec_received_lt", "ElectricityQuantityMeter", "CurrentElectricityQuantity", "energy_export_low"),
    Binding("elec_received_nt", "ElectricityQuantityMeter", "CurrentElectricityQuantity", "energy_export_normal"),
    Binding("elec_house_lt", "ElectricityQuantityMeter", "CurrentElectricityQuantity", "energy_house_low"),
    Binding("elec_house_nt", "ElectricityQuantityMeter", "CurrentElectricityQuantity", "energy_house_normal"),
    Binding("settingsDev", "TariffLowIndicator", "CurrentTariffLow", "low_tariff"),
    Binding("gas", "GasQuantityMeter", "CurrentGasQuantity", "gas_total"),
    # The plain gas device has no flow on a Toon with a meter adapter: it reports NaN, which is ignored
    Binding("gas", "GasFlowMeter", "CurrentGasFlow", "gas_flow"),
    Binding(_ADAPTER_GAS, "GasFlowMeter", "CurrentGasFlow", "gas_flow"),
    Binding(_ADAPTER, "ConnectedState", "IsConnected", "meter_adapter_connected"),
    Binding(_ADAPTER, "NetworkHealth", "HealthValue", "meter_adapter_health"),
)


def device_matches(pattern: str, address: str, device_type: str) -> bool:
    """Whether a binding's device pattern is this device."""
    return bool(re.fullmatch(pattern, address) or re.fullmatch(pattern, device_type))


def convert(metric: Metric, raw) -> float | str | None:
    """The stored value of a raw reading; None for anything that isn't a real value."""
    if raw is None:
        return None
    if metric.text:
        return str(raw)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number == metric.unsupported:
        return None
    return round(number * metric.scale, 6)


# What burnerInfo in the thermostatInfo dataset means
BURNER_STATES = {"0": "off", "1": "heating", "2": "hot_water", "3": "preheating", "4": "error"}
