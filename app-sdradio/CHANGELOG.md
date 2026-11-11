# Changelog

## 0.1.1

**After updating:** open the **Configuration** tab and save it once: the `serial` and `sample_rate` options are gone.

- **Receiver options as in the Meldkamer app:** type, network host and port, gain, ppm, the HackRF amplifiers and bias tee. The receiver is found automatically (the first one of its type), always at 2.4 MS/s. A HackRF starts with its own LNA, VGA and amp settings.
- **A busy receiver says so:** when the receiver is found but can't be opened, the page says another app (such as Meldkamer) is probably using it.
- **Info:** the receiver's error wraps inside the panel instead of running out of it; the sample rate reads 2.4 MS/s.

## 0.1.0

**After updating:** plug in the receiver, check the **Configuration** tab (receiver type, sample rate) and start the app.

- First version: a software defined radio receiver in Home Assistant, with an RTL-SDR, a HackRF, any SoapySDR device or an `rtl_tcp` server.
- **Radio:** a receiver front panel with a display (frequency, mode, filter, step, indicators, channel name, signal meter), a band scale, knobs for volume, squelch, RF gain, filter and scan speed, a tune dial and a keypad; a favourites row; a live spectrum and waterfall to click on, and the stored channels beside it.
- **Listen in the browser:** AM, NFM, WFM, USB, LSB, CW and DSB, demodulated in the app.
- **Scan** the stored channels, a band or a range of your own; HOLD, lock-out (L/O) and priority channels (PRI). Every transmission is logged; signals that aren't stored yet show up as new finds to label.
- **Channels:** every stored channel with its mode, filter, step, squelch, gain, band, tags and notes, edited on the page; CSV export and import (also from CHIRP).
- **Stats:** what was heard, by hour and by band; the most active channels; listening time.
- **Settings:** scanner, spectrum, audio, display colour, bands, hit retention, Home Assistant sensors and events.
- **Backups** of the database every night and on request; download, upload and restore on the Channels page.
