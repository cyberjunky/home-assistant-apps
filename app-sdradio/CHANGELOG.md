# Changelog

## 0.5.2

**After updating:** nothing to do.

- **Fixed: channels this receiver can't use** were clickable and only gave a message. They are faded now, as 0.4.1 meant them to be, and can't be picked; hovering one says why. That covers shortwave and other out-of-range channels on an RTL-SDR, and channels of a mode it can't do (Video, Weather fax). It applies in the channel list, the favourites row and the Channels page (tagged *out of range* or *not on this receiver*), and ◀ ▶ skips them. (A check of the same name for the band plan had silently replaced the channel check in the page.)
- The Digital voice mode's button reads *Digital*, so it fits.

## 0.5.1

**After updating:** nothing to do.

- **No sound in the data modes:** Pager, APRS, Radiosondes, FT8, SSTV and Weather fax still decode the audio, but Listen no longer plays their bursts and tones; the display says *no sound*. Satellites (voice from the ISS and SO-50), Digital voice and DAB+ keep theirs.

## 0.5.0

**After updating:** import the [channel list](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/sdradio-channels.csv) again (without *Replace*) for the new modes' channels: radiosondes, VDL2, FT8 on 40, 20, 10 and 6 m, digital voice calling on 2 m and 70 cm, the DWD's and Northwood's weather fax.

- **Radiosondes** (new mode): the weather balloons launched twice a day (De Bilt for the Netherlands), on a map with their track, altitude, climb, temperature, humidity and pressure, and in the history once they are down, with how high they got. Decoded by rs1729's RS decoders (RS41, DFM, M10, M20), now built into the app.
- **VDL2** (new mode): ACARS's digital successor, busier than ACARS over Europe: ACARS messages and controller-pilot messages on the four European channels at once, decoded by dumpvdl2 (with libacars), now built in. The receiver runs at 2.1 MS/s in this mode.
- **FT8 / FT4** (new mode): the radio amateurs' weak-signal modes, decoded by ft8_lib slot by slot on the UTC clock; the stations that give their grid square on a map, coloured by signal strength, and every decode in a list. 28.074 and 50.313 MHz with an RTL-SDR, 7.074 and 14.074 MHz with a HackRF.
- **Digital voice** (new mode): DMR, D-STAR, YSF, NXDN, dPMR and P25 phase 1, decoded by DSD-FME (with mbelib), now built in. Each call is listed with its talkgroup and who spoke (and a text message, talker alias or GPS position when there is one), and Listen plays the decoded speech instead of the FM audio. Set the repeater or hotspot and the protocol under Settings → Modes.
- **Weather fax** (new mode, HackRF): the weather charts the DWD (Pinneberg, 3855 / 7880 / 13882.5 kHz) and others send for ships on shortwave, decoded by SDRadio itself from start tone to stop tone, lined up on the phasing lines; they grow line by line and go into a gallery.
- **Tones** on NFM: the line under the display shows the CTCSS tone or DCS code under a transmission and the DTMF digits keyed on it.
- **HackRF or RTL-SDR:** the docs now say what each one can do and which modes need which.

## 0.4.1

**After updating:** to give your stored channels their mode (P2000 and KPN to Pager, ISM 433 and SRD 868 to Sensors, AIS to Ships and so on), import the channel list again on the Channels page, without *Replace*: channels stored from an older list have no `mode:` tag, so picking them stays in the radio.

- **Channels outside the receiver's range** (shortwave on an RTL-SDR) are faded in the channel list, tagged *out of range* and can't be picked; the Channels page marks them too, where they can still be edited or deleted.
- **Locked-out channels** are greyed with a small L/O tag instead of struck through.
- **DAB+:** the display says *click a station to listen* instead of *no sound*.
- **Phone:** the top bar shows the name, Listen and the theme button (last) on top and the menu full width below, scrolling sideways instead of losing a tab. On a laptop the menu gives way the same way and the clock goes first.
- **Front panel:** the mode buttons keep their width at every screen size; below 1100 px the dial with the modes, the knobs and the keys sit side by side under the display.

## 0.4.0

**After updating:** nothing to do.

- **DAB+** (new mode): digital radio. The stations of a DAB+ block, decoded by welle-cli (now built into the app), with their programme type, bitrate and what is playing now; click a station to hear it in the page, with its slides. The receiver runs at 2.048 MS/s in this mode. Pick the block under Settings → Modes (12C NPO, 11C commercial in the Netherlands).
- **A channel with a mode:** a stored channel can belong to a mode (the new *Mode* field when storing or editing, or the tag `mode:dab`, `mode:pager` … in a CSV). Picking it switches the radio to that mode on the channel's frequency; a channel without one switches back to the radio.
- **The stored channels and the favourites stay** in every mode, beside the mode's view, so another channel or mode is a click away.
- **Fixed:** the Home Assistant tick on a sensor was not saved (it looked ticked until the next reading); clicking an APRS station did nothing; the waterfall and display colours could not be changed. A value with quotes broke the page's handlers.
- **No sound** in the modes whose decoder reads the samples and that carry no voice (Sensors, Aircraft, ACARS, Ships, Video): no audio demodulated for nothing, and the meter shows the channel's level.

## 0.3.0

**After updating:** nothing to do. A range scan you had set up is gone: SCAN does the band you are in, or your stored channels.

- **Mode buttons** under the tune dial, in place of the Mode list in the top bar. A mode your receiver can't do stays there, ghosted; hovering says why ("HackRF only: …").
- **A mode keeps to its frequency:** SCAN, HOLD, PRI, L/O, MODE, BAND, AUTO, the filter and the scan speed are off in a mode (Mode → Radio brings them back). The display shows how the mode's signal is modulated (FSK, OOK/FSK, PPM, GMSK, AFSK, …) and what decodes it.
- **No range scan:** SCAN steps through the band you are in (with the band's own step and mode) or through the stored channels.
- **Pager networks** to pick under Settings → Modes: P2000, KPN Nationaal 1 and 2, KPN Benelux, DAPNET; they set the frequency and the protocols.
- **Sensors** says how many bursts rtl_433 heard and how many it decoded, and what to try when it heard nothing (the RF gain, the antenna).
- **SSTV** (new mode): pictures sent as sound, decoded by SDRadio itself: PD50 to PD290 (the ISS sends PD120 and PD180), Martin M1 and M2, Robot 36. A picture grows line by line while it comes in, then goes into the gallery.
- **Satellites** (new mode): the passes over your home of the ISS, SO-50 and Meteor-M2-3 and M2-4, from their orbits (Celestrak), on a world map with their tracks. During a pass the radio follows the satellite with the Doppler shift taken out and keeps what it brings: ISS voice and SSTV pictures, ISS APRS packets, and Meteor passes recorded as IQ for SatDump (off by default: about 300 MB a pass).
- **Video** (new mode, HackRF only): analog FPV video (5.8 GHz, and 1.2 and 2.4 GHz cameras) in black and white, a few frames a second, PAL or NTSC; the HackRF runs at 20 MS/s in this mode. **Snapshot** keeps a picture.
- **Theme:** follows Home Assistant's light or dark mode by default, as the other apps; a choice made before stays.
- **README** with new screenshots of every mode.

## 0.2.1

**After updating:** nothing to do.

- **Theme:** the separate light and dark buttons are one button that cycles **auto** (follows Home Assistant's light or dark mode), **light** and **dark**, as in the other apps. SDRadio still starts light; a choice made before stays.

## 0.2.0

**Modes:** next to Listen in the top bar, **Mode** sets the radio up for one kind of signal and decodes what it hears, with its own view in place of the favourites and stored channels. Only the modes your receiver can use show up; the radio stays usable meanwhile.

- **Pager:** POCSAG (512, 1200, 2400) and FLEX with multimon-ng, 169.65 MHz (P2000) to start with; searchable, and optionally an `sdradio_pager` event in Home Assistant for every message.
- **Sensors:** weather stations, thermometers, remotes and meters on 433 and 868 MHz with rtl_433 (some 250 kinds); each device's last reading, and its readings as Home Assistant sensors with a tick.
- **Aircraft:** ADS-B on 1090 MHz with dump1090, on a map with their trails, coloured by altitude, and a list with callsign, registration and type, altitude, speed, distance and squawk.
- **Ships:** AIS on both channels, on the map pointing where they sail, coloured by kind, with name, call sign and destination.
- **APRS:** stations, cars and weather stations on 144.800 MHz on the map with their symbol, and every packet in a log.
- **ACARS:** the text messages of aircraft on 131.525, 131.725 and 131.825 MHz at once.
- **The map** shows OpenStreetMap through Home Assistant's own map tile service, centred on your home; it is darkened only with the dark theme. This needs Home Assistant 2026.9 or later.
- Settings → Modes holds each mode's frequency and options, and how long decoded data is kept (30 days).

## 0.1.6

- **Power button:** at the top left of the display. Off closes the receiver and stops all processing, so SDRadio uses next to no CPU and another program can use the receiver meanwhile; on opens it again where it was. It stays off after a restart until you switch it on. `sensor.sdradio_frequency` has a `power` attribute (`on` or `off`).
- **AUTO (autotune):** the AUTO button on the display (or the T key) centres the radio on the strongest signal within a channel of where you are: a broadcast station on the middle of its width, on the band's channel grid when it is that close to a channel. For AM, FM and DSB; SSB and CW are tuned by ear.
- **Going on while scanning:** when the scan has stopped on a signal (or is held), the SCAN key reads NEXT and goes on to the next channel at once; while the scan is stepping it still stops it.
- **Range scan:** the Range… window has a *Fill in* button that puts in the band you are in, to narrow down from.

## 0.1.5

**After updating:** to get the new band plan, press **Settings → Bands → Reset to defaults** (bands you changed yourself are lost; stored channels stay).

- **Band view:** the spectrum's **Band** button shows the whole band the radio is in, swept by the receiver step by step (about a second and a half for the FM band); the radio is silent meanwhile, and a click on a station tunes there and lets you hear it. The old Full button is now **2.4M**: everything the receiver takes in at once.
- **Band plan from LW to 6 GHz:** the HF amateur and broadcast bands, 6 m, 4 m, military air, LPD 433, SRD 868, 23 cm, and for a HackRF 13 cm, 2.4 GHz, 9 cm, 6 cm and 5.8 GHz. Less used bands have no BAND button.
- **Only what your receiver can tune:** BAND skips bands outside the receiver's range (an RTL-SDR 24 to 1766 MHz, a HackRF 1 MHz to 6 GHz), the Settings page hides them, and a frequency out of range is refused with a message instead of the receiver stopping at its lowest frequency. Scans leave out what is out of range.

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
