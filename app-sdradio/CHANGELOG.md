# Changelog

## 0.1.4

- **RDS on FM broadcast (WFM):** the station's name shows on the display when the frequency isn't stored (next to your own name when it is), and a line below it shows the artist and title when the station marks them (RadioText+), or else its radio text, with the programme type. A text longer than the line scrolls round. RDS takes a second or two after tuning; a weak station may not get that far.
- **Home Assistant:** `sensor.sdradio_frequency` has the RDS `station`, `radio_text`, `artist`, `title` and `programme_type` as attributes.

## 0.1.3

- **The display follows the radio:** after typing (part of) a frequency, tuning another way (the waterfall, the dial, a scan) showed the typed digits with a blinking cursor instead of the new frequency. Typing now ends when the radio tunes elsewhere, when you click outside the display and keypad, or after 10 seconds without a key; clicking the display a second time closes it too.
- **What to scan shows:** the SCAN key says what it scans (CH, BAND or RANGE), the Channels, Band and Range… buttons explain themselves on hover, and choosing one while scanning switches the scan over.

## 0.1.2

- **The RTL-SDR and HackRF open again:** 0.1.1 couldn't open a receiver it had found and said it was busy (`Device::make() no match`). The receiver was never busy: the app passed its settings to SoapySDR in a form the Alpine version doesn't read.
- **Clearer message when a receiver really is in use:** it says another app is probably using it, without the SoapySDR error text; that goes to the app's log.

## 0.1.1

**After updating:** open the **Configuration** tab and save it once: the `serial` and `sample_rate` options are gone.

- **The radio fits the screen:** a smaller display, the keypad in the top right corner with SCAN, HOLD, PRI and L/O under it, and a smaller tune dial; ◀ ▶ and M+ (store) moved under the stored channels. The waterfall takes the height that is left, so the whole radio fits on a 1440×900 screen or bigger.
- **Receiver options:** type, network host and port, gain, ppm, the HackRF amplifiers and bias tee. The receiver is found automatically (the first one of its type), always at 2.4 MS/s. A HackRF starts with its own LNA, VGA and amp settings.
- **A busy receiver says so:** when the receiver is found but can't be opened, the page says another app is probably using it.
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
