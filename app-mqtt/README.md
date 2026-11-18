[![Version][version-shield]][changelog]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-mqtt/LICENSE)
![Project Maintenance][maintenance-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

# MQTT App for Home Assistant

<img src="https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-mqtt/icon.png" alt="MQTT" width="128" align="right">

The Mosquitto MQTT broker, the same as Home Assistant's Mosquitto app — Home Assistant finds it
by itself, your Home Assistant users log in with their own name and password, and other apps
(Zigbee2MQTT, ESPHome…) get it as their MQTT service — with a dashboard that shows what goes
through it.

Which clients are connected, from where, with which login and MQTT version, what they subscribe
to and how often they reconnect. Every topic as a tree with its last value, a chart of its last
values and who subscribes to it. The messages as they pass, filtered by topic or text. The
devices Home Assistant gets over MQTT discovery, which integration sent them, whether they are
available, and the leftovers to remove. And the broker over time: messages, data, clients and
reconnects, from a day to a year.

## Supported Features

- 📡 **Mosquitto 2.1** — MQTT 3.1, 3.1.1 and 5, WebSockets, TLS, persistence, bridges through your own config
- 🔑 **The same logins** — Home Assistant users, local logins, and the system users for Home Assistant and the other apps
- 🏠 **Found by itself** — Home Assistant is offered MQTT, other apps get the `mqtt` service; switching Home Assistant over from another broker is one click
- 👥 **Clients** — Address, login, protocol, session, subscriptions, every connect and disconnect with its reason, the ones that keep reconnecting
- 🌳 **Topics** — A tree to open level by level, a search by text or MQTT filter, the last value (JSON laid out), a chart of numbers, retained or not, who subscribes
- 📜 **Live traffic** — The messages as they pass, filtered by topic or text, with pause
- 🧩 **Discovery** — Devices and entities Home Assistant gets over MQTT, which integration sent them, available or not, no news for a day, broken configs and duplicate IDs; remove a device's leftover configs
- ✉️ **Publish and clean up** — Publish a message, clear a retained message or every retained one under a branch
- 📈 **Stats** — Messages and data in and out, clients, subscriptions, retained messages, connects and refused logins, traffic per topic group, kept for 30 days (your choice)
- 📋 **Log** — The broker's log, searchable, with the problems marked

## Requirements

- Home Assistant OS or Supervised (the app uses the Supervisor for logins and discovery)
- Not at the same time as the Mosquitto app: both use ports 1883 and 1884

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcyberjunky%2Fhome-assistant-apps)

Alternatively:

1. Go to **Settings → Apps → App Store**
2. Open the menu (⋮) and choose **Repositories**
3. Add `https://github.com/cyberjunky/home-assistant-apps`
4. Install **MQTT**
5. Start the app and open **MQTT** from the sidebar

Home Assistant offers MQTT with this broker (Settings → Devices & services, Discovered). Coming
from the Mosquitto app? Stop it first; the app's Info page switches Home Assistant over. See the
[documentation](DOCS.md).

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

This project is licensed under the MIT License - see the [LICENSE](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-mqtt/LICENSE) file for
details. Mosquitto is © the Eclipse Foundation (EPL-2.0/EDL-1.0), mosquitto-go-auth © Ignacio Gómez (MIT).

[version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcyberjunky%2Fhome-assistant-apps%2Fmain%2Fapp-mqtt%2Fconfig.yaml&query=%24.version&label=version&style=for-the-badge
[changelog]: https://github.com/cyberjunky/home-assistant-apps/blob/main/app-mqtt/CHANGELOG.md
[commits-shield]: https://img.shields.io/github/commit-activity/y/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[commits]: https://github.com/cyberjunky/home-assistant-apps/commits/main
[license-shield]: https://img.shields.io/github/license/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-cyberjunky-blue.svg?style=for-the-badge
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg?style=for-the-badge
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg?style=for-the-badge
