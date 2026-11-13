# Security Policy

## Supported versions

This is a personal project under active development. Only the latest released
version receives fixes. Please update to the newest version before reporting a
problem.

## Reporting a vulnerability

Please report security issues **privately**, not in a public issue:

1. Go to the repository's **Security** tab.
2. Choose **Report a vulnerability** (GitHub private vulnerability reporting).

Include what you found, how to reproduce it, and the app version. You will get
an acknowledgement as soon as possible. Please give a reasonable amount of time
for a fix before any public disclosure.

## What this app handles

So you know what is at stake:

- **Received pager messages.** Meldkamer decodes the public P2000 FLEX broadcast
  and stores the messages, their locations and statistics in a local SQLite
  database in the app's data directory. Nothing is uploaded to any third party.
- **Credentials you enter.** An optional OpenCage geocoding token and, if you use
  an external broker, MQTT credentials are stored only in the app's own
  configuration on your Home Assistant instance and used only to reach those
  services.
- **The dashboard** is served through Home Assistant ingress, so it is reachable
  only to users already authenticated to Home Assistant. Changes (capcodes,
  places, streets, imports, the database) are only accepted through Home
  Assistant's ingress proxy, not from other apps on its network. The optional TCP
  broadcast port (5000) is plain and unauthenticated; only expose it on a trusted
  network.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact tokens, passwords and precise locations.
