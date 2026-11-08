# Changelog

## 0.1.0

First version. Reads only.

- Boiler, heating, energy and health values from the Toon's message bus, over one SSH connection that reconnects by itself.
- Local database with every sample for 90 days and hourly values for good.
- Import of the Toon's own history, and filling in the gap after an outage.
- Connection history that tells a restart of the Toon from a lost connection.
- Dashboard through ingress and MQTT discovery for Home Assistant.
