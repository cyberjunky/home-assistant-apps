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

- **Control over your miners.** Through the fixed calls in `src/miner.py`
  only: pause, resume, shut down the ASICs (NerdQAxe), and restart. They only
  run when a miner's Mining switch or Restart button is used in Home
  Assistant, which reaches the app over MQTT. Anyone who can publish to your
  MQTT broker can use them too, so keep the broker behind a login. The app
  never changes a miner's settings, pool, frequency, voltage or firmware.
- **Plain HTTP.** AxeOS speaks HTTP without a login. Keep the miners and Home
  Assistant inside your own network; never expose a miner to the internet.
- **Mining data.** The readings, best shares, daily totals and events are
  stored in a local SQLite database in the app's data directory. The pages show
  each miner's pool worker, which usually holds your payout address.
- **MQTT.** Each miner's values go to the broker you configured, or the one
  Home Assistant knows. The worker name and payout address are left out.
- **Outside calls.** With `network_data` on, the app reads public block data
  from mempool.space; with `firmware_check` on, the newest firmware releases
  from GitHub. Nothing about you or your miners is sent.
- **The dashboard** is served through Home Assistant ingress, so it is reachable
  only to users already authenticated to Home Assistant. It only reads; the one
  change it makes is deleting the app's own history.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact your miners' addresses and your
payout address.
