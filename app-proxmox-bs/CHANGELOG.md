# Changelog

## 0.1.0

**After installing:** turn **Protection mode** off (the app needs it to see the disks), set a `root_password`, start the app and open the web UI. Set up a disk under Administration → Storage / Disks → Directory.

- First version: Proxmox Backup Server 4.2.9 as a Home Assistant app, unchanged, with its original API, web interface and console on port 8007 for Proxmox VE. PBS's Disks page sets up disks as on a real PBS; the app mounts them, mounts them again after a restart, takes their datastores offline while a disk is unplugged and back online when it returns, and refuses to partition, wipe or format the disk Home Assistant runs from. PBS's log appears in the app's log. The configuration and certificate are kept in Home Assistant's backups; the backups stay on their disks.
