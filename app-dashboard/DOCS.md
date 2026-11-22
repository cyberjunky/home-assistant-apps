# Home Assistant App: Dashboard

Dashboard shows your home on a wall tablet in a calm, typographic style, served through ingress: paper and ink, large light numerals, bold names and small wide capitals, with lines instead of cards. Only what you can press gets a box. You make the pages yourself by dragging your entities onto them.

It reads Home Assistant itself (your entities, their areas and the recorder's history) and needs no setup.

## Configuration

```yaml
log_level: info
```

| Option | Default | What it does |
|---|---|---|
| `log_level` | `info` | Amount of logging |

Everything else is set in the app itself, in design mode.

## The first start

The app makes a first dashboard from what it finds: a **Now** page with the clock, the weather, your calendars or thermostat, scenes, a speaker, lights, blinds and power; a **Home** page with the thermostat, temperatures, the forecast and cameras; and an **Energy** page when you have power or energy sensors. It is only a start: change or remove anything.

## Using it

- **Pages**: tap a name on the top bar, or swipe left and right.
- **Tiles, scenes, blinds, the thermostat's − and +, pause and next**: tap them.
- **Hold** a light, blind, fan or speaker for its slider: brightness, position, volume.
- **Tap a reading** (a value, a chart, a bar, a reading on the top bar) for its history: 24 hours, 7 days or 30 days, with the lowest, highest and average. An energy counter shows a bar per day, with the total and the best day.
- **Tap the weather** for the next 24 hours or the days ahead.
- **Tap a camera** for its live picture.

## Design mode

Press the pencil on the top bar. A drawer opens on the left and the page shrinks next to it; **Done** closes it. Every change is saved straight away, and **Undo** (or Ctrl+Z) takes it back.

- **Entities**: search, or pick a kind. Drag an entity onto the page:
  - on an **empty spot** it becomes its own block (a light becomes a tile, a temperature a value, a thermostat a climate block, and so on),
  - on a **block that takes more** (tiles, a list, a chart, bars) it joins that block,
  - on the **top bar** it becomes a reading that shows on every page.
- **Blocks**: drag an empty block onto the page and then entities onto it.
- **A block**: click it. Change what it is shown as, its label, the names of its entities on this dashboard, and its own settings. Under **Entities** you can search for an entity and click it to add it to the block; the search shows only what that block takes. Drag it to move it, drag the round handle at its corner to size it, or use the arrow keys (with Shift to size). Delete removes it.
- **Pages**: name, order, copy, delete and add pages; the readings on the top bar; and the grid. Every page fills the screen, so more columns and rows only place things more precisely.
- **Style**: the look of every page. See below.
- **Tablet**: what the tablet does by itself. See below.

### The blocks

| Block | For | What it shows |
|---|---|---|
| Clock | | The time, the date and the week number |
| Heading | | A title and a line of text |
| Value | anything | One reading, large |
| Weather | a weather entity | Now, and the days or hours ahead if you like |
| Tiles | lights, switches, fans, scenes, scripts, blinds | A tile to tap for each; hold for the slider |
| Slider | lights, blinds, fans, speakers, numbers | Brightness, position, speed, volume, a value |
| Climate | a thermostat | The temperature now, the target, down and up |
| Blinds | covers | Up and down per blind, or two large buttons for all |
| Actions | scenes, scripts, buttons, automations | A large row for each |
| List | anything | Names and their states |
| Media | a media player | What is playing, with its cover, pause and next |
| Bars | numbers | Readings side by side, such as batteries |
| Split | numbers | Parts of a whole, such as where your power comes from |
| Chart | numbers | Up to three lines, or a bar per day |
| Agenda | calendars | The next event large, then what follows |
| Camera | cameras | A picture that renews itself; tap for live |

### A block's own colours and times

- **Its own colour** replaces the accent in that block: the dots, bars and lines, and the large number if you tick that.
- **A panel behind it** gives the block a background; the text turns light or dark to stay readable.
- **Show during**: untick the parts of the day in which the block should not show. In design mode such a block stays visible, faded.

## Style

Fonts for text and for labels (Inter, Archivo, Space Grotesk, Manrope, Fraunces, JetBrains Mono, or the tablet's own), the weight of the large numbers, the background by day and in the dark theme, and the accent colour. The fonts are inside the app, so the tablet needs no internet for them.

The sun button on the top bar switches between following Home Assistant, light and dark; `?theme=light` or `?theme=dark` in the address fixes it.

## Tablet

- **Parts of the day**: when the morning, the day, the evening and the night start. *Look at the pages as in the…* shows the pages as they will be then.
- **Night**: the dark theme during the night or whenever the sun is down, dimming the screen, and one page to show. A touch wakes the tablet for three minutes: full brightness and every page.
- **Going back**: after a while without a touch, the tablet returns to a page of your choice.
- **Doorbell**: choose what goes on when someone rings (a binary sensor, an event entity or a helper) and the camera to show. Every tablet then shows that camera live.
- **Keeping it**: save the whole dashboard as a file, and load one.

## On a wall tablet

Open the app from Home Assistant's sidebar and use that address in a kiosk browser such as Fully Kiosk Browser; the tablet stays signed in to Home Assistant. Two additions to the address help:

- `?kiosk` leaves the pencil and the theme button out.
- `#energy` (a page's name) starts on that page.

Any tablet with a recent browser works; the pages scale with the screen. A phone held upright shows the blocks below each other.

## What is kept, and where

- `/data/dashboard.json`: the pages, the look and the tablet's settings.

It is in Home Assistant's backups of the app. The app keeps no history of its own: charts read the recorder.

## Good to know

- The buttons can switch lights, switches, fans and helpers, move blinds, set a thermostat, run scenes, scripts and automations, press buttons, set numbers and control media players. Locks, alarms and anything else are left to Home Assistant itself.
- Changes and button presses are only accepted through Home Assistant's sidebar (ingress), from someone who is signed in.
- Charts go back as far as the recorder keeps its history (10 days by default).
- A camera's live picture is the MJPEG stream Home Assistant gives; where a camera has none, the picture renews every second.

## Development

The app runs without Home Assistant against a stand-in (`tools/mock_ha.py`) with a small house: lights, blinds, a thermostat, scenes, a speaker, solar, two cameras, a doorbell, two calendars and the weather.

```sh
docker compose -f tools/dev-compose.yaml up --build -d    # http://localhost:8099
curl http://localhost:8124/mock/ring                      # ring the doorbell
docker compose -f tools/dev-compose.yaml down -v          # stop, and forget the dashboard
```

Without Docker:

```sh
DASHBOARD_MOCK_PORT=8124 python3 -m tools.mock_ha &
DASHBOARD_SUPERVISOR_URL=http://127.0.0.1:8124 SUPERVISOR_TOKEN=mock-token DASHBOARD_DEV=1 \
DASHBOARD_DATA_DIR=/tmp/dashboard python3 -m src
```

Against a real Home Assistant, with a long-lived access token: `DASHBOARD_HA_URL=http://homeassistant.local:8123 SUPERVISOR_TOKEN=<token> DASHBOARD_DEV=1 DASHBOARD_DATA_DIR=/tmp/dashboard python3 -m src`.

The tests (`pytest`) use the same stand-in. `python3 -m tools.make_icons ../app-tesla/icon.png` draws the icon and logo.
