# Changelog

## 0.2.1

**After updating:** nothing to do. The sidebar entry, the dashboard and the Home Assistant device keep working; only the name changes.

- Renamed to **GoodWe Nerd** (the app's slug, image and data stay the same, so nothing needs reinstalling).
- The README says why it exists: an ES inverter without a proper zero-export mode, and a summer of using every bit of surplus solar at home.

## 0.2.0

**After updating:** nothing to do. If the charge cap was braking charging, it is released once at start. In Home Assistant the *Charge cap braking* entity disappears, and the *Work mode* sensor now shows the inverter's work mode (General, Backup, …) instead of its energy mode (that moved to the new *Energy mode* sensor).

Cleaner, and focused on what works on the ES: hold the battery for the evening by forecast, keep export at zero, and signal the solar surplus once the battery is full.

- **Three pages instead of five.** The **Dashboard** is everything live, solar and battery together: the energy flow, the battery (state of charge, power, temperature, health, BMS limits, cycles), the solar panels and strings against the forecast, what the controller is doing, live power and today's charts. **Stats** has all history, now including the solar forecast, its accuracy and the yields. **Settings** has the battery control and the configuration.
- **New energy flow diagram.** The live power is split over the paths it really takes (solar → home, solar → battery, grid → home, …) with the same model as the daily energies, so you see where the solar goes. Line width and speed follow the power; it shows the curtailed solar under zero export and the surplus signal.
- **The daily Sankey** is larger, horizontal with gradient ribbons, and part of the Stats day detail (same date as the day chart).
- **Work mode guard.** The controller reads the inverter's settings at start and once a day. Auto-hold only works in General mode; in any other mode it pauses, the new binary sensor *Work mode not General* turns on and a `work_mode_not_general` event is raised. Settings has a *Back to General* button instead of the four work-mode buttons (Backup does not hold the battery on the ES and Eco's schedule writes do not stick).
- **New statistics**, per day and period and as Home Assistant sensors: hours the solar-surplus signal was on and the solar used meanwhile (your miners), the solar curtailed under zero export (estimate), battery cycles (from the inverter's commissioned capacity), and battery charged and discharged in total (the ES does not count these itself, so *all time* read 0 before).
- **Removed:** the charge cap (with zero export it only curtailed solar, and LFP batteries take a full charge in their stride), the separate *while held* DoD (a hold is always DoD 0), the manual export-limit and switch controls (Zero export keeps them right; the Debug section can still send any dedicated command), and the work-mode-changed events that were really energy-mode changes.

## 0.1.2

**After updating:** nothing to do. The forecast option `planes` is now called `panels`; an existing `planes` entry keeps working, but rename it when you next edit the configuration.

- The forecast's roof entries are called **panels** in the configuration (one entry per roof side), and the dashboard and documentation say so too.

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
