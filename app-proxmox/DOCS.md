# Home Assistant App: Proxmox

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
```

| Option | Default | What it does |
|---|---|---|
| `hosts` | | Nodes of one cluster: an address (`192.168.1.11`), a name with a port (`pve1.lan:8443`) or a full address (`https://pve1:8006`). Without a scheme it is `https`, without a port `8006`. The app reads the whole cluster through the first node that answers, and moves on to the next when that one is down |
| `token_id` | | The token's full ID: `user@realm!tokenname` |
| `token_secret` | | The secret Proxmox showed once when you made the token |
| `verify_ssl` | `false` | Check the nodes' certificates. Proxmox's own are self-signed; turn this on when your nodes have a trusted certificate |
| `poll_seconds` | `10` | How often the cluster's state is read. The configuration (network, firewall, disks, updates) is read every 5 minutes and right after a change |
| `read_only` | `true` | Change nothing. Off: the power buttons, the firewall editing and the VLAN moves work (the token needs the rights for it too) |
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

## What the dashboard shows

- **Overview.** Quorum, nodes, guests, CPU, memory, storage, Ceph, HA and the Proxmox version as tiles; the cluster's CPU, IO wait, memory and network over an hour to a year; every node with its load, memory, root disk, updates and subscription; **health checks** (failing SMART, worn SSDs, degraded or full ZFS pools, stopped services, pending network changes, failed replication, guests in no backup job, failed tasks, updates); storage; the busiest guests; Ceph's OSDs, placement groups, IO and health checks; HA's resources and nodes; the latest tasks.
- **Nodes.** Per node: CPU, IO wait, load, memory, swap, root disk and KSM, their history, the hardware (CPU, cores, boot mode, secure boot), the software (versions, kernel), its guests; disks with SMART and SSD wear, ZFS pools, storage; updates, services, subscription, replication; and its tasks.
- **Guests.** Every VM and container to search (name, ID, tag, VLAN, MAC), filter and sort, with CPU, memory, disk, VLANs, firewall, uptime and the age of its last backup; vCPU overcommit and assigned memory as tiles. Per guest: history of CPU, memory, network and disk IO; its network devices with the addresses the guest agent reports; disks; its firewall and whether it actually applies; snapshots; backups with their verification; its configuration, pending changes and notes; and the power buttons.
- **Storage.** Every storage, shared ones once, with its growth over the last month and when it would be full at that pace; the history of one; the backups per guest (latest, count, oldest, size, verification, guests in no job); backup jobs with their schedule, retention and next run; replication jobs.
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
               ──when a page asks────▶ history (RRD), a guest's details, backups, firewall log, task logs
```

- **One cluster through any node.** Every Proxmox node answers for the whole cluster. The app uses the first node in `hosts` that answers and switches when it stops answering.
- **History from Proxmox.** The charts use Proxmox's own RRD data (hour, day, week, month, year), so there is history from the start and the app keeps no database.
- **Light on the cluster.** The live state is five small requests per `poll_seconds`. The configuration is a few requests per node and guest every 5 minutes.
- **Not in this version:** Proxmox Backup Server's own datastores and jobs, consoles, migration, creating or deleting guests, and editing the host network or the firewall options.
