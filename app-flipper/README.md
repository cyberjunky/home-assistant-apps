[![Version][version-shield]][changelog]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-flipper/LICENSE)
![Project Maintenance][maintenance-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

# Flipper App for Home Assistant

<img src="https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-flipper/icon.png" alt="Flipper" width="128" align="right">

A workbench for a Flipper Zero plugged into the machine Home Assistant runs on. Open it from the
sidebar to see the Flipper's screen and press its buttons, browse and change the files on its SD card,
and write, build and start apps on it: C apps built with Flipper's own build tool, and JavaScript the
Flipper runs as it is. The Flipper's command line and log are a tab away.

Not made by, or affiliated with, Flipper Devices.

![Device: the Flipper with its live screen and buttons, its name, firmware, battery and SD card, and what it says about itself (light theme)](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-flipper/screenshots/device-light.webp)

<details>
<summary>More screenshots</summary>

**Code** — projects, the editor, Build & run, the build's output, and the Flipper's screen beside it
![Code](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-flipper/screenshots/code-dark.webp)

**Files** — what is on the SD card and the internal storage: upload, download, rename, delete, run
![Files](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-flipper/screenshots/files-light.webp)

**Console** — the Flipper's own command line and its log
![Console](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-flipper/screenshots/console-light.webp)

</details>

The screenshots show a stand-in Flipper used for development, not a real one.

## What it does

- **Screen and buttons:** the Flipper's screen, live, and its six buttons on the page and on your
  keyboard, with short and long presses. Save a screenshot.
- **Files:** browse the SD card and the internal storage; upload, download, rename, delete, make
  folders; start a `.fap` or a `.js`.
- **C apps:** a project with an editor, **Build** (ufbt, Flipper's build tool) and **Build & run**, which
  puts the app on the Flipper and starts it. A compiler error is a click away from its line.
- **JavaScript:** scripts are put on the Flipper and started with its JS Runner. No build, no SDK.
- **Console:** the Flipper's command line and its log, at the level you choose.
- **Device:** name, firmware, battery, SD card; set the clock, make it beep to find it, restart it.

## Installation

1. Add this repository to Home Assistant (see the [repository README](https://github.com/cyberjunky/home-assistant-apps#installation)).
2. Install **Flipper**, start it, and open it from the sidebar.
3. Plug the Flipper into the machine Home Assistant runs on, with a USB cable that carries data.

See the [documentation](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-flipper/DOCS.md) for building apps, the SDK and what to know.

## Status

Version 0.1.0 is new. The SDK install and the build of both C templates were run for real in the app's
image; the link to the Flipper was written from the firmware's source and tested against a stand-in,
not yet on a real Flipper. Please report what you find.

[version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcyberjunky%2Fhome-assistant-apps%2Fmain%2Fapp-flipper%2Fconfig.yaml&query=%24.version&label=version&style=for-the-badge
[changelog]: https://github.com/cyberjunky/home-assistant-apps/blob/main/app-flipper/CHANGELOG.md
[commits-shield]: https://img.shields.io/github/commit-activity/y/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[commits]: https://github.com/cyberjunky/home-assistant-apps/commits/main
[license-shield]: https://img.shields.io/github/license/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-cyberjunky-blue.svg?style=for-the-badge
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg?style=for-the-badge
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg?style=for-the-badge
