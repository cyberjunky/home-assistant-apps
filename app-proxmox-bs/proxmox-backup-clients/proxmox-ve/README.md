# Back up a Proxmox VE node's own configuration to Proxmox Backup Server

Proxmox VE backs up its VMs and containers itself (vzdump). It does **not** back up the node:
- its network setup;
- the cluster's configuration;
- users, tokens and the firewall;
- your scripts.

When a node's system disk dies, that's what you rebuild by hand. This backs it up every night with the same `pbs-backup` script as on [Ubuntu](../ubuntu/README.md). `proxmox-backup-client` is already installed on every Proxmox VE node.

## What to back up

| Folder | What's in it |
|---|---|
| `/etc/pve` | The cluster's configuration (shared by all nodes): every guest's configuration (`nodes/*/qemu-server/*.conf`, `nodes/*/lxc/*.conf`), storage (`storage.cfg`), users, tokens and permissions (`user.cfg`), the firewall, SDN, HA, backup and replication jobs, notifications, and the nodes' certificates. `priv/` holds secrets (see below) |
| `/etc` | The node itself: `network/interfaces`, `hosts`, `hostname`, `resolv.conf`, `fstab`, APT sources, `modprobe.d`, `sysctl.d`, `ssh`, `postfix`, `cron.d`, your own systemd units, and `corosync/` in a cluster |
| `/root` | Your scripts, notes and `.ssh` |
| `/var/lib/pve-cluster` | `config.db`, the database `/etc/pve` is made from. Copied while the cluster service writes to it, so a copy can be caught mid-change; the files in `/etc/pve` are the reliable part |
| `/usr/local` | Scripts and programs you added |

Not needed: guest disks (vzdump does those), `/var/lib/vz` with ISOs and templates (downloaded again easily), and the rest of the system (a fresh Proxmox VE install gives it back).

`/etc/pve` is a file system of its own, so a backup of `/etc` doesn't include it. List it on its own line, as below.

## Setup

1. **On PBS:** the user and token from step 1 of the [Ubuntu instructions](../ubuntu/README.md). One token can serve every node, or give each node its own.
2. **On each node, as root:** install the script and settings as in step 3 of the Ubuntu instructions. Skip step 2: the client is already there.

   In `/etc/pve` the settings are:

   ```bash
   PBS_REPOSITORY='hosts@pbs!backup@192.168.1.20:backups'
   PBS_PASSWORD='the-token-secret'
   PBS_FINGERPRINT='AB:CD:...'

   BACKUP_DIRS=(
       /etc/pve
       /etc
       /root
       /var/lib/pve-cluster
       /usr/local
   )

   KEYFILE='/root/pbs-host-backup.key'
   ```

3. **Encrypt these backups.** `/etc/pve/priv` holds secrets:
   - the PBS storage's encryption key (`priv/storage/<storage>.enc`);
   - the storages' passwords;
   - the cluster's SSH and authentication keys.

   Unencrypted, anyone who can read the datastore can read them. Make a key of its own for these backups:

   ```sh
   proxmox-backup-client key create /root/pbs-host-backup.key --kdf none
   proxmox-backup-client key paperkey /root/pbs-host-backup.key --output-format text
   ```

   Keep that paper key (or a copy of the file) outside the cluster, for example in a password manager. With it, these backups are also a safe offsite copy of the cluster's own backup encryption key. Without it, they can't be restored.
4. **Run it once**, then switch the timer on (step 4 of the Ubuntu instructions):

   ```sh
   pbs-backup --dry-run
   pbs-backup
   ```

   Set the timer for a time when vzdump doesn't run, for example `OnCalendar=*-*-* 04:30`.

Each node's backups appear under its own name: `host/<node name>`. `/etc/pve` is the same on every node, and PBS stores the shared part once.

## Restore

These backups are for looking up and rebuilding, not for copying back over a running node. Restore to a folder first, as in the [Ubuntu instructions](../ubuntu/README.md#restore) (with `--keyfile /root/pbs-host-backup.key`), then take what you need:

- **A deleted guest's configuration:** copy its `.conf` from the restored `etc_pve/nodes/<node>/qemu-server/` (or `lxc/`) to `/etc/pve/nodes/<node>/qemu-server/`. Restore its disks from its vzdump backup.
- **A reinstalled node:**
  1. Install Proxmox VE with the same name and address.
  2. Take `network/interfaces`, `hosts`, `fstab`, the APT sources and your own changes in `/etc` from the restored `etc` archive.
  3. Join the cluster again with `pvecm add`. The cluster's configuration then comes from the other nodes.
- **A single node that is not in a cluster, reinstalled:** with the node's cluster service stopped (`systemctl stop pve-cluster`), put the restored `config.db` in `/var/lib/pve-cluster/`, then start the service again. That brings back all of `/etc/pve`. Do this only on a fresh install, never on a node in a cluster.
- **The cluster's backup encryption key**, if it was lost: restore `etc_pve/priv/storage/<storage>.enc` and upload it in **Datacenter → Storage → Edit → Encryption**.

## Not tested on Proxmox VE

The `pbs-backup` script is tested with `proxmox-backup-client` 4.2 against PBS 4.2, but not yet on a Proxmox VE node. Check after the first run that `etc_pve.pxar` contains the guest configurations: in PBS, open **Content**, then the snapshot, then **File Browser**. That works when the backup is not encrypted. For an encrypted one, use `proxmox-backup-client catalog shell` instead.
