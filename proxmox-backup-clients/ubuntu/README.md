# Back up directories of an Ubuntu host to Proxmox Backup Server

`pbs-backup` backs up one or more directories with `proxmox-backup-client`. Each directory becomes its own archive (`/etc` → `etc.pxar`, `/srv/data` → `srv_data.pxar`) in one snapshot per run, `host/<host name>/<time>` on the datastore. Only changed data is sent after the first run, and a systemd timer runs it every night.

Files:

| File | Goes to |
|---|---|
| `pbs-backup` | `/usr/local/sbin/pbs-backup` |
| `pbs-backup.conf` | `/etc/pbs-backup.conf` |
| `pbs-backup.service`, `pbs-backup.timer` | `/etc/systemd/system/` |

## 1. On PBS: a user and token for this host

In PBS's Shell (or under Configuration → Access Control), for the datastore `backups`:

```sh
proxmox-backup-manager user create hosts@pbs --comment "Computer backups"
proxmox-backup-manager user generate-token hosts@pbs backup
proxmox-backup-manager acl update /datastore/backups DatastoreBackup --auth-id hosts@pbs
proxmox-backup-manager acl update /datastore/backups DatastoreBackup --auth-id 'hosts@pbs!backup'
```

- The second command prints the token's secret once; copy it.
- A token never has more rights than its user, so both get the role.
- `DatastoreBackup` can make backups and restore its own, nothing else.
- Keep the single quotes: without them the shell reads `!backup` as a history command.

Copy the fingerprint too: **Datastore → backups → Summary → Show Connection Information**.

Several computers can share this token, or each can get its own: `generate-token hosts@pbs host2`, then the same `acl update` for `'hosts@pbs!host2'`. Each computer's backups go under its own name either way.

## 2. On the Ubuntu host: install the client

Proxmox publishes the client for Debian; on Ubuntu, use the **static** build, which needs no matching libraries:

```sh
sudo wget https://enterprise.proxmox.com/debian/proxmox-archive-keyring-trixie.gpg \
  -O /usr/share/keyrings/proxmox-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/proxmox-archive-keyring.gpg] http://download.proxmox.com/debian/pbs-client trixie main" \
  | sudo tee /etc/apt/sources.list.d/pbs-client.list
sudo apt update
sudo apt install proxmox-backup-client-static
proxmox-backup-client version
```

The client then updates with the rest of the host's packages.

## 3. Install the script

```sh
sudo install -m 755 pbs-backup /usr/local/sbin/pbs-backup
sudo install -m 600 pbs-backup.conf /etc/pbs-backup.conf
sudo nano /etc/pbs-backup.conf
```

In `/etc/pbs-backup.conf`, set:

- `PBS_REPOSITORY`: `hosts@pbs!backup@<PBS address>:backups`
- `PBS_PASSWORD`: the token's secret
- `PBS_FINGERPRINT`: the fingerprint from step 1
- `BACKUP_DIRS`: the directories, one per line
- `EXCLUDES` (optional): what to leave out, as gitignore-like patterns, for example `.cache` or `*.tmp`. A pattern with a leading `/` counts from each backed-up directory: `/ron/Downloads` leaves out `/home/ron/Downloads` when `/home` is backed up

Check what it would do, then run it once:

```sh
sudo pbs-backup --dry-run
sudo pbs-backup
```

The first run sends everything; later runs send only what changed. In PBS the backups appear under **Datastore → backups → Content** as `host/<host name>`.

## 4. Every night

```sh
sudo install -m 644 pbs-backup.service pbs-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pbs-backup.timer
systemctl list-timers pbs-backup.timer
```

- It runs at 01:30, give or take 15 minutes. Change `OnCalendar` in the timer for another time.
- If the host was off at that time, the timer runs the backup when the host is back.

To see the last run, use `journalctl -u pbs-backup` or `systemctl status pbs-backup`.

## Encryption (optional)

To encrypt on the host before anything is sent:

```sh
sudo proxmox-backup-client key create /etc/pbs-backup.key --kdf none
sudo proxmox-backup-client key paperkey /etc/pbs-backup.key --output-format text
```

Then set `KEYFILE='/etc/pbs-backup.key'` in `/etc/pbs-backup.conf`.

**Keep a copy of the key somewhere other than this host**, for example the paper key printout or a password manager. Without the key, the backups can't be restored, not even by PBS.

The first encrypted backup sends everything again, because encrypted data can't reuse unencrypted chunks.

## Retention

A prune job on the datastore in PBS (**Datastore → backups → Prune & GC Jobs**, for example `daily=7 weekly=4 monthly=6`) removes old snapshots. It covers these host backups as well, so nothing needs pruning on the host. `DatastoreBackup` can't prune anyway.

If you use a namespace (`PBS_NAMESPACE`), make sure the prune job covers it. That is the default, as the job reaches every namespace below the one it is set on.

## Restore

The commands need the same settings. Load them into the shell first, as root:

```sh
sudo -i
set -a; source /etc/pbs-backup.conf; set +a
proxmox-backup-client snapshot list
```

Snapshots are named `host/<host name>/<time>`. Add `--ns hosts` to these commands if you use a namespace, and `--keyfile /etc/pbs-backup.key` if the backups are encrypted.

```sh
# A whole archive, to a new folder (never straight over the live directory)
proxmox-backup-client restore host/myhost/2026-11-15T01:30:00Z etc.pxar /root/restore-etc

# Only some files: a gitignore-like pattern
proxmox-backup-client restore host/myhost/2026-11-15T01:30:00Z etc.pxar /root/restore-etc --pattern 'nginx/**'

# Browse a snapshot interactively (ls, cd, find, then restore what you select)
proxmox-backup-client catalog shell host/myhost/2026-11-15T01:30:00Z etc.pxar
```

Single files can also be downloaded in PBS's web interface: **Datastore → backups → Content**, select the snapshot, then **File Browser**. That only works when the backup isn't encrypted.

## Notes

- **Run as root**, so every file can be read. The timer does that.
- **One file system per directory**: the backup doesn't follow other disks mounted below a listed directory. Either list them as their own directory, or set `ALL_FILE_SYSTEMS=true`.
- **Databases**: copying a running database's files can give an inconsistent copy. Dump it first, for example with `pg_dump` or `mysqldump` from a `ExecStartPre=` line in the service, into a directory that is backed up.
- **Change detection**: `metadata` (the default here) doesn't read files whose size and modification time are unchanged, which makes nightly runs fast. Set `CHANGE_DETECTION=data` to read everything every time.
