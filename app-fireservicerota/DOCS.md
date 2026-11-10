# Home Assistant App: FireServiceRota

> **Unofficial.** An independent project, not affiliated with, endorsed by or sponsored by
> FireServiceRota or Brandweerrooster.

A dashboard for Home Assistant's [FireServiceRota integration](https://www.home-assistant.io/integrations/fireservicerota/)
(FireServiceRota in the UK, Brandweerrooster in the Netherlands). It shows the incident
you are called for on a map, with a countdown to the end of the response window and
buttons to acknowledge or reject it. It shows whether you are on duty, and keeps every
incident, your responses and your duty in its own history, with statistics.

The app reads everything from Home Assistant. It never contacts FireServiceRota or
Brandweerrooster itself, and needs no account or password of its own.

## Before you start

Set up the FireServiceRota integration in Home Assistant (Settings → Devices & services
→ Add integration → FireServiceRota). It creates three entities:

| Entity | What it is |
|--------|------------|
| `sensor.<name>_incidents` | The latest incident: its text as the state, the rest as attributes |
| `binary_sensor.<name>_duty` | On while you are on duty, with your shift and function |
| `switch.<name>_incident_response` | Your answer to the incident: on is acknowledged, off is rejected |

Then install this app, start it and open **FireServiceRota** from the sidebar. It finds
the three entities by itself and imports their history from Home Assistant's recorder.

## How it works

1. **Finding the entities.** At start the app reads Home Assistant's entity registry and
   takes the three entities of the FireServiceRota integration. With more than one
   FireServiceRota account it follows the first; set the entity options to pick another.
2. **Live.** It follows the entities over Home Assistant's websocket. A new incident,
   an update of an incident, your answer and a change of duty show on the dashboard
   within a second. When the connection drops, the app reconnects by itself and reads
   what it missed.
3. **History.** At the first start it imports the last `history_days` days from Home
   Assistant's recorder (the recorder keeps 10 days unless you changed that). After
   that, each start only reads the gap since the last change it has. An import can run
   any number of times: nothing is stored twice.
4. **Responding.** Acknowledge and Reject switch the integration's response switch on or
   off through Home Assistant (`switch.turn_on` / `switch.turn_off`). The integration
   sends your answer to FireServiceRota.
5. **Storing.** Every incident (by its FireServiceRota id), every update of it, every
   change of the response switch and every change of duty goes into the app's own
   database. It keeps them after Home Assistant's recorder has purged them.

### What the integration gives, and what that means here

- **Incident text.** Home Assistant can store at most 255 characters in a state. A
  longer incident text is lost there, so the app shows "text not available" for it; all
  the other fields are still there.
- **Updates.** An incident can come again with `trigger: update` (a new text, a new
  state). The app keeps it as one incident and shows the updates on its timeline.
- **The response switch** goes back to *unknown* for each new incident: that is
  shown as *no response yet*. It is unavailable while you are off duty, and FireServiceRota
  only takes an answer until `can_respond_until`, usually about 30 minutes after the call.
  The buttons are greyed out, with the reason, outside that window.
- **Which incident an answer belongs to.** The switch doesn't say. The app links an
  answer to the incident that was showing at that moment, and ignores an answer given
  before the incident was created (the switch may still show the previous one's).

## The dashboard

The tabs in the top bar:

- **Console**: the current incident on top, with the countdown and the Acknowledge and
  Reject buttons. Below it the table of incidents with their response. Search (press `/`),
  and narrow it with the priority, type and response chips. *Older incidents* loads more.
  Click an incident to see it on the map and in full under the map: all its fields, a
  player for the spoken message, and the timeline of its updates and your responses.
  `j`/`k` or the arrow keys step through the list.
- **Wallboard**: for a screen that is always on. The current incident with its details
  (alarm and deadline time, status, responder mode, tasks, who responds and whether you
  were available at the alarm), the countdown with a bar that runs out, the buttons and
  your duty. Below that a chart of incidents per hour over the last 48 hours, by
  priority, and the last 10 calls; click one to open it on the Console. The map zooms in
  to street level on the incident (zoom out by hand and it remembers that). It fits a
  1024x600 screen (the size of a Nest Hub) and 1280x800 tablets. Open the app with
  `#tab=wallboard` at the end of the address to start on it.

  After you acknowledge or reject, the countdown keeps running, in grey: it is the time
  you can still change your answer. Your answer is outlined; press the other button to
  change it.
- **Stats**: for 7, 30 or 90 days, a year or everything: incidents per day, week or
  month, by type and priority, the busiest hours and weekdays, how often you acknowledged,
  rejected or didn't answer, the median time to answer, and your hours on duty with the
  duty periods.
- **Data**: export each table as CSV, import the history again, download or restore a
  backup of the database, and reset it.
- **Info**: the connection to Home Assistant, the entities the app follows and their
  state, the history import, events and announcements, and the database.

The top bar shows whether you are on duty. The buttons next to it switch the sound (three
beeps on a new incident), your browser reading the incident aloud, browser notifications
while the dashboard is in the background, opening a new incident on the map, and the
theme. The theme follows Home Assistant's light or dark mode; 🌓 switches between
automatic, light and dark.

Acknowledge and Reject always ask for a confirmation first.

**The spoken message.** FireServiceRota makes a sound file of each incident
(`message_to_speech_url`). The player in the detail and on the Wallboard lets your browser
play it from FireServiceRota when you press play. The app itself never downloads it.

Selecting an incident puts `#incident=<id>` in the address. Open the dashboard with that
(for example from a notification, see `dashboard_url` below) and it shows that incident.

## Configuration

| Option | Default | Description |
|--------|---------|-------------|
| `incidents_entity` | empty | The incidents sensor. Empty: found by itself |
| `duty_entity` | empty | The duty binary sensor. Empty: found by itself |
| `response_entity` | empty | The incident response switch. Empty: found by itself |
| `history_days` | `30` | Days of recorder history to import at the first start (0 to 365) |
| `ha_events` | `true` | Fire `fireservicerota_app_incident` events |
| `announce` | off | Speak new incidents on your speakers, see below |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

### Home Assistant events

With `ha_events: true` the app fires **`fireservicerota_app_incident`** for every new
incident and every update of one, with the incident's fields: `id`, `body`, `prio`, `type`,
`trigger`, `state`, `responder_mode`, `created_at`, `can_respond_until`, `latitude`,
`longitude`, `address_type`, `formatted_address`, `message_to_speech_url`, `task_ids`, plus
`update` (true for an update), `distance_km` from your home and `dashboard_url`, a Home
Assistant path (`/hassio/ingress/<app>#incident=<id>`) that opens the dashboard on that
incident. Incidents older than an hour (for example after a restart) don't fire one.

```yaml
automation:
  - alias: "FireServiceRota: notify with a link to the dashboard"
    triggers:
      - trigger: event
        event_type: fireservicerota_app_incident
        event_data:
          update: false
    actions:
      - action: notify.mobile_app_phone
        data:
          title: "{{ trigger.event.data.prio | upper }} {{ trigger.event.data.type }}"
          message: "{{ trigger.event.data.body }} ({{ trigger.event.data.distance_km }} km)"
          data:
            url: "{{ trigger.event.data.dashboard_url }}"
```

### Announcements

Speak new incidents on Home Assistant speakers with `tts.speak`:

```yaml
announce:
  tts_entity: tts.google_translate_nl_nl
  media_players: "media_player.kitchen, media_player.living_room"
  language: nl
  quiet_hours: "23:00-07:00"
```

The priority and the incident text are spoken. `quiet_hours` (optional, local time, may
cross midnight) silences the speakers between those times. The dashboard follows it too:
no sound, browser speech or browser notifications during quiet hours (the buttons dim
and say so).

## Works alongside your own automations

The app adds a dashboard and history; it doesn't replace the automations on the
integration's documentation page. Keep using them, for example:

- **Lights on an incident**: trigger on the incidents sensor (or on
  `fireservicerota_app_incident`) and switch on the hall light.
- **Play the spoken message on a speaker**: `media_player.play_media` with the
  `message_to_speech_url` attribute of the incidents sensor.
- **A button that acknowledges**: a button or NFC tag that runs `switch.turn_on` on the
  incident response switch. The dashboard shows the answer right away.

The app doesn't create sensors of its own: the integration's entities already are the
sensors.

## Your data

All incidents, responses and duty changes live in one SQLite database in the app's
data folder, so they are kept across updates and included in Home Assistant backups.
The app reaches only Home Assistant (through the Supervisor) and, for the dashboard map,
OpenStreetMap tiles through Home Assistant's map tile proxy. No telemetry.

## Requirements

- Home Assistant 2026.9 or newer, with the FireServiceRota integration set up. The map
  loads its tiles through Home Assistant's map tile proxy, added in 2026.9.
- The app's Home Assistant API access (on by default).
- The recorder (on by default) for the history import. Without it, the app only has
  what happens while it runs.

## Development

No Home Assistant needed: `tools/mock_ha.py` is a stand-in Supervisor and Home Assistant
with the integration's three entities. You go on and off duty, get an incident every
couple of minutes, and Acknowledge and Reject work. It makes up two weeks of history.

```bash
pip install aiohttp ruff
FSR_MOCK=1 FSR_DATA_DIR=/tmp/fsr python3 -m src     # http://localhost:8099
curl -X POST localhost:8124/mock/incident             # a new incident now
curl -X POST localhost:8124/mock/update               # update the current one
curl -X POST localhost:8124/mock/duty                 # go on or off duty
```

`FSR_MOCK_INCIDENT_SECONDS` and `FSR_MOCK_DUTY_SECONDS` set how often incidents come and
duty changes. `./lint.sh` checks formatting and lint; `./lint.sh fix` fixes what it can.

## Credits

- [FireServiceRota integration](https://www.home-assistant.io/integrations/fireservicerota/) — where all the data comes from
- [Leaflet](https://leafletjs.com/) — Interactive maps
- [OpenStreetMap](https://www.openstreetmap.org/) — Map tiles

This app is an independent project. It is not affiliated with, endorsed by or sponsored
by FireServiceRota or Brandweerrooster; the names are trademarks of their owners.
