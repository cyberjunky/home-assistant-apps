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

A vulnerability in Proxmox Backup Server itself belongs with Proxmox
(security@proxmox.com); this app runs their packages unchanged. Tell me as well,
so the app can move to the fixed version.

## What this app handles

So you know what is at stake:

- **Your backups.** Everything Proxmox VE backs up to this server is on its
  datastore disks, readable by anyone who has a disk unless you set an
  encryption key on the storage in Proxmox VE (then the backups are encrypted
  before they leave the node).
- **Port 8007 on your network.** The app uses the machine's own network, and
  PBS's API and web interface listen on port 8007, as on any PBS, with their own
  login and a self-signed certificate. Anyone who can reach the port can try to
  log in. Use a long `root_password`, give Proxmox VE its own user with only
  `DatastoreBackup` (DOCS.md shows how), and don't forward the port to the
  internet.
- **The root password.** `root_password` is stored in the app's configuration on
  your Home Assistant instance and set as the password of `root@pam` inside the
  app when it starts. The web interface's Shell is a root console inside the app.
- **Full access to the machine's disks.** The app runs without protection mode,
  with full device access, `SYS_ADMIN` (to mount the datastore disks and a tmpfs
  PBS needs) and `SYS_RAWIO` (to read SMART), so PBS's Disks page can set up any
  disk. The commands that partition, wipe or format a disk refuse the disk Home
  Assistant runs from and any disk Home Assistant uses; everything else is up to
  whoever is logged in to PBS. The AppArmor profile only allows mounts under
  `/mnt/datastore` and the tmpfs.
- **Data kept by the app.** PBS's configuration, users, tokens, jobs and
  certificate (`/data/pbs/etc`), its history (`/data/pbs/lib`), its mount units
  (`/data/pbs/systemd`) and its task logs (`/data/pbs/log`, left out of Home
  Assistant's backups).
- **No telemetry.** The app makes no analytics or tracking calls. PBS's own
  update check is off: there is no package source in the image.

When reporting or attaching logs, redact passwords, your addresses and your
guests' names if they are private.
