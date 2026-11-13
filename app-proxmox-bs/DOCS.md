# Home Assistant App: Proxmox Backup Server

> **Unofficial.** An independent project, not affiliated with, endorsed by or sponsored by Proxmox Server Solutions GmbH.

This app runs a real [Proxmox Backup Server](https://pbs.proxmox.com/docs/) (PBS) on the machine Home Assistant runs on. It is PBS itself, from the same packages a PBS installation uses: its API, backup protocol, web interface and console are on port 8007, and everything is set up in PBS's own web interface. The [PBS documentation](https://pbs.proxmox.com/docs/) applies as it is; this page covers what is different in an app.

## Setup

1. Connect the disk for your backups: SATA, NVMe or USB. An SSD is best; on USB, give a hard disk a powered enclosure.
2. On the app's **Info** tab, turn **Protection mode** off. The app needs it to see the disks; with it on, Home Assistant hides them and the log says so.
3. On the **Configuration** tab, set a `root_password`.
4. Start the app and choose **Open Web UI**. Log in as `root` with the realm **Linux PAM**. The browser warns once about PBS's self-signed certificate.
5. In PBS, go to **Administration → Storage / Disks → Directory → Create: Directory**: choose the disk, a name (for example `backups`), ext4 or xfs, and keep **Add as Datastore** on. PBS partitions and formats the disk, and the app mounts it. This erases the disk; for a disk that already holds backups, see [A disk with backups on it](#a-disk-with-backups-on-it).
6. In Proxmox VE, add the datastore under **Datacenter → Storage → Add → Proxmox Backup Server**. PBS shows the server, datastore and fingerprint under the datastore's **Summary → Show Connection Information**.

## Configuration

```yaml
root_password: a-long-password
log_level: info
```

| Option | Default | What it does |
|---|---|---|
| `root_password` | | The password of `root@pam`: the login for PBS's web interface and console, and for Proxmox VE. Empty: nobody can log in |
| `log_level` | `info` | Amount of logging. PBS's own messages appear in the app's log too |

Everything else (datastores, users, API tokens, permissions, jobs, notifications, retention) is set in PBS's web interface and kept by the app.

## Connect Proxmox VE

In Proxmox VE, **Datacenter → Storage → Add → Proxmox Backup Server**: an ID, the Home Assistant machine's address, a user and password, the datastore and the fingerprint. Or on a node, as root:

```sh
pvesm add pbs homeassistant --server 192.168.1.20 --datastore backups --username root@pam --fingerprint AB:CD:… --password
```

Rather than root, give Proxmox VE its own user: in PBS under **Configuration → Access Control**, or in its **Shell**:

```sh
proxmox-backup-manager user create pve@pbs --password 'a-long-password'
proxmox-backup-manager acl update /datastore/backups DatastoreBackup --auth-id pve@pbs
```

`DatastoreBackup` may back up and restore its own backups. Give `DatastoreAdmin` instead if Proxmox VE should prune them with the storage's retention settings.

## What is different in an app

- **The network** is the machine's own (the app uses the host network): PBS is on port 8007 of Home Assistant's address. Its network, DNS and time settings are Home Assistant's; change them there.
- **Disks.** PBS sets up a disk with a systemd mount unit, and there is no systemd in an app. The app answers PBS's systemd requests itself: it mounts the disk at `/mnt/datastore/<name>` as PBS asked, keeps PBS's mount units in its data, and mounts the disks again after every start.
- **Unplugging.** When a disk with a datastore is disconnected, the app sets each datastore on it to maintenance mode **offline** with the message "Disk not connected", then lets go of the disk. Proxmox VE then reports why backups can't run, and the empty mount point is locked, so nothing is written to Home Assistant's own storage instead. When the disk is back, the app checks its file system (`e2fsck -p` for ext4), mounts it and brings the datastores online. A maintenance mode you set yourself stays as it is. Still, stop the app or wait until no backup, verify or garbage collection runs before you unplug a disk.
- **Home Assistant's own disk.** The app sees every disk of the machine, including the one Home Assistant runs from (and its data disk, if it has one). The commands that partition, wipe or format a disk (`sgdisk`, `wipefs`, `dd`, `mkfs`, …) refuse any disk that holds Home Assistant OS's partitions or is in use by Home Assistant, from PBS's Disks page and from the Shell alike. PBS itself also shows such a disk as in use.
- **ZFS** is not available: Home Assistant's kernel has no ZFS. PBS's ZFS page stays empty; use ext4 or xfs directories.
- **Updates.** PBS is updated with the app: a new PBS version comes as a new app version. PBS's own Updates page has nothing to do, and the subscription notice can be ignored.
- **The system log** in PBS's web interface stays empty; PBS's messages are in the app's log, marked `[api]` and `[proxy]`.
- **The Shell** is a root console inside the app. Changes there outside PBS's configuration and the disks are gone after the app restarts.

## A disk with backups on it

A disk that already holds a datastore (from this app before a reinstall, or from another PBS) can be used again without losing its backups:

1. Note its file system UUID in the Shell: `blkid` (for example `/dev/sdb1: UUID="0f0e…" TYPE="ext4"`).
2. Write a mount unit for it like PBS does, for example for a datastore called `backups`:

   ```sh
   cat > /etc/systemd/system/mnt-datastore-backups.mount <<'UNIT'
   [Mount]
   What=/dev/disk/by-uuid/0f0e…
   Where=/mnt/datastore/backups
   Type=ext4
   Options=defaults
   UNIT
   systemctl start mnt-datastore-backups.mount
   ```

3. Add the datastore with **Datastore → Add Datastore**, the backing path `/mnt/datastore/backups` (or the folder on the disk that holds `.chunks`), and **Reuse existing datastore** on. Or: `proxmox-backup-manager datastore create backups /mnt/datastore/backups --reuse-datastore true`.

## Backups of Home Assistant

The backups on the datastore disks are not part of Home Assistant's backups; they would make them as large as the disks. Home Assistant's backups do contain PBS's configuration (`/data/pbs/etc`: datastores, users, tokens, jobs, the certificate), its history (`/data/pbs/lib`) and its mount units (`/data/pbs/systemd`). Task logs (`/data/pbs/log`) are left out. Restoring a Home Assistant backup therefore gives back the same server, and it finds its disks again.

## Good to know

- **Memory.** PBS idles lightly, but garbage collection and verifying a large datastore use memory and keep the disk busy. On a 4 GB machine, schedule them for the night (Datastore → Options).
- **Prune** old backups with Proxmox VE's retention settings on the storage, or with a prune job. Without either, the disk fills up.
- **Verify** with a verify job, for example weekly, to find a failing disk early.
- **Encryption**: set an encryption key on the storage in Proxmox VE; the backups are then encrypted before they leave the node.
- **One disk is one copy.** Keep the important backups somewhere else as well, for example with a sync job to another PBS, or by rotating two disks.
- **Packages.** On amd64 the image installs PBS from Proxmox's own repository; on aarch64 the arm64 build of the same version from [wofferl/proxmox-backup-arm64](https://github.com/wofferl/proxmox-backup-arm64), as Proxmox doesn't publish one. Both are pinned to one version and checked against checksums at build time.

## Troubleshooting

- **PBS shows no disks**: Protection mode is on. Turn it off on the app's Info tab and restart the app.
- **"… is on the disk Home Assistant runs from; the app refuses to change it"**: that disk is Home Assistant's own. Choose another one.
- **A datastore is offline with "Disk not connected"**: its disk isn't there. Connect it; the app brings the datastore back by itself.
- **"file system errors e2fsck -p can't fix"** in the log: connect the disk to a Linux computer and run `e2fsck -f` on it, then connect it again.
- **Proxmox VE says the fingerprint doesn't match**: the app's data was reset, so PBS made a new certificate. Copy the new fingerprint from the datastore's Summary into the storage's settings.
- **Login fails**: set `root_password`, restart the app, and choose the realm Linux PAM.

## Development

The app runs outside Home Assistant too, which is how it was tested: build the image and start it privileged, with an image file attached with `losetup` as a disk and a mount unit for it in `/etc/systemd/system`. `proxmox-backup-client` in a second container then backs up to it like Proxmox VE does. The unit tests (`pytest`) need neither PBS nor root.
