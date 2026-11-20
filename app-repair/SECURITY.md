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

- **It can change and delete things in Home Assistant.** That is its purpose.
  It can reload an integration, disable an entity, remove an entity or a
  device from the registries, delete an entity's recorded history and
  long-term statistics, and apply a suggestion of the Supervisor. Nothing else:
  the list is fixed in `src/fixes.py`, and a request for anything not in it is
  refused.
- **Only when you press a button.** No repair runs by itself, on a schedule or
  from a scan. Each asks first, and one that removes something asks you to type
  a word. The app checks that word again on its side.
- **Only repairs it offered.** The browser names a finding and a repair; what
  is done to which entity or device is decided by the app from its own latest
  scan. A page can't make it remove something the scan didn't offer for that
  finding, other than through the Clean up page, which only takes an entity or
  device Home Assistant actually has.
- **Only from Home Assistant's signed-in ingress.** Changes are accepted from
  the ingress proxy and as JSON only. The panel is for administrators.
- **`allow_fixes: false` turns every repair off.** The app then only reports.
- **Rights it asks for.** `homeassistant_api` to read and change Home Assistant
  through its own API, and `hassio_api` with the `manager` role to read the
  Supervisor's resolution centre and apply its suggestions. That role allows
  more than the app uses; the app only calls `/resolution/info` and
  `/resolution/suggestion/<id>`.
- **It never writes to Home Assistant's database or its `.storage` files.**
  Every change goes through the API, so Home Assistant itself does it.
- **The deep scan reads them, when you turn it on.** With `deep_scan: true` the
  app reads the recorder's database and the two registry files, to find history
  of entities that no longer exist. The configuration folder is mapped
  read-only, so the app can't write there. The mapping is there whether the
  option is on or off; with it off, the app doesn't open the folder. That
  folder also holds `secrets.yaml` and your tokens: the app reads only the
  three files named here.
- **What it reads.** Entity states, the registries, the configuration of your
  automations, scripts and dashboards, and the log. Log lines can hold
  addresses, names and tokens that integrations print; they are shown on the
  Log page and kept only in memory.
- **What it stores.** The health score per scan, what you ignored, and what it
  changed, in a local SQLite database. No entity states, no log lines and no
  configuration.
- **No telemetry.** The app talks to Home Assistant and the Supervisor and to
  nothing else. Links to an integration's documentation or issue tracker open
  in your browser when you click them.

**Make a backup of Home Assistant before a big clean-up.** A removed entity's
history and statistics can't be brought back by this app.

When reporting or attaching screenshots, check them for names of people,
addresses and device names you would rather not share.
