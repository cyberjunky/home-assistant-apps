# Toon App for Home Assistant

Logger and dashboard for a rooted Toon thermostat. It reads boiler, heating and energy values straight from the Toon's internal message bus, stores them locally and publishes them to Home Assistant over MQTT. It also keeps track of the Toon's own health and of every time it dropped off the network.

Toon runs entirely on your Home Assistant, with no cloud service and no telemetry. Everything it records stays in the app's data folder and is included in your Home Assistant backups.

**This version only reads.** It sends nothing that changes your Toon; the actions it may invoke are an explicit list in `src/boxtalk.py`.

## Supported Features

- 🔌 **One connection** — A single SSH session carries everything, and reconnects by itself after the network or the Toon has been away
- 🔥 **Boiler values the web API doesn't have** — Supply and return temperature, burner hours, hot water burner hours, failed starts, fault codes
- ⚡ **Live** — The Toon pushes changes as they happen: power and meters about every 10 seconds
- 📈 **History** — Up to 10 years of the Toon's own meter history is imported, and gaps from outages are filled in afterwards
- 🩺 **The Toon's health** — Memory, uptime, WiFi signal, dropped packets and clock offset
- 🕵️ **Why it went away** — Each outage is logged as a restart of the Toon or as a lost connection
- 📨 **MQTT discovery** — The Toon appears in Home Assistant as a device with all its sensors

## Requirements

- A rooted Toon with SSH access and its original software. Tested on a Toon 1 with firmware 6.3.30 and a meter adapter
- An MQTT broker for the entities in Home Assistant (optional; the dashboard works without one)

See [DOCS.md](DOCS.md) for setup and details.
