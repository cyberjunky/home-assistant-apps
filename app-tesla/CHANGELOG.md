# Changelog

## 0.7.1

**After updating:** nothing to do. The first start corrects charging sessions recorded earlier that continued a paused charge (see below); the log says how many.

- **Fewer requests to Tesla while driving.** When the live stream sent frames without a gear, every frame (about one a second) made the app read the car's data. It now reads at most once per driving interval. After Tesla answers "too many requests", the app waits as long as Tesla asks, whatever comes in from the stream meanwhile.
- **The odometer no longer steps back.** The stream rounds the odometer to a tenth of a mile, and a reading that arrived late could be older than the stream's last one; Home Assistant then saw the meter go down. The odometer now only goes up, and a late reading no longer undoes newer stream values (position, speed, battery).
- **A paused charge is counted once.** Charging that stopped and started again while the car stayed plugged in became two sessions, and the second one booked the energy of the first part again (the car's counter keeps counting until it is unplugged). Within six hours, at the same place, it now continues the same session; after a longer pause the new session only counts what was added after it. Sessions recorded like that before are corrected once, at the first start.
- **Learned consumption** no longer comes out too low: the car's energy counter starts a little later than its range rises, so both are now counted over the same part of the session. Trip energy follows the corrected value.
- An unknown charger power is shown as unknown instead of 0 kW.
- A Model 3 with trim badge "50" from model year 2022 on is shown as **RWD**, as Tesla sells it, instead of "Standard Range".
- The live stream ignores garbled frames (an unknown gear, a position off the globe) and reconnects after ten of them in a row. A stream that goes quiet during a drive is reopened after one minute instead of five.

## 0.7.0

**After updating:** this is a new app, not an update, because the name and slug changed.

- Renamed from TeslaMeter to **Tesla**: the app, its slug and image (`app-tesla`), the database file (`tesla.db`) and the MQTT topic and devices (`tesla/...`, `tesla_<vin>`).
- The README, the store description and the dashboard's status bar say that this is an unofficial app.

## 0.6.2

**After updating:** nothing to do.

- After two weeks of use, a small card in the corner of the dashboard asks for a donation. It never blocks the dashboard, which keeps running and stays usable. Maybe later asks again after 30, then 60, then every 90 days. Don't show again, or following a donate link, ends it for good.

## 0.6.1

**After updating:** if a car shows that isn't yours any more, for example a loan car that came along with a TeslaMate import, press **Remove car** on it under Settings → Vehicles. Then press **Fetch charging history** once.

- **Charging history went to the wrong car.** Tesla can answer with the whole account's Supercharger sessions, whichever car is asked about, and each car got all of them. A car that was on your account for a few weeks ended up with sessions it never had. Sessions now go to the car Tesla's record names.
- **Cars that aren't on your Tesla account** are no longer asked about: no more "404 not_found", and the card says so. Their history stays until you press **Remove car**, which deletes them with what is recorded under them and shows how much that is first. An import doesn't bring a removed car back. Their entities disappear from Home Assistant too.
- A car that comes in through an import shows right away, with the paint and wheels the import knows, instead of after the next restart.
- **Car pictures.** The new Model 3 (2024 on) and Model Y (2025 on) show their own new shape, worked out from the wheels and model year. Older Model Y, which Tesla only has at a three-quarter angle, and facelifted Model S and X now get a picture too, with their wheels. Each picture is fetched once and kept by the app, so it also shows when Tesla's server can't be reached. A car whose paint isn't known yet shows in white. Quicksilver and Stealth Grey were swapped.
- **New drawings** for when there is no picture, as for Cybertruck and Roadster: one per model, slimmer and to scale, with windows, doors, lights and proper wheels.
- When an import fails, its button works again, so you can correct the details and start it right away. It used to stay on "Importing…" until you closed the dialog.
- Import connection errors say what to check: the login, the database name, or the host and port.

## 0.6.0

**After updating:** the first start upgrades the database and maps your trips for the Stats map in the background; the map says "mapping N trips…" until it is done (a minute or so for a few years of history). To add the tire pressures, climate, cables and parked positions to history imported earlier, run **Settings → Data → Import history** again on every database you imported from. It adds what is missing and does not duplicate anything.

- **Everything the car reports.** The dashboard now shows which door or window is open, ideal range, tire pressure warnings and when the tires were measured, and has new Climate and Charging settings blocks: set temperatures, fan, preconditioning, defrost, seat, steering wheel, mirror and wiper heaters, Dog and Camp mode, cabin overheat protection, battery heater, requested and offered current, cable, port door and latch, schedule and off-peak. Also valet and speed limit mode, dashcam, and how the car is set to show its units.
- **More history.** Positions now keep tire pressures, ideal range, climate settings and battery heater state; charging sessions keep the cable, charger type, offered and requested current and battery heater state. A new **Tire pressure** chart in Stats follows the four tires over time, and trip and charging details show these values.
- **Import from TeslaMate brings all of it**, including parked positions, the car's version name and LFP battery and free Supercharging settings. Running it again fills in trips and sessions that are already there, also those TeslaMeter recorded itself, keeping the values it has. TeslaMate's sleep timing and geofence prices are not taken over.
- **More MQTT entities**, as diagnostics: climate and heater details, each door and window, tire warnings, charger details, valet and speed limit mode.
- **Stats loads fast again.** The "Where you've been" map used to read every recorded position (millions of them after a TeslaMate import) each time you picked another period or bar size, and every other query waited behind it. The roads driven are now mapped once per trip, when it ends, and kept per period; switching between Day, Week, Month and Year only reloads the bar charts.
- A database of an older layout is upgraded in place at start instead of being refused.
- A chart whose values are all the same (one trip in every period, say) no longer fails with "Invalid array length" and leaves the Stats view half drawn.

## 0.5.5

**After updating:** nothing to do.

- Import from TeslaMate also reads databases of older TeslaMate versions, which have no ascent and descent per drive (the import stopped with "column d.ascent does not exist"). Those are worked out from the positions' elevations instead.

## 0.5.4

**After updating:** nothing to do. If you have Home Assistant zones, press **Apply zones to places** once (Settings → Data) to name the trips recorded before the zones existed.

- **Zones made visible and applied to history.** Settings → About lists the Home Assistant zones the app loaded, with their radius, so you can see why a place isn't named after your zone (not loaded, or the car parks outside the radius). **Apply zones to places** in Settings → Data renames every recorded trip start, trip end and charging place that lies inside a zone after that zone, and says how many it found.
- **Rename a place** (Settings → Data): give a place that OpenStreetMap named wrongly its proper name, everywhere it was recorded and in the address cache, so new trips there get the corrected name too. The price tag of a charging location moves along.
- Settings gets a Support panel (documentation, issues, version and changelog), the same as in Meldkamer.
- After an import (from TeslaMate or Tesla's charging history) the Trips, Charging and Stats views load their lists again. Before, a Charging list that was open during a TeslaMate import kept the old entries, and opening one of the Supercharger sessions the import had replaced said "Charge not found".

## 0.5.3

**After updating:** nothing to do.

- **Delete all recorded data.** A "Danger zone" at the bottom of Settings → Data removes every trip, charging session, position, state and software version after you type DELETE to confirm. Your cars, your Tesla sign-in and your charging prices stay, so logging simply continues. It refuses while a trip or charge is in progress, and the database file shrinks right away.
- The period you pick at the top of Stats now applies to **every** panel. Battery health shows the charges, the current full range and the degradation for that period (the "When new" figure stays the best ever seen, as that is what it means), and Software versions lists the versions first seen in that period — choose "All" to see the whole history.
- Battery health explains what it measures: the car's own rated range at 100%, which drops in cold weather and with BMS drift. It is not temperature-corrected.
- The Software versions list no longer stops at the latest 30 versions.

## 0.5.2

**After updating:** nothing to do.

- The map's heat map opens much faster, especially for "all time" on a long history — it now reads only the points it draws instead of loading every recorded position. The heat map looks exactly the same.

## 0.5.1

**After updating:** nothing to do — unless you imported history before this version, in which case start fresh (remove the database or reinstall) and import again, then fetch the charging history. The old import put sessions at the wrong time (see below), so some may appear twice until you re-import.

- Imported history now keeps the correct time. The source stores times without a timezone; they were being read as the add-on's local time and so landed an hour or two off (and differently across daylight saving). Besides showing trips and charges at the wrong clock time, this stopped an imported charging session from lining up with the same session in Tesla's own charging history, so the billed cost couldn't attach and the session could appear twice. Imports now read these times as UTC, as the source stores them.
- The car picture is a clean pearl white (a standard Model 3 colour) until Tesla reports the real paint, instead of a dull grey — so a freshly signed-in car looks right straight away.
- Fixed a grey square showing next to the last item in the Vehicle details when the number of items was odd (an empty grid cell).

## 0.5.0

**After updating:** the database layout changed. The add-on refuses an older database and the log explains how to start fresh — remove the old database file (or uninstall and reinstall the add-on).

- **Charging price tags.** Tag where you charge (home, work, a public network…) and give each tag a price per kWh. A session is priced by its location's tag; untagged locations use the default price from the add-on configuration, and Superchargers keep the amount Tesla billed. Manage tags and rates in Settings, and set a location's tag on any session in the Charging view. This replaces the old assumption that every non-Supercharger session cost the home rate.
- **Heatmap** on the map: a "Heatmap" control shows where the car has been over 30 days, 90 days, a year or all time, with the places it parks weighing more.
- The Vehicle panel now shows more of what the car reports, when it's available: **usable charge** (the usable state of charge when it differs from the displayed percentage), **charge port type**, **roof**, **exterior trim** and **air suspension**. Fields the car doesn't report stay hidden.
- The Data section counts Supercharger and other charging sessions separately.
- The history import button now names the database it reads, so its purpose is clearer.

## 0.4.0

**After updating:** nothing to do.

- A **year selector** on Trips, Charging and Stats to jump to a specific year, next to the period buttons. Dates show the year where they'd otherwise be ambiguous (the charging and trip lists, and the detail headers).
- Charge detail now shows the **price per kWh** (approximate — cost ÷ kWh) and **how many times you've charged at that site**, with the total energy and cost there.

## 0.3.4

**After updating:** press Fetch charging history again to pull older sessions.

- Charging history now asks Tesla for the full date range (it defaulted to a recent window before), falling back automatically if Tesla rejects the date parameters. The add-on log shows how many sessions came back.
- README documents the data-fetch intervals and how multiple cars on one account are handled.

## 0.3.3

**After updating:** nothing to do.

- Supercharger history pages through every session Tesla returns, stopping only when there are no new ones. (Tesla's own history is limited to a recent window; for the full multi-year history and home charging, use Import history.)

## 0.3.2

**After updating:** nothing to do.

- Nudged the car picture up a little in the dashboard header.

## 0.3.1

**After updating:** nothing to do.

- The car picture uses a side view, so both wheels show (a default wheel is used when Tesla reports an unusual wheel code) and the panel is less tall.
- Supercharger history now fetches all pages, not just the most recent sessions.
- The history import replaces the sparse billing-only Supercharge entries with the fuller detail (battery, range, power curve) from the source, keeping Tesla's billed cost.
- Aligned the bars in the "most visited" and "where you charge" lists.

## 0.3.0

**After updating:** nothing to do.

- Import existing history: in Settings → Data, **Import history** connects to another logger's PostgreSQL database and brings in your drives (with GPS tracks), charging sessions (with power curves and cost), positions, states and software history, matched by VIN. It only reads; nothing in the source changes, and it never duplicates on a re-run.

## 0.2.3

**After updating:** nothing to do.

- Dashboard: the car picture sits on the right next to the name, with the state and time above it. The trim is read correctly even before Tesla reports the model (e.g. "Standard Range" instead of "50"), and unknown wheel names read better.

## 0.2.2

**After updating:** your past Supercharge sessions are imported automatically on the first start; or use **Fetch charging history** under Settings → Data.

- Import past charging sessions from Tesla's own records (date, place, energy and the billed cost) so Charging and Stats have data from day one. Runs once on the first start, with a Fetch button in Settings to run it again.
- The car picture now matches the model (sedan, SUV, truck or roadster), is smaller and more icon-like, and tints to the real paint. The model is worked out from the VIN when Tesla hasn't reported it yet.

## 0.2.1

**After updating:** nothing to do.

- Icon: the gauge and car sit a little lower, better centred in the circle.

## 0.2.0

First release.

- Sign in to your Tesla account right in the dashboard: Tesla's own sign-in page is shown in a browser running inside the add-on, so there is nothing to copy or paste. Access renews itself afterwards. Signing in with a refresh token works too.
- Dashboard with the car on a live map, a picture of the car in its own paint colour, battery, charging and driving details, vehicle state, totals and recent activity.
- Trips: every drive with addresses, energy and consumption, drawn on the map coloured by speed, with speed, power, elevation and battery charts and GPX export.
- Charging: every session with energy added and drawn, efficiency, power curve and cost — what Tesla billed at Superchargers, your price per kWh elsewhere, or set per session.
- Stats: distance and energy per day, week, month or year, consumption over time and by temperature, battery health, standby loss, most visited places, driving rhythm, records and a heat map of where you've been.
- The car is left alone when idle so it can fall asleep; Tesla's live stream gives positions every second while driving.
- MQTT with Home Assistant discovery: sensors, binary sensors and a device tracker per car.
- Place names from Home Assistant zones and OpenStreetMap; CSV export and database download.
