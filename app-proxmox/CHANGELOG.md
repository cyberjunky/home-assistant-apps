# Changelog

## 0.1.2

- **Backup server tasks open from every row**: clicking a Proxmox Backup Server task whose name has a `/` or `-` in it (a host backup such as `host/freqle-org`, a restore) gave "Bad Request". PBS writes such characters in a task's ID as `\x3a`, which the click turned into `:`; the ID now reaches PBS unchanged
- Names with a quote or `"` (a guest, a firewall group) no longer break their row's buttons or the page
- When a backup server refuses something, its reason is shown, not only "Bad Request"

## 0.1.1

- **Proxmox Backup Server token**: the instructions (Documentation and Info page) now give the `Audit` role to the user as well as its token. PBS never gives a token more rights than its user, so with the token's role alone the Backup server section showed no datastores and "status Forbidden · updates Forbidden". That warning now says what to do. An existing setup only needs `proxmox-backup-manager acl update / Audit --auth-id homeassistant@pbs` on the backup server

## 0.1.0

**After installing:** make an API token in Proxmox (the Info page and DOCS.md tell how), enter `hosts`, `token_id` and `token_secret` in the app's configuration, then start the app.

- First version: a dashboard for a Proxmox VE cluster. Overview with health checks, nodes, guests, storage with a fill-up forecast, tasks, Ceph and HA; backups at a glance with RPO against the job's schedule, estimated restore time, a 14-day calendar, each night's runs from the job logs, restore points, verification and what backups take on storage; optionally a Proxmox Backup Server with its datastores, jobs and tasks; a Console button per guest that opens Proxmox's own console; the network as a map and as VLAN lanes to drag guests between; the firewall as a flow per guest, node or group, with drag-and-drop rules, the rule tables, groups, IP sets, aliases, coverage and the log with its statistics. Read-only unless you turn `read_only` off.
