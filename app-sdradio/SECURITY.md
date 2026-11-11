# Security Policy

## Supported versions

This is a personal project under active development. Only the latest released
version receives fixes. Please update to the newest version before reporting a
problem.

## Reporting a vulnerability

Please report security issues **privately**, not in a public issue:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub private vulnerability reporting).

Include what you found, how to reproduce it, and the app version. You will
get an acknowledgement as soon as possible. Please give a reasonable amount of
time for a fix before any public disclosure.

## What this app handles

So you know what is at stake:

- **The receiver.** The app gets USB access to read your SDR receiver. It only receives: an
  RTL-SDR can't transmit, and the app never sets a HackRF to transmit.
- **What you listen to.** The stored channels and the log of every transmission heard are in
  the app's database in `/data`. The page is served through Home Assistant ingress only, so it
  is reachable only to users already authenticated to Home Assistant; direct connections to its
  port are refused when it runs under the Supervisor.
- **Home Assistant.** The app has Home Assistant API access, used only when you switch on the
  sensors or events on the Settings page: it then writes `sensor.sdradio_*` states and fires
  `sdradio_signal` events, nothing else.
- **Uploads.** An imported CSV is only read as a channel list. An uploaded backup is checked to
  be an SDRadio database before it is listed, and only replaces the database when you restore it
  (after a backup of the current one).
- **Network receivers.** With receiver type `network` the app connects to the `rtl_tcp` server
  you set; `rtl_tcp` has no authentication, so keep that server on your own network.
- **No third parties.** Nothing is uploaded anywhere. The page loads its fonts from Google
  Fonts, like the other apps in this repository.
