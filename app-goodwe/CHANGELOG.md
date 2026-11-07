# Changelog

## 0.1.1

**After updating:** nothing to do.

- Gentler on the inverter's network module: every request is spaced two seconds from the previous one and five seconds from a write, reconnect attempts back off up to two minutes while the Wi-Fi is down, and opening the Battery page no longer reads the settings from the inverter by itself.

## 0.1.0

First release.

- **Dashboard** with the live energy flow (solar, grid, home, battery with state of charge), the modes the inverter and the controller are in, inverter details, today's and all-time energies, the last two hours of power and the day's energy flow as a Sankey diagram.
- **Solar**: production and forecast for today, PV strings, the next days' forecast, forecast accuracy, yields per month and per year.
- **Battery**: state of charge and health, cell and BMS details, and the battery control: Hold / Normal, the forecast-driven auto-hold with a threshold, a forecast offset and a release level, the charge cap, zero export, and the solar-surplus signal.
- **Stats**: energies per day, week or month over a chosen period, grid and battery throughput, self-sufficiency and self-consumption, a day detail with the battery's state over the day, and records.
- **Settings**: inverter details, the work mode and export limit, the forecast and MQTT configuration in use, data exports, an event log and a debug section with the raw inverter data and settings.
- **MQTT discovery**: the inverter appears in Home Assistant as a device with sensors, binary sensors (among them *Solar surplus*) and an *Events* entity for automations.
- All inverter writes use the dedicated commands that actually stick on ES/EM/BP inverters: the work mode, the on-grid depth of discharge, the grid export limit and its switch, and the battery charge current.
