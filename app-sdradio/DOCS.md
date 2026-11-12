# Home Assistant App: SDRadio

SDRadio turns an SDR receiver (RTL-SDR, HackRF, any SoapySDR device, or an `rtl_tcp` server on
your network) into a radio you use from the Home Assistant sidebar: tune, listen in your
browser, scan, and keep the channels you find in a database.

After installing, plug in the receiver, check the **Configuration** tab, start the app and open
**SDRadio** from the sidebar. Press **Listen** in the top bar to hear the radio in this browser.

## Options

The receiver is set with the app options below; where the radio starts is an app option too. Everything else is on the app's **Settings** page. The radio receives 2.4 MHz at a
time (2.4 MS/s), the same for every receiver.

| Option | Default | |
|---|---|---|
| `receiver.type` | `rtl-sdr` | `rtl-sdr`, `hackrf`, `soapysdr` (the first SoapySDR device found) or `network` (an `rtl_tcp` server). The receiver itself is found automatically |
| `receiver.network_host` / `network_port` | / `1234` | The `rtl_tcp` server, for type `network` |
| `receiver.sdr_gain` | `40` | Gain in dB at start, `0` = automatic. The **RF gain** knob changes it while listening |
| `receiver.ppm_correction` | `0` | Frequency correction of the receiver's crystal |
| `receiver.hackrf_lna_gain` / `hackrf_vga_gain` / `hackrf_amp_enable` | `40` / `30` / `true` | The HackRF's own amplifiers |
| `receiver.bias_tee` | `false` | Power for an active antenna or LNA (RTL-SDR only) |
| `tune.frequency` / `tune.mode` | `145.5` / `nfm` | Where the radio starts, in MHz |
| `tune.restore_last` | `true` | Start where you left off instead |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

One receiver can only be used by one app at a time. To share a dongle with another app, run
`rtl_tcp` for it on another machine and use type `network` in one of them.

## The radio

**The display** shows the frequency, the mode (click it for the next one), the filter width and
the tuning step, the channel's name when it is stored, and the signal level in dBFS with the
signal-to-noise ratio. Along the top: SCAN, HOLD and PRI light up while they are on; **AGC**
(automatic gain for AM, SSB and CW), **NB** (noise blanker) and **MUTE** switch with a click;
**L/O** shows that this channel is locked out of scans.

**Power**: the button at the top left of the display switches the radio off and on. Off, the
receiver is closed and nothing is processed, so SDRadio uses next to no CPU and another program
can use the receiver meanwhile. It stays off after a restart until you switch it on again; the
`sensor.sdradio_frequency` attribute `power` shows `on` or `off`.

**RDS** (WFM): an FM broadcast station's name shows where the channel name goes when the
frequency isn't stored, and a line below it shows the artist and title (when the station marks
them with RadioText+) or else its radio text, with the programme type on the right. It is read
from the 57 kHz RDS signal and takes a second or two after tuning; a weak station may not get
that far.

**Tuning**
- Scroll over a digit of the frequency to change that digit.
- Type a frequency in MHz on the keypad or keyboard and press **MHz** or Enter (CLR or Esc cancels).
- Turn the **Tune** dial (drag round, scroll, or the arrow keys); ↑ ↓ and PgUp PgDn work anywhere on the page.
- **AUTO** on the display (or T) centres on the strongest signal within a channel of where you are: a broadcast station on the middle of its width, on the band's channel grid when it is that close to a channel. It works on AM, FM and DSB; SSB and CW are tuned by ear.
- Click the spectrum or waterfall, drag it to move, or scroll it to step.
- Click or drag the band scale under the display.
- **BAND** goes to the next band, **STEP** to the next tuning step, **MODE** to the next mode.

Going into another band takes that band's mode, filter and step; landing on a stored channel
takes the channel's own settings (mode, filter, step, squelch, gain).

The band plan covers LW to 6 GHz: long and medium wave, the HF amateur and broadcast bands, CB,
VHF and UHF, and with a HackRF 13 cm, 2.4 GHz, 9 cm, 6 cm and 5.8 GHz. Only the bands your
receiver can tune to are used (an RTL-SDR 24 to 1766 MHz, a HackRF 1 MHz to 6 GHz): BAND skips the
others, the Settings page hides them, and a frequency out of range is refused with a message.
Less used bands have no BAND button; switch it on for a band on the Settings page.

**The spectrum** shows **2.4M**, everything the receiver takes in at once, or a zoom of 1 MHz,
200 kHz or 50 kHz round the tuned frequency. **Band** shows the whole band the radio is in (or
10 MHz round it, at most 60 MHz): the receiver sweeps across it in steps, about a second and a
half for the FM band, and the radio is silent meanwhile. Click a station there to listen to it.

**Knobs**: drag up or down, scroll, or use the arrow keys; double-click for the default.
Volume is per browser. Squelch fully left is off. RF gain fully left is automatic. Filter steps
through the widths the mode offers. Scan speed is how many channels a second the scan looks at.

**Favourites**: the row under the front panel. **+ ★** adds the current channel (or stores the
frequency as a new favourite); **− ★** takes it off again.

**Stored channels**: the list in the bottom right, for this band, all bands or the favourites,
with search. Click one to tune to it. **Store current** saves the frequency with its mode,
filter, step and squelch (or updates the stored channel you are on); **Edit…** opens it on the
Channels page. Under the list, **M+** stores and **◀ ▶** step through the stored channels.

## Scanning

Pick what to scan under the keypad, then press **SCAN**:
- **Channels**: the stored channels that are in the scan, of this band when the list shows *This band*, otherwise all.
- **Band**: every step of the band you are in (from the band plan on the Settings page).

The scan stops on every signal above the squelch and stays while it lasts; after the
**Resume after** time of quiet it moves on. With the squelch off, it stops on a signal 10 dB
above the noise, and it never stops on a signal less than 6 dB above the noise, so a squelch
set too low doesn't stop it on every channel.

- **SCAN** on a signal (the key then reads *NEXT*) goes on to the next channel without waiting; pressed while the scan is stepping, it stops the scan. Tuning anywhere stops it too.
- **HOLD** stays on the current channel until you press it again, or until SCAN goes on.
- **L/O** (lock out) takes a stored channel out of the scans, or ignores a frequency that isn't stored in band scans; press it again to undo.
- **PRI** (priority): channels marked *Priority* on the Channels page are checked every few seconds while you listen elsewhere, and between the other channels while scanning. When one is busy, the radio goes there and comes back after.

Every transmission the squelch lets through is logged as a hit: while scanning, and while
listening with a squelch set. A frequency with signals that isn't stored becomes a **new find**
after a few hits (the number is on the Settings page).

## Modes

The **mode buttons** under the tune dial set the radio up for one kind of signal and decode
what it hears. The radio tunes to the mode's frequency (set on the Settings page, under Modes);
you can still tune elsewhere and listen. In place of the favourites and the stored channels the
page shows what the mode decodes. The modes only show up when your receiver can use them.
In a mode the display shows how its signal is modulated (FSK for pagers, OOK/FSK for sensors,
PPM for ADS-B, MSK on AM for ACARS, GMSK for AIS, AFSK for APRS) and what decodes it; the
demodulation beside it is only how it sounds with Listen. The modes whose decoder reads the
receiver's samples and that carry no voice (Sensors, Aircraft, ACARS, Ships, Video) have no sound:
the radio doesn't demodulate audio for them, and the meter shows the level in the channel.
Pager, APRS, SSTV and Satellites decode from the audio and can be listened to. What would take the mode off its
frequency or change how it listens is off meanwhile: SCAN, HOLD, PRI, L/O, MODE, BAND, AUTO,
the filter and the scan speed. Mode → Radio brings them back.

- **Pager**: POCSAG (512, 1200 and 2400 baud) and FLEX messages, decoded by multimon-ng from
  the radio's own NFM audio. 169.65 MHz is P2000, the Dutch emergency services; Settings → Modes has the other networks to pick (KPN Nationaal 1 and 2, KPN Benelux, DAPNET), which set the frequency and protocols. Search the
  messages by text or capcode. With **Pager events** on, every message is an `sdradio_pager`
  event in Home Assistant.
- **Sensors**: weather stations, thermometers, doorbells, remotes and meters, decoded by rtl_433
  (some 250 kinds of device). 433.92 MHz for most, 868.3 MHz for many weather stations,
  868.95 MHz for wireless M-Bus meters. Each device shows its last reading; tick **HA** to get
  its readings as Home Assistant sensors (`sensor.sdradio_<device>_temperature_c` and so on).
- **Aircraft**: ADS-B on 1090 MHz, decoded by dump1090. The aircraft in view are on a map with
  the trail each one leaves, coloured by altitude, and in a list with their callsign,
  registration and type (from dump1090's aircraft database), altitude, speed, distance and
  squawk; an emergency squawk (7500, 7600, 7700) is marked. An aircraft goes into the
  **History** when it has been out of reach for a minute. For this mode the receiver is centred
  on 1090 MHz, as ADS-B is 2 MHz wide. The stock RTL-SDR antenna works best high up and outside;
  a 1090 MHz antenna reaches 200 km and more.

- **ACARS**: the short text messages between aircraft and their airlines (positions, out/off/on/in
  times, gate and weather requests), on up to three channels at once: 131.525, 131.725 and
  131.825 MHz in Europe (set under Modes). Decoded by SDRadio itself; each message shows the
  flight, the registration, its label and the text.
- **Ships**: AIS on 161.975 and 162.025 MHz (both channels at once: the radio tunes between them),
  decoded by SDRadio itself. Ships on rivers, canals and the sea send their position every few
  seconds while under way and their name, call sign, kind and destination every few minutes; on
  the map they point where they sail, coloured by kind (cargo green, tanker red, passenger blue,
  pleasure craft purple), and lie as a dot when moored. A ship goes into the **History** when it
  has not been heard for a quarter of an hour.

- **APRS**: amateur radio positions, weather stations, objects, status texts and messages on
  144.800 MHz (144.390 in North America, 145.825 for the ISS; set under Modes), decoded by
  multimon-ng with the packets read by SDRadio (plain, compressed and Mic-E positions, weather
  reports). Stations with a position are on the map with their symbol, moving ones with their
  trail; **Packets** lists everything heard.

- **SSTV**: pictures sent as sound (slow-scan television), decoded by SDRadio itself: PD50 to
  PD290 (the ISS sends PD120 and PD180), Martin M1 and M2, and Robot 36. 145.800 MHz is the ISS
  during its SSTV events, 144.500 MHz radio amateurs on 2 m, 14.230 MHz on HF (upper sideband,
  HackRF). A picture grows line by line while it comes in and then goes into the gallery; the
  pictures are kept in the app's data folder as long as the other decoded data.

- **Satellites**: the passes over Home Assistant's home of the satellites you follow (Settings →
  Modes), worked out from their orbits (fetched from Celestrak once a day). During a pass the
  radio follows the satellite with the Doppler shift taken out (up to ±3.5 kHz on 2 m, ±10 kHz on
  70 cm) and keeps what it brings:
  - **ISS** on 145.800 MHz: its voice, and during its SSTV events the pictures (as the SSTV mode);
  - **ISS APRS** on 145.825 MHz: the packets its digipeater relays;
  - **SO-50** on 436.795 MHz: an FM repeater to listen to;
  - **Meteor-M2-3 and M2-4** on 137.1 / 137.9 MHz (their frequencies change now and then: set them
    under Modes): with *Record Meteor passes* on, a pass is recorded as 8-bit IQ at 200 kHz (about
    300 MB, the last few kept) to download and turn into weather pictures with SatDump on a
    computer. Decoding the pictures themselves needs far more computing than fits in the app.

  The map shows where they are, their tracks for the next orbit and the area each one can be heard
  in; **Passes** lists the coming day's (when, how high, from where to where), **Heard** the
  passes that are over, with their pictures, packets and recordings. The NOAA weather satellites
  were switched off in 2025.

- **Video** (HackRF only): analog video as FPV drones and wireless cameras send it, in black and
  white, a few frames a second. Analog video is FM some 16 MHz wide, so in this mode the HackRF
  runs at 20 MS/s (back to 2.4 when you leave it); the RTL-SDR takes in 2.4 MHz, too little. PAL
  or NTSC, and video sent upside down, are recognised by themselves. 5.8 GHz FPV (Raceband
  R1–R8, F4 5800), 1.2 GHz and 2.4 GHz cameras are under Modes. **Snapshot** keeps a picture.
  Only for video that is meant to be seen: drones (yours, or at a race) and your own cameras;
  watching other people's camera feeds is not allowed.

A mode your receiver can't do is ghosted: hovering over it says why ("HackRF only: …").

**The map** (Aircraft, Ships, APRS, Satellites) shows OpenStreetMap through Home Assistant's own map tile service (which
needs Home Assistant 2026.9 or later), centred on Home Assistant's home location; the distances
are from there. The map is darkened only with the dark theme.

What the modes decode is kept in the database (30 days by default, set under Modes).

## Channels

The Channels page lists every stored channel; click one to edit it on the right. The views at
the top show all channels, the favourites, the **new finds** (listen, *Label & store* or ignore
them) and the locked-out channels.

**CSV**: *Export CSV* saves every channel. *Import CSV…* adds channels from a CSV with a header
row: SDRadio's own export, or lists from CHIRP and similar programs. The columns it knows are
`number` (or `location`), `name`, `frequency_mhz` (or `frequency`), `mode`, `bandwidth_khz`,
`step_khz`, `squelch_db`, `band`, `tags`, `notes`, `favourite`, `scan` and `priority`. A row
whose number, or frequency and mode, is already stored updates that channel. With *Replace*, the
stored channels are replaced. A file with errors imports nothing and says which lines are wrong.

## Backups

The database (`/data/sdradio.db`) is backed up every night (the time and how many are kept are on
the Settings page) and with **Back up now**. The Channels page lists the backups to download or
restore; restoring first makes a backup of the current database. *Upload a backup…* adds a
downloaded backup to the list. The database and its backups are in the app's data folder, so
Home Assistant backups include them.

## Home Assistant

The modes add the `sdradio_pager` event and the sensors of the devices picked in Sensors (see Modes).

On the Settings page:
- **Sensors**: `sensor.sdradio_frequency` (MHz, with the mode, band and channel, and on FM the RDS `station`, `radio_text`, `artist`, `title` and `programme_type`), `sensor.sdradio_signal` (dBFS) and `sensor.sdradio_scan` (`off`, `scanning` or `holding`), updated at most every 5 seconds.
- **Events**: `sdradio_signal` for every transmission on a stored channel, with `frequency`, `frequency_mhz`, `mode`, `channel`, `channel_number`, `favourite`, `duration` and `peak`.

## How it works

The app reads the receiver's samples through SoapySDR, 20 blocks a second. Every block it
computes the spectrum (an averaged FFT), shifts the tuned frequency to 0 Hz, filters and
decimates it to the channel and demodulates it with numpy and scipy: AM by its envelope, NFM and
WFM by the phase change (WFM with 50 µs de-emphasis), SSB, CW and DSB as the filtered real
signal. The receiver only retunes when you go outside the span it receives, so tuning within the
waterfall is instant. Audio and spectrum go to the page over one WebSocket; audio only to pages
that listen.

All of that runs on the Home Assistant machine; a Raspberry Pi 4 or better is advised.

## Support

- [Issues](https://github.com/cyberjunky/home-assistant-apps/issues)
- [Source](https://github.com/cyberjunky/home-assistant-apps/tree/main/app-sdradio)
