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

In the channel list beside the radio, a channel locked out of scans is greyed with an **L/O** tag;
a channel outside the receiver's range (shortwave on an RTL-SDR, for example) is faded, tagged
*out of range* and can't be picked. The Channels page marks it *out of range* too, so you can still
edit or delete it there.

**Power**: the button at the top left of the display switches the radio off and on. Off, the
receiver is closed and nothing is processed, so SDRadio uses next to no CPU and another program
can use the receiver meanwhile. It stays off after a restart until you switch it on again; the
`sensor.sdradio_frequency` attribute `power` shows `on` or `off`.

**RDS** (WFM): an FM broadcast station's name shows where the channel name goes when the
frequency isn't stored, and a line below it shows the artist and title (when the station marks
them with RadioText+) or else its radio text, with the programme type on the right. It is read
from the 57 kHz RDS signal and takes a second or two after tuning; a weak station may not get
that far.

**Tones** (NFM, in the Radio mode): the line under the display shows the **CTCSS** tone (67 to
254.1 Hz) or the **DCS** code a transmission carries under the speech, the way repeaters and groups
of walkie-talkies tell their users apart, and the **DTMF** digits keyed on it. A tone takes a second
to read and stays shown for two seconds after it stops; DTMF digits for ten. DCS codes that are
each other's rotation are the same signal on air (023 and 047 inverted, for example), as on any
radio; the lowest of them is shown, with N or I for normal or inverted.

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
you can still tune elsewhere and listen. Beside the spectrum the page shows what the mode decodes,
and the favourites and the stored channels stay, so another channel (or mode) is a click away.

**A channel with a mode**: a stored channel can belong to a mode (the *Mode* field when storing or
editing it; in a CSV the tag `mode:<mode>`, e.g. `mode:dab`, `mode:pager`). Picking it (in the
list, with ◀ ▶ or as a favourite) switches the radio to that mode on the channel's frequency; a
channel without one switches back to the radio. A channel stored while in a mode belongs to it.
In a mode the display shows how its signal is modulated (FSK for pagers, OOK/FSK for sensors,
PPM for ADS-B, MSK on AM for ACARS, D8PSK for VDL2, GMSK for AIS, AFSK for APRS, GFSK for
radiosondes, 8-FSK for FT8, 4FSK or GMSK for digital voice) and what decodes it; the
demodulation beside it is only how it sounds with Listen. The modes whose decoder reads the
receiver's samples and that carry no voice (Sensors, Aircraft, ACARS, VDL2, Ships, Video) have no sound:
the radio doesn't demodulate audio for them, and the meter shows the level in the channel.
Pager, APRS, Radiosondes, FT8, SSTV, Weather fax and Satellites decode from the audio and can be
listened to; Digital voice plays the speech it decodes; DAB+ plays its
stations in the page itself. What would take the mode off its
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
- **VDL2**: VHF Data Link mode 2, ACARS's digital successor, over Europe busier than ACARS itself:
  the same kind of messages, and controller-pilot messages (CPDLC) and position reports (ADS-C),
  on 136.725, 136.775, 136.875 and 136.975 MHz, all at once (set under Modes; within 800 kHz).
  Decoded by dumpvdl2 with libacars. The receiver runs at 2.1 MS/s in this mode (twenty times
  VDL2's 105 kHz, as dumpvdl2 wants it). The frames that only keep the link up are counted, not
  listed.
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

- **Radiosondes**: the weather balloons weather services launch twice a day (around 11:00 and
  23:00 UTC; for the Netherlands the KNMI from De Bilt), on 400 to 406 MHz. Decoded by the
  decoders of rs1729's RS project (as radiosonde_auto_rx uses them): Vaisala RS41, Graw DFM,
  Meteomodem M10 and M20, all four side by side. Each sonde is on the map with its track, its
  altitude and climb, the temperature, humidity and pressure it measures, and how far away it is;
  it goes into the **History** when it has not been heard for ten minutes (usually when it has come
  down), with the highest altitude it reached (where the balloon burst) and where it was last
  heard. The frequency changes from launch to launch: sondehub.org and radiosondy.info show
  the last ones from each site. A sonde can be heard 200 km and more away while it is high up.

- **Digital voice**: DMR, D-STAR, YSF (System Fusion), NXDN, dPMR and P25 phase 1, decoded by
  DSD-FME: the radio amateurs' digital repeaters and hotspots, and businesses, taxis and services
  that talk digitally without encryption. Each call is listed when it ends: the system, the
  talkgroup or call sign it went to and the radio ID or call sign that spoke, the colour code
  (DMR), NAC (P25) or RAN (NXDN), and a text message, talker alias or GPS position when the call
  carried one. **Listen** plays the decoded speech instead of the FM audio, so you hear the people,
  not the digital noise. Set the repeater's or hotspot's output frequency and the protocol under
  Modes (*Auto* finds DMR, D-STAR, YSF and P25 by themselves; NXDN and dPMR must be picked).
  Encrypted calls are listed as encrypted and stay silent. The voice codecs (AMBE, IMBE) are
  decoded by mbelib, a software implementation of them; whether using it is allowed where you
  live is for you to check (the codecs are patented in some countries).

- **FT8 / FT4**: the radio amateurs' weak-signal modes, the busiest on HF: call signs, grid
  squares and signal reports in 15-second slots (FT4: 7.5 seconds) on the UTC clock, readable far
  below the noise. Decoded by ft8_lib from the radio's upper-sideband audio, one slot at a time.
  The stations that give their grid square are on the map, coloured by how strong they came in;
  **Decodes** lists every message. Busiest on 14.074 MHz by day and 7.074 MHz by night (HackRF);
  with an RTL-SDR 28.074 MHz (10 m) opens by day around the solar maximum, and 50.313 (6 m) in
  summer. The app's clock must be right to the second (Home Assistant's is, through NTP).

- **SSTV**: pictures sent as sound (slow-scan television), decoded by SDRadio itself: PD50 to
  PD290 (the ISS sends PD120 and PD180), Martin M1 and M2, and Robot 36. 145.800 MHz is the ISS
  during its SSTV events, 144.500 MHz radio amateurs on 2 m, 14.230 MHz on HF (upper sideband,
  HackRF). A picture grows line by line while it comes in and then goes into the gallery; the
  pictures are kept in the app's data folder as long as the other decoded data.
- **Weather fax** (HackRF only): the weather charts that ships have received by radio for decades,
  on shortwave: surface analyses, forecasts, sea state and ice charts, sent on a schedule day and
  night. Decoded by SDRadio itself (120 lines a minute, IOC 576): a chart starts with the
  station's start tone, is lined up on its phasing lines and ends with the stop tone; it grows
  line by line on the page and then goes into the gallery. The frequency to set is the dial
  frequency, 1.9 kHz below the station's: the DWD in Pinneberg sends on 3855, 7880 and 13882.5 kHz
  (dial 3.8531, 7.8781, 13.8806 MHz), Northwood (UK) on 2618.5, 4610 and 8040 kHz.

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

- **DAB+**: digital radio: the stations of a DAB+ block (an ensemble), decoded by welle-cli: their
  names, programme type, bitrate and what is playing now (DLS); click a station to hear it in the
  page, with its slides (album art, logos) when it sends them. In this mode the receiver runs at
  2.048 MS/s, DAB's own rate. Pick the block under Modes: in the Netherlands 12C is the NPO,
  11C the national commercial stations, and regional ensembles are on 8A, 8B, 5C and others.
- **Video** (HackRF only): analog video as FPV drones and wireless cameras send it, in black and
  white, a few frames a second. Analog video is FM some 16 MHz wide, so in this mode the HackRF
  runs at 20 MS/s (back to 2.4 when you leave it); the RTL-SDR takes in 2.4 MHz, too little. PAL
  or NTSC, and video sent upside down, are recognised by themselves. 5.8 GHz FPV (Raceband
  R1–R8, F4 5800), 1.2 GHz and 2.4 GHz cameras are under Modes. **Snapshot** keeps a picture.
  Only for video that is meant to be seen: drones (yours, or at a race) and your own cameras;
  watching other people's camera feeds is not allowed.

A mode your receiver can't do is ghosted: hovering over it says why ("HackRF only: …").

**The map** (Aircraft, Ships, APRS, Radiosondes, FT8, Satellites) shows OpenStreetMap through Home Assistant's own map tile service (which
needs Home Assistant 2026.9 or later), centred on Home Assistant's home location; the distances
are from there. The map is darkened only with the dark theme.

What the modes decode is kept in the database (30 days by default, set under Modes).

## HackRF or RTL-SDR

Both work for nearly everything; where they differ:

| | RTL-SDR (R820T/R828D) | HackRF One |
|---|---|---|
| Frequencies | 24 MHz to 1.766 GHz | 1 MHz to 6 GHz |
| Bandwidth | 2.4 MHz (up to 3.2, with lost samples) | up to 20 MHz |
| Sensitivity | good from 50 MHz up; with the stock antenna weak below that | a little less on VHF and UHF; set the LNA and VGA gains (options) |
| Frequency accuracy | 1 ppm with a TCXO (most current dongles); set `ppm_correction` otherwise | about 20 ppm: fine for most, a few hundred Hz off at UHF |
| Antenna power | a bias tee on many (`bias_tee`) | its antenna port power is not switched by SDRadio |
| Power | about 300 mA | about 500 mA: a powered USB hub helps on a Raspberry Pi |

**Only with the HackRF**: everything below 24 MHz, so long, medium and shortwave broadcasts,
the HF amateur bands (FT8 on 7.074 and 14.074 MHz, SSTV on 14.230 MHz) and **Weather fax**; above
1.766 GHz the 2.4 and 5.8 GHz bands; and **Video**, which needs its 20 MHz. These modes are
ghosted on an RTL-SDR, saying why.

**Better with the RTL-SDR**: VHF and UHF listening, Pager, Sensors, Aircraft, ACARS, VDL2,
Ships, APRS, Radiosondes, Digital voice, DAB+ and the satellites, which are within its range: it is a little more
sensitive there, draws less power and is cheap enough to leave on one job. An RTL-SDR with
direct sampling (an RTL-SDR Blog V3 or V4) hears HF too, but SDRadio doesn't use that mode: the
HackRF is the receiver for shortwave here.

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

**A ready channel list**: [sdradio-channels.csv](https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-sdradio/sdradio-channels.csv) has 113 channels for every band and mode,
each with a description and often a fact: broadcast, air, marine, radio amateurs, satellites,
shortwave and odd signals (number stations, the Buzzer, over-the-horizon radar), and a channel for
each mode, tagged `mode:<mode>`, so picking it switches to that mode. Download it and import it on
the Channels page without *Replace*: your own channels stay. Channels outside your receiver's
range (shortwave on an RTL-SDR) are kept but faded, and tagged `hackrf`. Import it again after an
update to get the new channels.

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
