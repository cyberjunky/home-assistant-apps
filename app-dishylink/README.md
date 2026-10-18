[![Version][version-shield]][changelog]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

<img src="https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-dishylink/icon.png" alt="Dishylink" width="128" align="right">

# Dishylink App for Home Assistant

A Home Assistant app that runs the [Dishylink](https://github.com/DaveyHert/Dishylink)
Starlink dashboard and its always-on history recorder on your Home Assistant host, and
opens it from the sidebar.

Dishylink reads your Starlink dish and router directly over the local network, so it
keeps working during an outage. No account, no cloud, no telemetry: everything it records
stays in the app's data folder and is included in your Home Assistant backups.

## Supported Features

- **Live view**: downlink, uplink, latency, power draw, ping success and sky obstruction
- **History**: throughput, latency and energy by day, week and month, recorded 24/7
- **Sky**: obstruction map, time-lapse and a satellite sky view
- **Devices**: per-device data usage, network rules, alerts and outage log
- **Controls**: snow melt, sleep schedule, update window, reboot, SSIDs, DNS, bypass mode
- **Starlink account (optional)**: plan, billing cycle, authoritative usage and device pausing
- **Home Assistant integration**: sidebar panel through ingress, follows your light/dark
  mode, host identity detected automatically
- **Flash friendly**: the recorder works in RAM and writes to disk on a configurable
  interval, so SD cards and eMMC are spared

> [!NOTE]
> Dishylink records continuously, so history only exists from the moment the app is
> started. Do not run a second Dishylink recorder (desktop app or Docker) against the same
> router: two recorders double the router's poll rate.

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcyberjunky%2Fhome-assistant-apps)

Alternatively:

1. Go to **Settings → Apps → App Store**
2. Open the menu (⋮) and choose **Repositories**
3. Add `https://github.com/cyberjunky/home-assistant-apps`
4. Install **Dishylink** and start it
5. Open it from the sidebar, or with **Open Web UI** on the app page

## Configuration

The app works without any configuration on a standard Starlink setup. The options cover
the host's identity on the Starlink LAN, custom dish and router addresses, and how often
recorded history is written to disk. See [DOCS.md](DOCS.md) for every option, the
requirements, the optional direct port and troubleshooting.

## Disclaimer

Dishylink is an unofficial, independent project with no affiliation to SpaceX or
Starlink. Starlink is a trademark of Space Exploration Technologies Corp. This app
packages Dishylink unchanged; the dashboard and recorder are the work of
[DaveyHert](https://github.com/DaveyHert).

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
details. Dishylink itself is MIT licensed as well; its license ships in the image as
`LICENSE.dishylink`.

---

[version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcyberjunky%2Fhome-assistant-apps%2Fmain%2Fapp-dishylink%2Fconfig.yaml&query=%24.version&label=version&style=for-the-badge
[changelog]: https://github.com/cyberjunky/home-assistant-apps/blob/main/app-dishylink/CHANGELOG.md
[commits-shield]: https://img.shields.io/github/commit-activity/y/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[commits]: https://github.com/cyberjunky/home-assistant-apps/commits/main
[license-shield]: https://img.shields.io/github/license/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-cyberjunky-blue.svg?style=for-the-badge
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg?style=for-the-badge
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg?style=for-the-badge
