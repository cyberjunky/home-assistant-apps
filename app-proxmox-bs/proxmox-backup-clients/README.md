# Proxmox Backup Server clients

Back up computers other than Proxmox VE to a Proxmox Backup Server, for example the [Proxmox Backup Server app](..). These are folder backups with Proxmox's own `proxmox-backup-client`:
- Each run is a snapshot on the datastore, `host/<computer name>/<time>`.
- After the first run, only changed data is sent.
- They can be encrypted before they leave the computer.
- PBS's prune, verify and garbage collection jobs cover them like any other backup.

| Computer | How |
|---|---|
| [Ubuntu / Debian](ubuntu/README.md) | `pbs-backup` script, settings file, systemd timer |
| [Windows 11](windows/README.md) | the native Salvanza client (VSS, whole machine, a service) |
| [Proxmox VE node](proxmox-ve/README.md) | the node's own configuration (`/etc/pve`, `/etc`, ...), which vzdump doesn't back up |

Proxmox VE's VMs and containers don't need these: Proxmox VE backs them up to PBS itself (see the app's [Connect Proxmox VE](../DOCS.md#connect-proxmox-ve)).
