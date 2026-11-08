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

- **Root access to your Toon.** The SSH password is stored in the app's
  configuration on your Home Assistant instance and is only used to log in to
  the Toon.
- **Weak SSH algorithms.** A Toon only speaks old algorithms, such as
  `diffie-hellman-group1-sha1` and `ssh-rsa`. The app enables them for this
  connection only. Keep the connection inside your own network.
- **The Toon's SSH key.** The key is remembered on first contact, and a changed
  key is refused until you turn on `ssh.trust_new_host_key`. The very first
  connection can't be verified this way.
- **Read-only.** The app only invokes the actions listed in `src/boxtalk.py`,
  none of which change your Toon. It does run one fixed read-only command over
  SSH to collect the Toon's health values.
- **Energy and heating history** is stored in a local SQLite database in the
  app's data directory. It shows when you are home, so treat backups with care.
- **MQTT.** Sensor values go to the broker you configured, or the one Home
  Assistant knows.
- **The dashboard** is served through Home Assistant ingress, so it is reachable
  only to users already authenticated to Home Assistant.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact passwords and your Toon's address and
serial number.
