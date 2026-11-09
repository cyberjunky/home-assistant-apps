# Changelog

## 0.3.6

**After updating:** nothing to do. Home Assistant gets about forty new diagnostic entities, fifteen of them disabled until you switch them on on the inverter's device.

- **Everything the inverter reports is shown.** A new *Inverter readings* panel on the Settings page shows, live, all the values that were only in Debug: PV1 and PV2 voltage, current and mode, grid current, power factor and status, the backup (EPS) output, the inverter's own output and counters, the battery's status, BMS limits, warnings and errors, and the diagnosis.
- **All inverter settings are shown.** *Inverter settings* adds the four Eco schedule slots in plain text (so you can see what an Eco mode would follow), fast charging, off-grid charge, battery activation and BMS protocol, the power factor setting, shadow scan and the grid quality check.
- **The same values as MQTT sensors** (diagnostic), the eco schedules and the settings included. The modes, Eco schedules, backup output and backup supply are added disabled: switch them on in Home Assistant if you want them.
- Rows that don't apply in the current mode are greyed out: the Eco schedules outside Eco mode, the off-grid charge settings outside Off-grid mode, fast charging while it is off, and the backup output while nothing draws from it.

## 0.3.5

**After updating:** nothing to do. Auto-hold switches on, also if you had switched it off; switch it off again under *Settings → Battery control* if you don't want it. At start the app recalculates the last two weeks from its snapshots, so the solar history and the forecast accuracy change. In Home Assistant *PV today* jumps to the corrected figure once, up or down.

- **Auto-hold is on by default.** The solar forecast is what controls the battery, but auto-hold was off until switched on, so nothing happened. It now holds the battery while solar is expected and releases it when production drops off.
- **Auto-hold puts the inverter back in General mode.** It used to pause and only warn when the inverter was in another mode (Eco, say). With auto-hold on, the app now switches it back to General (at most once an hour) and carries on. With auto-hold off it leaves the mode alone.
- **Checked every hour instead of once a day.** The app reads the inverter's settings every hour, so a work mode or DoD changed elsewhere (SolarGo, the GoodWe cloud) is put right within the hour. That is one read per hour, no extra writes.
- **Solar today was wrong on ES inverters.** The inverter's "today's PV generation" counter left out solar that went straight into the battery and kept climbing on battery power after dark. Solar is now calculated from the measured power, like the other energies. That corrects the dashboard, % of forecast, the forecast accuracy and its bias, Stats and the *PV today* sensor. It updates every five minutes.
- **Yesterday's totals could carry into the new day.** Polls just after midnight stored yesterday's home, grid and battery energies under the new day, and they stuck until the real figure passed them. Fixed, and past days are corrected by the recalculation.

## 0.3.4

**After updating:** nothing to do.

- After two weeks of use, a small card in the corner of the dashboard asks for a donation. It never blocks the dashboard, which keeps running and stays usable. Maybe later asks again after 30, then 60, then every 90 days. Don't show again, or following a donate link, ends it for good.
- The README and the dashboard's status bar say that this is an unofficial app.

## 0.3.3

**After updating:** nothing to do. The first hours after the update the add-on averages the snapshots older than 14 days per minute, a few days at a time.

- **Everything is kept for good.** The 20-second snapshots used to be deleted after 14 days and the events after a year. Now nothing is deleted: snapshots older than 14 days are averaged per minute (the energies stay exact, at about a third of the size: roughly 150–200 MB a year), so every recorded day keeps its day chart and Sankey in Stats. Events are kept too.

## 0.3.2

**After updating:** nothing to do.

- The dashboard's *Live power* line chart is replaced by **Energy flow today**: a thin Sankey of where today's energy came from and went (on a phone with just the names; the values are in the tooltips). The day Sankey on Stats is unchanged.

## 0.3.1

**After updating:** nothing to do.

- New icon: a sun in the middle of a ring for the battery level, centred, in the same style as the other apps. The logo now says GoodWe.

## 0.3.0

**After updating:** nothing to do. To follow your miners, set `miners.power_w` (and optionally `miners.entity`) on the Configuration tab.

- **Miners.** The loads you switch on with the solar surplus signal (bitcoin miners) get a status: running or off, since when and what they draw, on the dashboard (a tile and in Control), as a dashed line in the live and day charts, and as hours and energy per day in Stats (tiles, the surplus chart, the day detail, a record). New configuration `miners`: `name`, `power_w` (their total rated power) and an optional `entity` (a switch or a power sensor; without it they follow the surplus signal). In Home Assistant: *Miners running*, *Miners power*, *Miners today* and *Miners energy today*, and the events `miners_started` / `miners_stopped`.
- The dashboard no longer says "DoD not read yet" (and "Work mode: not read yet") for half an hour when the first settings read fails: the controller tries again every 5 minutes until a read succeeds, the dashboard says the read failed and shows the error, and the log has it as a warning.

## 0.2.1

**After updating:** nothing to do. The sidebar entry, the dashboard and the Home Assistant device keep working; only the name changes.

- Renamed from GoodWe Monitor to **GoodWe** (the app's slug, image and data stay the same, so nothing needs reinstalling).
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
