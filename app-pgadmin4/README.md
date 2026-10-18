[![Version][version-shield]][changelog]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

<img src="https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-pgadmin4/icon.png" alt="pgAdmin 4" width="128" align="right">

# pgAdmin 4 App for Home Assistant

A Home Assistant app that runs [pgAdmin 4](https://www.pgadmin.org/), the web-based
administration and development platform for PostgreSQL, and opens it from the sidebar.

Use it to manage any PostgreSQL database on your network, including the TimescaleDB app
from the same repository, which is registered in pgAdmin automatically.

## Supported Features

- **Sidebar panel**: served through Home Assistant ingress, protected by your Home
  Assistant login
- **Auto-registered servers**: the TimescaleDB app and any servers you list in the
  options appear in pgAdmin without manual setup
- **PostgreSQL 17 and 18 client tools**: backup, restore and maintenance from the browser
- **Optional direct access**: HTTP or HTTPS on a port of your choice, with your own
  certificates
- **Extensible**: install extra Alpine packages and run commands at start-up

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcyberjunky%2Fhome-assistant-apps)

Alternatively:

1. Go to **Settings → Apps → App Store**
2. Open the menu (⋮) and choose **Repositories**
3. Add `https://github.com/cyberjunky/home-assistant-apps`
4. Install **pgAdmin 4** and start it
5. Open it from the sidebar, or with **Open Web UI** on the app page

The first start creates pgAdmin's configuration and can take a while.

## Configuration

Example configuration:

```yaml
ssl: true
certfile: fullchain.pem
keyfile: privkey.pem
servers: []
system_packages: []
init_commands: []
leave_front_door_open: false
```

| Option | Default | Description |
|--------|---------|-------------|
| `ssl` | `true` | Serve the optional direct port over HTTPS (port 443) instead of HTTP (port 80). Ingress is not affected. |
| `certfile` | `fullchain.pem` | Certificate file in the `ssl` folder, used when `ssl` is on. |
| `keyfile` | `privkey.pem` | Private key file in the `ssl` folder, used when `ssl` is on. |
| `servers` | `[]` | Database servers to register in pgAdmin automatically, see below. |
| `system_packages` | `[]` | Extra Alpine packages to install at start-up. Many packages slow the start down. |
| `init_commands` | `[]` | Shell commands to run at start-up. |
| `leave_front_door_open` | `false` | Serve pgAdmin without Home Assistant authentication. **A security risk, leave it off unless you know why you need it.** |

### Option: `servers`

Servers listed here are added to pgAdmin when it does not have a server with that name yet,
so anything you rename or remove in pgAdmin stays that way. Passwords cannot be imported:
enter them once in pgAdmin and tick **Save password**.

```yaml
servers:
  - name: Grafana database
    host: 192.168.1.10
    port: 5432          # optional, default 5432
    username: grafana   # optional, default postgres
    database: grafana   # optional maintenance database, default postgres
    group: Servers      # optional server group in pgAdmin
```

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

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for
details. It is derived from an Apache-licensed project by Hans van Essen; attribution is in
the [NOTICE](NOTICE) file. pgAdmin 4 is licensed under the PostgreSQL licence by the pgAdmin
Development Team.

---

[version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcyberjunky%2Fhome-assistant-apps%2Fmain%2Fapp-pgadmin4%2Fconfig.yaml&query=%24.version&label=version&style=for-the-badge
[changelog]: https://github.com/cyberjunky/home-assistant-apps/blob/main/app-pgadmin4/CHANGELOG.md
[commits-shield]: https://img.shields.io/github/commit-activity/y/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[commits]: https://github.com/cyberjunky/home-assistant-apps/commits/main
[license-shield]: https://img.shields.io/badge/license-Apache%202.0-blue.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-cyberjunky-blue.svg?style=for-the-badge
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg?style=for-the-badge
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg?style=for-the-badge
