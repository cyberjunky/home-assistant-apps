[![Version][version-shield]][changelog]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

# SDRadio App for Home Assistant

<img src="https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/icon.png" alt="SDRadio" width="128" align="right">

A software defined radio receiver in Home Assistant, opened from the sidebar. Plug in an
RTL-SDR or HackRF (or point it at an `rtl_tcp` server), and you get a receiver front panel: a
display with the frequency, mode and signal meter, knobs, a tune dial and a keypad, a live
spectrum and waterfall to click on, and your stored channels beside it. Listen in the browser,
scan your channels or a band, and keep every channel and every transmission heard in a
database, with CSV import and export, statistics and nightly backups. The mode buttons under the
tune dial turn it into a decoder: pagers, weather sensors, aircraft, ships, weather balloons
and radio amateurs on a map, ACARS and VDL2, APRS, FT8, DMR and D-STAR voice, SSTV pictures, DAB+ digital radio,
satellite passes, and with a HackRF weather fax charts and analog FPV video.

![The radio: display, band scale, knobs, tune dial with the mode buttons and keypad, favourites, the spectrum and waterfall, and the stored channels (light theme)](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/radio-light.webp)

![The Aircraft mode: ADS-B on 1090 MHz, the aircraft on a map with their trails and in a list](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-aircraft-light.webp)

<details>
<summary>More screenshots</summary>

**Channels** — every stored channel, edited on the page, with CSV import and export and the backups
![Channels](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/channels-light.webp)

**New finds** — signals the scan found that aren't stored yet: listen, label and store, or ignore
![New finds](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/finds-light.webp)

**Stats** — what was heard, by hour and by band, and the most active channels
![Stats](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/stats-light.webp)

**Settings** — the receiver options, scanner, spectrum, audio, backups, Home Assistant and the band plan
![Settings](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/settings-light.webp)

**The radio, dark theme**
![The radio in the dark theme](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/radio-dark.webp)

**Pager** — P2000 (FLEX) and POCSAG messages, decoded by multimon-ng
![Pager mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-pager-light.webp)

**Ships** — AIS on both channels: the ships on a map, pointing where they sail, with name and destination
![Ships mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-ships-light.webp)

**APRS** — radio amateur stations, cars and weather stations on 144.800 MHz, with their symbols
![APRS mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-aprs-light.webp)

**ACARS** — the text messages of aircraft, three channels at once
![ACARS mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-acars-light.webp)

**VDL2** — ACARS's digital successor, four channels at once, decoded by dumpvdl2
![VDL2 mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-vdl2-light.webp)

**Radiosondes** — weather balloons on a map, from the launch to where they come down
![Radiosondes mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-sondes-light.webp)

**FT8** — radio amateurs far away, on a map by their grid square, coloured by signal strength
![FT8 mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-ft8-light.webp)

**Weather fax** (HackRF) — the DWD's weather charts on shortwave, line by line
![Weather fax mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-wefax-light.webp)

**SSTV** — pictures sent as sound, from the ISS and radio amateurs
![SSTV mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-sstv-light.webp)

**DAB+** — the stations of a DAB+ block with what's playing; click one to listen
![DAB+ mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-dab-light.webp)

**Satellites** — the passes over your home, followed with the Doppler shift taken out
![Satellites mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-satellites-light.webp)

**Video** (HackRF) — analog FPV video, in black and white
![Video mode](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/mode-video-light.webp)

**Settings → Modes** — pager networks, the frequencies and options of each mode
![Settings for the modes](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/screenshots/settings-modes-light.webp)

</details>

## Supported Features

- 📻 **Receiver front panel** — Frequency display (scroll a digit to change it), mode, filter and step, indicators, channel name and a signal meter in dBFS with SNR; a band scale like an old radio dial; knobs for volume, squelch, RF gain, filter and scan speed; a tune dial and a keypad
- 🌊 **Spectrum and waterfall** — Live, with your stored channels labelled; click to tune, drag to move, scroll to step; zoom from the whole band (swept) to 50 kHz
- 🎧 **Listen in the browser** — AM, NFM, WFM, USB, LSB, CW and DSB, demodulated in the app; AGC and noise blanker
- 📟 **Modes** — The radio set up for pagers (POCSAG and FLEX, with multimon-ng), sensors on 433/868 MHz (weather stations, thermometers, meters, with rtl_433), aircraft (ADS-B with dump1090), ships (AIS), APRS stations, radiosondes (weather balloons, with rs1729's decoders) and FT8/FT4 stations (ft8_lib) on a map, ACARS and VDL2 messages (dumpvdl2), DMR, D-STAR, YSF, NXDN and P25 calls heard as speech (DSD-FME), SSTV pictures, DAB+ digital radio (welle-cli), satellite passes followed with Doppler correction (ISS, SO-50, Meteor), and with the HackRF weather fax charts and analog FPV video, each with its own view and Home Assistant events or sensors
- 🔔 **Tones** — On NFM: the CTCSS tone or DCS code under a transmission, and DTMF digits
- 📝 **RDS** — On FM broadcast: the station's name, its radio text, and the artist and title when the station sends them
- 🔍 **Scanning** — The stored channels or the band you are in; HOLD, lock-out and priority channels; every transmission logged, and unknown signals listed as new finds
- 🗂️ **Channels** — Name, frequency, mode, filter, step, squelch, gain, band, tags, notes, favourite, scan and priority; favourites one click away on the radio
- 📄 **CSV** — Export, and import from SDRadio or CHIRP-style lists
- 📊 **Stats** — Hits by hour and band, recently heard, the most active channels, listening time
- 💾 **Backups** — The database every night and on request; download, upload and restore
- 🏠 **Home Assistant** — Optional sensors for the frequency, signal and scan, and an `sdradio_signal` event for automations
- 🌗 **Light and dark** — Light by default, dark with a click

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcyberjunky%2Fhome-assistant-apps)

Alternatively:

1. Go to **Settings → Apps → App Store**
2. Open the menu (⋮) and choose **Repositories**
3. Add `https://github.com/cyberjunky/home-assistant-apps`
4. Install **SDRadio**, plug in the receiver, check the Configuration tab and start it
5. Open **SDRadio** from the sidebar and press **Listen**

## Configuration

| Option | Default | |
|---|---|---|
| `receiver.type` | `rtl-sdr` | `rtl-sdr`, `hackrf`, `soapysdr` or `network` (`rtl_tcp`); the receiver is found automatically |
| `receiver.sdr_gain` | `40` | `0` = automatic; the RF gain knob changes it while listening |
| `tune.frequency` / `tune.mode` | `145.5` / `nfm` | Where the radio starts |

All options, and how scanning, channels and backups work: [DOCS.md](DOCS.md).

**HackRF or RTL-SDR?** Both do nearly everything. The HackRF adds shortwave (below 24 MHz: HF
broadcast, FT8 on 7 and 14 MHz, weather fax), 1.7 to 6 GHz and the 20 MHz that analog video
needs; the RTL-SDR is a little more sensitive on VHF and UHF and draws less power. The table is
in [DOCS.md](DOCS.md#hackrf-or-rtl-sdr).

One receiver serves one app at a time: two apps can't share a dongle, unless one of them uses it
through `rtl_tcp`.

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

[version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcyberjunky%2Fhome-assistant-apps%2Fmain%2Fapp-sdradio%2Fconfig.yaml&query=%24.version&label=version&style=for-the-badge
[changelog]: https://github.com/cyberjunky/home-assistant-apps/blob/main/app-sdradio/CHANGELOG.md
[commits-shield]: https://img.shields.io/github/commit-activity/y/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[commits]: https://github.com/cyberjunky/home-assistant-apps/commits/main
[license-shield]: https://img.shields.io/github/license/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-cyberjunky-blue.svg?style=for-the-badge
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg
