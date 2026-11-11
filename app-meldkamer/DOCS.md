# Home Assistant App: Meldkamer

Meldkamer decodes Dutch P2000 FLEX pager messages with an RTL-SDR, HackRF or a network
`rtl_tcp` stream and shows them on a live dashboard: a message table, an incident map with
street-level pins, statistics and optional Home Assistant sensors, events and
text-to-speech. It runs entirely on your Home Assistant, served through ingress.

After installing, plug in your SDR receiver, review the **Configuration** tab, start the
app and open it from the sidebar. Fill the reference database (first section below):
without it, messages are decoded but have no discipline, town, street or map pin.

## Getting the most out of it

A fresh install only knows the P2000 abbreviations. Everything else the dashboard shows comes from reference data you import once from the **Data** tab. All imports use free public data, no account needed:

| Step | Where | What it gives you |
|------|-------|-------------------|
| 1. **Import capcodes** | Data → Capcodes → *Import Bommel* | Discipline (Brandweer/Ambulance/Politie), region and the units alerted, for every message. Needed for the Lanes view, discipline colours, vehicle types and for sensors that filter on `disciplines` or `region`. Also fills the Vehicles table from the capcode descriptions |
| 2. **Import all places** | Data → Places → *Import All Places* | All ~2,500 Dutch towns with their centre position. Needed to recognise the town in a message, for `cities` sensor filters, and for a map pin at the town centre when no street is known |
| 3. **Import streets** for your province(s) | Data → Streets → *Import BAG* (per province, 5–10 min for a large one) | Official street names with their position. A message naming one of these streets gets a street-level map pin, distance from home and an exact zone match, **without OpenCage**. Import every province you care about; streets outside them fall back to OpenCage or the town centre |
| 4. *(optional)* **OpenCage** | Add-on configuration → `geocoding` | Street-level positions for streets that aren't imported. The free tier is 2,500 requests a day; receiving the whole country uses more, so set `only_sensor_matches: true` to only look up messages that match one of your sensors |
| 5. **Sensors and home** | Add-on configuration → `sensors`; Home Assistant's home zone | Filtered Home Assistant sensors, events and announcements; distance from home on each message |

Re-run *Import All Places* and *Import BAG* after updates that add data to them (the changelog says when). Re-imports keep your own edits to streets; empty values never wipe stored data.

## How it works

1. **Decoding** — `rtl_fm` (RTL-SDR) or `rx_fm` (HackRF, SoapySDR, `rtl_tcp`) tunes to 169.65 MHz and pipes the audio into `multimon-ng`, which decodes the FLEX pager protocol into one line per message: time, capcodes and text.
2. **Filtering** — Messages matching an *Ignore text* pattern, or whose capcodes are all on the *Ignore capcodes* list, are dropped. An identical text received again within `repeat_window_seconds` counts as a repeat of the first message instead of a new one.
3. **Parsing** — From the text:
   - **Priority** from codes such as `A1`, `A2`, `B1`, `P 1`, `PRIO 2`.
   - **Town**: the last words of the message are looked up in the Places table (including multi-word names such as "Koog aan de Zaan") and in the Place codes table of P2000 town abbreviations (e.g. `OEGSTG` → Oegstgeest). A province code right after the town ("Velp NB") or in the abbreviation (`VELPNB`, `RIJSZH`) picks the town in that province, from the Province codes table; it goes before the province of the capcode's region. If that finds nothing, the whole text is searched.
   - **Street**: the remaining text is matched against the Streets table (longest names first, whole words only), then against address patterns with a postcode, then against common street-name endings (`-straat`, `-weg`, `-laan`, …).
   - **Capcodes** are looked up in the Capcodes table for the discipline, region, location and the names of the alerted units.
   - **Abbreviations** are explained from the Abbreviations table, and **vehicles** recognised from the Vehicles table or from the text ("TS 07-1782").
   - **Incident type** from the text (the Incident types table): fire, automatic alarm (OMS), traffic accident, medical (reanimatie/AED), water, gas/smell, lift, storm, animal, assistance, GRIP. Also the **reference numbers** (rit, bon, icnum), the meldkamer **talk group** ("BAD-02", see [Talk groups and place codes](#talk-groups-and-place-codes)) and "Directe inzet: ja". A vehicle named with its type ("ambu 16-107") gives the region when the capcodes don't.
4. **Location** — The first source that knows the address wins:
   1. the position of a full postcode in the message ("3068HA" covers a few houses), looked up once at the PDOK Locatieserver, free, and kept in the geocode cache;
   2. the street's position in the **Streets** table (street + town, from the BAG import or entered by you);
   3. the **geocode cache** of earlier OpenCage results;
   4. **OpenCage**, when `geocoding` is enabled (with `only_sensor_matches`, only for messages a sensor can match);
   5. the town centre from the **Places** table (marked as city-level).

   Distance from home is only given for street-level positions, so a message placed at a town centre doesn't show that centre's distance.
5. **Incidents** — Messages for the same address within `incident_window_minutes` form one incident. It escalates when the GRIP level or priority goes up, or more units or disciplines are alerted.
6. **Output** — Every message is stored (shown in the dashboard and its history), matched against your sensors (all of a sensor's filters must match) and published to Home Assistant through the REST API and/or MQTT. With `ha_events` a `meldkamer_message` event is fired; with `announce` configured, messages of sensors with `announce: true` are spoken on your speakers.

All data lives in one SQLite database in the add-on's `/data` folder, so it is kept across updates and included in Home Assistant backups.

## Network

| Network | Frequency | Protocol |
|---------|-----------|----------|
| P2000 FLEX | 169.65 MHz | FLEX |

## Web Dashboard

Access via Home Assistant sidebar → Meldkamer:

- **Console / Wallboard / Lanes** — Three live views of the same feed, switched with tabs in the top bar:
  - **Console** — dense message table, with the map and the selected message's full detail (vehicle photo, incident timeline, earlier messages at the address) docked on the right. A search box (press `/`) searches all stored messages, the P1/P2/P3+ and discipline chips narrow the list, and "Older messages" at the bottom loads earlier ones from the database
  - **Wallboard** — large map with live counts, incidents sorted by priority and a ticker of the newest messages; for an always-on screen
  - **Lanes** — one column per discipline (Brandweer, Ambulance, Politie, other) next to the map

  The message detail shows the full message, abbreviations, vehicle photo, incident timeline and earlier messages at the address, with Previous/Next to step through the list (or `j`/`k` and the arrow keys; `1`/`2`/`3` switch views, Escape closes the floating detail). Console docks it under the map; on the Wallboard and Lanes it floats in the map's corner. Drag the dividers between the panes to resize them (double-click resets). The sensor filter, sound, speech, browser-notification and auto-focus toggles sit in the top bar. Browser notifications (💬, asks the browser for permission once) show new messages of the current feed while the dashboard is in the background; clicking one opens that message. The map shows your home (from Home Assistant's home zone) and the zones your sensors filter on. A message's detail lists which of your sensors it matched, and selecting a message puts `#msg=<id>` in the URL: open the dashboard with that hash (for example from a notification, see the `dashboard_url` event field) and it shows that message, also one that has already left the live list. The theme follows Home Assistant's light/dark mode; the 🌓 button switches between automatic, light and dark.
- **Data** — Imports, the tables below, backup and restore of the whole database, and the database reset (the add-on's own options are on its Configuration tab in Home Assistant)
- **Info** — Receiver, device, locations and database status, reception quality, statistics (messages per hour and the busiest towns of the last 24 hours, totals by region and discipline) and an overview of your sensors with their filters, matches and last match

### Data Tab

A fresh install starts with an **empty** database (aside from the built-in P2000 abbreviation dictionary and TTS/ignore-text defaults) — message parsing, badges, geocoding, and map pins all improve as you populate it. Capcodes, Places, and Streets are one-click imports that fetch live, free, public Dutch data over the internet, no API key needed:

- **Capcodes** — "Import Bommel" pulls ~10,000 capcodes from all 25 regions directly from [p2000.bommel.net](https://p2000.bommel.net/)
- **Places** — "Import All Places" / "Import BAG" pull towns and their centre positions from the [PDOK Locatieserver](https://www.pdok.nl/) (Dutch government address/place registry)
- **Streets** — "Import BAG" pulls official street names and their positions per province, also from PDOK. A message with a known street is placed on that street without an OpenCage request; OpenCage (when enabled) is only used for streets that aren't in the table. Add or edit a street's `latitude`/`longitude` yourself, or import them with the optional `latitude`,`longitude` CSV columns

Each table gets its own card with row counts and Manage/Import actions:

| Table | Description | Import Options |
|-------|-------------|----------------|
| 📟 Capcodes | Emergency service identifiers | CSV, Bommel |
| 🏙️ Places | Town recognition and town-centre positions | CSV, BAG, all-places |
| 🛤️ Streets | Street recognition and street positions | CSV, BAG |
| 📖 Abbreviations | P2000 abbreviation dictionary | Built-in, CSV |
| 📨 Messages | Received P2000 messages | — (filled automatically) |
| 📍 Geocodes | OpenCage address lookup cache | — (filled automatically) |
| 🗣️ Replacement rules | Regex rules for speakable text-to-speech | CSV |
| 📻 Talk groups | Discipline and meldkamer/region letters of talk group codes (BRT-01) | Built-in, CSV |
| 🏷️ Place codes | Place-name codes in messages (UTRECH → Utrecht) | Built-in, CSV |
| 🗺️ Province codes | The province after a town name ("Velp NB", VELPNB → Noord-Brabant) | Built-in, CSV |
| 🏷️ Incident types | Patterns that give a message its incident type (fire, traffic, …) | Built-in, CSV |
| ❗ Priority rules | Patterns that give a message its priority (A1, P 1, PRIO 1 → 1) | Built-in, CSV |
| 🔤 Address words | Street endings and words that are no street or town, for the address search | Built-in, CSV |
| 🚑 Discipline meanings | Codes that mean something else per discipline (B1 for the ambulance) | Built-in, CSV |
| 🚒 Vehicle type codes | Type codes in messages (TS, AL-42, AMBU) and their vehicle category | Built-in, CSV |
| 🔢 Vehicle regions | Safety region of a vehicle number's prefix (17 → Rotterdam-Rijnmond) | Built-in, CSV |
| 🗾 Region provinces | Province of each safety region | Built-in, CSV |
| 🙈 Ignore text | Wildcard patterns to drop matching messages | CSV |
| 🚫 Ignore capcodes | Capcodes to drop | CSV |
| 📷 Vehicle photos | Photos you've uploaded per vehicle number or category | — (upload via message detail or +Add) |
| 🚒 Vehicles | What each vehicle number is (type, description) | Capcode list, messages naming a vehicle ("TS 07-1782"), +Add, CSV import |

Click "Manage" on any card to view/edit/delete that table's rows.

**Backup and restore** — "Download backup" saves the whole database (messages, imports, your edits, filters and photos) as one `.sqlite3` file, taken as a consistent snapshot while the add-on keeps running. "Restore from backup" replaces the database with such a file, for example when moving to new hardware; the file is checked before anything is replaced. Home Assistant's own backups include the database as well. The **Danger zone** at the bottom holds "Reset database".

## Configuration

### Receiver Settings

| Option | Description |
| ------ | ----------- |
| `receiver.type` | Receiver type: `rtl-sdr`, `hackrf`, `soapysdr`, `network` |
| `receiver.network_host` | IP address for network receiver (e.g., `192.168.1.100`) |
| `receiver.network_port` | Port for network receiver (default: `1234`) |
| `receiver.sdr_gain` | SDR device gain (0-100, for RTL-SDR) |
| `receiver.ppm_correction` | Frequency correction in PPM for crystal oscillator drift (see [Reception Quality](#reception-quality)) |
| `receiver.bias_tee` | Power an active antenna or LNA over the coax (`rtl-sdr` only, e.g. RTL-SDR Blog V3/V4) |
| `receiver.offline_after_minutes` | Minutes without FLEX frames before the receiver counts as offline and the decoder fails over / restarts (default: `10`) |
| `receiver.backup_host` | Optional backup `rtl_tcp` server, used when the active receiver goes quiet |
| `receiver.backup_port` | Port of the backup `rtl_tcp` server (default: `1234`) |
| `receiver.backup_ppm_correction` | PPM correction of the backup receiver's stick |

**Receiver Types:**
- **rtl-sdr** — RTL-SDR dongle, no device probing (uses `rtl_fm`) — default
- **hackrf** — HackRF One (uses `rx_fm` with HackRF driver)
- **soapysdr** — SoapySDR auto-detect (uses `rx_fm`)
- **network** — Connect to remote `rtl_tcp` server

### HackRF Settings

The HackRF One has three separate gain stages:

| Option | Range | Description |
| ------ | ----- | ----------- |
| `hackrf_lna_gain` | 0-40 dB | Low Noise Amplifier gain (steps of 8 dB) |
| `hackrf_vga_gain` | 0-62 dB | Variable Gain Amplifier (steps of 2 dB) |
| `hackrf_amp_enable` | true/false | Enable RF amplifier (+14 dB when enabled) |

**Recommended P2000 Settings:**
```yaml
receiver:
  type: hackrf
  hackrf_lna_gain: 40
  hackrf_vga_gain: 30
  hackrf_amp_enable: true
```

**Tuning Tips:**
- Start with recommended settings and adjust if needed
- Increase VGA gain if signal is weak but clean
- Reduce gains if you see overload/distortion
- Use `ppm_correction` if messages aren't decoding

### Network Receiver Setup

To use a remote RTL-SDR via network:

1. On the remote machine:
   ```bash
   rtl_tcp -a 0.0.0.0 -p 1234
   ```

2. In Meldkamer config:
   ```yaml
   receiver:
     type: network
     network_host: "192.168.1.100"
     network_port: 1234
   ```

### Receiver Failover

With `backup_host` set, the decoder watches for FLEX frames. When the active receiver produces none for `offline_after_minutes` (or its pipeline keeps failing), it switches to the other one, and back again if that one goes quiet too. Without a backup it restarts the active receiver instead. The active receiver is shown on the Info tab.

```yaml
receiver:
  type: rtl-sdr
  backup_host: "192.168.1.100"
  backup_port: 1234
  offline_after_minutes: 10
```

### Reception Quality

The Info tab shows FLEX frames per minute for the last hour, messages per hour, bit errors fixed by error correction per frame, failed decodes and lost synchronisations. Fewer corrected bits and failed decodes mean a cleaner signal, which helps when tuning gain, ppm and the antenna. `rtl_fm` and `rx_fm` don't report an RF signal level, so none is shown.

### Geocoding

| Option | Description |
| ------ | ----------- |
| `geocoding.enabled` | Enable/disable geocoding |
| `geocoding.opencage_token` | OpenCage API key for address lookup |
| `geocoding.only_sensor_matches` | Only call OpenCage for messages that match a sensor (default: `false`). Other messages use cached coordinates, or the city-level position from the Places table — on the map, in `meldkamer_message` events and without a distance from home. Saves OpenCage requests when receiving the whole national network. A zone filter also counts messages up to 2 km outside the zone (and messages whose city has no coordinates), as the street may lie inside it. The Info tab shows how many messages were skipped. |

### Filters

Global message filters are managed live from the dashboard's **Data** tab (no restart needed) rather than the add-on config:

- **Ignore text** — wildcard patterns (e.g. `*TESTOPROEP*`); a message is dropped if its body matches any enabled pattern. Seeded by default with `*TESTOPROEP*` and `*MOB*` (system test calls and mobilofoon checks).
- **Ignore capcodes** — exact capcodes to drop; a message is only dropped when **every** capcode on it is on this list (so a single ignored capcode can't kill a multi-capcode group call).

Both support add/edit/delete/enable-toggle/export from their own "Manage" page, the same way Replacement rules does.

### Messages

| Option | Description |
| ------ | ----------- |
| `repeat_window_seconds` | An identical message received again within this many seconds counts as a repeat (shown as 🔁 on the dashboard) instead of a new message. `0` disables it (default: `60`) |
| `incident_window_minutes` | Messages for the same street and city within this many minutes of each other form one incident (default: `15`) |

On the dashboard, an incident's messages are shown as one card (🚨 with the message count); its details list every message. When a new message arrives for an incident on screen, a toast alerts you.

### Sensor Integration

Meldkamer can create Home Assistant sensors that automatically update when P2000 messages match your filters. Sensors are published directly via the Home Assistant REST API — **no MQTT broker or credentials needed!**

An optional MQTT publishing path (see [MQTT](#mqtt) below) can run alongside the REST API sensors.

1. Enable `Sensors Enabled` in the app configuration
2. Define sensors in the `Sensors` section
3. Sensors appear automatically in Home Assistant on startup

```yaml
sensors_enabled: true

sensors:
  # All messages in your area
  - name: "Omgeving"
    icon: "mdi:radio-tower"

  # Fire department in specific cities
  - name: "Brandweer Oegstgeest"
    disciplines: "Brandweer"
    cities: "Oegstgeest, Leiden"

  # Ambulance calls inside your home zone, announced on your speakers
  - name: "Ambulance Nearby"
    disciplines: "Ambulance, Ambulancezorg"
    zone: zone.home
    announce: true

  # Specific station capcodes
  - name: "My Fire Station"
    capcodes: "002029583, 001520021"

  # Urgent incidents inside a zone you created in Home Assistant
  - name: "Schiphol Airport"
    icon: "mdi:airplane"
    zone: zone.schiphol
    priorities: "P1, A1"

  # High priority alerts
  - name: "Priority 1 Alerts"
    priorities: "P1, A1"
```

> [!NOTE]
> Entity IDs are auto-generated from sensor names: lowercase ASCII letters, digits and underscores (accents are transliterated). Example: "Ambulance Nearby" → `sensor.ambulance_nearby`, "Café IJsselmonde" → `sensor.cafe_ijsselmonde`. A sensor whose name yields no letters/digits, or whose ID duplicates another sensor's, is skipped with an error in the log.

> [!IMPORTANT]
> All filter fields use **comma-separated strings** (e.g., `"Ambulance, Brandweer"`). Leave fields empty if not needed.

### Sensor Filter Types

All filters within a sensor use **AND** logic — message must match ALL filters.

| Filter | Type | Description | Example |
|--------|------|-------------|---------|
| `disciplines` | string | Comma-separated disciplines | `"Ambulance, Brandweer"` |
| `cities` | string | Comma-separated cities (case-insensitive) | `"Oegstgeest, Leiden"` |
| `capcodes` | string | Comma-separated capcodes (leading zeros optional) | `"002029583, 001520021"` |
| `priorities` | string | Comma-separated priorities: `P1`/`A1` = 1, `P2`/`A2` = 2, `P3`/`B1`–`B3` = 3, `P4` = 4 (a plain number works too). For GRIP use `text_contains: "GRIP"` | `"P1, A1"` |
| `zone` | string | Only messages inside this Home Assistant zone (its location and radius). Create zones under Settings → Areas, labels & zones → Zones | `zone.home` |
| `text_contains` | string | Keyword search in message body | `"woningbrand"` |
| `incident_types` | string | Only messages of these types (comma-separated): `fire`, `alarm`, `traffic`, `medical`, `water`, `gas`, `lift`, `storm`, `animal`, `assist`, `grip` |
| `icon` | string | MDI icon override | `"mdi:fire-truck"` |
| `announce` | bool | Speak matching messages on Home Assistant speakers (see [Announcements](#announcements)) | `true` |

### Sensor State & Attributes

**State**: The spoken form of the message (TTS replacement rules applied), truncated to 255 characters (Home Assistant's state limit); the full text is in the `tts` and `body` attributes
```
Ambulance zonder spoed HA Doude v Troostwijk/Goeskens Frankenslag Den Haag
```

**Attributes** (available in automations):
```yaml
friendly_name: "Ambulance Nearby"
icon: "mdi:radio-tower"
timestamp: "2026-02-06T14:16:57"
priority: "A2"
discipline: "Ambulance"
region: "Haaglanden"
capcodes: ["002029583", "001520021"]
address: "Frankenslag, 2582 's-Gravenhage, Netherlands"
street: "Frankenslag"
city: "'s-Gravenhage"
postalcode: "2582"
latitude: 52.0973458
longitude: 4.2776374
location_accuracy: "street"
distance_km: 3.42        # from Home Assistant's home zone
distance: 1.27           # from the centre of this sensor's zone
incident_id: "3f9c2a1b7d4e"
tts: "Ambulance zonder spoed HA Doude v Troostwijk/Goeskens Frankenslag Den Haag"
```

### Receiver Health Sensors

With `sensors_enabled`, Meldkamer also publishes (every minute, and over MQTT with discovery when MQTT is enabled):

| Entity | Description |
|--------|-------------|
| `binary_sensor.meldkamer_receiver_online` | `on` while FLEX frames arrive, `off` after `receiver.offline_after_minutes` without any |
| `sensor.meldkamer_messages_per_hour` | Messages decoded in the last hour |

### Home Assistant Events

With `ha_events: true` (default) Meldkamer fires on the Home Assistant event bus:

- **`meldkamer_message`** for every new message, with all message fields (`body`, `tts`, `priority`, `discipline`, `capcodes`, `region`, `city`, `street`, `address`, `latitude`, `longitude`, `distance_km`, `incident_id`, …) plus `incident_type`/`incident_label`, `ride_number`, `bon_number`, `incident_number`, `talk_group`, `talk_group_name`, `direct_deployment`, `sensors`, the names of the sensors it matched, and `dashboard_url`, a Home Assistant path (`/hassio/ingress/<addon>#msg=<id>`) that opens the dashboard on that message, for use as the `url` of a notification.
- **`meldkamer_incident_escalated`** when a later message for an incident raises the GRIP level or priority, or alerts more units or disciplines: `incident_id`, `address`, `disciplines`, `units`, `priority`, `grip`, `message_count`, `reasons` and the new message's `body`.

```yaml
automation:
  - alias: "P2000 GRIP nearby"
    triggers:
      - trigger: event
        event_type: meldkamer_incident_escalated
    conditions:
      - condition: template
        value_template: "{{ trigger.event.data.grip >= 1 }}"
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "GRIP {{ trigger.event.data.grip }}"
          message: "{{ trigger.event.data.address }}: {{ trigger.event.data.reasons | join(', ') }}"
```

```yaml
automation:
  - alias: "P2000 P1 within 5 km"
    triggers:
      - trigger: event
        event_type: meldkamer_message
    conditions:
      - condition: template
        value_template: >
          {{ trigger.event.data.priority == 1 and trigger.event.data.distance_km is not none
             and trigger.event.data.distance_km < 5 }}
    actions:
      - action: notify.mobile_app_phone
        data:
          message: "{{ trigger.event.data.tts }}"
```

### Announcements

Speak messages on Home Assistant speakers with `tts.speak`, using the same TTS replacement rules as the dashboard. Only messages matching a sensor with `announce: true` are spoken.

```yaml
announce:
  tts_entity: tts.google_translate_nl_nl
  media_players: "media_player.kitchen, media_player.living_room"
  language: nl
  quiet_hours: "23:00-07:00"
sensors:
  - name: "Thuis"
    zone: zone.home
    announce: true
```

`quiet_hours` (optional, local time, may cross midnight) silences the speakers between those times. The dashboard follows it too: no sound, browser speech or browser notifications during quiet hours (the toggles dim and say so).

### Talk groups and place codes

A message names its C2000 talk group as letters, a dash and a number, e.g. `BRT-01`: the first letter is the discipline (B Brandweer, A Ambulancezorg, P Politie, M Koninklijke Marechaussee, D Defensiediensten, S Multidisciplinaire samenwerking), the next two the meldkamer or region (RT Rotterdam, DH Den Haag, AD Amsterdam, MD Midden-Nederland, …), and the number the talk group's quick-dial number. The spoken text says it as "gespreksgroep Brandweer Rotterdam 0 1", the message detail shows "Gespreksgroep Brandweer Rotterdam 01" (also as `talk_group_name` in events), and a code whose letters aren't known is spelled out ("B X X 0 1"), never guessed. Source: Brandweer Nederland / NIPV, [Nota van toelichting Standaard Presentatietekst Gespreksgroep in GMS pagertekst](https://nipv.nl/documenten/nota-van-toelichting-standaard-presentatietekst-gespreksgroep-in-gms-pagertekst) (25 January 2019).

The letters are in the **Talk Groups** table and the place-name codes (`UTRECH` → Utrecht, used to find the town, as tags and in the spoken text) in the **Place Codes** table, both on the Data tab: add, edit, delete, export and import CSV (the same files as the FireServiceRota app). Both are filled once with the built-in list; a row you change keeps your text and a row you delete stays deleted, also after updates. The default TTS rules work the same way since 1.4.13. Backups include both tables.

**Province codes** (`NB` → Noord-Brabant, `GLD` → Gelderland, …) are in the **Province codes** table on the Data tab, added once like the others. A code marked `abbreviation_only` (`L` → Limburg) only counts in a place code ("Beek L"), not as a word after a town in the message. The spoken text keeps the province: `VELPNB` is said as "Velp Noord-Brabant".

### Map Card

The sensors carry `latitude`/`longitude` attributes, so Home Assistant's standard Map card shows where their latest message was:

```yaml
type: map
title: P2000
hours_to_show: 6
entities:
  - entity: sensor.thuis
  - entity: sensor.brandweer
  - entity: zone.home
```

### MQTT

An optional MQTT publishing path can be enabled alongside (or instead of) the REST API sensors above. It reuses the same `sensors` list/filters.

```yaml
sensors_enabled: true
sensors:
  - name: "P2000 Brandweer"
    disciplines: "Brandweer"
    # Optional: a stable identifier for this sensor's MQTT topics/unique_id.
    # Falls back to the auto-generated entity_id if not set.
    mqtt_id: "2005"

mqtt:
  enabled: true
  host: ""              # leave empty to auto-discover the Mosquitto add-on via Supervisor
  port: 1883
  user: ""
  password: ""
  client_id: p2000_rtlsdr
  base_topic: p2000_rtlsdr
  ha_autodiscovery: true
  ha_autodiscovery_topic: homeassistant
  retain: false
  tls_enabled: false
```

| Option | Description |
| ------ | ----------- |
| `mqtt.enabled` | Enable MQTT publishing (additive to REST API sensors) |
| `mqtt.host` / `mqtt.port` | Broker address. Leave `host` empty to auto-discover the Home Assistant Mosquitto add-on |
| `mqtt.user` / `mqtt.password` | Broker credentials, if required |
| `mqtt.client_id` | MQTT client id |
| `mqtt.base_topic` | Availability topic prefix (`{base_topic}/status`) |
| `mqtt.ha_autodiscovery` | Publish Home Assistant MQTT discovery config for each sensor |
| `mqtt.retain` | Retain state/attribute messages on the broker |
| `mqtt.tls_enabled` | Enable TLS to the broker (see `tls_ca`, `tls_cert`, `tls_keyfile`, `tls_insecure`). Put your own certificate files in Home Assistant's `/ssl` folder and use those paths, e.g. `/ssl/ca.crt` |

> [!NOTE]
> State/attribute/discovery topics are published under `homeassistant/sensor/p2000_rtlsdr/...` (this prefix is fixed, independent of `base_topic`). The sensor's `mqtt_id` (or its auto-generated `entity_id` if not set) forms the last topic segment and the discovery `unique_id`.

### Automation Examples

#### Notification for nearby ambulance calls

```yaml
automation:
  - alias: "P2000 Ambulance Alert"
    triggers:
      - trigger: state
        entity_id: sensor.ambulance_nearby
    conditions:
      - condition: template
        value_template: "{{ trigger.to_state.state != trigger.from_state.state }}"
    actions:
      - action: notify.mobile_app
        data:
          title: "🚑 Ambulance Alert"
          message: "{{ trigger.to_state.state }}"
```

#### Persistent notification with address details

```yaml
automation:
  - alias: "P2000 Station Alert"
    triggers:
      - trigger: state
        entity_id: sensor.my_fire_station
    actions:
      - action: persistent_notification.create
        data:
          title: "🚒 Station Called Out"
          message: >
            **{{ trigger.to_state.attributes.discipline }}** — {{ trigger.to_state.attributes.priority }}
            📍 {{ trigger.to_state.attributes.address }}
            {{ trigger.to_state.state }}
```

### Other Settings

| Option | Description |
| ------ | ----------- |
| `log_level` | Logging verbosity: `debug`, `info`, `warning`, `error` |


### Lists in the database

Everything the app looks up while reading a message is a table on the Data tab, not a list in the program: abbreviations, TTS rules, ignore texts, talk groups, place codes, province codes, incident types, priority rules, address words, discipline meanings, vehicle type codes, vehicle regions and region provinces. Each one can be searched, added to, edited, deleted, exported and imported as CSV, and is in backups. The built-in rows are added once, at the first start of the version that brings them: a row you change keeps your text and a row you delete stays deleted, also after updates. A change is used right away, without a restart.

The built-in rows ship with the app as CSV files, one per table, in the same format as the Data tab's export: `src/defaults/<table>.csv` ([on GitHub](https://github.com/cyberjunky/home-assistant-apps/tree/main/app-meldkamer/src/defaults)). They are only read to fill the tables at the first start of a version; after that the database is the only source. `previous.csv` lists values older versions shipped: a row still at such a value gets the new one (a row you changed is left alone). *Import Texts* on the Abbreviations page reads `abbreviations.csv` again.

Spoken text defaults added in 1.4.16 (TTS rules and abbreviations, with their sources):

- `Li` / `Re` right after a hectometer marker (`hmp 45,2 Li`, `A12 Li 45,2`) is spoken "rijbaan links" / "rijbaan rechts": the carriageway, Re where the hectometers go up and Li where they go down (Rijksoverheid, "doel hectometerpaaltjes snelwegen"; Rijkswaterstaat, "voorwerp dier schade melden"). Elsewhere Li and Re stay as written.
- `OM` is spelled out ("O M"): it can be Openbaar Ministerie or the word "om".
- `PM` means "Personeel- en materieelvoertuig" (the number after it is the number of seats; PM-VEB and the like are specialisations), from [112meldingen.nl](https://112meldingen.nl/afkortingen), [112hub.nl](https://112hub.nl/informatie/afkortingen-en-termen) and [tss2000.nl](https://tss2000.nl/afkorting-termen-p2000-112-meldingen). An unchanged old meaning ("Personeel / Materieelwagen") is updated once.

Some things stay in the program because they are rules of the format or of the app rather than lists: the shapes of codes and numbers (talk group `XYY-NNNN`, vehicle numbers, postal codes, times), the letters Dutch postal codes never use (SA, SD, SS), the 12 provinces of the BAG import (PDOK's names), the icons and stock photos of the vehicle categories (with the two description patterns that pick a category from a capcode description), and B1/B2 on an ambulance message being spoken as "Ambulance besteld vervoer".

## Ports

| Port | Purpose |
|------|---------|
| 5000 | TCP — Integration connection |
| 8099 | HTTP — Web dashboard (ingress) |

## Requirements

- Home Assistant with Supervisor, on a 64-bit system (`amd64` or `aarch64`, e.g. a Raspberry Pi 4/5 running 64-bit Home Assistant OS)
- RTL-SDR, HackRF One, or network access to rtl_tcp server
- For zone filters, `distance_km`, events, announcements and health sensors: the add-on's Home Assistant API access (enabled by default)
- Home Assistant 2026.9 or newer: the dashboard map loads its OpenStreetMap tiles through Home Assistant's built-in map tile proxy

## Credits

- [multimon-ng](https://github.com/EliasOenal/multimon-ng) — FLEX decoder
- [rtl-sdr](https://osmocom.org/projects/rtl-sdr) — RTL-SDR drivers
- [SoapySDR](https://github.com/pothosware/SoapySDR) — SDR abstraction layer
- [Leaflet](https://leafletjs.com/) — Interactive maps
- [OpenStreetMap](https://www.openstreetmap.org/) — Map tiles
- [Bommel](https://p2000.bommel.net/) — P2000 Capcode Database
- [BAG](https://bag.basisregistraties.nl/) — Basisregistratie Adressen en Gebouwen
- [PDOK Locatieserver](https://www.pdok.nl/) — Dutch address/place lookup for BAG imports

