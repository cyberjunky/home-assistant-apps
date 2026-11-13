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

- **Tesla tokens.** A one-time sign-in produces an access and refresh token,
  stored only in the app's own data directory on your Home Assistant
  instance. They are sent only to Tesla's own services. They are never logged in
  full, and never sent anywhere else.
- **Vehicle and location history** is stored in a local SQLite database in the
  app's data directory. Nothing is uploaded to any third party.
- **The dashboard** is served through Home Assistant ingress, so it is reachable
  only to users already authenticated to Home Assistant.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact tokens, passwords and precise
locations.
