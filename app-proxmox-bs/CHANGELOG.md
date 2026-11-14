# Changelog

## 0.1.2

- **In the sidebar**: turn on Show in sidebar on the Info tab. With Home Assistant on `https://`, PBS's own interface appears inside Home Assistant (accept its certificate once). On `http://` browsers don't keep PBS's login inside Home Assistant's page, so the page shows whether PBS runs and opens it in its own tab. **Open Web UI** opens this page too
- The app starts its services the current s6-overlay way (no change in what it does)

## 0.1.1

- **Starts under Home Assistant**: 0.1.0 stopped right after starting, because Home Assistant's AppArmor refused the tmpfs it mounted for PBS's shared memory. PBS now keeps that in `/dev/shm`, so nothing needs mounting at start
- Datastore disks are mounted with the classic mount call, which the app's AppArmor profile checks against its rules
- PBS's schedules follow Home Assistant's time zone again; a warning in the log when no disk is visible (Protection mode on)
- `root_password` is described as this backup server's own login. The Documentation tab shows how to give Proxmox VE its own token with backup rights only, instead of root's password

## 0.1.0

**After installing:** turn **Protection mode** off (the app needs it to see the disks), set a `root_password`, start the app and open the web UI. Set up a disk under Administration → Storage / Disks → Directory.

- First version: Proxmox Backup Server 4.2.9 as a Home Assistant app, unchanged, with its original API, web interface and console on port 8007 for Proxmox VE. PBS's Disks page sets up disks as on a real PBS; the app mounts them, mounts them again after a restart, takes their datastores offline while a disk is unplugged and back online when it returns, and refuses to partition, wipe or format the disk Home Assistant runs from. PBS's log appears in the app's log. The configuration and certificate are kept in Home Assistant's backups; the backups stay on their disks.
