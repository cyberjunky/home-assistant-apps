[![Version][version-shield]][changelog]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

# Toon App for Home Assistant

<img src="https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-toon/icon.png" alt="Toon" width="128" align="right">

> **Unofficial.** An independent project, not affiliated with, endorsed by or sponsored by Eneco or Quby.

Logger and dashboard for a rooted Toon thermostat — reads boiler, heating and energy values
straight from the Toon's internal message bus, stores them locally and publishes them to
Home Assistant over MQTT. It also keeps track of the Toon's own health and of every time it
dropped off the network.

Toon runs entirely on your Home Assistant, with no cloud service and no telemetry. Everything
it records stays in the app's data folder and is included in your Home Assistant backups.

**It can change one thing: your thermostat**, from the Dashboard's controls. Those are the setpoint,
the presets and the week program, and nothing else. Set `read_only` to turn them off.

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

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcyberjunky%2Fhome-assistant-apps)

Alternatively:

1. Go to **Settings → Apps → App Store**
2. Open the menu (⋮) and choose **Repositories**
3. Add `https://github.com/cyberjunky/home-assistant-apps`
4. Install **Toon**
5. Enter your Toon's address and root password under `ssh` in the app's configuration
6. Start the app and open **Toon** from the sidebar

## Configuration

Toon needs the SSH details of your Toon; the other options cover the history import, how long
samples are kept and the MQTT broker. See [DOCS.md](DOCS.md) for every option, how it works
and what the dashboard shows.

## Disclaimer

This app is an independent project. It is not affiliated with, endorsed by or sponsored by
Eneco or Quby; "Toon" is a trademark of its owner. It uses the Toon's internal message bus,
which is not an official interface and can change without notice. Rooting a Toon and giving
an app root access to it is at your own risk.

## 💖 Support This Project

If you find this app useful, please consider supporting its continued development and
maintenance:

### 🌟 Ways to Support

- **⭐ Star this repository** - Help others discover the project
- **💰 Financial Support** - Contribute to development and hosting costs
- **🐛 Report Issues** - Help improve stability and compatibility
- **📖 Spread the Word** - Share with other Home Assistant users

### 💳 Financial Support Options

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

Every contribution, no matter the size, makes a difference and is greatly appreciated! 🙏

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for
details.

---

[version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcyberjunky%2Fhome-assistant-apps%2Fmain%2Fapp-toon%2Fconfig.yaml&query=%24.version&label=version&style=for-the-badge
[changelog]: https://github.com/cyberjunky/home-assistant-apps/blob/main/app-toon/CHANGELOG.md
[commits-shield]: https://img.shields.io/github/commit-activity/y/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[commits]: https://github.com/cyberjunky/home-assistant-apps/commits/main
[license-shield]: https://img.shields.io/github/license/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-cyberjunky-blue.svg?style=for-the-badge
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg?style=for-the-badge
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg?style=for-the-badge
