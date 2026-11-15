# Changelog

## 0.1.1

- **Proxmox Backup Server token**: the instructions (Documentation and Info page) now give the `Audit` role to the user as well as its token. PBS never gives a token more rights than its user, so with the token's role alone the Backup server section showed no datastores and "status Forbidden · updates Forbidden". That warning now says what to do. An existing setup only needs `proxmox-backup-manager acl update / Audit --auth-id homeassistant@pbs` on the backup server

## 0.1.0

**After installing:** make an API token in Proxmox (the Info page and DOCS.md tell how), enter `hosts`, `token_id` and `token_secret` in the app's configuration, then start the app.

- First version: a dashboard for a Proxmox VE cluster. Overview with health checks, nodes, guests, storage with a fill-up forecast, tasks, Ceph and HA; backups at a glance with RPO against the job's schedule, estimated restore time, a 14-day calendar, each night's runs from the job logs, restore points, verification and what backups take on storage; optionally a Proxmox Backup Server with its datastores, jobs and tasks; a Console button per guest that opens Proxmox's own console; the network as a map and as VLAN lanes to drag guests between; the firewall as a flow per guest, node or group, with drag-and-drop rules, the rule tables, groups, IP sets, aliases, coverage and the log with its statistics. Read-only unless you turn `read_only` off.
