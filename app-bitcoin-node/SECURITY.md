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

- **It only reads.** This version has no code that pays, opens or closes a
  channel, or changes anything on Bitcoin Core, LND or Tor. Every call that
  would change the wallet has to go through one function
  (`LndClient.change()` in `src/lightning.py`), which refuses while the
  `read_only` option is on, and nothing calls it yet.
- **Access to Bitcoin Core.** The RPC username and password are stored in the
  app's configuration on your Home Assistant instance and only sent to your
  node. Bitcoin Core's RPC speaks plain HTTP, so on the network the password
  is readable: keep the node and Home Assistant on a network you trust. Use an
  RPC user limited with `rpcwhitelist` to the methods listed in the
  documentation; that user cannot stop the node or touch a wallet.
- **Access to LND.** Use the **read-only macaroon**. It can see your balances,
  channels and payments, and it cannot spend. Never enter the admin macaroon:
  whoever reads the app's configuration could then empty the wallet. Set
  `tls_cert` so the app checks that it talks to your LND; without it the
  connection is encrypted but not checked.
- **Access to Tor.** The control port password, when you set it up, gives full
  control over Tor and crosses the network unencrypted. The app only sends
  `GETINFO`. Leave it out if you don't want that on your network.
- **Nothing of this reaches the browser.** The dashboard talks to this app, and
  the app talks to the node. Passwords and the macaroon are never sent to a
  page, and the status the pages get does not contain them.
- **Channel backups.** The app stores LND's static channel backup in its data
  directory and offers it as a download through Home Assistant's signed-in
  ingress. The file is encrypted by LND with a key from the wallet's seed. It
  cannot move funds; with the seed it can close your channels.
- **Financial data.** Your balances over time are stored in a local SQLite
  database in the app's data directory, and your balances and payments go to
  the MQTT broker when MQTT is on. Treat backups of Home Assistant with care.
- **Onion addresses and the node's public key** are shown on the pages. They
  are not published over MQTT.
- **The dashboard** is served through Home Assistant ingress, so it is reachable
  only to users already authenticated to Home Assistant. Deleting the app's
  history is only accepted from that ingress.
- **No telemetry.** The app makes no analytics or tracking calls and talks to
  nothing but your node, your MQTT broker and Home Assistant.

When reporting or attaching logs, redact passwords, the macaroon, onion
addresses, your node's public key and your balances.
