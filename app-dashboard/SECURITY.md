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
  Assistant (`homeassistant_api`). It reads your entities, their areas, the
  recorder's history, calendars, forecasts and camera pictures. The token never
  reaches the browser, and neither do the tokens in picture and camera
  addresses: pictures are fetched by the app.
- **What it can do.** A fixed list of services only: lights, switches, fans,
  helpers, covers, thermostats, scenes, scripts, automations, buttons, numbers
  and media players, and only for entities that exist. Locks, alarm panels and
  everything else are refused. Note that a cover can be a garage door or a
  gate, and a script can do anything you wrote it to do.
- **Who can change things.** Button presses and changes to the dashboard are
  only accepted from Home Assistant's signed-in ingress proxy, as JSON; other
  apps on the network and other websites can't send them. Anyone who can open
  the app in Home Assistant's sidebar can use every button on it and see every
  camera, calendar and state on it, so give the app only to users who may.
- **A wall tablet is signed in.** Whoever stands at the tablet can use what is
  on the dashboard. Put on it only what a visitor may touch.
- **Your dashboard.** The pages and settings are kept in `/data/dashboard.json`,
  in Home Assistant's backups. A dashboard file you load is checked and reduced
  to what a dashboard is.
- **No telemetry.** The app makes no analytics or tracking calls and loads
  nothing from the internet: fonts and scripts are part of the app.

When reporting or attaching logs or a dashboard file, redact names, addresses
and calendar entries you don't want to share.
