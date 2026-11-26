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

- **Your Garmin password** is sent to Garmin once, during the sign-in, and is
  never stored or logged.
- **Garmin tokens.** The sign-in produces a token that python-garminconnect
  writes, readable by its owner only, in the app's own data directory, apart
  from the database. It is sent only to Garmin's own services, never logged,
  and never part of a database download.
- **Health and location data** (heart rate, sleep, weight, activity tracks, and
  when switched on ECG, glucose or menstrual data) is stored in a local SQLite
  database in the app's data directory. Nothing is uploaded to any third party.
- **The dashboard** is served through Home Assistant ingress, so it is reachable
  only to users already authenticated to Home Assistant; other clients get 403.
- **Map tiles** come through Home Assistant's own map tile proxy.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact tokens, passwords, health values and
precise locations.
