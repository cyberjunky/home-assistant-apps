# Home Assistant App: Proxmox

> **Unofficial.** An independent project, not affiliated with, endorsed by or sponsored by Proxmox Server Solutions GmbH.

Proxmox shows your [Proxmox VE](https://www.proxmox.com/en/proxmox-virtual-environment) cluster in a dashboard, served through ingress: the whole datacenter on one overview, a page per node and per guest, storage and backups, the network as a map and as VLAN lanes, the firewall as a flow, and the cluster's task log. It reads the cluster over its REST API with an API token.

The app is read-only by default. Turn `read_only` off to start and stop guests, edit firewall rules and move a guest's network device to another VLAN from the dashboard.

## Setup

1. Make an API token in Proxmox (see [The token](#the-token)).
2. Enter one or more nodes under `hosts`, and the token under `token_id` and `token_secret`.
3. Start the app and open **Proxmox** from the sidebar.

## Configuration

```yaml
hosts:
  - 192.168.1.11
  - 192.168.1.12
token_id: homeassistant@pve!dashboard
token_secret: 3f1c2a6e-0000-0000-0000-000000000000
verify_ssl: false
poll_seconds: 10
read_only: true
pbs:
  - host: 192.168.1.20
    token_id: homeassistant@pbs!dashboard
    token_secret: 7c1d0b2e-0000-0000-0000-000000000000
    fingerprint: "AB:CD:...:EF"
```

| Option | Default | What it does |
|---|---|---|
| `hosts` | | Nodes of one cluster: an address (`192.168.1.11`), a name with a port (`pve1.lan:8443`) or a full address (`https://pve1:8006`). Without a scheme it is `https`, without a port `8006`. The app reads the whole cluster through the first node that answers, and moves on to the next when that one is down |
| `token_id` | | The token's full ID: `user@realm!tokenname` |
| `token_secret` | | The secret Proxmox showed once when you made the token |
| `verify_ssl` | `false` | Check the nodes' certificates. Proxmox's own are self-signed; turn this on when your nodes have a trusted certificate |
| `poll_seconds` | `10` | How often the cluster's state is read. The configuration (network, firewall, disks, updates) is read every 5 minutes and right after a change |
| `read_only` | `true` | Change nothing. Off: the power buttons, the firewall editing and the VLAN moves work (the token needs the rights for it too) |
| `pbs` | | Optional: Proxmox Backup Servers to show on the Backups page, each with its own token (see [A Proxmox Backup Server](#a-proxmox-backup-server)) |
| `pbs[].host` | | Its address; without a port it is `8007` |
| `pbs[].token_id`, `pbs[].token_secret` | | Its API token, `user@realm!tokenname` and the secret |
| `pbs[].fingerprint` | | Optional: the SHA-256 fingerprint of its certificate, `AB:CD:…` (32 pairs). The app then only talks to that exact certificate. Proxmox VE shows it under Datacenter → Storage → your PBS storage |
| `pbs[].verify_ssl` | `false` | Check its certificate against trusted authorities instead; for a server with a trusted certificate |
| `pbs[].name` | host name | Its name on the pages |
| `log_level` | `info` | Amount of logging |

## The token

Make a user and a token on any node, as root. The role below can read everything the dashboard shows and change nothing:

```sh
pveum user add homeassistant@pve --comment "Home Assistant dashboard"
# Proxmox VE 9
pveum role add HADashboard --privs "Sys.Audit,Sys.Modify,VM.Audit,VM.GuestAgent.Audit,Datastore.Audit,SDN.Audit,Pool.Audit"
# Proxmox VE 8
pveum role add HADashboard --privs "Sys.Audit,Sys.Modify,VM.Audit,VM.Monitor,Datastore.Audit,SDN.Audit,Pool.Audit"
pveum acl modify / --users homeassistant@pve --roles HADashboard
pveum user token add homeassistant@pve dashboard --privsep 0
```

The last command prints the secret, once. `token_id` is then `homeassistant@pve!dashboard`.

| Privilege | For |
|---|---|
| `Sys.Audit` | The cluster, the nodes, their network, disks, services and subscription, Ceph, HA, replication and the firewall configuration |
| `VM.Audit` | The guests: configuration, history, snapshots and their firewall |
| `Datastore.Audit` | Storage, its history and the backups on it |
| `VM.GuestAgent.Audit` (9) or `VM.Monitor` (8) | The addresses inside a VM, from the QEMU guest agent |
| `SDN.Audit`, `Pool.Audit` | SDN zones, VNets and subnets; pools |
| `Sys.Modify` | Only the firewall log and the list of available updates, which Proxmox guards with this privilege. Leave it out if you don't want those two |

For the buttons, add `VM.PowerMgmt` (start, stop and the rest) and `VM.Config.Network` (a guest's VLAN and its firewall rules), and set `read_only: false`:

```sh
pveum role modify HADashboard --append 1 --privs "VM.PowerMgmt,VM.Config.Network"
```

Datacenter and node firewall rules need `Sys.Modify`, which the role already has. Whatever the token may not read is listed on the Info page, with what Proxmox said; the rest of the dashboard works without it.

`--privsep 0` gives the token the user's rights. With `--privsep 1` the token needs its own: `pveum acl modify / --tokens 'homeassistant@pve!dashboard' --roles HADashboard`.

## A Proxmox Backup Server

Optional. With a backup server under `pbs`, the Backups page gets a **Backup server** section. Make its token on the backup server, as root:

```sh
proxmox-backup-manager user create homeassistant@pbs --comment "Home Assistant dashboard"
proxmox-backup-manager user generate-token homeassistant@pbs dashboard
proxmox-backup-manager acl update / Audit --auth-id 'homeassistant@pbs!dashboard'
```

The second command prints the secret, once. The `Audit` role only reads. A PBS token always needs its own permission, so the ACL is for the token (`--auth-id`), not the user.

Without a token the app still knows which backup servers the cluster uses (from Proxmox VE's storage settings), and says so on the Backups page, with their fingerprints to copy.

## What the dashboard shows

- **Overview.** Quorum, nodes, guests, CPU, memory, storage, Ceph, HA and the Proxmox version as tiles; the cluster's CPU, IO wait, memory and network over an hour to a year; every node with its load, memory, root disk, updates and subscription; **health checks** (failing SMART, worn SSDs, degraded or full ZFS pools, stopped services, pending network changes, failed replication, guests in no backup job, failed tasks, updates); storage; the busiest guests; Ceph's OSDs, placement groups, IO and health checks; HA's resources and nodes; the latest tasks.
- **Nodes.** Per node: CPU, IO wait, load, memory, swap, root disk and KSM, their history, the hardware (CPU, cores, boot mode, secure boot), the software (versions, kernel), its guests; disks with SMART and SSD wear, ZFS pools, storage; updates, services, subscription, replication; and its tasks.
- **Guests.** Every VM and container to search (name, ID, tag, VLAN, MAC), filter and sort, with CPU, memory, disk, VLANs, firewall, uptime and the age of its last backup; vCPU overcommit and assigned memory as tiles. Per guest: history of CPU, memory, network and disk IO; its network devices with the addresses the guest agent reports; disks; its firewall and whether it actually applies; snapshots; backups with their verification; its configuration, pending changes and notes; and the power buttons.
- **Storage.** Every storage, shared ones once, with its growth over the last month and when it would be full at that pace; the history of one; replication jobs.
- **Backups.**
  - **At a glance:** how many guests a job protects, how many meet their RPO, the worst RPO, what last night's jobs did, the restore points, the longest restore, failed verifications and the next run; a calendar of the last 14 days per guest (backed up, failed, nothing), and what needs a look.
  - **Per guest:** state, RPO and its target, the last run, how many runs of the last 14 days worked, restore points, the oldest, sizes, how long a backup takes, the estimated restore time (RTO), verification and storage. A guest's own page has its restore points, every run with its duration, size and speed (and the log), and charts of both.
  - **Storage:** what the backups take per storage, Proxmox Backup Server's deduplication, the data the jobs moved per night and the largest guests.
  - **Jobs:** every backup job with its schedule, retention, the guests it covers and its next run, and each node's job runs of the last 14 days.
  - **Backup server** (with `pbs`): the server's state, version, CPU, memory, root disk and updates; per datastore its usage, free space, when it is full (the server's own estimate), the deduplication factor, the snapshots of VMs, containers and hosts, garbage collection (last run, what it freed and what is pending) and bad chunks; a month of datastore usage; every garbage collection, verify, prune and sync job with its last run, how long it took and the next run; and its tasks. A failed job, bad chunks, a nearly full datastore or a server that can't be reached also show on the Overview's health checks.
- **Network.**
  - **VLANs:** a lane per bridge and VLAN, with the SDN VNet, subnet, gateway and host addresses that belong to it, and a card for every guest network device on it. Drag a card to another lane of the same bridge to put it on that VLAN; the dialog shows the line of the guest's configuration before and after.
  - **Map:** per node, the physical ports, bonds, bridges, VLAN interfaces and the guests on them, connected. Drag the boxes where you like them; the layout is kept by the app.
  - **Interfaces:** every interface of every node, the changes waiting to be applied, and which bridges a node misses.
  - **SDN:** zones, VNets and subnets with the guests on them.
- **Firewall.**
  - **Flow:** pick a guest, a node, the datacenter or a security group, and see what may come in and go out: every rule an arrow from its source or to its destination, coloured by ACCEPT, DROP or REJECT, security groups unfolded, the default policy last. Drag an alias, IP set or security group onto it to add a rule.
  - **Rules:** the rules of any scope, in order, to add, edit, switch on or off, drag into another order and delete.
  - **Groups, IP sets and aliases:** each with what uses it.
  - **Log and statistics:** what was blocked and allowed over the last day, from where, to which ports and which guests, and the log to filter.
  - **Options:** the datacenter's and nodes' firewall options, and per guest whether its firewall is on, which network devices bypass it, its policies and filters.
- **Tasks.** The cluster's task log per day, to filter by node, type and status; click one for its log.
- **Info.** The connection, the options, what the token can't read, how to make a token.

## RPO and RTO

- **RPO** (how much you could lose) is the age of a guest's latest good backup: the newest restore point that didn't fail verification. Its **target** is how often the guest's backup job runs, from the job's schedule (`02:00` is a day, `sat 04:00` a week, `*/6:00` six hours); or pick one target for every guest at the top of the page. A guest is **late** when its latest good backup is more than a quarter over the target, and has **missed** its RPO at more than twice the target.
- **RTO** (how long a restore takes) is estimated as the size of the latest backup divided by the restore speed you enter at the top of the page (150 MB/s to start with), not counting the time the guest needs to boot. Measure one real restore and enter its speed for a better estimate. The setting is kept in your browser.
- **Runs** come from the logs of the backup tasks of the last 14 days: per guest whether it worked, how long it took and what it moved. Proxmox keeps those logs on each node.

## Changes, and what stops a mistake

With `read_only: false`:

- Every change asks first. Stopping a guest says it is like pulling the plug; a VLAN move shows the configuration line before and after, and warns when the guest runs.
- A datacenter or node firewall rule that would drop or reject port 8006 (the web interface and this app) or SSH asks a second time. Proxmox itself still lets the management network in, unless a rule above it says otherwise.
- A change only comes in through Home Assistant's signed-in ingress, and Proxmox checks the configuration's digest, so a change made elsewhere in the meantime is refused instead of overwritten.
- Proxmox runs a power action as a task; the dashboard shows its log as it runs.

## How it works

```
Proxmox VE API ──every poll_seconds──▶ cluster status, resources, node status, tasks, HA, Ceph
(any node)     ──every 5 minutes─────▶ network, disks, ZFS, services, updates, firewall, SDN,
                                       guest configurations, backup and replication jobs
               ──when a page asks────▶ history (RRD), a guest's details, backups, backup job logs,
                                       firewall log, task logs
```

- **One cluster through any node.** Every Proxmox node answers for the whole cluster. The app uses the first node in `hosts` that answers and switches when it stops answering.
- **History from Proxmox.** The charts use Proxmox's own RRD data (hour, day, week, month, year), so there is history from the start and the app keeps no database.
- **Light on the cluster.** The live state is five small requests per `poll_seconds`. The configuration is a few requests per node and guest every 5 minutes.
- **The backup server** is read every minute, its list of updates every 30 minutes, over its own API on port 8007.
- **Not in this version:** consoles, migration, creating or deleting guests, and editing the host network or the firewall options.

## Disclaimer

This app is an independent project. It is not affiliated with, endorsed by or sponsored by
Proxmox Server Solutions GmbH. Proxmox, Proxmox VE and Proxmox Backup Server are registered
trademarks of Proxmox Server Solutions GmbH. With `read_only` off, the buttons in this app act on
your cluster for real.
