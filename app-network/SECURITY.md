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

- **Home Assistant's API.** The app uses the Supervisor's token for Home
  Assistant (`homeassistant_api`). It reads the device trackers, devices,
  areas, zones and the recorder's history, and changes only two things: a
  tracker's name and icon in the entity registry, and a device's name in the
  device registry, when you click Rename or Use in HA. The token never reaches
  the browser.
- **Who can change things.** Renames, icons and the map are only accepted from
  Home Assistant's signed-in ingress proxy, as JSON; other apps on the
  network and other websites can't send them.
- **Where your phones have been.** The Mobile page shows positions from the
  recorder. The app doesn't store them: they are read when you open the page.
  Anyone who can open the app in Home Assistant's sidebar can see them, so
  give the app only to users who may.
- **Your network.** The map, notes and networks you make are kept in
  `/data/network.json`, and every change of a tracker between on and off the
  network in `/data/history.sqlite3`, both in Home Assistant's backups.
- **Map tiles.** They come from OpenStreetMap through Home Assistant's own
  tile proxy; the browser doesn't contact OpenStreetMap.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact your devices' names, IP and MAC
addresses, and any positions.
