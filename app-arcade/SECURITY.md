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

- **ROM files** you upload, stored in the ROM folder (default `/share/arcade/roms`).
  Uploads must be zip files; the app reads only the file list of a zip and never unpacks
  it on the server. The files are served back only to the page that plays them.
- **Save files** (high scores, NVRAM, settings, save states) per game in `/data/saves`.
  The page can write only that game's own files, with checked paths.
- **MAME runs in your browser**, inside the page's WebAssembly sandbox, not on the server.
- **The page** is served through Home Assistant ingress, so it is reachable only to users
  already authenticated to Home Assistant. The app opens no ports and needs no Home
  Assistant API access.
- **No telemetry.** The app makes no analytics or tracking calls. The page loads its fonts
  from Google Fonts, like the other apps in this repository.

When reporting or attaching logs, redact anything personal.
