[![Version][version-shield]][changelog]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

# Nuclei Scanner App for Home Assistant

<img src="https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-nuclei/icon.png" alt="Nuclei Scanner" width="128" align="right">

> **Internal use.** A vulnerability scanner for systems you own or are authorised to test. It will not scan a target until you register it and confirm you are allowed to.

An internal vulnerability scanner with a ticket workflow, built on
[nuclei](https://github.com/projectdiscovery/nuclei) and
[naabu](https://github.com/projectdiscovery/naabu) from ProjectDiscovery. It finds open
ports, known CVEs and exposed or misconfigured services, and turns each finding into a
ticket you work through.

## Supported Features

- 🔎 **Open ports** with naabu, then **CVEs and exposures** with nuclei's community templates, which carry essentially every public CVE that has a template. You don't curate lists, and a scan is thorough rather than quick: web templates run on web ports, and protocol templates (SSH, SMB, FTP, databases, TLS and more) on the other ports
- 🔄 **Templates kept current**: the community set updates on its own; paste your own templates in the app and they run alongside it
- 🖥️ **Devices**: every host found is listed with its services, a best-guess type and whether it is online; rename it, describe it and tag it (linux, switch, camera, doorbell, tv and more)
- 🎫 **Tickets**: every finding is a ticket with a status, the proof, a description, remediation, the CVE and CVSS score, and a severity; a later scan reopens a ticket that comes back or resolves one that is gone
- 🔐 **Ownership gate**: a scan only ever runs against objects you register and confirm you are authorised to test
- 📅 **Schedules**: scan an object daily, weekly or monthly
- 💾 **Your data**: the database stays in the app's data folder, with a backup download and a danger-zone delete

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcyberjunky%2Fhome-assistant-apps)

Alternatively:

1. Go to **Settings → Apps → App Store**
2. Open the menu (⋮) and choose **Repositories**
3. Add `https://github.com/cyberjunky/home-assistant-apps`
4. Install **Nuclei Scanner** from the store, start it and open it from the sidebar
5. Add a scan object (an IP range, host name, URL or ASN), confirm you are authorised to
   scan it, and run or schedule a scan

## The ownership gate

A scan only ever runs against objects you register, and only after you confirm you are
authorised to scan them. A public IP or network is expanded to a list of hosts you see
and approve before it can be scanned; a host name, URL or ASN must be authorised
explicitly. Private ranges (RFC1918, loopback) are treated as yours. Do not point this at
systems you do not own or have permission to test.

## Configuration

See [DOCS.md](DOCS.md) for every option, how the two-pass scan works, the data it
handles, the requirements and development notes.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for
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
