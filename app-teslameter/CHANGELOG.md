# Changelog

## 0.5.3

**After updating:** nothing to do.

- **Delete all recorded data.** A "Danger zone" at the bottom of Settings → Data removes every trip, charging session, position, state and software version after you type DELETE to confirm. Your cars, your Tesla sign-in and your charging prices stay, so logging simply continues. It refuses while a trip or charge is in progress, and the database file shrinks right away.
- The period you pick at the top of Stats now applies to **every** panel. Battery health shows the charges, the current full range and the degradation for that period (the "When new" figure stays the best ever seen, as that is what it means), and Software versions lists the versions first seen in that period — choose "All" to see the whole history.
- Battery health explains what it measures: the car's own rated range at 100%, which drops in cold weather and with BMS drift. It is not temperature-corrected.
- The Software versions list no longer stops at the latest 30 versions.
- After an import (from TeslaMate or Tesla's charging history) the Trips, Charging and Stats views load their lists again. Before, a Charging list that was open during a TeslaMate import kept the old entries, and opening one of the Supercharger sessions the import had replaced said "Charge not found".

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
