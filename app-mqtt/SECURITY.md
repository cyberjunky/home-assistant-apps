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

- **Logins.** Home Assistant users are checked by the Supervisor (`auth_api`); the app never sees their passwords after the check. Local logins from the options are stored hashed in the container (`/etc/mosquitto/pw`, made again at every start). The app's own users (`homeassistant`, `addons`, `mqtt-dashboard`) have random 64-character passwords in `/data/system_user.json`, readable only by root and part of Home Assistant's backups.
- **Everything on the broker.** The dashboard subscribes to every topic. With `capture_payloads` on, it keeps the last value of every topic and the last messages in memory, and shows them to anyone who can open the app in Home Assistant's sidebar: give the app only to users who may see your MQTT traffic. Payloads are never written to disk; the history in `/data/history.sqlite3` has counts only.
- **Changes.** Publishing, clearing retained messages and switching Home Assistant to this broker are only accepted from Home Assistant's signed-in ingress proxy, as JSON; other apps and websites can't send them. Clearing a discovery config removes that entity from Home Assistant.
- **Home Assistant's API.** The app uses the Supervisor's token (`homeassistant_api`) only to read the MQTT integration's entry and, when you click *Switch*, to run its reconfigure flow. The token never reaches the browser.
- **Ports.** 1883 and 1884 are unencrypted: use TLS (8883, 8884, with a certificate) for clients outside your network, or better a VPN.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact usernames, addresses and payloads.
