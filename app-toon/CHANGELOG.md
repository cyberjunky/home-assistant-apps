# Changelog

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
