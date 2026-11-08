# Changelog

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
