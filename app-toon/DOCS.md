# Home Assistant App: Toon

Toon logs what a rooted Toon thermostat knows and shows it in a dashboard, served through ingress, and publishes it to Home Assistant over MQTT. This version only reads; it can't change anything on your Toon.

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

The **Connection history** table shows each time the Toon could not be reached:

| What happened | Meaning |
|---|---|
| Restarted | The Toon's uptime shows that it rebooted during the outage |
| Connection lost | The Toon kept running and only the connection was gone: network, WiFi or SSH |
| App was stopped | The app itself wasn't running, so nothing is known about this period |

**Memory free before** is the last value before the outage. Together with the charts for the Toon's memory and bus threads, this helps to find out why a Toon hangs.

A notice appears when the Toon's clock is more than 2 minutes off, because its week program then runs late or early as well.

## History

The Toon keeps its own history files: meters up to 10 years back per day and 5 years per hour, thermostat values a year per hour and 30 days per minute. They are read in small pieces with a pause in between, which takes up to an hour and continues where it was after an interruption. History has the Toon's own timestamps, so a Toon with a wrong clock has its history off by that much. The one exception is the gap after an outage, which is read back with the known offset taken out.

## Sensors you may expect but won't find

- **Burner starts, hot water burner starts, pump starts** are off by default in Home Assistant, because a boiler that doesn't support them reports nothing useful.
- **Outside temperature, hot water temperature, fan speed:** the Toon doesn't appear to read these from the boiler.

## Limitations

- Tested on one Toon 1 with firmware 6.3.30, an OpenTherm boiler and a meter adapter. A Toon with a different setup may lack some values; please report what you find.
- Controlling the thermostat isn't in this version.
