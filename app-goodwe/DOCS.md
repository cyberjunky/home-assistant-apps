# Home Assistant App: GoodWe Monitor

GoodWe Monitor reads your GoodWe inverter over the local network and shows the live energy flow
between solar, battery, grid and home, with statistics on production, consumption, storage
and grid exchange. It also controls the battery: with the solar forecast it keeps the battery
for the evening, it can cap charging, keep export at zero, and tell Home Assistant when solar
is going spare. It runs entirely on your Home Assistant, served through ingress, and stores
everything in its own data folder.

After installing, enter the inverter's IP address on the app's Configuration tab, start it
and open **GoodWe Monitor** from the sidebar.

## How it works

```
inverter ──▶ read every 20 s ──▶ snapshots (SQLite) ──▶ dashboard (ingress)
   ▲                 │                    └──▶ daily energies, statistics
   │                 ├──▶ battery controller ◀── solar forecast (Forecast.Solar)
   │                 └──▶ MQTT discovery ──▶ Home Assistant entities and events
   └── dedicated commands only: work mode, DoD, export limit + switch, charge current
```

- **Reading.** The inverter is read every `poll_seconds` (default 20) with the
  [goodwe](https://github.com/marcelblijleven/goodwe) library. ES/EM/BP inverters speak the
  AA55 protocol over UDP through their Wi-Fi module, a small embedded stack that stops
  answering when it is polled too fast or by two parties at once, so every request the app
  makes goes through one queue, with two seconds of quiet between requests and five after a
  write. An inverter that stops answering (its Wi-Fi drops now and then) is retried with a
  growing pause, reported offline after three minutes, and reconnected automatically; the
  controller never acts on data older than two minutes.
- **Signs and names.** Whatever the inverter series, the dashboard uses one convention: grid
  power above zero is import, below zero export; battery power above zero is charging, below
  zero discharging; "home" is the whole house (on the ES the `house_consumption` field, which
  balances the flow diagram).
- **Energies.** ES inverters count solar production but not import, export, house or
  battery energy. Those are derived every five minutes by integrating the recorded power
  with a priority model (solar → home, then battery, then grid; battery → home; grid → home,
  then battery). The same integration gives the day's Sankey diagram and the battery's
  state over the day. Where an inverter does report such counters, the larger of the two
  wins. Snapshots are kept for 14 days; the daily energies forever.
- **Writing.** The app only ever sends five commands, each the dedicated command that
  actually sticks on the ES (a plain register write reports success and is silently
  ignored): the work mode (`set_operation_mode`), the on-grid depth of discharge
  (`set_ongrid_battery_dod`, AA55 0x0560), the grid export limit (`set_grid_export_limit`,
  0x0335), the export-limit switch (0x0353, what SolarGo sends) and the battery charge
  current (0x0351). The inverter's flash has limited write endurance, so everything writes
  only on a change of mind, the controller has a daily budget, and nothing retries in a
  loop. Every command and its result is an event, in the dashboard and over MQTT.

## Battery control

All of this is on the **Battery** page; the settings are kept in the app's data folder.

- **Hold / Normal.** Sets the on-grid depth of discharge (DoD): *Hold* = DoD 0, the
  battery stops discharging and the grid covers the house; *Normal* = DoD 80, the battery
  may discharge to 20%. Backup mode does not hold the battery on the ES; the DoD does.
- **Auto-hold.** Every five minutes the controller compares the solar forecast for the
  current hour (and the live production, so a wrong forecast can't trick it) with the
  **threshold**. At or above it the battery is held, so the solar surplus goes into the
  battery and the evening runs on a full battery; below it the battery is released. That
  follows the real production window far better than sunrise and sunset. The **forecast
  offset** scales the forecast first: the Solar page shows the forecast's bias over the last
  two weeks, so +20% when production keeps beating the forecast, −20% when it falls short.
  **Release at** releases the hold once the battery reaches that state of charge, so it can
  serve the house the moment the load exceeds the production instead of sitting full for
  hours. Each DoD write is read back and re-sent up to three times; a hold that the
  inverter still ignores (discharging more than 100 W for 15 minutes) raises an event.
- **Charge cap.** Brakes all charging, including from solar surplus, once the state of
  charge reaches the cap, by writing a charge current of 0 A; releases 5% below it. Every
  *N* days the cap pauses for a 100% charge so the BMS can balance its cells. With zero
  export on, the braked solar is curtailed rather than exported; the page says so.
- **Zero export.** Keeps the grid export limit at 0 W with the limit enforced, so solar
  that neither the house nor the battery can take is curtailed. Checked on the inverter
  once when switched on and when the app starts; after that only read back when the grid
  power shows export above 150 W for 15 minutes. A limit that drifted is re-sent; a limit
  the inverter ignores raises an event instead of a pointless write.
- **Solar surplus.** A signal for Home Assistant (the binary sensor *Solar surplus* and
  the `surplus_started` / `surplus_ended` events): **on** when the battery is full (from the
  *battery full* state of charge, or while the charge cap is braking) and the estimated
  surplus is at least the **margin**; **off** when the house imports more than *off when
  importing* from the grid, drains the battery, or the battery drops 10% below full. That
  asymmetry is the hysteresis: a load you switch on because of the signal does not switch
  it off, as long as the sun carries it. Under zero export a full battery means the panels
  are curtailed and the surplus is invisible in the grid power; it is then estimated as the
  forecast's headroom above the live production. Both switches wait **debounce** seconds.

## Web Dashboard

Open it from the sidebar. It follows Home Assistant's light/dark theme (🌓 in the top bar
overrides that).

- **Dashboard** — Solar, battery, grid, home and self-sufficiency; the animated energy
  flow; the modes of inverter and controller; inverter details; today's and all-time
  energies; the last two hours of power; the day's energy flow as a Sankey diagram (step
  through the last 14 days).
- **Solar** — Production against the forecast for today, PV strings, the forecast and its
  accuracy, yields per month and per year.
- **Battery** — State of charge and health, battery and BMS details, the battery control,
  the day's state of charge and battery power with its charging, discharging and held
  periods, and the last 30 days' throughput.
- **Stats** — Pick a period and bar size. Energies per period, grid, battery,
  self-sufficiency and self-consumption, a day detail with the battery's state, records.
- **Settings** — Inverter details; the work mode and export limit; the forecast and MQTT
  configuration in use; data counts and exports; the event log; and a **Debug** section
  with the raw inverter data and settings, the controller's internals, and a form to send
  one of the five commands by hand.

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `inverter.host` | | IP address or host name of the inverter's Wi-Fi/LAN module. Give it a fixed address in your router |
| `inverter.poll_seconds` | `20` | Seconds between reads. 20 is safe for the ES; faster polling can make its network stack stop answering for a while |
| `forecast.enabled` | `true` | Fetch the solar forecast (needed for auto-hold and the surplus estimate under zero export) |
| `forecast.latitude` / `longitude` | `0` | Location of the panels; `0` uses Home Assistant's home location |
| `forecast.planes` | one plane of 4 kWp | One entry per roof orientation: `name`, `kwp` (peak power), `tilt` (0 = flat, 90 = vertical), `azimuth` (0 = south, −90 = east, 90 = west) |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

### MQTT

With Home Assistant's Mosquitto add-on installed nothing needs to be set: the broker is
found automatically. For another broker fill in `host`, `port`, `user` and `password`.
`enabled: false` turns MQTT off; `base_topic` and `discovery_prefix` change the topics.

The inverter becomes a device named after its model with:

- **Sensors** — solar power (and per string), solar today and total, grid power (above zero
  = import), grid import and export today and total, grid voltage and frequency, battery
  power (above zero = charging), battery state of charge and health, voltage, current and
  temperature, battery charged and discharged today, battery mode, home power and home
  today, backup power, inverter temperature, work mode, grid mode, error codes, run hours,
  battery hold (`held` / `discharging`), depth of discharge, the auto-hold decision, the
  solar surplus in watts, and the forecast for today, tomorrow and this hour
- **Binary sensors** — inverter connected, **solar surplus**, battery held, auto-hold,
  charge cap braking, zero export, exporting, fault
- **Events** — an event entity whose `event_type` is one of the app's events, with the
  message and details as attributes

The state is one retained JSON message on `goodwe/state`, sent when something changed
(about once per poll); events go out on `goodwe/event` as they happen; `goodwe/status` is
`online` while the app runs.

### Event types

`inverter_online`, `inverter_offline`, `inverter_write`, `inverter_write_failed`,
`work_mode_changed`, `hold_applied`, `hold_released`, `hold_violation`,
`hold_violation_recovered`, `charge_cap_on`, `charge_cap_off`, `charge_cap_balance`,
`zero_export_set`, `export_violation`, `export_violation_recovered`,
`write_budget_exhausted`, `fault_detected`, `fault_cleared`, `grid_outage`, `grid_restored`,
`battery_full`, `battery_low`, `surplus_started`, `surplus_ended`.

A Telegram message for every command the app sends and every alert, as a Home Assistant
automation (replace the entity id with yours):

```yaml
triggers:
  - trigger: state
    entity_id: event.goodwe_gw3648d_es_events
conditions:
  - condition: template
    value_template: >-
      {{ trigger.to_state.attributes.event_type in
         ['inverter_write', 'inverter_write_failed', 'hold_violation', 'export_violation',
          'inverter_offline', 'fault_detected', 'surplus_started', 'surplus_ended'] }}
actions:
  - action: notify.telegram
    data:
      message: "{{ trigger.to_state.attributes.message }}"
```

To switch a load on solar surplus, trigger on `binary_sensor.<device>_solar_surplus` instead,
or build your own rule from the surplus, state of charge and grid power sensors.

## Privacy

GoodWe Monitor runs entirely on your Home Assistant. It has no cloud service of its own, no
analytics and no telemetry.

### What it fetches

| From | What | Why |
|------|------|-----|
| Your inverter (local network) | Runtime data every poll; the settings when you read them, when the controller verifies a write and once at start for zero export | The dashboard, the statistics and the controller |
| Forecast.Solar (`api.forecast.solar`) | The hourly production estimate for your location and planes, every 30 minutes | The forecast, auto-hold and the surplus estimate (`forecast.enabled`) |
| Open-Meteo (`api.open-meteo.com`) | Hourly irradiance for your location, only when Forecast.Solar fails | The same, as a fallback |
| Home Assistant | Your home location (when the options leave it at 0), time zone, and the MQTT broker's address and login | The forecast, local days, publishing entities |

### What it stores

Everything is in the app's private storage (`/data`), part of Home Assistant backups of
the app: `goodwe.db` (snapshots for 14 days, daily energies, the forecast log and the
events), `control.json` (the battery control settings and state) and
`forecast_cache.json`.

### What it sends

| To | What | When |
|----|------|------|
| Your inverter | The five commands, only when you press a button or the controller changes its mind | See Battery control |
| Forecast.Solar / Open-Meteo | Your coordinates (four decimals) and each plane's kWp, tilt and azimuth | With the forecast on |
| Your MQTT broker | The inverter's state and the events | With MQTT on and a broker available |
| Google Fonts | A request for the IBM Plex fonts, from your browser | When the dashboard loads; without internet it falls back to system fonts |

**Access.** The dashboard is only reachable through Home Assistant (ingress); under the
Supervisor the app refuses any other client. Only signed-in Home Assistant users can see
the data or change inverter settings.

## Good to know

- Built and tested on a GoodWe GW3648D-ES (platform ES, AA55 protocol). The battery
  control commands are confirmed there; other series are read fine, but the export-limit
  switch and the charge-current command are only sent to ES inverters.
- On the ES, *Self-use* and *Peak shaving* modes do not exist (General mode already
  self-consumes) and are not offered. *Backup supply* can only be switched in SolarGo.
- Statistics start when the app starts recording. The energy flow and the day detail need
  snapshots, which are kept for 14 days; the daily totals are kept forever.
- A short discharge while held is normal: when production sits just under the house load
  the inverter hunts around the balance point for a few minutes. Only a sustained one
  counts as a violation.

## Requirements

- Home Assistant with Supervisor, on a 64-bit system (`amd64` or `aarch64`)
- A GoodWe inverter reachable on the local network (its Wi-Fi or LAN module, UDP port 8899)
- For the entities: an MQTT broker (the Mosquitto add-on) and Home Assistant's MQTT
  integration

## Development

No inverter needed: `tools/mock_inverter.py` is a stand-in ES inverter that runs a sunny
day on fast-forward, with a house and a battery that follow the DoD, charge current and
export limit written to it.

```bash
pip install goodwe aiohttp "paho-mqtt>=2.1" ruff
mkdir -p /tmp/goodwe && echo '{"inverter": {"host": "mock"}, "forecast": {"enabled": true, "latitude": 52.1, "longitude": 5.1, "planes": [{"name": "Roof", "kwp": 4}]}, "mqtt": {"enabled": false}}' > /tmp/goodwe/options.json
GOODWE_MOCK_INVERTER=tools.mock_inverter GOODWE_DATA_DIR=/tmp/goodwe MOCK_SPEED=60 python3 -m src   # http://localhost:8099
```

`MOCK_SPEED` is how many simulated seconds pass per real second. `./lint.sh` checks
formatting and lint; `./lint.sh fix` fixes what it can.

## Credits

- [goodwe](https://github.com/marcelblijleven/goodwe) — The inverter protocol library
- [Forecast.Solar](https://forecast.solar/) and [Open-Meteo](https://open-meteo.com/) — Solar forecasts

GoodWe Monitor is an independent project. It is not affiliated with, endorsed by or sponsored
by GoodWe Technologies Co., Ltd.
