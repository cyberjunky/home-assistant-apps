# Back up folders of a Windows 11 PC to Proxmox Backup Server

Proxmox doesn't make `proxmox-backup-client` for Windows, but it runs in **WSL** (the Windows Subsystem for Linux), which can read the Windows drives under `/mnt/c`. So this uses the same `pbs-backup` script as on [Ubuntu](../ubuntu/README.md), inside WSL, and Windows Task Scheduler starts it every day.

Files:

| File | What it does |
|---|---|
| [`../ubuntu/pbs-backup`](../ubuntu/pbs-backup), [`../ubuntu/pbs-backup.conf`](../ubuntu/pbs-backup.conf) | The backup script and its settings, installed inside WSL |
| `pbs-backup.ps1` | Runs the backup in WSL and keeps a log in `%LOCALAPPDATA%\pbs-backup\backup.log` |
| `register-task.ps1` | Creates the daily task "PBS backup" in Task Scheduler |

## What to expect

- **Files and folders are backed up with their contents and times.** NTFS permissions, alternate data streams and the Windows file attributes are not kept; a restore gives the files back, not a Windows installation.
- **No shadow copy (VSS).** Files that are open while the backup runs can come out half-written, and files Windows keeps locked can't be read. Back up while those programs are closed, or leave such files out with `EXCLUDES`. Examples: Outlook's `.pst` and `.ost` files, a running virtual machine's disks, database files.
- **OneDrive "files on demand"**: reading a file that is only in the cloud downloads it. Leave the OneDrive folder out, or keep it "Always keep on this device".
- **The first backup reads everything through WSL**, which is slower than on Linux. After that, `metadata` change detection only reads files whose size or time changed, so daily runs are quick.
- **The PC must be on and you logged on** when the task runs. If it was off at that time, the task runs at the next logon.

## 1. On PBS: a user and token

As in step 1 of the [Ubuntu instructions](../ubuntu/README.md). If the `hosts@pbs` user exists already, give this PC a token of its own:

```sh
proxmox-backup-manager user generate-token hosts@pbs mypc
proxmox-backup-manager acl update /datastore/backups DatastoreBackup --auth-id 'hosts@pbs!mypc'
```

## 2. WSL with Ubuntu

In PowerShell as administrator:

```powershell
wsl --install -d Ubuntu
```

Restart if asked, then open **Ubuntu** from the Start menu once and create its user.

## 3. The client and the script, inside WSL

In the Ubuntu window, follow steps 2 and 3 of the [Ubuntu instructions](../ubuntu/README.md): install `proxmox-backup-client-static`, then install `pbs-backup` and `/etc/pbs-backup.conf`.

The Windows drive is at `/mnt/c`. A copy of this repository on the PC, for example in `C:\pbs-backup`, is `/mnt/c/pbs-backup` there.

In `/etc/pbs-backup.conf`, the folders are Windows folders below `/mnt/c`. Use quotes, because Windows paths often contain spaces:

```bash
BACKUP_DIRS=(
    '/mnt/c/Users/Ron/Documents'
    '/mnt/c/Users/Ron/Pictures'
    '/mnt/c/Users/Ron/Desktop'
)

EXCLUDES=(
    'desktop.ini'
    'Thumbs.db'
    '~$*'
    '*.tmp'
    '*.pst'
    '*.ost'
)
```

The backups are named after the PC's computer name, which is WSL's host name. Set `BACKUP_ID` to choose another.

Test it in the Ubuntu window:

```sh
sudo pbs-backup --dry-run
sudo pbs-backup
```

## 4. Every day: Task Scheduler

Copy this `windows` folder to a fixed place, for example `C:\pbs-backup`. Then in PowerShell, as yourself and not as administrator:

```powershell
cd C:\pbs-backup
powershell -ExecutionPolicy Bypass -File .\register-task.ps1 -Time 12:30
Start-ScheduledTask -TaskName "PBS backup"
Get-Content "$env:LOCALAPPDATA\pbs-backup\backup.log" -Tail 20
```

The task appears in **Task Scheduler** as "PBS backup". Its **Last Run Result** is `0x0` when the backup worked.

If your WSL distribution has another name (see `wsl -l`), add it to both commands: `-Distro Ubuntu-24.04`.

## Restore

In the Ubuntu window, the same way as on [Ubuntu](../ubuntu/README.md#restore), to a folder on the Windows drive:

```sh
sudo -i
set -a; source /etc/pbs-backup.conf; set +a
proxmox-backup-client snapshot list
proxmox-backup-client restore host/MYPC/2026-11-15T11:30:00Z mnt_c_Users_Ron_Documents.pxar /mnt/c/Restore/Documents
```

- Each folder is its own archive, named after its path (`/mnt/c/Users/Ron/Documents` → `mnt_c_Users_Ron_Documents.pxar`). `sudo pbs-backup --dry-run` lists them.
- Single files can also be downloaded in PBS's web interface: **Datastore → backups → Content**, the snapshot, **File Browser**.

## Not tested on Windows

The `pbs-backup` script is tested with `proxmox-backup-client` 4.2 against PBS 4.2. The PowerShell scripts and the WSL setup have not been run on a Windows PC yet.
