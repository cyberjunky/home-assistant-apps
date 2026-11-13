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

- **Incidents, responses and duty.** The app reads them from Home Assistant's
  FireServiceRota integration and stores them, with the incident addresses and
  positions, in a local SQLite database in the app's data directory. Nothing is
  uploaded to any third party.
- **No credentials of its own.** The app has no FireServiceRota account, password
  or token. It talks only to Home Assistant, with the token the Supervisor gives
  every app, and never to FireServiceRota or Brandweerrooster. The spoken message
  (`message_to_speech_url`) is fetched by your browser only when you press play.
- **Responding.** Acknowledge and Reject switch the integration's response switch
  through Home Assistant. Anyone who can open the dashboard can answer an incident
  in your name, as anyone with access to that switch in Home Assistant already can.
- **The dashboard** is served through Home Assistant ingress, so it is reachable
  only to users already authenticated to Home Assistant. The app opens no ports.
  Changes (responding, restores, speech settings) are only accepted through
  Home Assistant's ingress proxy, not from other apps on its network.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact tokens, names and incident addresses.
