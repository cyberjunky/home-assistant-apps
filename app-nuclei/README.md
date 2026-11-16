[![Version][version-shield]][changelog]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-nuclei/LICENSE)
![Project Maintenance][maintenance-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

# Nuclei Scanner App for Home Assistant

<img src="https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-nuclei/icon.png" alt="Nuclei Scanner" width="128" align="right">

> **Internal use.** It scans the devices Home Assistant tracks on your own network, and nothing else.

An internal vulnerability scanner built on
[nuclei](https://github.com/projectdiscovery/nuclei) and
[naabu](https://github.com/projectdiscovery/naabu) from ProjectDiscovery. It scans the devices
Home Assistant's device trackers report as home, finds open ports, known CVEs and exposed or
misconfigured services, and turns each finding into an issue you work through.

## Supported Features

- 📡 **Your devices, nothing to add**: the devices come from Home Assistant's device trackers; only those that are home, on a private address, are scanned
- 🕒 **Scanned when it matters**: a new device straight away, and a device that comes home once its last scan is older than the rescan period
- 🔎 **Open ports** with naabu, then **CVEs and exposures** with nuclei's community templates: web templates on web ports, protocol templates (SSH, SMB, FTP, databases, TLS and more) on the others
- 🎫 **Issues**: each with the proof, what to do, the CVE and CVSS score; acknowledge it, and a later scan resolves what is gone
- 📊 **Dashboard**: a security score, issues per severity, coverage, a 30-day trend, the scan that is running and what needs attention
- 🔄 **Up to date**: the templates update themselves; the Scanner page shows nuclei, naabu and the templates against the newest releases, and the newest CVE covered
- ✍️ **Your own templates** run alongside the community set, checked by nuclei before they are saved
- 💾 **Your data**: the database stays in the app's data folder, with a backup download and a danger-zone delete

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcyberjunky%2Fhome-assistant-apps)

Alternatively:

1. Go to **Settings → Apps → App Store**
2. Open the menu (⋮) and choose **Repositories**
3. Add `https://github.com/cyberjunky/home-assistant-apps`
4. Install **Nuclei Scanner** from the store, start it and open it from the sidebar
5. The devices Home Assistant tracks appear by themselves; the first scans start within a minute

## Configuration

See [DOCS.md](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-nuclei/DOCS.md) for every option, which devices are scanned and when, how a
scan works, and development notes.

## License

This project is licensed under the MIT License - see the [LICENSE](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-nuclei/LICENSE) file for
details.

---

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

---

[version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcyberjunky%2Fhome-assistant-apps%2Fmain%2Fapp-nuclei%2Fconfig.yaml&query=%24.version&label=version&style=for-the-badge
[changelog]: https://github.com/cyberjunky/home-assistant-apps/blob/main/app-nuclei/CHANGELOG.md
[commits-shield]: https://img.shields.io/github/commit-activity/y/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[commits]: https://github.com/cyberjunky/home-assistant-apps/commits/main
[license-shield]: https://img.shields.io/github/license/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-cyberjunky-blue.svg?style=for-the-badge
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg?style=for-the-badge
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg?style=for-the-badge
