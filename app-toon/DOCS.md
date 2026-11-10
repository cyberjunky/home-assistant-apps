# Home Assistant App: Toon

Toon logs what a rooted Toon thermostat knows and shows it in a dashboard, served through ingress, and publishes it to Home Assistant over MQTT. From the Dashboard it can also control the thermostat.

## Setup

1. Make sure you can log in to your Toon over SSH, as root.
2. Enter the Toon's address and root password under `ssh` in the app's configuration.
3. Start the app and open **Toon** from the sidebar.

The Toon appears in Home Assistant by itself when an MQTT broker is available. Without the Mosquitto app, enter a broker under `mqtt`.

## Configuration

| Option | Default | What it does |
|---|---|---|
| `ssh.host` | | Address of the Toon |
| `ssh.port` | `22` | SSH port |
| `ssh.username` | `root` | SSH user |
| `ssh.password` | | SSH password |
| `ssh.trust_new_host_key` | `false` | Accept a changed SSH key of the Toon. Turn it on once after reinstalling the Toon's software, then off again |
| `import_history` | `true` | Read the history the Toon keeps itself into the database |
| `raw_retention_days` | `90` | How long every single sample is kept. Hourly values are kept for good |
| `mqtt.*` | | Broker details; empty uses the broker Home Assistant knows |
| `log_level` | `info` | Amount of logging |

## How it works

```
Toon: message bus (1337) ──SSH──▶ monitor ──▶ SQLite ──▶ dashboard (ingress)
      read-only command  ──SSH──▶    │
                                     └──▶ MQTT discovery ──▶ Home Assistant entities
```

- **One connection.** The Toon's message bus only listens on the Toon itself, so everything goes through one SSH session. The Toon's web server isn't used at all.
- **Old algorithms.** A Toon runs Dropbear 0.51, which only speaks algorithms that are considered weak today. The app enables them for this connection only. Keep this connection inside your own network.
- **Reconnecting.** A dead connection is noticed within about 80 seconds by SSH keepalives, or sooner when it is closed. The app then retries after 5 seconds, backing off to once a minute. A refused password or a changed SSH key is retried every 5 minutes.
- **Reading.** The bus pushes changes as they happen, and the app also asks for the thermostat's dataset every 10 seconds and all other values every 5 minutes.
- **Clock.** Live values get the time they were received, so a wrong clock on the Toon doesn't affect them.

## The dashboard

**Dashboard** shows what matters right now: room and setpoint, the burner, water pressure, power and gas today, a picture of the heating system with its temperatures, the thermostat's preset and program with the next change, the boiler's state, and the last 24 hours in charts.

A notice appears when something needs attention: the Toon can't be reached, its clock is more than 2 minutes off (its week program then runs late or early as well), or the water pressure is under 1 bar. Usual is 1 to 2 bar, but go by your boiler's manual.

When the boiler reports a fault, a red **Boiler fault** banner shows two codes and the water pressure:

- **Code on the Toon** is the number the Toon's own screen shows with the fault ("Errorcode" or "ketelstoring"). It comes from the Toon's `errorFound`.
- **Boiler's own code** is the fault code the boiler sends over OpenTherm (`OTBoilerOEMFaultCode`). Your boiler's manual says what it means.

Both are shown exactly as the Toon gives them. 255 also appears while nothing is wrong, so on its own it doesn't tell what the fault is: check the boiler's own display. The banner shows the water pressure because low pressure is a common cause.

**Stats** is for analysis, over any period and per day, week, month or year:

- **Smart meter:** gas and electricity per period, per tariff, compared with a year earlier; power; the night base load; and when gas is used per weekday and hour.
- **Heating:** room temperature against setpoint and all the boiler's temperatures, with their lowest, average and highest values.
- **Boiler:** its hour counters, burner time per state, burner starts and short runs, modulation, supply and return temperature while heating, supply above setpoint, the share of heating time with a return under 55 °C (where a condensing boiler can condense), failed starts, the water pressure with its trend, and a log of boiler faults with their codes and the lowest pressure during each.
- **Day detail:** everything of one day on the same time scale, with the burner's periods marked.
- **Export:** the table per period, all samples or all hourly values of the chosen period as CSV.

Burner starts, burner time and the temperatures while heating are worked out from what the app records, so they start at the first day it runs this version. A day with little data shows little. Use is worked out from meter readings: a meter reading that drops (a replaced or reset meter) and a gap of more than 3 days give no value for that interval, rather than one big jump. "Last top-up seen" is an estimate from the day's lowest pressure over several days.

**Settings** shows the Toon's own health with its charts, the connection history, and the app's configuration without passwords. Change the configuration in the app's page in Home Assistant.

The **Connection history** table shows each time the Toon could not be reached:

| What happened | Meaning |
|---|---|
| Restarted | The Toon's uptime shows that it rebooted during the outage |
| Connection lost | The Toon kept running and only the connection was gone: network, WiFi or SSH |
| App was stopped | The app itself wasn't running, so nothing is known about this period |

**Memory free before** is the last value before the outage. Together with the charts for the Toon's memory and bus threads, this helps to find out why a Toon hangs.

## History

The Toon keeps its own history files: meters and burner minutes up to 10 years back per day and 5 years per hour, power per 5 minutes for 31 days, and thermostat values per minute for 30 days. It keeps no thermostat values longer than that; from the day you install this app, the app keeps them itself. History is read in small pieces with a pause in between, which takes up to an hour and continues where it was after an interruption, also for files added by a later version.

History has the Toon's own timestamps, so a Toon with a wrong clock has its history off by that much. The one exception is the gap after an outage, which is read back with the offset at that moment taken out.

## Sensors you may expect but won't find

- **Burner starts, hot water burner starts, pump starts** are off by default in Home Assistant, because a boiler that doesn't support them reports nothing useful.
- **Outside temperature, hot water temperature, fan speed:** the Toon doesn't appear to read these from the boiler.

## Limitations

- Tested on one Toon 1 with firmware 6.3.30, an OpenTherm boiler and a meter adapter. A Toon with a different setup may lack some values; please report what you find.
- Controlling the thermostat isn't in this version.

## External sensors

Add Home Assistant sensors under the app's **Configuration**, for example 1-wire sensors on your pipes:

```yaml
outside_temperature: sensor.outside_temperature
external_sensors:
  - name: Radiators supply
    entity: sensor.pipe_supply
  - name: Radiators return
    entity: sensor.pipe_return
```

They are read once a minute and stored like the Toon's own values: every reading for the days under `raw_retention_days`, and hourly values for good. Each gets a tile on the Dashboard, and they appear in the charts there and on the Stats page: the outside temperature with the room, other temperatures with the boiler, and other units in a chart of their own. The table of measured values and the CSV exports include them too. Keep a sensor's name once set, because its history is stored under it.

## Boiler energy

**Energy in gas** on the Stats page is measured: the gas you used times `gas_kwh_per_m3`. The default of 9.77 kWh per m³ is the upper heating value of Dutch gas, and your energy bill may show a slightly different number.

**Heat, estimated** is not measured. It assumes a typical condensing boiler: about 97% of that energy at a return temperature of 30 °C or lower, down to about 88% at 55 °C or higher. It doesn't know your boiler, or how much went to hot water. Return temperatures are only recorded from version 0.2.0 on, so over a longer period the estimate applies recent temperatures to older gas use.

Measuring the heat itself takes the water's flow as well as its temperatures: heat is flow times the difference between supply and return. Sensors on the pipes give you the temperatures, which the Toon already reports for the boiler, but not the flow. For a real figure you need a flow meter or a heat meter.

## Backup

**Settings → Database backup** downloads a copy of all recorded values while the app keeps running. Your options and passwords aren't in it.

The **Danger zone** under it deletes everything recorded (values, hourly history, outages and the history of your external sensors) after you type DELETE. Your options are not touched. The app then fetches the Toon's own history again and records from now.

## Controlling the thermostat

The Thermostat panel on the Dashboard has controls:

- **− and +** change the setpoint by 0.5 °C, between 6 and 30 °C. A row of presses counts as one change. At the temperature of a preset, that preset becomes active.
- **Comfort, Home, Sleep and Away** switch to that preset until the next change in the week program.
- **Turn on, Turn off and Back to program** control the week program.

Set `read_only: true` to remove the controls and refuse changes. The controls also disappear while the Toon isn't connected.

### In Home Assistant

With MQTT, the Toon device also has a **Thermostat** climate entity, so it works with the thermostat card, voice assistants and automations. It replaces the toon_climate integration:

| In Home Assistant | On the Toon |
|---|---|
| Target temperature | The setpoint, as with − and + |
| Preset Comfort, Home, Sleep, Away | That preset until the next change in the week program |
| Mode **Auto** | The week program on, or back to the program after a temporary change |
| Mode **Heat** | The week program off: the Toon holds the setpoint |
| Action Heating / Idle | Heating while the burner heats or preheats; idle when it's off or making hot water |

Holiday mode shows as no preset. Changes go through the same checked commands as the Dashboard's controls, and the new state shows within a few seconds. A change the Toon refuses is logged in the app's log. With `read_only: true`, the entity only shows the state and accepts no changes.
