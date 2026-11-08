# Home Assistant App: TeslaMeter

TeslaMeter records every trip and charging session of your Tesla and shows them on a live
map, with statistics on distance, energy, consumption, charging and battery health. It
runs entirely on your Home Assistant, served through ingress, and stores everything in its
own data folder.

After installing and starting the app, open **TeslaMeter** from the sidebar and sign in to
Tesla (next section). Logging starts as soon as your cars appear.

## Signing in to Tesla

Tesla has no way for third-party tools to log in by themselves, and its sign-in ends in an
app link that an ordinary browser can't open. TeslaMeter therefore brings its own browser:

1. Open the dashboard and click **Sign in** (or **Settings → Sign in with Tesla**).
2. Tesla's own sign-in page appears in the dialog. It runs in a browser inside the app;
   you see its screen and it gets your mouse and keyboard. Click in it and sign in as
   usual, including your verification code or captcha if Tesla asks for one.
3. That's it. When Tesla accepts the sign-in the dialog closes, your cars appear within
   seconds and logging starts.

You only do this again if you change your Tesla password or sign out. The sign-in browser
only runs while the dialog is open (it needs about 300 MB of memory for that minute) and
starts empty each time.

**With a refresh token instead.** If you'd rather not type your password into a browser
running in the app, or the built-in browser doesn't work for you, pick **With a refresh
token** in the dialog. [Tesla Auth](https://github.com/adriankumpf/tesla_auth) is a small,
free and open-source program for Windows, macOS and Linux that shows Tesla's sign-in in a
window on your own computer and hands you the tokens. Paste its refresh token (or
everything it shows) into the dialog and TeslaMeter connects.

## Importing existing history

Already have history in another logger that uses a PostgreSQL database? **Settings → Data
→ Import history** reads your drives, positions, charging sessions, states and software
versions straight from it and adds them, matching cars by VIN. Enter the database host,
name, user and password (from that add-on's configuration). The values are stored in
metric km, so they carry over directly. It only reads; nothing in the source changes, and
running it again never duplicates, and it fills in what trips and sessions already here are
missing, such as tire pressures. Trips imported this way keep their GPS track; home charging
and Supercharging both come across. Parked positions, cables, chargers and climate settings
come along too; TeslaMate's sleep timing and geofence prices do not.

## How it works

```
Tesla account ──▶ owner API ──▶ vehicle monitor ──▶ SQLite ──▶ dashboard (ingress)
                  streaming ──▶      │                    └──▶ statistics
                                     └──▶ MQTT discovery ──▶ Home Assistant entities
```

- **Signing in.** Tesla's sign-on service hands a plain HTTP client (any library) access
  tokens that only its Fleet API accepts, so the owner API would refuse them with a 403.
  TeslaMeter therefore requests the tokens through its own browser — the real Chromium
  that shows you the sign-in page — whose requests Tesla accepts. The owner API calls
  themselves are made by the app directly, since the API accepts any valid token. Token
  renewals briefly start that browser again.
- **Reading the car.** While the car is awake its data is read on these intervals (all
  adjustable under `polling`):

  | When | How often it reads the car |
  |------|-----------|
  | Driving | every **3 s** (and Tesla's live stream adds ~1 position per second) |
  | Charging | every **10 s** |
  | Online & idle | every **15 s** |
  | Asleep / offline | a lightweight online-or-asleep check every **30 s** (this one doesn't wake the car) |

  Separately, the **charging history** (billed Supercharger costs) is checked about every
  5 minutes, but only actually calls Tesla when a fast-charge is waiting for its bill, on
  the first start, or when you press Fetch. Home Assistant **zones** are refreshed hourly,
  and an **address** is looked up once per place and then cached.
- **More than one car.** Every vehicle on the account is followed separately. When there's
  more than one, a car selector appears in the top bar; the dashboard, trips, charging and
  stats all show the selected car, and each car becomes its own device over MQTT.
- **Letting it sleep.** Reading a car's data keeps it awake, which costs range. After 15
  minutes without activity (no driving, charging, climate, sentry mode, open doors or
  software update) TeslaMeter stops reading the data and only checks whether the car is
  online, which doesn't keep it awake. If it is still awake after 20 minutes its data is
  read once and the wait starts again. An asleep car is never woken up, unless you press
  **Wake up** on the dashboard.
- **Live stream.** Tesla's streaming channel delivers position, speed, power and elevation
  about once a second while driving, and reports activity while the car is being left
  alone, so a drive that starts in that window is caught right away. With `streaming` off,
  trips are still recorded (a position every few seconds), but without elevation, and a
  drive or charge that starts while the car is being left alone is only noticed when that
  window ends.
- **Trips and charges.** A trip starts when the car is put in gear and ends when it is
  parked; a charging session starts when power flows and ends when it stops. Trips shorter
  than 50 m and sessions that added nothing are dropped. A trip survives a tunnel or an
  app restart of up to five minutes.
- **Energy.** The car doesn't report the energy a trip used, only its rated range.
  TeslaMeter converts range to kWh with the car's rated consumption (Wh per km of range),
  which it learns from your charging sessions (energy added ÷ range gained, median of the
  last 25). Until the first decent session a typical value for the model is used; all trips
  are recalculated when the learned value changes. `efficiency_wh_km` fixes it instead.
- **Charging cost and efficiency.** The energy drawn from the charger is measured from
  voltage, current and phases (AC) or the reported power (DC), so it includes charging
  losses; efficiency is energy added ÷ energy drawn.
- **What a session cost.** The car doesn't know. For a fast-charge session TeslaMeter looks
  up what Tesla billed in your account's charging history (the one the Tesla app shows) and
  uses that amount, including any idle fee; the bill usually appears within minutes to
  hours. An amount in another currency than yours is shown with the session but not counted
  in the totals. Sessions at home and at other networks get energy drawn × `cost_per_kwh`.
  **Set cost** on the Charging page overrides either.

## Web Dashboard

Open it from the sidebar. It follows Home Assistant's light/dark theme (🌓 in the top bar
overrides that).

- **Dashboard** — Map with the car, the drive in progress and the last 24 hours of routes;
  battery, range and charge limit; what the car is doing right now; vehicle details; totals
  for today, 7 days and 30 days; recent activity. 📍 keeps the car in view.
- **Trips** — Pick a period, click a trip. The map shows the route coloured by speed; hover
  the chart (speed, power, elevation or battery) to see where on the route you were. **GPX**
  downloads the track.
- **Charging** — Pick a session to see where it was, its power curve and battery level.
  **Set cost** overrides the calculated cost, for example for a fast charger with its own
  price.
- **Stats** — Pick a period and bar size, including tire pressure over time. Hover any chart for exact values; click a record
  to open that trip or session. The "Where you've been" map is built from cells of about
  100 m that each trip is mapped into when it ends, so it stays quick however much history
  there is; right after an update or an import it says "mapping N trips…" while it catches up.
- **Settings** — Tesla account, vehicles, CSV and database downloads, and what the app is
  configured to do.

The dividers between list, map and details can be dragged; double-click one to reset it.

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `units` | `metric` | `metric` (km, °C, bar) or `imperial` (mi, °F, psi). Display only: data is stored in metric, so you can switch at any time |
| `currency` | `EUR` | Three-letter currency code for charging costs |
| `cost_per_kwh` | `0` | Price per kWh for new charging sessions; `0` turns cost calculation off |
| `supercharger_costs` | `true` | Use what Tesla billed for sessions at its own chargers (from your account's charging history) as their cost |
| `efficiency_wh_km` | `0` | Rated consumption in Wh per km of rated range; `0` learns it from your charging sessions |
| `streaming` | `true` | Use Tesla's live stream (see [How it works](#how-it-works)) |
| `geocoding` | `true` | Look up addresses at OpenStreetMap Nominatim |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

### Polling

| Option | Default | Description |
|--------|---------|-------------|
| `driving_seconds` | `3` | Interval while driving (10 seconds or this value, whichever is larger, while the live stream delivers positions) |
| `charging_seconds` | `10` | Interval while charging |
| `online_seconds` | `15` | Interval while awake and doing nothing in particular |
| `asleep_seconds` | `30` | Interval of the online/asleep check while asleep or being left alone |
| `idle_minutes` | `15` | Idle time before the car is left alone to fall asleep |
| `sleep_window_minutes` | `20` | How long the car is left alone before its data is read once again |

The defaults suit most cars. A car that doesn't fall asleep usually has sentry mode on, or
needs a longer `sleep_window_minutes`.

### MQTT

With Home Assistant's Mosquitto add-on installed nothing needs to be set: the broker is
found automatically. For another broker fill in `host`, `port`, `user` and `password`.
`enabled: false` turns MQTT off.

Each car becomes a device with:

- **Sensors** — state (`online`, `driving`, `charging`, `updating`, `asleep`, `offline`),
  battery, usable battery, range, estimated range, odometer, speed, power, inside and
  outside temperature, charging state, charger power, charge energy added, charge limit,
  time to full charge, software version, tire pressures; as diagnostics also ideal range,
  set temperatures, fan, seat heaters, keep climate mode, cabin overheat protection, charger
  voltage, current, phases, type, brand and cable, offered and requested current, port latch,
  scheduled charging and sunroof
- **Binary sensors** — lock, plugged in, climate, sentry mode, doors, windows, trunk,
  frunk, occupied, software update available, preconditioning, charge port door, battery
  heater, front and rear defrost, steering wheel heater, each door and window, a warning per
  tire, valet mode, speed limit mode
- **Device tracker** — the car's location, so it shows on Home Assistant's map and in zones

The car's whole state is one retained JSON message on `teslameter/<VIN>/state`
(`base_topic` changes the first part), sent again only when something changes;
`teslameter/status` is `online` while the app runs.

## Privacy

TeslaMeter runs entirely on your Home Assistant. It has no cloud service of its own, no
analytics and no telemetry, and nothing is ever sent to its author. This is everything it
fetches, stores and sends, and why.

### What it fetches

| From | What | Why |
|------|------|-----|
| Tesla sign-on (`auth.tesla.com`) | Access token and refresh token, when you sign in and at every renewal | To read your car's data on your behalf |
| Tesla owner API (`owner-api.teslamotors.com`) | The vehicles of your account; for each car its state, position, speed, power, odometer, battery level and range, charging details and settings, temperatures, climate and heaters, lock/doors/windows, tire pressures and warnings, valet and speed limit mode, the car's display settings, software version and navigation destination | The dashboard, and recording trips and charging sessions |
| Tesla streaming (`streaming.vn.teslamotors.com`) | Position, speed, power, elevation, battery level and range, about once a second while driving | Detailed trips, and noticing activity without keeping the car awake (`streaming`) |
| Tesla charging history (same API) | Your sessions at Tesla's chargers: time, location name and billed amounts | The real cost of Supercharger sessions (`supercharger_costs`) |
| OpenStreetMap Nominatim | The address at a coordinate | Naming where trips and charging sessions start and end (`geocoding`) |
| Home Assistant | Your zones (name, position, radius), map tiles, time zone, and the MQTT broker's address and login | Place names such as "Home", the maps, local times, and publishing entities |
| Another logger's database (only when you run the import) | Your drives, charges, positions, states, versions and the car's details, read once from the PostgreSQL database you point it at | Bringing your existing history into TeslaMeter; nothing is written back |

The only thing it ever asks the car to do is **Wake up**, and only when you press that
button.

### What it stores

Everything is in the app's private storage (`/data`), which is part of Home Assistant
backups of the app.

| Where | What | Why |
|-------|------|-----|
| `tokens.json` | Tesla access token, refresh token, their expiry and your account's e-mail address | Staying signed in; the address is shown in Settings so you know which account is connected. Never your password |
| `teslameter.db`: cars | VIN, name, model, trim, colour, wheels, spoiler, version name, battery type, learned consumption, last known state | Showing the car and its last values while it sleeps |
| `teslameter.db`: positions | Time, position, heading, elevation, speed, power, odometer, battery level, range, temperatures, climate settings, battery heater state and tire pressures: every second or few seconds while driving, about once a minute while parked and awake | Drawing trips, the charts and the statistics |
| `teslameter.db`: trips and charges | Start and end time, place and position, distance, energy, battery levels, speeds, temperatures; per charging session also the power curve, cable, charger type, offered and requested current, battery heater state, cost and what Tesla billed | The Trips, Charging and Stats pages |
| `teslameter.db`: states and software versions | When the car was driving, charging, online, asleep or offline; when a software version was first seen | Time-in-state and standby loss statistics, software history |
| `teslameter.db`: addresses | Looked-up addresses per coordinate | So the same place is never looked up twice |

The database is never sent anywhere. You can download it, or trips and charges as CSV,
under **Settings → Data**; those downloads never contain the tokens. Uninstalling the app
removes all of it.

### What it sends

| To | What | When |
|----|------|------|
| Tesla | Your tokens with every request; your keystrokes and clicks while you sign in; the token requests, made by the app's own browser | Always; the sign-in runs in a browser inside the app, so what you type travels from your browser through Home Assistant to that browser and on to Tesla. It is not stored or logged, and that browser's cookies and cache are deleted when the dialog closes |
| OpenStreetMap Nominatim | The coordinates where a trip or charging session starts and ends, once each | Only with `geocoding` on. Places inside a Home Assistant zone are named without a lookup |
| Your MQTT broker | The car's current state including its position, as one retained message | Only with MQTT on and a broker available |
| Google Fonts | A request for the IBM Plex fonts, from your browser | When the dashboard loads; without internet it falls back to system fonts |

The app's log (visible in Home Assistant) contains state changes, trip and charge summaries
without positions, and once per start a summary of your newest Tesla charging-history entry
(time, location name, amount). It never contains tokens, your password or what you type.

**Access.** The dashboard is only reachable through Home Assistant (ingress), so only
signed-in Home Assistant users can see where the car is and has been, or sign in to Tesla.
If you open Home Assistant over plain `http://`, everything between your browser and Home
Assistant, including a Tesla password typed in the sign-in dialog, is unencrypted on that
network, as with Home Assistant's own login.

## Good to know

- TeslaMeter uses Tesla's owner API, the one the Tesla app uses. It is not an official
  third-party interface; Tesla can change it without notice, and an update of this app may
  then be needed.
- Tesla business (fleet) accounts that were moved to the Fleet API cannot use the owner API
  and are not supported.
- History starts when the app starts logging. Statistics such as battery health and the
  learned consumption get better after a few weeks of trips and charges.

## Requirements

- Home Assistant with Supervisor, on a 64-bit system (`amd64` or `aarch64`, e.g. a
  Raspberry Pi 4/5 running 64-bit Home Assistant OS)
- Home Assistant 2026.9 or newer: the maps load their OpenStreetMap tiles through Home
  Assistant's built-in map tile proxy
- A Tesla account with at least one car
- About 900 MB of disk space for the image (it includes the browser used for signing in)
  and about 300 MB of free memory while you sign in
- For the entities: an MQTT broker (the Mosquitto add-on) and Home Assistant's MQTT
  integration

## Development

No car needed: `tools/mock_tesla.py` is a stand-in for Tesla's servers with one simulated
car that keeps driving, charging and falling asleep, and `tools/seed_demo.py` fills a
database with a year of invented history.

```bash
pip install aiohttp "httpx[http2]" "paho-mqtt>=2.1" ruff

python3 tools/mock_tesla.py &
TESLAMETER_DATA_DIR=/tmp/teslameter python3 tools/seed_demo.py        # optional
TESLA_AUTH_HOST=http://127.0.0.1:9911 TESLA_API_HOST=http://127.0.0.1:9911 \
TESLA_WSS_HOST=ws://127.0.0.1:9911 TESLAMETER_DATA_DIR=/tmp/teslameter \
python3 -m src                                                        # http://localhost:8099
```

Sign in on the dashboard; the simulated sign-in page ends in the same kind of app link as
Tesla's. The sign-in browser needs Chromium and Xvfb on your machine; without a virtual
display, `TESLAMETER_BROWSER_HEADLESS=1` runs it headless (fine for the simulator, refused
by the real Tesla). `./lint.sh` checks formatting and lint; `./lint.sh fix` fixes what it
can.

## Credits

- [Leaflet](https://leafletjs.com/) and [Leaflet.heat](https://github.com/Leaflet/Leaflet.heat) — Interactive maps
- [Chromium](https://www.chromium.org/) — The sign-in browser
- [Tesla Auth](https://github.com/adriankumpf/tesla_auth) — Token app for the alternative way to sign in (a separate program, not part of this app)
- [OpenStreetMap](https://www.openstreetmap.org/) — Map tiles and, through [Nominatim](https://nominatim.org/), addresses

TeslaMeter is an independent project. It is not affiliated with, endorsed by or sponsored
by Tesla, Inc.; "Tesla" is a trademark of Tesla, Inc.
