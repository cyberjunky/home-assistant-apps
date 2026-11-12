# Changelog

## 0.5.3

**After updating:** nothing to do.

- **Connection errors in plain words.** "[Errno 113] Connect call failed ('192.168.2.106', 22)" now reads "nothing answers at 192.168.2.106. Check that the Toon is on and connected to Wi-Fi, and that this is still its address". A refused connection, a timeout, an address that can't be found and a connection the Toon closed each say what happened and what to check. The original error is in the app's log at debug level. Outages from before keep their old text.

## 0.5.2

**After updating:** nothing to do.

- The Settings page is now called **Info**, like in the other apps.

## 0.5.1

**After updating:** nothing to do.

- The top bar is as high as Home Assistant's own header next to it (the sidebar's "Home Assistant" bar), so the two line up. It follows Home Assistant's header height, 56 px by default.

## 0.5.0

**After updating:** nothing to do. For costs, add your prices under the new option `prices`: see Prices in the documentation.

- **Stats is now two pages:** **Electricity** and **Gas & heating**. They share the chosen period. Each has its own tiles, day timeline, records and table of values.
- **Costs:** with your contract prices in `prices`, both pages show the costs per day, week, month or year, with fixed costs included. Returned electricity is deducted. The Dashboard shows the costs of today. Each price entry applies from its start date, so the past keeps its own prices.
- **Trends:** when electricity is used per weekday and hour, and the low tariff's share of what came from the grid.

## 0.4.11

**After updating:** nothing to do.

- Removed the one-time correction of the stored gas flow from 0.4.10, now that it has run. A database backup from before 0.4.10 keeps its gas flow 12 times too low if you restore it.

## 0.4.10

**After updating:** nothing to do. The app corrects the gas flow it stored before, once, when it starts.

- **Gas flow was shown 12 times too low.** The Toon gives the litres of the gas meter's last 5-minute step, not litres per hour. Checked on a Toon: 52 and 71 in its gas flow went with steps of 52 and 71 litres on the meter. The Dashboard, the day timeline, the Stats table and the **Gas flow** sensor in Home Assistant now show m³ per hour. Home Assistant's own history of that sensor from before the update stays as it was.

## 0.4.9

**After updating:** nothing to do.

- **Gas per hour** and **Gas flow** on the day timeline also cover a gap in the live readings, from the gas meter's 5-minute history that the app fills in after an outage. Before, a day with some live readings showed nothing for the hours in between.

## 0.4.8

**After updating:** nothing to do. On its first connect the app fills the gaps it can by itself.

- Gaps are filled from the Toon's own history after every connect, also when the app was restarted during an outage. Before, a restart (for example by an update) cut the outage short, and only the part after the restart was filled.
- **Fetch missing history** button under Settings → Connection history, which does the same on request. It only reads from the Toon.

## 0.4.7

**After updating:** nothing to do.

- A **Thermostat** climate entity in Home Assistant, through MQTT: target temperature, the presets Comfort, Home, Sleep and Away, and the modes Auto (week program) and Heat (program off). It uses the same commands as the Dashboard's controls, so you can remove the toon_climate integration. With `read_only: true` it only shows the state.
- The thermostat's setpoint control on the Dashboard is labelled **Setpoint**, and the room temperature under it reads "room now …". The old line "setpoint · room 18.9 °C" made the room temperature look like the setpoint.

## 0.4.6

**After updating:** nothing to do. Codes are recorded from this version on.

- A boiler fault gets a red **Boiler fault** banner at the top of the Dashboard, with the code the Toon's own screen shows, the boiler's own (OEM) code and the water pressure.
- The Dashboard's Boiler block shows both codes all the time.
- Stats has a **Boiler faults** log for the chosen period: when each fault started, how long it lasted, its codes and the lowest water pressure meanwhile.
- New diagnostic sensor **Boiler error code** in Home Assistant: the number the Toon shows with a fault ("Errorcode" or "ketelstoring"). The app now records it too.

## 0.4.5

**After updating:** nothing to do.

- A **Danger zone** at the bottom of Settings → Data deletes everything recorded: all values, the hourly history, the connection outages and the history of your external sensors. You have to type DELETE to confirm. Your options are not touched. The app fetches the Toon's own history again and records from now. It refuses while that history import is running. The **Database backup** button above it takes a copy first.

## 0.4.4

**After updating:** nothing to do.

- The thermostat controls respond right away. Your choice shows immediately and the controls stay usable while it's sent. It no longer waits for every value on the Toon to be read again. If the Toon refuses a change, the display goes back and says why.

## 0.4.3

**After updating:** nothing to do. The app fetches the two new histories from your Toon by itself.

- Burner hours go back about 30 days, from the boiler's real hour counter in the Toon's history.
- The day timeline's Gas flow chart also works for about the week before the update, from the gas meter's 5-minute history in the Toon.

## 0.4.2

**After updating:** nothing to do.

- The day timeline on the Stats page shows gas per hour, also for days from before the app was installed, as far back as the Toon keeps hourly values (about five years). The Gas flow chart only appears on days that have readings, because the Toon keeps no history of it.
- New option `boiler_name`: the name of your boiler, shown on the Dashboard and in Settings.
- **Burner hours** on the Stats page shows the boiler's own hour counter again, with the date it starts from. 0.4.0 used the Toon's history of burner minutes, which is far too low to be real burner time (637 hours where the gas used would take at least 2,270). The number starts small, but it's true.

## 0.4.1

**After updating:** nothing to do.

- The flame in the heating diagram's boiler grows with the modulation, from small at low power to big at full power, and flickers while the burner is on.

## 0.4.0

**After updating:** nothing to do. The Dashboard can now change your thermostat; set the new option `read_only` to turn that off.

- Control the thermostat from the Dashboard: raise or lower the setpoint, choose a preset, and turn the week program on or off.
- The change goes to the Toon right away and the Dashboard shows the result. If it doesn't go through, you see why.
- The Stats tiles also show what the total comes to per day, week, month or year, following your **Per** choice.
- **Burner hours** on the Stats page now includes the Toon's own history, where it has burner minutes. It only counted from when the app started logging.
- **Condensing** says since when it has data. It and the heat estimate rest on return temperatures, which are only recorded from version 0.2.0 on.

## 0.3.0

**After updating:** nothing to do. New options are optional. The app now asks for access to Home Assistant's API, which it uses only to read the sensors you name.

- The heating diagram has the same design as GoodWe's energy flow, with moving dots along the pipes in use.
- A water pressure chart of the last 30 days on the Dashboard.
- Settings shows what's in the database and has a **Database backup** download.
- Stats shows the energy in the gas you used, and an estimate of the heat it gave. That estimate assumes a typical condensing boiler at your return temperature: measuring the real heat needs a flow meter.
- New options: `external_sensors`, a list of Home Assistant sensors with a name and an entity, and `outside_temperature`. They get a tile on the Dashboard and appear in the charts: outside with the room, other temperatures with the boiler, other units in a chart of their own. Every reading is stored like the Toon's own values, and they are in the Stats table and the CSV exports.
- New option: `gas_kwh_per_m3`, the energy in your gas.

## 0.2.1

**After updating:** if you still see the old single page, reload it once in a private window or clear the browser's cache for Home Assistant.

- The browser no longer keeps an old copy of the dashboard page after an update. A cached page could keep showing the old single page, with fresh values, after updating to 0.2.0.

## 0.2.0

A real dashboard. Still only reads.

- New **Dashboard**: the heating system at a glance, thermostat and boiler state, the next program change, today's use and 24-hour charts.
- New **Stats**: smart meter, heating and boiler over any period, with burner starts, short runs, temperatures while heating, modulation, pressure trend, night base load, gas per weekday and hour, records, a day timeline and CSV export.
- New **Settings**: the Toon's health, connection history and configuration.
- Imports the Toon's burner minutes and boiler operating mode too, also on existing installs. The operating mode is only stored for now, because what its values mean isn't known yet.
- Records the burner's state over time, which burner statistics are based on.
- Removed the import of a thermostat year file that doesn't exist; the Toon keeps thermostat values for 30 days.

## 0.1.2

**After updating:** nothing to do.

- The README and the dashboard's footer say that this is an unofficial app.

## 0.1.1

**After updating:** nothing to do.

- After two weeks of use, a small card in the corner of the dashboard asks for a donation. It never blocks the dashboard, which keeps running and stays usable. Maybe later asks again after 30, then 60, then every 90 days. Don't show again, or following a donate link, ends it for good.

## 0.1.0

First version. Reads only.

- Boiler, heating, energy and health values from the Toon's message bus, over one SSH connection that reconnects by itself.
- Local database with every sample for 90 days and hourly values for good.
- Import of the Toon's own history, and filling in the gap after an outage.
- Connection history that tells a restart of the Toon from a lost connection.
- Dashboard through ingress and MQTT discovery for Home Assistant.
