[![Version][version-shield]][changelog]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-proxmox-bs/LICENSE)
![Project Maintenance][maintenance-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

# Proxmox Backup Server App for Home Assistant

<img src="https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-proxmox-bs/icon.png" alt="Proxmox Backup Server" width="128" align="right">

> **Unofficial.** An independent project, not affiliated with, endorsed by or sponsored by Proxmox Server Solutions GmbH.

A real [Proxmox Backup Server](https://www.proxmox.com/en/proxmox-backup-server) as a Home
Assistant app. Not a look-alike and not a dashboard on top of one: the image installs Proxmox's
own packages, unchanged, so port 8007 serves PBS's original API, web interface and console.
Proxmox VE adds it as normal PBS storage, and everything is done in PBS's own interface, as on any
PBS: disks and datastores, backups and restores, verify, prune and sync jobs, users, tokens and
notifications.

## Why

A backup server should be a separate machine from the servers it backs up, and it mostly sits
idle. A Home Assistant Blue, Green, Yellow or a Raspberry Pi that is no longer needed for Home
Assistant itself (or one that runs little else) is a good fit:

- **Hardware you already have**, put to use instead of buying a mini PC for backups
- **Low power**: a few watts around the clock, against tens of watts for an x86 box, for a machine that waits most of the day
- **Silent and small**, with a USB 3 SSD or disk next to it
- **Separate from Proxmox VE**: when a Proxmox node fails, its backups are on another machine
- **Managed like the rest of Home Assistant**: installed, updated, started and backed up (its configuration) as an app

Proxmox doesn't publish PBS for ARM, so on aarch64 the app uses the arm64 build of the same
version by [wofferl](https://github.com/wofferl/proxmox-backup-arm64). On amd64 it uses
Proxmox's own repository.

## What the app adds

PBS expects a full Debian system with systemd; an app is a container. The app fills the gaps,
and nothing more:

- **PBS's Disks page works**: Disks → Directory → Create formats a disk and makes a datastore on it, as on a real PBS. The app mounts it (PBS asks systemd for that; the app answers instead), mounts it again after a restart, and follows it when it is unplugged and plugged back in
- **A disk that is unplugged** takes its datastores offline ("Disk not connected"), so Proxmox VE sees why backups can't run, and nothing is written to Home Assistant's own storage meanwhile. Plugged in again, the disk is checked, mounted and its datastores come back online
- **Home Assistant's own disk is protected**: the app sees every disk of the machine, but the commands that partition, wipe or format a disk refuse the one Home Assistant runs from
- **In Home Assistant's sidebar**: PBS's own interface inside Home Assistant when it runs on `https://`; on `http://`, its status and a button that opens it
- **PBS's log** is in the app's log
- **The configuration is kept** (datastores, users, tokens, jobs, the certificate) across updates and in Home Assistant's backups, so Proxmox VE keeps trusting the same certificate. The backups themselves stay on their disks

## What doesn't work in an app

- **ZFS**: Home Assistant's kernel has no ZFS. Use ext4 or xfs
- **Updates from PBS's Updates page**: PBS is updated with the app
- **PBS's network, time and DNS settings**: they are Home Assistant's

## Requirements

- Home Assistant OS or Supervised, aarch64 (Home Assistant Blue, Green or Yellow, Raspberry Pi 4 or 5) or amd64
- A disk for the datastores: SATA, NVMe or USB, an SSD or a hard disk. On a Blue, Green or Raspberry Pi that means USB 3; give a hard disk a powered enclosure
- **Protection mode off** for this app, so it can see the disks
- About 1 GB of free memory while garbage collection or a verify runs
- Proxmox VE 8 or 9 to back up

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcyberjunky%2Fhome-assistant-apps)

Alternatively:

1. Go to **Settings → Apps → App Store**
2. Open the menu (⋮) and choose **Repositories**
3. Add `https://github.com/cyberjunky/home-assistant-apps`
4. Install **Proxmox Backup Server**, turn **Protection mode** off and set a `root_password` (for this backup server's own login, not your Home Assistant or Proxmox VE password)
5. Start the app and choose **Open Web UI**; log in as `root` with the realm Linux PAM
6. Under **Administration → Storage / Disks → Directory → Create: Directory**, choose your disk (with "Add as Datastore")
7. Give Proxmox VE its own token with backup rights only, and add the datastore under Datacenter → Storage → Add → Proxmox Backup Server (the app's Documentation tab shows the commands)

## Configuration

See [DOCS.md](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-proxmox-bs/DOCS.md) for the options, setting up a disk, connecting Proxmox VE and what to
expect.

## Disclaimer

This app is an independent project. It is not affiliated with, endorsed by or sponsored by
Proxmox Server Solutions GmbH. Proxmox is a registered trademark of Proxmox Server Solutions
GmbH. Keep a second copy of what matters: one disk is one copy.

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

This app's own code is licensed under the MIT License - see the [LICENSE](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-proxmox-bs/LICENSE) file for
details. Proxmox Backup Server is free software by Proxmox Server Solutions GmbH under the
GNU AGPL v3; the image installs it unchanged from Proxmox's repository (amd64) or from
[wofferl/proxmox-backup-arm64](https://github.com/wofferl/proxmox-backup-arm64) (aarch64).

---

[version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcyberjunky%2Fhome-assistant-apps%2Fmain%2Fapp-proxmox-bs%2Fconfig.yaml&query=%24.version&label=version&style=for-the-badge
[changelog]: https://github.com/cyberjunky/home-assistant-apps/blob/main/app-proxmox-bs/CHANGELOG.md
[commits-shield]: https://img.shields.io/github/commit-activity/y/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[commits]: https://github.com/cyberjunky/home-assistant-apps/commits/main
[license-shield]: https://img.shields.io/github/license/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-cyberjunky-blue.svg?style=for-the-badge
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg?style=for-the-badge
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg?style=for-the-badge
