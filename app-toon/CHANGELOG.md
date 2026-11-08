# Changelog

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
