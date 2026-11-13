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

- **An API token for your cluster.** The token ID and secret are stored in the
  app's configuration on your Home Assistant instance and only sent to the
  nodes you list. They never reach the browser: the dashboard talks to this
  app, and the app talks to Proxmox. Give the token only the rights it needs
  (DOCS.md lists them); a read-only token can't change anything, whatever this
  app does.
- **Certificates.** Proxmox nodes use self-signed certificates, so by default
  the app does not check them (`verify_ssl: false`). Anyone who can intercept
  traffic between Home Assistant and the nodes could then read the token. Keep
  them on a trusted network, or give the nodes a trusted certificate and turn
  `verify_ssl` on.
- **What it can change.** Nothing while `read_only` is on, which is the
  default. With it off, only through the fixed calls in `src/proxmox.py`:
  start, shut down, reboot, pause, resume and stop a guest; add, change,
  switch, move and delete firewall rules (datacenter, node, guest or security
  group); and the VLAN tag of a guest's network device. Each asks first in the
  dashboard, and a host firewall rule that could lock you out of port 8006 or
  SSH asks a second time. Changes are only accepted through Home Assistant's
  signed-in ingress. The app never creates, deletes, migrates or restores
  guests, and never changes storage, users or the host network configuration.
- **Data kept by the app.** Only where you dragged the boxes on the network
  map (`/data/layout.json`) and the donation prompt's state. The cluster's
  history comes from Proxmox's own RRD data and is not stored here.
- **The dashboard** is served through Home Assistant ingress, so it is reachable
  only to users already authenticated to Home Assistant. Every Home Assistant
  user who can open it sees the whole cluster, and with `read_only` off can
  change it.
- **No telemetry.** The app makes no analytics or tracking calls.

When reporting or attaching logs, redact the token secret, your nodes'
addresses and your guests' names if they are private.
