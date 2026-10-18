[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

# Cyberjunky's Home Assistant Apps

My collection of Home Assistant apps (add-ons). Each app lives in its own folder with
its documentation, changelog and security policy.

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcyberjunky%2Fhome-assistant-apps)

Alternatively:

1. Go to **Settings → Apps → App Store**
2. Open the menu (⋮) and choose **Repositories**
3. Add the following URL and click **Add**:

```txt
https://github.com/cyberjunky/home-assistant-apps
```

The apps below then appear in the store under **Cyberjunky's Home Assistant Apps**.

## Apps

### [Dishylink](app-dishylink/)

![Version][dishylink-version-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

Starlink dish and router dashboard with 24/7 recorded history, powered by
[Dishylink](https://github.com/DaveyHert/Dishylink). Reads the dish and router over the
local network, opens from the sidebar through ingress, follows your light/dark mode and
keeps flash writes low by recording in RAM.

[:books: Documentation](app-dishylink/DOCS.md) · [:memo: Changelog](app-dishylink/CHANGELOG.md) · [:lock: Security](app-dishylink/SECURITY.md)

## Releases

Each app is versioned on its own, following [Semantic Versioning](https://semver.org/):
`MAJOR` for incompatible changes, `MINOR` for new features, `PATCH` for fixes. The
version in an app's `config.yaml` is what Home Assistant offers as an update; the
matching entry in its `CHANGELOG.md` describes what changed.

Images are built by GitHub Actions and published to the GitHub Container Registry.
Publishing a GitHub release tagged `<app folder>-<version>`, for example
`app-dishylink-1.0.3`, builds that app for every architecture it supports and pushes
`ghcr.io/cyberjunky/<app>:<version>` plus `latest`. Pushes and pull requests only
test-build the apps whose files changed.

## 💖 Support This Project

If you find these apps useful, please consider supporting their continued development
and maintenance:

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

Unless an app folder contains its own `LICENSE` file, this repository is licensed under the
MIT License - see the [LICENSE](LICENSE) file for details. Apps derived from other projects
keep that project's license and a `NOTICE` file with attribution in their own folder. The
software each app packages keeps its own license, noted in the app's README.

---

[commits-shield]: https://img.shields.io/github/commit-activity/y/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[commits]: https://github.com/cyberjunky/home-assistant-apps/commits/main
[license-shield]: https://img.shields.io/github/license/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-cyberjunky-blue.svg?style=for-the-badge
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg?style=for-the-badge
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg?style=for-the-badge
[dishylink-version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcyberjunky%2Fhome-assistant-apps%2Fmain%2Fapp-dishylink%2Fconfig.yaml&query=%24.version&label=version&style=for-the-badge
