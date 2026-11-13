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

- **Inverter control.** The dashboard can change the inverter's work mode, its
  battery depth of discharge, its export limit and its battery charge current.
  It is served through Home Assistant ingress only, so it is reachable only to
  users already authenticated to Home Assistant; direct connections to its port
  are refused when it runs under the Supervisor.
- **Energy history** is stored in a local SQLite database in the app's data
  directory. Nothing is uploaded to any third party.
- **The solar forecast** sends your home coordinates (rounded to four decimals)
  and the panel orientation to Forecast.Solar and, as a fallback, Open-Meteo.
  This is the only outbound internet traffic, and it can be turned off.
- **MQTT credentials** come from the Supervisor or the app options and are
  used only to connect to your broker.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact your inverter's serial number and your
location.
