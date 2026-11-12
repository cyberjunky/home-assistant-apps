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

- **Access to your trading bots.** The username, password and optional
  `ws_token` of each bot's API server are stored in the app's configuration on
  your Home Assistant instance, and only sent to that bot. They never reach the
  browser: the dashboard talks to this app, and the app talks to the bots.
- **Plain HTTP.** Freqtrade's API server speaks HTTP unless you put it behind
  TLS, and then the password crosses your network readable with basic
  authentication. Keep the bots and Home Assistant inside your own network, or
  use an `https` address. Never expose a bot's API server to the internet.
- **What it can change.** Only through the fixed calls in `src/bot.py`: start,
  stop, pause entries, reload the configuration, and a market exit of one open
  trade. They only run when someone presses a button on a bot's page, and only
  through Home Assistant's signed-in ingress. The `read_only` option turns them
  off. The app never opens a trade, changes a strategy, the pairlists or the
  locks, or deletes anything on a bot. MQTT only publishes; nothing is taken
  from it.
- **Trading data.** Each bot's balance and profit every 5 minutes and the
  bots' events are stored in a local SQLite database in the app's data
  directory. It shows how much you hold and trade, so treat backups with care.
- **MQTT.** Each bot's balance, profit and trade events go to the broker you
  configured, or the one Home Assistant knows.
- **The dashboard** is served through Home Assistant ingress, so it is reachable
  only to users already authenticated to Home Assistant.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact passwords, tokens, your bots'
addresses and your balances.
