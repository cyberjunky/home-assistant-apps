# Back up a Windows 11 PC to Proxmox Backup Server

Proxmox doesn't make `proxmox-backup-client` for Windows. [Salvanza](https://github.com/salvanza/salvanza_client) (formerly "Proxmox Backup Client") is a native Windows client and app for PBS. It's an independent community project (GPL-3.0), not by Proxmox and not by this repository.
- **Folder backups through a VSS shadow copy**, so open files are backed up consistently.
- **NTFS permissions and attributes** are kept and restored.
- **Whole-machine backups** as bootable disk images, restorable onto new hardware or as a Proxmox VE VM.
- **A Windows service** runs the schedules, also when nobody is logged on.
- **Client-side encryption** with a paper key.
- **PBS's own formats**: its folder backups also restore with Proxmox's client.

## Setup

1. **On PBS**, make a token as in step 1 of the [Ubuntu instructions](../ubuntu/README.md):
   ```sh
   proxmox-backup-manager user generate-token hosts@pbs mypc
   proxmox-backup-manager acl update /datastore/backups DatastoreBackup --auth-id 'hosts@pbs!mypc'
   ```
   The `hosts@pbs` user must exist and have the same role (step 1 there). Copy the fingerprint from **Datastore → backups → Summary → Show Connection Information**.
2. **On the PC**, install `Salvanza-<version>-x64.msi` from its [releases](https://github.com/salvanza/salvanza_client/releases), and let it install its background service.
3. **In the Salvanza app**, add the PBS server:
   - **address:** PBS's address; for the Home Assistant app that is Home Assistant's address, port `8007`;
   - **datastore:** `backups`;
   - **user:** the token `hosts@pbs!mypc`, with its secret as the password;
   - **fingerprint:** the one you copied.
4. **Make a Backup Set:**
   - **folders:** for example Documents, Pictures and Desktop; or the whole machine;
   - **a schedule;**
   - **an encryption key** if you want one. Save or print its paper key outside the PC.
5. **Run it once.** It appears in PBS under **Datastore → backups → Content** as `host/<computer name>`.

Its manuals (GUI, command line, bare-metal restore) are in its repository under [docs/manual](https://github.com/salvanza/salvanza_client/tree/master/docs/manual).

## Not tested here

Salvanza is tested by its own project; it has not been run against the Proxmox Backup Server app for this repository yet.
