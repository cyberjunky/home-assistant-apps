# Home Assistant App: Lottery

> **Unofficial.** An independent project, not affiliated with, endorsed by or sponsored by Bitaxe, NerdQAxe, Lucky Miner or any other miner maker.

Lottery shows your solo miners in a dashboard, served through ingress: your odds of a block for all miners together on **Draw**, and a page per miner. It reads each miner over its AxeOS web API, and publishes every miner to Home Assistant over MQTT, with its sensors, a **Mining** switch and a **Restart** button.

The app doesn't mine and doesn't change your miners by itself. Pausing, resuming and restarting only happen through a miner's Mining switch or Restart button in Home Assistant.

## Setup

1. Add each miner under `miners` in the app's configuration: a name and its IP address.
2. Start the app and open **Lottery** from the sidebar.

Each miner appears in Home Assistant by itself when an MQTT broker is available. Without the Mosquitto app, enter a broker under `mqtt`.

## Configuration

```yaml
miners:
  - name: Lucky LV08
    host: 192.168.1.50
  - name: NerdQAxe+
    host: 192.168.1.51
poll_seconds: 15
network_data: true
firmware_check: true
```

| Option | Default | What it does |
|---|---|---|
| `miners[].name` | | The name on the pages and in Home Assistant. Keep it once set: the app's history and the entities are stored under it |
| `miners[].host` | | The miner's IP address or host name, for example `192.168.1.50`. `http://` in front is fine |
| `poll_seconds` | `15` | How often each miner is read |
| `network_data` | `true` | Read the recent blocks from mempool.space, see [The network](#the-network) |
| `firmware_check` | `true` | Look up the newest Bitaxe and NerdQAxe firmware on GitHub, for each miner's Firmware entity |
| `mqtt.*` | | Broker details; empty uses the broker Home Assistant knows |
| `log_level` | `info` | Amount of logging |

## Moving over from the Bitaxe integration

The app makes the same entities as the [Bitaxe integration](https://github.com/cyberjunky/home-assistant-bitaxe_monitor), under the same names. Home Assistant builds an entity id from the device's name and the entity's name, so the ids come out the same when two things are true:

- **The integration is gone first.** While its entities exist, Home Assistant gives the app's entities a `_2` at the end.
- **Each miner has the same name here as its device had there.** A device called `Bitaxe BM1368` in the integration needs `name: Bitaxe BM1368` here to get `sensor.bitaxe_bm1368_hash_rate` again.

So: note each miner's device name under **Settings → Devices**, remove the integration, add the miners here with those names, and start the app. Dashboards and automations that use the entity ids keep working. The history of the old entities stays in Home Assistant's recorder under the same ids.

## The miners

Anything with AxeOS firmware or a fork of it works: the Bitaxe family (ESP-Miner), the NerdQAxe family, Lucky Miner and the forks other makers ship. They all answer `/api/system/info`; values a firmware doesn't report are left out, and so are their entities.

- **Pausing** goes the way the firmware allows. AxeOS reports `miningPaused` and pauses and resumes in place. The NerdQAxe firmware reports `shutdown`: it can only shut its ASICs down, and needs a restart to mine again, so turning its Mining switch on restarts it. A firmware that reports neither gets no Mining switch.
- **Per ASIC** hash rate, errors and temperature, where the firmware gives them: AxeOS through its hash rate monitor, the NerdQAxe through its v2 dashboard.
- **Firmware entity** only for firmware the app knows for sure: the NerdQAxe and Bitaxe boards. Other makers' forks, like Lucky Miner's, have their own releases, so they get no Firmware entity rather than a wrong one. Installing always stays in AxeOS.

## What the app keeps

The miners forget a lot when they restart. The app keeps:

- **The work done:** the hashes of each miner, counted from its hash rate between readings, and the energy from its power.
- **Every new best share of a session:** the closest tickets.
- **Per day:** each miner's work, energy, best share, shares and time mining, against that day's network difficulty.
- **What happened:** paused, resumed, restarted, a new best ever, the fallback pool, a pool lost, overheating, new firmware, a found block.
- **Readings** once a minute for the charts, for 31 days.

All of it is in a SQLite database in the app's data, in its backups, and as a download on **Info**. **Info** also has a danger zone to delete it.

## The odds

Every block is a draw. A share of difficulty *d* takes on average *d* × 2³² hashes, and a block needs a share of at least the network difficulty *D*; the network finds one every ten minutes. With a hash rate of *H* hashes a second, each block is yours with a chance of

> *H* × 600 / (*D* × 2³²)

That is the **this block** figure. **Today** is 144 such draws, **this year** 52,560. **On average** is how long it takes to find one at this hash rate. The chance doesn't grow with time spent: every block starts over, and a good best share doesn't bring the next block closer.

The hash rate used is the 10 minute average when the firmware has one. A paused or unreachable miner holds no tickets.

- **Best share against a block:** your best share ever, on a log scale up to what a block needs. Each step of the scale is 1000× harder.
- **Luck:** this session's best share against the median best share for the work done since the miner started. Half the time you do better, half the time worse: 1× is usual, 10× is very lucky. The work is counted by the app, or worked out from the uptime and average hash rate when the session began before the app watched it.
- **A new best:** the chance of a share above your best ever within a day, and how long one takes on average.
- **If you win:** the block reward at the current height plus the average fees of the last blocks.
- **A ticket costs:** what all miners use in a day at their current power.

## The network

Your miners report the network difficulty and block height from their pool. With `network_data` on, the app also reads the last blocks from [mempool.space](https://mempool.space): their fees, for the prize, and the difficulty for when no miner is running. Only public data is asked for; nothing about you or your miners is sent. Turn it off to use only what your miners report.

## Entities

Each miner is a device, linked to a **Lottery** device with the totals.

| Kind | Entities |
|---|---|
| Hash rate | Hash rate, and its 1m, 10m, 1h and 1d averages; Expected hash rate; ASIC *n* hash rate |
| Power | Power, Input voltage, Current, Core voltage target and actual, Efficiency, Energy (for the Energy dashboard) |
| Cooling | ASIC temperature (2), VR temperature, ASIC *n* temperature, Fan speed, Fan RPM, Fan 2 |
| Mining | Best difficulty (all time and session, as a number and formatted), Pool and Network difficulty, Block height, Shares accepted and rejected, Blocks found, Error rate, ASIC frequency |
| Lottery | Block chance today, Years to a block, Best share against network, Luck |
| Device | Uptime, WiFi signal, Pool, Pool ping, Pool response time, Ping loss, Free memory, Last reset reason |
| States | Pool connected, Using fallback pool, Overheat mode, Auto fan speed, Overclock, Online |
| Controls | **Mining** switch (on = mining, off = paused), **Restart** button |
| Firmware | Firmware update entity (NerdQAxe and Bitaxe) |

The **Lottery** device has the hash rate and power of all miners, the block chance today and this year, years to a block, the best share ever, luck, how many miners are mining, the network difficulty, the block height and the prize.

A miner's entities are unavailable while it doesn't answer, except **Online**, which tells you it doesn't. A miner that was never seen gets its entities after its first answer; after that they are set up from the last answer, also while it is off.

### Topics

| Topic | |
|---|---|
| `lottery/status` | `online` or `offline` (the app) |
| `lottery/<miner>/link` | `online` or `offline` (the miner) |
| `lottery/<miner>/state` | the miner's values, one retained JSON message |
| `lottery/<miner>/mining/set` | `ON` or `OFF`: the Mining switch |
| `lottery/<miner>/restart/set` | `PRESS`: the Restart button |
| `lottery/<miner>/firmware` | installed and newest firmware |
| `lottery/total/state` | the Lottery device's values |

`<miner>` is the miner's name in lower case with `_` for anything else, for example `lucky_lv08`.

## Troubleshooting

- **A miner says it hasn't answered:** open its address in a browser. AxeOS should show its page, and `http://<address>/api/system/info` its values.
- **No entities in Home Assistant:** check **Info** → **In Home Assistant** for the broker. Without the Mosquitto app, set `mqtt.host`, `mqtt.user` and `mqtt.password`.
- **The entities got `_2` at the end:** the Bitaxe integration's entities still existed. Remove the integration, then remove the entities with `_2` (or the devices) and restart the app.
- **No Mining switch:** the firmware reports no way to pause (`miningPaused` or `shutdown`). The Restart button is still there.
