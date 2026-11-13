"""A stand-in Proxmox VE cluster for development: its REST API with a made-up three-node homelab.

    python -m tools.mock_proxmox --port 18006

Token: root@pam!dashboard with secret "secret". Point the app at http://localhost:18006.
Power actions, firewall rule changes and VLAN changes work and are kept until it stops.
"""

import argparse
import copy
import math
import random
import time
from datetime import datetime

from aiohttp import web

TOKEN = "PVEAPIToken=root@pam!dashboard=secret"
GB = 1024**3
NODES = {
    "pve1": {
        "cores": 16,
        "sockets": 1,
        "model": "AMD Ryzen 9 7950X 16-Core Processor",
        "mem": 128 * GB,
        "ip": "192.168.1.11",
    },
    "pve2": {"cores": 8, "sockets": 1, "model": "Intel(R) Core(TM) i7-12700T", "mem": 64 * GB, "ip": "192.168.1.12"},
    "pve3": {"cores": 12, "sockets": 1, "model": "Intel(R) Core(TM) i5-13500", "mem": 64 * GB, "ip": "192.168.1.13"},
}
# vmid, type, name, node, status, cores, mem GB, disk GB, nics [(bridge, tag, firewall)], tags, ha
GUESTS = [
    (
        100,
        "qemu",
        "opnsense",
        "pve1",
        "running",
        4,
        8,
        32,
        [("vmbr0", None, False), ("vmbr0", 40, True), ("vmbr0", 50, True)],
        "network;core",
        True,
    ),
    (101, "qemu", "homeassistant", "pve1", "running", 4, 8, 64, [("vmbr0", 30, True)], "core;smarthome", True),
    (102, "qemu", "docker01", "pve2", "running", 8, 16, 200, [("vmbr0", 20, True)], "docker", False),
    (103, "qemu", "k8s-cp01", "pve1", "running", 4, 8, 64, [("vmbr0", 20, True)], "k8s", False),
    (104, "qemu", "k8s-worker01", "pve2", "running", 6, 16, 120, [("vmbr0", 20, True)], "k8s", False),
    (105, "qemu", "k8s-worker02", "pve3", "running", 6, 16, 120, [("vmbr0", 20, True)], "k8s", False),
    (106, "qemu", "win11-desk", "pve3", "stopped", 8, 16, 128, [("vmbr0", 10, False)], "desktop", False),
    (
        107,
        "qemu",
        "truenas",
        "pve3",
        "running",
        4,
        32,
        32,
        [("vmbr0", 20, True), ("vmbr1", None, False)],
        "storage;core",
        True,
    ),
    (108, "qemu", "kali", "pve2", "stopped", 4, 8, 80, [("vmbr0", 40, True)], "lab", False),
    (200, "lxc", "pihole", "pve1", "running", 1, 1, 8, [("vmbr0", 20, True)], "dns;core", True),
    (201, "lxc", "unifi", "pve2", "running", 2, 2, 16, [("vmbr0", 10, True)], "network", False),
    (202, "lxc", "nginx-proxy", "pve1", "running", 2, 1, 8, [("vmbr0", 40, True)], "web", True),
    (203, "lxc", "jellyfin", "pve3", "running", 4, 4, 32, [("vmbr0", 20, True)], "media", False),
    (204, "lxc", "grafana", "pve2", "running", 2, 2, 16, [("vmbr0", 20, True)], "monitoring", False),
    (205, "lxc", "influxdb", "pve2", "running", 2, 4, 64, [("vmbr0", 20, True)], "monitoring", False),
    (206, "lxc", "mosquitto", "pve1", "running", 1, 1, 4, [("vmbr0", 30, True)], "smarthome", False),
    (207, "lxc", "nextcloud", "pve3", "running", 4, 4, 64, [("vmbr0", 40, True)], "web", False),
    (208, "lxc", "zigbee2mqtt", "pve1", "running", 1, 1, 4, [("vmbr0", 30, True)], "smarthome", False),
    (209, "lxc", "guest-portal", "pve3", "stopped", 1, 1, 8, [("vmbr0", 50, True)], "", False),
    (9000, "qemu", "debian13-template", "pve2", "stopped", 2, 2, 16, [("vmbr0", None, False)], "template", False),
]
STORAGES = [
    # name, type, content, shared, total GB, used GB (per node or shared), nodes
    ("local", "dir", "iso,vztmpl,backup", False, 100, 38, None),
    ("local-lvm", "lvmthin", "images,rootdir", False, 1800, 640, None),
    ("ceph-vm", "rbd", "images,rootdir", True, 5400, 2310, None),
    ("pbs", "pbs", "backup", True, 8000, 3420, None),
    ("nas-iso", "nfs", "iso,vztmpl", True, 4000, 1280, None),
]


def mac(vmid: int, index: int) -> str:
    return f"BC:24:11:{vmid // 256 % 256:02X}:{vmid % 256:02X}:{index:02X}"


class MockCluster:
    def __init__(self, seed: int):
        self.rng = random.Random(seed)
        self.started = time.time()
        self.next_task = 1
        self.tasks: list[dict] = []
        self.guests = {}
        for vmid, kind, name, node, status, cores, mem, disk, nics, tags, ha in GUESTS:
            cfg = {
                "name" if kind == "qemu" else "hostname": name,
                "cores": cores,
                "memory": mem * 1024,
                "onboot": 1 if status == "running" else 0,
            }
            for i, (bridge, tag, fw) in enumerate(nics):
                if kind == "qemu":
                    value = (
                        f"virtio={mac(vmid, i)},bridge={bridge}"
                        + (",firewall=1" if fw else "")
                        + (f",tag={tag}" if tag else "")
                    )
                else:
                    value = (
                        f"name=eth{i},bridge={bridge}"
                        + (",firewall=1" if fw else "")
                        + f",hwaddr={mac(vmid, i)},ip=dhcp"
                        + (f",tag={tag}" if tag else "")
                        + ",type=veth"
                    )
                cfg[f"net{i}"] = value
            if kind == "qemu":
                cfg.update(
                    {
                        "scsi0": f"ceph-vm:vm-{vmid}-disk-0,discard=on,iothread=1,size={disk}G,ssd=1",
                        "scsihw": "virtio-scsi-single",
                        "ostype": "win11" if "win" in name else "l26",
                        "sockets": 1,
                        "cpu": "host",
                        "bios": "ovmf",
                        "machine": "q35",
                        "agent": "1" if name not in ("opnsense", "kali") else "0",
                        "boot": "order=scsi0",
                        "ide2": "nas-iso:iso/debian-13.1.0-amd64-netinst.iso,media=cdrom",
                    }
                )
                if name == "win11-desk":
                    cfg["tpmstate0"] = f"ceph-vm:vm-{vmid}-disk-1,size=4M,version=v2.0"
                    cfg["efidisk0"] = f"ceph-vm:vm-{vmid}-disk-2,efitype=4m,pre-enrolled-keys=1,size=1M"
            else:
                cfg.update(
                    {
                        "rootfs": f"local-lvm:vm-{vmid}-disk-0,size={disk}G",
                        "ostype": "debian",
                        "arch": "amd64",
                        "swap": 512,
                        "unprivileged": 1,
                        "features": "nesting=1",
                    }
                )
                if name == "jellyfin":
                    cfg["mp0"] = "/mnt/media,mp=/media,backup=0"
            if vmid == 9000:
                cfg["template"] = 1
            self.guests[vmid] = {
                "vmid": vmid,
                "type": kind,
                "name": name,
                "node": node,
                "status": status,
                "maxcpu": cores,
                "maxmem": mem * GB,
                "maxdisk": disk * GB,
                "tags": tags,
                "ha": ha,
                "template": 1 if vmid == 9000 else 0,
                "config": cfg,
                "digest": self._digest(),
                "uptime_from": self.started - self.rng.uniform(3600, 40 * 86400),
                "load": self.rng.uniform(0.02, 0.4),
                "memshare": self.rng.uniform(0.35, 0.85),
                "snapshots": self._snapshots(vmid),
                "fw": self._guest_firewall(vmid, name),
            }
        self.cluster_fw = {
            "options": {
                "enable": 1,
                "policy_in": "DROP",
                "policy_out": "ACCEPT",
                "ebtables": 1,
                "log_ratelimit": "enable=1,burst=5,rate=1/second",
            },
            "rules": [
                {
                    "type": "in",
                    "action": "ACCEPT",
                    "source": "+trusted",
                    "proto": "tcp",
                    "dport": "8006",
                    "comment": "Web interface from trusted networks",
                    "enable": 1,
                },
                {
                    "type": "in",
                    "action": "ACCEPT",
                    "macro": "SSH",
                    "source": "admin-net",
                    "comment": "SSH from the admin VLAN",
                    "enable": 1,
                },
                {"type": "in", "action": "ACCEPT", "macro": "Ping", "comment": "", "enable": 1},
                {
                    "type": "in",
                    "action": "ACCEPT",
                    "proto": "tcp",
                    "dport": "3128",
                    "source": "lan",
                    "comment": "SPICE proxy",
                    "enable": 0,
                },
                {
                    "type": "in",
                    "action": "DROP",
                    "source": "+blocklist",
                    "log": "info",
                    "comment": "Known bad",
                    "enable": 1,
                },
            ],
            "groups": {
                "webserver": {
                    "comment": "HTTP and HTTPS in",
                    "rules": [
                        {"type": "in", "action": "ACCEPT", "macro": "HTTP", "enable": 1},
                        {"type": "in", "action": "ACCEPT", "macro": "HTTPS", "enable": 1},
                    ],
                },
                "mgmt": {
                    "comment": "SSH and monitoring from the admin VLAN",
                    "rules": [
                        {"type": "in", "action": "ACCEPT", "macro": "SSH", "source": "admin-net", "enable": 1},
                        {
                            "type": "in",
                            "action": "ACCEPT",
                            "proto": "tcp",
                            "dport": "9100",
                            "source": "grafana",
                            "comment": "node exporter",
                            "enable": 1,
                        },
                        {"type": "in", "action": "ACCEPT", "macro": "Ping", "source": "admin-net", "enable": 1},
                    ],
                },
                "dns": {
                    "comment": "DNS for everyone",
                    "rules": [
                        {"type": "in", "action": "ACCEPT", "macro": "DNS", "enable": 1},
                    ],
                },
                "iot-isolate": {
                    "comment": "IoT may only reach MQTT and the internet",
                    "rules": [
                        {
                            "type": "out",
                            "action": "ACCEPT",
                            "dest": "mqtt-broker",
                            "proto": "tcp",
                            "dport": "1883,8883",
                            "enable": 1,
                        },
                        {"type": "out", "action": "ACCEPT", "macro": "DNS", "dest": "pihole", "enable": 1},
                        {
                            "type": "out",
                            "action": "DROP",
                            "dest": "+rfc1918",
                            "log": "info",
                            "comment": "no LAN",
                            "enable": 1,
                        },
                    ],
                },
            },
            "ipsets": {
                "trusted": {
                    "comment": "Admin and LAN",
                    "entries": [
                        {"cidr": "192.168.10.0/24", "comment": "admin VLAN"},
                        {"cidr": "192.168.1.0/24", "comment": "LAN"},
                    ],
                },
                "blocklist": {
                    "comment": "Blocked addresses",
                    "entries": [
                        {"cidr": "45.155.205.0/24"},
                        {"cidr": "185.220.101.0/24", "comment": "tor exits"},
                        {"cidr": "92.118.39.12"},
                    ],
                },
                "rfc1918": {
                    "comment": "Private networks",
                    "entries": [{"cidr": "10.0.0.0/8"}, {"cidr": "172.16.0.0/12"}, {"cidr": "192.168.0.0/16"}],
                },
                "management": {"comment": "", "entries": [{"cidr": "192.168.10.0/24"}]},
            },
            "aliases": [
                {"name": "admin-net", "cidr": "192.168.10.0/24", "comment": "Admin VLAN 10"},
                {"name": "lan", "cidr": "192.168.1.0/24", "comment": "Untagged LAN"},
                {"name": "iot-net", "cidr": "192.168.30.0/24", "comment": "IoT VLAN 30"},
                {"name": "dmz-net", "cidr": "192.168.40.0/24", "comment": "DMZ VLAN 40"},
                {"name": "mqtt-broker", "cidr": "192.168.30.6", "comment": "mosquitto"},
                {"name": "pihole", "cidr": "192.168.20.53", "comment": ""},
                {"name": "grafana", "cidr": "192.168.20.14", "comment": ""},
            ],
        }
        self.node_fw = {
            n: {
                "options": {
                    "enable": 1,
                    "log_level_in": "info" if n == "pve1" else "nolog",
                    "log_level_out": "nolog",
                    "nosmurfs": 1,
                    "tcpflags": 1 if n != "pve3" else 0,
                    "nf_conntrack_max": 262144,
                },
                "rules": [
                    {
                        "type": "in",
                        "action": "ACCEPT",
                        "source": "10.10.10.0/24",
                        "iface": "vmbr1",
                        "comment": "Ceph and migration",
                        "enable": 1,
                    }
                ]
                if n != "pve3"
                else [],
            }
            for n in NODES
        }
        self.network = {n: self._network(n, i) for i, n in enumerate(NODES)}
        self.pending_network = {
            "pve2": "--- /etc/network/interfaces\t2026-10-02 09:12:44.000000000 +0200\n+++ /etc/network/interfaces.new\t2026-11-13 08:41:02.000000000 +0100\n@@ -31,4 +31,11 @@\n \tbridge-vids 2-4094\n \n+auto vmbr0.60\n+iface vmbr0.60 inet static\n+\taddress 192.168.60.12/24\n+#Cameras\n+\n source /etc/network/interfaces.d/*\n"
        }
        for _ in range(60):
            self._task(
                self.rng.choice(
                    [
                        "vzdump",
                        "qmstart",
                        "vzstart",
                        "qmigrate",
                        "vncproxy",
                        "qmsnapshot",
                        "aptupdate",
                        "vzshutdown",
                        "imgcopy",
                    ]
                ),
                ago=self.rng.uniform(60, 6 * 86400),
            )
        self._task("vzdump", ago=4000, status="job errors", vmid="")
        self._task("qmigrate", ago=50, running=True, vmid=104)
        self.backups = self._backups()

    @staticmethod
    def _digest() -> str:
        return f"{random.getrandbits(160):040x}"

    def _snapshots(self, vmid):
        if vmid % 3:
            return [{"name": "current", "description": "You are here!", "digest": "x"}]
        now = time.time()
        return [
            {
                "name": "before-upgrade",
                "description": "Before the Debian 13 upgrade",
                "snaptime": int(now - 12 * 86400),
                "vmstate": 0,
            },
            {"name": "clean-install", "description": "", "snaptime": int(now - 80 * 86400), "vmstate": 0},
            {"name": "current", "description": "You are here!", "parent": "before-upgrade"},
        ]

    def _guest_firewall(self, vmid, name):
        rules = []
        enable = 1
        if name in ("nginx-proxy", "nextcloud"):
            rules = [
                {"type": "group", "action": "webserver", "enable": 1},
                {"type": "group", "action": "mgmt", "enable": 1},
            ]
        elif name == "pihole":
            rules = [
                {"type": "group", "action": "dns", "enable": 1},
                {"type": "group", "action": "mgmt", "enable": 1},
                {
                    "type": "in",
                    "action": "ACCEPT",
                    "proto": "tcp",
                    "dport": "80",
                    "source": "admin-net",
                    "comment": "admin page",
                    "enable": 1,
                },
            ]
        elif name in ("mosquitto",):
            rules = [
                {
                    "type": "in",
                    "action": "ACCEPT",
                    "proto": "tcp",
                    "dport": "1883,8883",
                    "source": "iot-net",
                    "enable": 1,
                },
                {
                    "type": "in",
                    "action": "ACCEPT",
                    "proto": "tcp",
                    "dport": "1883",
                    "source": "192.168.30.5",
                    "comment": "Home Assistant",
                    "enable": 1,
                },
                {"type": "group", "action": "mgmt", "enable": 1},
            ]
        elif name in ("zigbee2mqtt",):
            rules = [
                {"type": "group", "action": "iot-isolate", "enable": 1},
                {
                    "type": "in",
                    "action": "ACCEPT",
                    "proto": "tcp",
                    "dport": "8080",
                    "source": "admin-net",
                    "comment": "frontend",
                    "enable": 1,
                },
            ]
        elif name == "homeassistant":
            rules = [
                {
                    "type": "in",
                    "action": "ACCEPT",
                    "proto": "tcp",
                    "dport": "8123",
                    "source": "+trusted",
                    "comment": "Home Assistant UI",
                    "enable": 1,
                },
                {"type": "in", "action": "ACCEPT", "proto": "udp", "dport": "5353", "comment": "mDNS", "enable": 1},
                {"type": "group", "action": "mgmt", "enable": 1},
                {"type": "in", "action": "REJECT", "source": "+blocklist", "log": "info", "enable": 1},
            ]
        elif name in ("grafana", "influxdb"):
            rules = [
                {
                    "type": "in",
                    "action": "ACCEPT",
                    "proto": "tcp",
                    "dport": "3000" if name == "grafana" else "8086",
                    "source": "+trusted",
                    "enable": 1,
                },
                {"type": "group", "action": "mgmt", "enable": 1},
            ]
        elif name in ("docker01", "k8s-cp01", "k8s-worker01", "k8s-worker02"):
            rules = [
                {"type": "group", "action": "webserver", "enable": 1},
                {"type": "group", "action": "mgmt", "enable": 1},
                {
                    "type": "in",
                    "action": "ACCEPT",
                    "proto": "tcp",
                    "dport": "6443",
                    "source": "admin-net",
                    "comment": "kube API",
                    "enable": 1 if "k8s" in name else 0,
                },
            ]
        elif name == "jellyfin":
            rules = [{"type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "8096", "source": "lan", "enable": 1}]
        elif name == "kali":
            rules = [
                {
                    "type": "out",
                    "action": "DROP",
                    "dest": "+rfc1918",
                    "comment": "keep the lab off the LAN",
                    "enable": 1,
                }
            ]
        else:
            enable = 0
        options = {
            "enable": enable,
            "dhcp": 1,
            "ipfilter": 1 if name in ("homeassistant", "pihole") else 0,
            "macfilter": 1,
            "ndp": 1,
            "radv": 0,
            "policy_in": "DROP" if enable else "ACCEPT",
            "policy_out": "ACCEPT",
            "log_level_in": "info" if enable else "nolog",
            "log_level_out": "nolog",
        }
        return {
            "options": options,
            "rules": rules,
            "aliases": [],
            "ipsets": [{"name": "ipfilter-net0", "comment": ""}] if options["ipfilter"] else [],
        }

    def _network(self, node, i):
        n = i + 1
        ifaces = [
            {
                "iface": "eno1",
                "type": "eth",
                "active": 1,
                "autostart": 0,
                "method": "manual",
                "families": ["inet"],
                "exists": 1,
            },
            {
                "iface": "eno2",
                "type": "eth",
                "active": 1,
                "autostart": 0,
                "method": "manual",
                "families": ["inet"],
                "exists": 1,
            },
            {
                "iface": "enp3s0f0",
                "type": "eth",
                "active": 1,
                "autostart": 1,
                "method": "manual",
                "families": ["inet"],
                "exists": 1,
                "comments": "10G storage\n",
            },
            {
                "iface": "enp3s0f1",
                "type": "eth",
                "active": 0,
                "autostart": 0,
                "method": "manual",
                "families": ["inet"],
                "exists": 1,
            },
            {
                "iface": "bond0",
                "type": "bond",
                "active": 1,
                "autostart": 1,
                "method": "manual",
                "families": ["inet"],
                "slaves": "eno1 eno2",
                "bond_mode": "802.3ad",
                "bond-xmit-hash-policy": "layer3+4",
                "comments": "LACP to the switch\n",
            },
            {
                "iface": "vmbr0",
                "type": "bridge",
                "active": 1,
                "autostart": 1,
                "method": "static",
                "families": ["inet"],
                "bridge_ports": "bond0",
                "bridge_stp": "off",
                "bridge_fd": "0",
                "bridge_vlan_aware": 1,
                "bridge_vids": "2-4094",
                "cidr": f"192.168.1.1{n}/24",
                "address": f"192.168.1.1{n}",
                "netmask": "24",
                "gateway": "192.168.1.1",
                "mtu": "1500",
            },
            {
                "iface": "vmbr0.10",
                "type": "vlan",
                "active": 1,
                "autostart": 1,
                "method": "static",
                "families": ["inet"],
                "vlan-raw-device": "vmbr0",
                "vlan-id": 10,
                "cidr": f"192.168.10.1{n}/24",
                "address": f"192.168.10.1{n}",
                "comments": "Management\n",
            },
            {
                "iface": "vmbr1",
                "type": "bridge",
                "active": 1,
                "autostart": 1,
                "method": "static",
                "families": ["inet"],
                "bridge_ports": "enp3s0f0",
                "bridge_stp": "off",
                "bridge_fd": "0",
                "cidr": f"10.10.10.1{n}/24",
                "address": f"10.10.10.1{n}",
                "mtu": "9000",
                "comments": "Ceph and migration\n",
            },
        ]
        return ifaces

    def _backups(self):
        out = []
        now = time.time()
        for vmid, g in self.guests.items():
            if vmid in (108, 209, 9000):
                continue
            count = 6 if vmid != 106 else 2
            for k in range(count):
                age = (k * 1 + (0 if vmid != 106 else 19)) * 86400 + 3600 * 2 + vmid
                ts = int(now - age)
                stamp = datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")
                kind = "vm" if g["type"] == "qemu" else "ct"
                out.append(
                    {
                        "volid": f"pbs:backup/{kind}/{vmid}/{stamp}",
                        "content": "backup",
                        "format": "pbs-" + kind,
                        "vmid": vmid,
                        "ctime": ts,
                        "size": int(g["maxdisk"] * self.rng.uniform(0.15, 0.45)),
                        "notes": f"{g['name']}",
                        "protected": 1 if k == count - 1 and vmid in (101, 107) else 0,
                        "verification": {
                            "state": "ok" if k < 4 else "failed" if vmid == 102 and k == 5 else "ok",
                            "upid": "x",
                        }
                        if k < 5
                        else None,
                        "subtype": g["type"],
                    }
                )
        return out

    def _task(self, kind, ago=0.0, status="OK", vmid=None, running=False, node=None, user="root@pam"):
        node = node or self.rng.choice(list(NODES))
        start = int(time.time() - ago)
        if vmid is None:
            vmid = self.rng.choice(list(self.guests)) if kind not in ("aptupdate",) else ""
        upid = f"UPID:{node}:{self.next_task:08X}:{self.rng.randint(1, 99999):08X}:{start:08X}:{kind}:{vmid}:{user}:"
        self.next_task += 1
        if status == "OK" and not running and self.rng.random() < 0.05:
            status = "unexpected status"
        task = {
            "upid": upid,
            "node": node,
            "type": kind,
            "id": str(vmid),
            "user": user,
            "starttime": start,
            "pid": 1000 + self.next_task,
            "pstart": 1,
        }
        if not running:
            task["endtime"] = start + int(self.rng.uniform(1, 600 if kind == "vzdump" else 30))
            task["status"] = status
        self.tasks.append(task)
        return upid

    # ------------------------------------------------------------------
    def wave(self, period, phase=0.0, now=None):
        now = now or time.time()
        return (math.sin(now / period + phase) + 1) / 2

    def guest_resource(self, g):
        now = time.time()
        running = g["status"] == "running"
        cpu = (g["load"] * (0.6 + 0.8 * self.wave(300, g["vmid"])) + self.rng.uniform(0, 0.03)) if running else 0
        r = {
            "id": f"{g['type']}/{g['vmid']}",
            "type": g["type"],
            "vmid": g["vmid"],
            "name": g["name"],
            "node": g["node"],
            "status": g["status"],
            "cpu": cpu,
            "maxcpu": g["maxcpu"],
            "mem": int(g["maxmem"] * g["memshare"] * (0.9 + 0.1 * self.wave(600, g["vmid"]))) if running else 0,
            "maxmem": g["maxmem"],
            "disk": int(g["maxdisk"] * 0.42) if g["type"] == "lxc" else 0,
            "maxdisk": g["maxdisk"],
            "uptime": int(now - g["uptime_from"]) if running else 0,
            "netin": int((now - g["uptime_from"]) * 9000) if running else 0,
            "netout": int((now - g["uptime_from"]) * 4000) if running else 0,
            "diskread": 0,
            "diskwrite": 0,
            "template": g["template"],
            "tags": g["tags"],
        }
        if g["ha"]:
            r["hastate"] = "started" if running else "stopped"
        if g["vmid"] in (101, 107, 200):
            r["pool"] = "core"
        if g.get("lock"):
            r["lock"] = g["lock"]
        return r

    def node_load(self, node):
        guests = [g for g in self.guests.values() if g["node"] == node and g["status"] == "running"]
        used = sum(g["load"] * g["maxcpu"] for g in guests) / NODES[node]["cores"]
        mem = sum(g["maxmem"] * g["memshare"] for g in guests) + 6 * GB
        return min(0.95, used * (0.7 + 0.6 * self.wave(300, len(node)))), min(mem, NODES[node]["mem"] * 0.95)

    def resources(self):
        out = []
        for name, n in NODES.items():
            cpu, mem = self.node_load(name)
            out.append(
                {
                    "id": f"node/{name}",
                    "type": "node",
                    "node": name,
                    "status": "online",
                    "cpu": cpu,
                    "maxcpu": n["cores"],
                    "mem": int(mem),
                    "maxmem": n["mem"],
                    "disk": 21 * GB,
                    "maxdisk": 94 * GB,
                    "uptime": int(time.time() - self.started + 31 * 86400 + 3600 * len(name)),
                    "level": "",
                    "cgroup-mode": 2,
                }
            )
        out += [self.guest_resource(g) for g in self.guests.values()]
        for st, kind, content, shared, total, used, _ in STORAGES:
            for i, node in enumerate(NODES):
                u = used if shared else used * (0.6 + 0.3 * i)
                out.append(
                    {
                        "id": f"storage/{node}/{st}",
                        "type": "storage",
                        "storage": st,
                        "node": node,
                        "status": "available",
                        "plugintype": kind,
                        "content": content,
                        "shared": 1 if shared else 0,
                        "disk": int(u * GB),
                        "maxdisk": int(total * GB),
                    }
                )
        out.append({"id": "pool/core", "type": "pool", "pool": "core"})
        out.append({"id": "sdn/pve1/lab", "type": "sdn", "sdn": "lab", "node": "pve1", "status": "ok"})
        return out

    def node_status(self, node):
        n = NODES[node]
        cpu, mem = self.node_load(node)
        return {
            "uptime": int(time.time() - self.started + 31 * 86400),
            "cpu": cpu,
            "wait": 0.004 + 0.01 * self.wave(200),
            "loadavg": [f"{cpu * n['cores']:.2f}", f"{cpu * n['cores'] * 0.9:.2f}", f"{cpu * n['cores'] * 0.8:.2f}"],
            "memory": {
                "total": n["mem"],
                "used": int(mem),
                "free": int(n["mem"] - mem),
                "available": int(n["mem"] - mem),
            },
            "swap": {"total": 8 * GB, "used": int(0.2 * GB), "free": int(7.8 * GB)},
            "rootfs": {"total": 94 * GB, "used": 21 * GB, "avail": 73 * GB, "free": 73 * GB},
            "ksm": {"shared": 2 * GB if node == "pve1" else 0},
            "cpuinfo": {
                "model": n["model"],
                "cpus": n["cores"] * (2 if "AMD" in n["model"] else 1),
                "sockets": n["sockets"],
                "cores": n["cores"],
                "mhz": "4500.000",
                "hvm": "1",
                "user_hz": 100,
            },
            "kversion": "Linux 6.17.2-1-pve #1 SMP PREEMPT_DYNAMIC PMX 6.17.2-1 (2026-10-21T08:41Z)",
            "pveversion": "pve-manager/9.1.1/42db4a6cf33dac83",
            "current-kernel": {
                "sysname": "Linux",
                "release": "6.17.2-1-pve",
                "machine": "x86_64",
                "version": "#1 SMP PREEMPT_DYNAMIC PMX 6.17.2-1",
            },
            "boot-info": {"mode": "efi", "secureboot": 1 if node == "pve1" else 0},
        }

    def rrd(self, timeframe, kind, key, node=None):
        span = {"hour": 3600, "day": 86400, "week": 7 * 86400, "month": 30 * 86400, "year": 365 * 86400}[timeframe]
        step = span // 70
        now = int(time.time()) // step * step
        rows = []
        rng = random.Random(f"{key}{timeframe}")
        for i in range(70):
            t = now - (69 - i) * step
            daily = (math.sin((t % 86400) / 86400 * 2 * math.pi - 1.8) + 1) / 2
            noise = rng.uniform(-0.04, 0.04)
            if kind == "node":
                n = NODES[node]
                cpu, mem = self.node_load(node)
                c = max(0.01, cpu * (0.5 + daily) + noise)
                rows.append(
                    {
                        "time": t,
                        "cpu": c,
                        "maxcpu": n["cores"],
                        "loadavg": c * n["cores"],
                        "iowait": max(0, 0.01 + noise / 4 + (0.05 if i in (20, 21) else 0)),
                        "memused": mem * (0.92 + 0.08 * daily),
                        "memtotal": n["mem"],
                        "swapused": 0.2 * GB,
                        "swaptotal": 8 * GB,
                        "rootused": 21 * GB,
                        "roottotal": 94 * GB,
                        "netin": (2e6 + 8e6 * daily) * (1 + noise * 5) * (1.5 if node == "pve1" else 1),
                        "netout": (1e6 + 5e6 * daily) * (1 + noise * 5),
                    }
                )
            elif kind == "storage":
                total = next(s[4] for s in STORAGES if s[0] == key.split("/")[-1]) * GB
                used = next(s[5] for s in STORAGES if s[0] == key.split("/")[-1]) * GB
                growth = (i - 69) / 69 * span / (30 * 86400) * 0.06
                rows.append({"time": t, "used": used * (1 + growth + noise / 10), "total": total})
            else:
                g = self.guests[int(key)]
                if g["status"] != "running":
                    rows.append({"time": t})
                    continue
                c = max(0.0, g["load"] * (0.5 + daily) + noise / 2)
                rows.append(
                    {
                        "time": t,
                        "cpu": c,
                        "maxcpu": g["maxcpu"],
                        "mem": g["maxmem"] * g["memshare"] * (0.9 + 0.1 * daily),
                        "maxmem": g["maxmem"],
                        "disk": 0,
                        "maxdisk": g["maxdisk"],
                        "netin": (5e4 + 6e5 * daily) * (1 + noise * 5),
                        "netout": (2e4 + 3e5 * daily) * (1 + noise * 5),
                        "diskread": 2e5 * daily + rng.uniform(0, 1e5),
                        "diskwrite": 4e5 * daily + rng.uniform(0, 2e5),
                    }
                )
        return rows

    def firewall_log(self, node, limit):
        lines = []
        rng = random.Random(node + str(int(time.time() // 30)))
        now = time.time()
        sources = [
            "45.155.205.233",
            "185.220.101.44",
            "192.168.30.41",
            "192.168.30.52",
            "192.168.50.23",
            "198.51.100.7",
            "203.0.113.9",
            "192.168.1.66",
            "92.118.39.12",
            "192.168.40.12",
        ]
        ports = [22, 23, 3389, 445, 8006, 80, 443, 5900, 1883, 53, 8080, 3306]
        guests = [
            g
            for g in self.guests.values()
            if g["node"] == node and g["fw"]["options"]["enable"] and g["status"] == "running"
        ]
        for _i in range(min(limit, 400)):
            ts = now - rng.uniform(0, 86400) ** 1.15 / 86400**0.15
            target = rng.choice([None, None, *guests]) if guests else None
            vmid = target["vmid"] if target else 0
            chain = (
                f"tap{vmid}i0-IN"
                if target and target["type"] == "qemu"
                else f"veth{vmid}i0-IN"
                if target
                else "PVEFW-HOST-IN"
            )
            src = rng.choice(sources)
            dpt = rng.choice(ports)
            action = rng.choices(["DROP", "REJECT", "ACCEPT"], [8, 2, 3])[0]
            policy = action != "ACCEPT" and rng.random() < 0.7
            proto = "UDP" if dpt in (53, 5353) else "TCP"
            if target and "iot-isolate" in [r.get("action") for r in target["fw"]["rules"]] and rng.random() < 0.5:
                chain = chain.replace("-IN", "-OUT")
                src, dst, action, policy = (
                    "192.168.30.8",
                    rng.choice(["192.168.1.20", "192.168.20.5", "192.168.10.2"]),
                    "DROP",
                    False,
                )
            else:
                dst = (
                    NODES[node]["ip"]
                    if not target
                    else f"192.168.{(target['vmid'] % 5 + 1) * 10}.{target['vmid'] % 250}"
                )
            stamp = datetime.fromtimestamp(ts).astimezone().strftime("%d/%b/%Y:%H:%M:%S %z")
            head = f"policy {action}" if policy else action
            lines.append(
                (
                    ts,
                    f"{vmid} 6 {chain} {stamp} {head}: IN=vmbr0 OUT=vmbr0 PHYSIN=bond0 MAC=bc:24:11:00:00:00 SRC={src} DST={dst} LEN=60 TOS=0x00 PREC=0x00 TTL=52 ID=0 DF PROTO={proto} SPT={rng.randint(1024, 65535)} DPT={dpt} SEQ=1 ACK=0 WINDOW=64240 SYN",
                )
            )
        lines.sort()
        out = [
            {
                "n": 1,
                "t": f"0 5 - {datetime.fromtimestamp(now - 90000).astimezone().strftime('%d/%b/%Y:%H:%M:%S %z')} starting pvefw logger",
            }
        ]
        out += [{"n": i + 2, "t": t} for i, (_, t) in enumerate(lines)]
        return out


def build_app(cluster: MockCluster) -> web.Application:
    app = web.Application()

    @web.middleware
    async def auth(request, handler):
        if request.headers.get("Authorization") != TOKEN:
            return web.json_response({"data": None}, status=401, reason="authentication failure")
        try:
            result = await handler(request)
        except KeyError:
            return web.json_response({"data": None}, status=500, reason="does not exist")
        if isinstance(result, web.StreamResponse):
            return result
        return web.json_response({"data": result})

    app.middlewares.append(auth)
    r = app.router
    P = "/api2/json"

    def guest_of(request):
        g = cluster.guests[int(request.match_info["vmid"])]
        if g["node"] != request.match_info["node"] or g["type"] != request.match_info["kind"]:
            raise KeyError
        return g

    async def form(request):
        return dict(await request.post()) if request.can_read_body else {}

    # ---------------- cluster
    async def version(_):
        return {"version": "9.1.1", "release": "9.1", "repoid": "42db4a6cf33dac83"}

    async def status(_):
        out = [
            {"type": "cluster", "id": "cluster", "name": "homelab", "nodes": len(NODES), "quorate": 1, "version": 14}
        ]
        for i, (name, n) in enumerate(NODES.items(), 1):
            out.append(
                {
                    "type": "node",
                    "id": f"node/{name}",
                    "name": name,
                    "nodeid": i,
                    "ip": n["ip"],
                    "online": 1,
                    "local": 1 if i == 1 else 0,
                    "level": "c" if i < 3 else "",
                }
            )
        return out

    async def resources(_):
        return cluster.resources()

    async def tasks(_):
        return sorted(cluster.tasks, key=lambda t: t["starttime"], reverse=True)[:50]

    async def ha_status(_):
        out = [
            {"id": "quorum", "type": "quorum", "status": "OK", "quorate": 1, "node": "pve1"},
            {
                "id": "master",
                "type": "master",
                "status": "pve2 (active, Thu Nov 13 09:10:41 2026)",
                "node": "pve2",
                "timestamp": int(time.time() - 4),
            },
        ]
        for n in NODES:
            active = any(g["ha"] and g["node"] == n for g in cluster.guests.values())
            out.append(
                {
                    "id": f"lrm:{n}",
                    "type": "lrm",
                    "node": n,
                    "status": f"{n} ({'active' if active else 'idle'}, Thu Nov 13 09:10:43 2026)",
                    "state": "active" if active else "idle",
                    "timestamp": int(time.time() - 3),
                }
            )
        for g in cluster.guests.values():
            if g["ha"]:
                kind = "vm" if g["type"] == "qemu" else "ct"
                state = "started" if g["status"] == "running" else "stopped"
                out.append(
                    {
                        "id": f"service:{kind}:{g['vmid']}",
                        "sid": f"{kind}:{g['vmid']}",
                        "type": "service",
                        "node": g["node"],
                        "state": state,
                        "crm_state": state,
                        "request_state": state,
                        "max_restart": 1,
                        "max_relocate": 1,
                        "status": f"{kind}:{g['vmid']} ({g['node']}, {state})",
                    }
                )
        return out

    async def ha_resources(_):
        return [
            {
                "sid": f"{'vm' if g['type'] == 'qemu' else 'ct'}:{g['vmid']}",
                "type": "vm" if g["type"] == "qemu" else "ct",
                "state": "started",
                "group": "prefer-pve1" if g["node"] == "pve1" else "",
                "max_restart": 1,
                "max_relocate": 1,
            }
            for g in cluster.guests.values()
            if g["ha"]
        ]

    async def ha_groups(_):
        return [
            {"group": "prefer-pve1", "nodes": "pve1:2,pve2:1,pve3:1", "restricted": 0, "nofailback": 0, "type": "group"}
        ]

    async def ceph(_):
        t = time.time()
        return {
            "fsid": "6c1f0b0e-6a1b-4f3e-9a5d-2f4b8c9d1e7a",
            "health": {
                "status": "HEALTH_WARN",
                "checks": {
                    "OSD_NEARFULL": {
                        "severity": "HEALTH_WARN",
                        "summary": {"message": "1 nearfull osd(s)", "count": 1},
                    },
                    "POOL_NO_REDUNDANCY": {
                        "severity": "HEALTH_WARN",
                        "summary": {"message": "1 pool(s) have no replicas configured"},
                    },
                },
            },
            "quorum_names": list(NODES),
            "monmap": {"mons": [{"name": n} for n in NODES]},
            "osdmap": {"num_osds": 6, "num_up_osds": 6, "num_in_osds": 6},
            "mgrmap": {"available": True, "active_name": "pve2"},
            "pgmap": {
                "num_pgs": 129,
                "bytes_total": 5400 * GB,
                "bytes_used": 2310 * GB,
                "bytes_avail": 3090 * GB,
                "data_bytes": 770 * GB,
                "read_bytes_sec": int(2e7 * cluster.wave(60)),
                "write_bytes_sec": int(3.5e7 * cluster.wave(90, 1)),
                "read_op_per_sec": int(300 * cluster.wave(60)),
                "write_op_per_sec": int(450 * cluster.wave(80)),
                "pgs_by_state": [
                    {"state_name": "active+clean", "count": 127 if int(t) % 120 > 30 else 125},
                    *([] if int(t) % 120 > 30 else [{"state_name": "active+clean+scrubbing+deep", "count": 2}]),
                    {"state_name": "active+clean+snaptrim", "count": 2},
                ],
            },
        }

    async def backup_jobs(_):
        return [
            {
                "id": "backup-3c7e5f12-1a2b",
                "type": "vzdump",
                "schedule": "02:00",
                "storage": "pbs",
                "all": 1,
                "exclude": "108,209,9000",
                "mode": "snapshot",
                "enabled": 1,
                "next-run": int(time.time() // 86400 * 86400 + 86400 + 3600),
                "notes-template": "{{guestname}}",
                "prune-backups": "keep-daily=7,keep-weekly=4",
            },
            {
                "id": "backup-weekly-iso",
                "type": "vzdump",
                "schedule": "sat 04:00",
                "storage": "local",
                "vmid": "101,107",
                "mode": "stop",
                "enabled": 0,
                "comment": "Offline copy",
            },
        ]

    async def not_backed_up(_):
        return [{"vmid": v, "name": cluster.guests[v]["name"], "type": cluster.guests[v]["type"]} for v in (108, 209)]

    async def replication(_):
        return [
            {
                "id": "203-0",
                "guest": 203,
                "jobnum": 0,
                "target": "pve1",
                "schedule": "*/15",
                "type": "local",
                "source": "pve3",
                "rate": 50,
            }
        ]

    async def node_replication(request):
        if request.match_info["node"] != "pve3":
            return []
        return [
            {
                "id": "203-0",
                "guest": 203,
                "target": "pve1",
                "last_sync": int(time.time() - 420),
                "duration": 3.4,
                "fail_count": 0,
                "next_sync": int(time.time() + 480),
                "schedule": "*/15",
                "state": "ok",
            }
        ]

    async def pools(_):
        return [{"poolid": "core", "comment": "Things the house needs"}]

    async def sdn_zones(_):
        return [{"zone": "lab", "type": "vlan", "bridge": "vmbr0", "ipam": "pve", "nodes": "", "digest": "x"}]

    async def sdn_vnets(_):
        return [
            {"vnet": "servers", "zone": "lab", "tag": 20, "alias": "Servers VLAN", "type": "vnet"},
            {"vnet": "iot", "zone": "lab", "tag": 30, "alias": "IoT", "type": "vnet"},
            {"vnet": "dmz", "zone": "lab", "tag": 40, "alias": "DMZ", "type": "vnet"},
        ]

    async def sdn_subnets(request):
        tag = {"servers": 20, "iot": 30, "dmz": 40}[request.match_info["vnet"]]
        return [
            {
                "subnet": f"lab-192.168.{tag}.0-24",
                "cidr": f"192.168.{tag}.0/24",
                "gateway": f"192.168.{tag}.1",
                "snat": 0 if tag != 40 else 1,
                "type": "subnet",
                "dhcp-range": [{"start-address": f"192.168.{tag}.100", "end-address": f"192.168.{tag}.199"}]
                if tag == 30
                else [],
            }
        ]

    # ---------------- firewall
    def with_pos(rules):
        return [
            {
                **copy.deepcopy(rule),
                "pos": i,
                "digest": "d" * 40,
                "ipversion": 4 if "." in str(rule.get("source", "")) else None,
            }
            for i, rule in enumerate(rules)
        ]

    def rules_of(request):
        m = request.match_info
        if "group" in m:
            return cluster.cluster_fw["groups"][m["group"]]["rules"]
        if "vmid" in m:
            return guest_of(request)["fw"]["rules"]
        if "node" in m:
            return cluster.node_fw[m["node"]]["rules"]
        return cluster.cluster_fw["rules"]

    async def rules_get(request):
        return with_pos(rules_of(request))

    def rule_from(data):
        out = {
            k: v
            for k, v in data.items()
            if k in ("type", "action", "macro", "source", "dest", "proto", "dport", "sport", "iface", "log", "comment")
        }
        out["enable"] = int(data.get("enable", 0))
        return out

    async def rules_post(request):
        data = await form(request)
        rules = rules_of(request)
        pos = int(data.get("pos", 0)) if "pos" in data else 0
        rules.insert(min(pos, len(rules)), rule_from(data))
        return None

    async def rule_put(request):
        data = await form(request)
        rules = rules_of(request)
        pos = int(request.match_info["pos"])
        rule = rules[pos]
        if "moveto" in data:
            rules.pop(pos)
            to = int(data["moveto"])
            rules.insert(to if to < pos else max(0, to - 1), rule)
        else:
            for key in str(data.get("delete", "")).split(","):
                rule.pop(key, None)
            for key in (
                "type",
                "action",
                "macro",
                "source",
                "dest",
                "proto",
                "dport",
                "sport",
                "iface",
                "log",
                "comment",
            ):
                if key in data:
                    rule[key] = data[key]
            if "enable" in data:
                rule["enable"] = int(data["enable"])
        return None

    async def rule_delete(request):
        rules_of(request).pop(int(request.match_info["pos"]))
        return None

    async def fw_options(request):
        m = request.match_info
        if "vmid" in m:
            return guest_of(request)["fw"]["options"]
        if "node" in m:
            return cluster.node_fw[m["node"]]["options"]
        return cluster.cluster_fw["options"]

    async def groups(_):
        return [{"group": k, "comment": v["comment"], "digest": "x"} for k, v in cluster.cluster_fw["groups"].items()]

    async def ipsets(_):
        return [{"name": k, "comment": v["comment"], "digest": "x"} for k, v in cluster.cluster_fw["ipsets"].items()]

    async def ipset(request):
        return [{**e, "nomatch": 0} for e in cluster.cluster_fw["ipsets"][request.match_info["name"]]["entries"]]

    async def aliases(_):
        return cluster.cluster_fw["aliases"]

    async def macros(_):
        return [
            {"macro": m, "descr": d}
            for m, d in (
                ("DNS", "Domain Name System traffic (upd and tcp)"),
                ("HTTP", "Hypertext Transfer Protocol (WWW)"),
                ("HTTPS", "Hypertext Transfer Protocol (WWW) over SSL"),
                ("SSH", "Secure shell traffic"),
                ("Ping", "ICMP echo request"),
                ("SMB", "Microsoft SMB traffic"),
                ("NFS", "Network File System (NFS) server"),
                ("RDP", "Microsoft Remote Desktop Protocol traffic"),
                ("SMTP", "Simple Mail Transfer Protocol"),
                ("NTP", "Network Time Protocol (ntpd)"),
                ("MySQL", "MySQL server"),
                ("PostgreSQL", "PostgreSQL server"),
                ("VNC", "VNC traffic for VNC display's 0 - 99"),
                ("Telnet", "Telnet traffic"),
            )
        ]

    async def guest_aliases(request):
        return guest_of(request)["fw"]["aliases"]

    async def guest_ipsets(request):
        return guest_of(request)["fw"]["ipsets"]

    async def fw_log(request):
        return cluster.firewall_log(request.match_info["node"], int(request.query.get("limit", 500)))

    # ---------------- nodes
    async def node_status(request):
        return cluster.node_status(request.match_info["node"])

    async def node_network(request):
        node = request.match_info["node"]
        body = {"data": cluster.network[node]}
        if node in cluster.pending_network:
            body["changes"] = cluster.pending_network[node]
        return web.json_response(body)

    async def disks(request):
        i = list(NODES).index(request.match_info["node"])
        return [
            {
                "devpath": "/dev/nvme0n1",
                "type": "nvme",
                "model": "Samsung SSD 990 PRO 2TB",
                "serial": f"S7KHNJ0W{i}12345",
                "size": 2000398934016,
                "health": "PASSED",
                "wearout": 97 - i * 6,
                "used": "LVM",
                "gpt": 1,
            },
            {
                "devpath": "/dev/sda",
                "type": "ssd",
                "model": "Micron_5400_MTFDDAK1T9TGA",
                "serial": f"2234{i}F00AB1",
                "size": 1920383410176,
                "health": "PASSED",
                "wearout": 99,
                "used": f"ceph osd.{i * 2}",
                "osdid": i * 2,
                "gpt": 1,
            },
            {
                "devpath": "/dev/sdb",
                "type": "ssd",
                "model": "Micron_5400_MTFDDAK1T9TGA",
                "serial": f"2234{i}F00AB2",
                "size": 1920383410176,
                "health": "PASSED" if i != 2 else "FAILED",
                "wearout": 98 if i != 2 else 41,
                "used": f"ceph osd.{i * 2 + 1}",
                "osdid": i * 2 + 1,
                "gpt": 1,
            },
            {
                "devpath": "/dev/sdc",
                "type": "hdd",
                "model": "WDC WD120EFBX-68B0EN0",
                "serial": f"5PJ{i}X1AB",
                "size": 12000138625024,
                "health": "PASSED",
                "wearout": "N/A",
                "used": "ZFS",
                "rpm": 7200,
                "gpt": 1,
            },
        ]

    async def zfs(request):
        node = request.match_info["node"]
        return [
            {
                "name": "tank",
                "size": 12 * 1000**4,
                "alloc": int((7.1 if node != "pve2" else 9.8) * 1000**4),
                "free": int((4.9 if node != "pve2" else 2.2) * 1000**4),
                "frag": 12 if node != "pve2" else 31,
                "dedup": 1.0,
                "health": "ONLINE" if node != "pve3" else "DEGRADED",
            }
        ]

    async def services(request):
        names = [
            ("pveproxy", "PVE API Proxy Server"),
            ("pvedaemon", "PVE API Daemon"),
            ("pvestatd", "PVE Status Daemon"),
            ("pve-cluster", "The Proxmox VE cluster filesystem"),
            ("corosync", "Corosync Cluster Engine"),
            ("pve-firewall", "Proxmox VE firewall"),
            ("pve-ha-crm", "PVE Cluster HA Resource Manager Daemon"),
            ("pve-ha-lrm", "PVE Local HA Resource Manager Daemon"),
            ("chrony", "chrony, an NTP client/server"),
            ("postfix", "Postfix Mail Transport Agent"),
            ("spiceproxy", "PVE SPICE Proxy Server"),
            ("sshd", "OpenBSD Secure Shell server"),
            ("cron", "Regular background program processing daemon"),
            ("ksmtuned", "Kernel Samepage Merging (KSM) Tuning Daemon"),
            ("systemd-journald", "Journal Service"),
            ("syslog", "System Logging Service"),
        ]
        node = request.match_info["node"]
        return [
            {
                "service": s,
                "name": s,
                "desc": d,
                "state": "running" if not (s == "postfix" and node == "pve3") else "dead",
                "active-state": "active" if not (s == "postfix" and node == "pve3") else "failed",
                "unit-state": "enabled",
            }
            for s, d in names
        ]

    async def updates(request):
        node = request.match_info["node"]
        if node == "pve3":
            return []
        pk = [
            ("pve-manager", "9.1.1", "9.1.2", "The Proxmox Virtual Environment"),
            ("proxmox-kernel-6.17", "6.17.2-1", "6.17.2-2", "Latest Proxmox Kernel Image"),
            ("libpve-storage-perl", "9.0.14", "9.0.15", "Proxmox VE storage management library"),
            ("openssl", "3.5.4-1", "3.5.4-1+deb13u1", "Secure Sockets Layer toolkit"),
            ("qemu-server", "9.0.26", "9.0.27", "Qemu Server Tools"),
            (
                "ceph-common",
                "19.2.3-pve2",
                "19.2.3-pve3",
                "common utilities to mount and interact with a ceph storage cluster",
            ),
        ]
        return [
            {
                "Package": p,
                "OldVersion": o,
                "Version": v,
                "Title": t,
                "Origin": "Proxmox" if not p.startswith("openssl") else "Debian",
                "Priority": "optional",
                "Section": "admin",
            }
            for p, o, v, t in (pk if node == "pve1" else pk[:2])
        ]

    async def subscription(request):
        if request.match_info["node"] == "pve1":
            return {
                "status": "active",
                "level": "c",
                "productname": "Proxmox VE Community Subscription 1 CPU/year",
                "nextduedate": "2027-03-01",
                "regdate": "2026-03-01 00:00:00",
            }
        return {"status": "notfound", "message": "There is no subscription key"}

    async def storage_rrd(request):
        return cluster.rrd(
            request.query.get("timeframe", "hour"),
            "storage",
            f"{request.match_info['node']}/{request.match_info['storage']}",
        )

    async def node_rrd(request):
        return cluster.rrd(
            request.query.get("timeframe", "hour"), "node", request.match_info["node"], request.match_info["node"]
        )

    async def storage_content(request):
        if request.match_info["storage"] != "pbs":
            return []
        return cluster.backups

    async def task_status(request):
        upid = request.match_info["upid"]
        t = next(t for t in cluster.tasks if t["upid"] == upid)
        return {
            "upid": upid,
            "node": t["node"],
            "type": t["type"],
            "id": t["id"],
            "user": t["user"],
            "starttime": t["starttime"],
            "status": "stopped" if "endtime" in t else "running",
            "exitstatus": t.get("status"),
        }

    async def task_log(request):
        upid = request.match_info["upid"]
        t = next(t for t in cluster.tasks if t["upid"] == upid)
        lines = [
            "task started by HA resource agent"
            if t["type"].startswith(("qm", "vz")) and t["id"] in ("101", "107")
            else f"starting {t['type']} for {t['id'] or 'node'}"
        ]
        if t["type"] == "vzdump":
            lines += [
                f"INFO: Starting Backup of VM {t['id']} (qemu)",
                "INFO: Backup started at 2026-11-13 02:00:04",
                "INFO: status = running",
                "INFO: include disk 'scsi0' 'ceph-vm:vm-102-disk-0' 200G",
                "INFO: creating Proxmox Backup Server archive 'vm/102/2026-11-13T01:00:04Z'",
                "INFO: started backup task 'b54e1f2c-...'",
                "INFO: scsi0: dirty-bitmap status: OK (12.4 GiB of 200.0 GiB dirty)",
                "INFO: using fast incremental mode (dirty-bitmap), 12.4 GiB dirty of 200.0 GiB total",
                "INFO:  52% (6.5 GiB of 12.4 GiB) in 3s, read: 2.2 GiB/s, write: 1.9 GiB/s",
                "INFO: 100% (12.4 GiB of 12.4 GiB) in 6s",
                "INFO: backup was done incrementally, reused 187.6 GiB (93%)",
                "INFO: transferred 12.4 GiB in 6 seconds (2.1 GiB/s)",
                "INFO: Finished Backup of VM 102 (00:00:08)",
            ]
        if "status" in t:
            lines.append(f"TASK {'OK' if t['status'] == 'OK' else 'ERROR: ' + t['status']}")
        return [{"n": i + 1, "t": line} for i, line in enumerate(lines)]

    # ---------------- guests
    async def guest_config(request):
        g = guest_of(request)
        return {**g["config"], "digest": g["digest"]}

    async def guest_config_put(request):
        g = guest_of(request)
        data = await form(request)
        if data.get("digest") and data["digest"] != g["digest"]:
            return web.json_response(
                {"data": None},
                status=500,
                reason="detected modified configuration - file changed by other user? Try again.",
            )
        for key, value in data.items():
            if key.startswith("net"):
                g["config"][key] = value
        g["digest"] = cluster._digest()
        return None

    async def guest_current(request):
        g = guest_of(request)
        r = cluster.guest_resource(g)
        extra = (
            {
                "qmpstatus": g["status"],
                "running-machine": "pc-q35-10.1+pve0",
                "running-qemu": "10.1.2",
                "ha": {"managed": 1 if g["ha"] else 0},
            }
            if g["type"] == "qemu"
            else {"ha": {"managed": 1 if g["ha"] else 0}}
        )
        return {**r, **extra, "cpus": g["maxcpu"], "agent": 1 if g["config"].get("agent") == "1" else 0}

    async def guest_rrd(request):
        g = guest_of(request)
        return cluster.rrd(request.query.get("timeframe", "hour"), "guest", str(g["vmid"]))

    async def guest_snapshots(request):
        return guest_of(request)["snapshots"]

    async def guest_pending(request):
        g = guest_of(request)
        out = [{"key": k, "value": v} for k, v in g["config"].items()]
        if g["vmid"] == 102:
            out = [{**o, "pending": 24576} if o["key"] == "memory" else o for o in out]
        return out

    async def agent_net(request):
        g = guest_of(request)
        if g["status"] != "running" or g["config"].get("agent") != "1":
            return web.json_response({"data": None}, status=500, reason="QEMU guest agent is not running")
        tag = next((int(p.split("=")[1]) for p in g["config"]["net0"].split(",") if p.startswith("tag=")), 1)
        return {
            "result": [
                {
                    "name": "lo",
                    "hardware-address": "00:00:00:00:00:00",
                    "ip-addresses": [{"ip-address": "127.0.0.1", "ip-address-type": "ipv4", "prefix": 8}],
                },
                {
                    "name": "ens18",
                    "hardware-address": mac(g["vmid"], 0).lower(),
                    "ip-addresses": [
                        {"ip-address": f"192.168.{tag}.{g['vmid'] % 250}", "ip-address-type": "ipv4", "prefix": 24},
                        {"ip-address": f"fe80::be24:11ff:fe00:{g['vmid']:x}", "ip-address-type": "ipv6", "prefix": 64},
                    ],
                    "statistics": {"rx-bytes": 9.1e9, "tx-bytes": 2.2e9},
                },
            ]
        }

    async def lxc_interfaces(request):
        g = guest_of(request)
        tag = next((int(p.split("=")[1]) for p in g["config"]["net0"].split(",") if p.startswith("tag=")), 1)
        return [
            {"name": "lo", "hwaddr": "00:00:00:00:00:00", "inet": "127.0.0.1/8"},
            {
                "name": "eth0",
                "hwaddr": mac(g["vmid"], 0).lower(),
                "inet": f"192.168.{tag}.{g['vmid'] % 250}/24",
                "inet6": f"fe80::be24:11ff:fe00:{g['vmid']:x}/64",
            },
        ]

    async def power(request):
        g = guest_of(request)
        action = request.match_info["action"]
        kind = ("qm" if g["type"] == "qemu" else "vz") + action
        upid = cluster._task(kind, vmid=g["vmid"], node=g["node"], user="root@pam!dashboard")
        if action in ("start", "resume", "reboot"):
            g["status"] = "running"
            g["uptime_from"] = time.time() if action != "resume" else g["uptime_from"]
        elif action in ("stop", "shutdown"):
            g["status"] = "stopped"
        elif action == "suspend":
            g["status"] = "paused"
        return upid

    routes = [
        ("GET", "/version", version),
        ("GET", "/cluster/status", status),
        ("GET", "/cluster/resources", resources),
        ("GET", "/cluster/tasks", tasks),
        ("GET", "/cluster/ha/status/current", ha_status),
        ("GET", "/cluster/ha/resources", ha_resources),
        ("GET", "/cluster/ha/groups", ha_groups),
        ("GET", "/cluster/ceph/status", ceph),
        ("GET", "/cluster/backup", backup_jobs),
        ("GET", "/cluster/backup-info/not-backed-up", not_backed_up),
        ("GET", "/cluster/replication", replication),
        ("GET", "/pools", pools),
        ("GET", "/cluster/sdn/zones", sdn_zones),
        ("GET", "/cluster/sdn/vnets", sdn_vnets),
        ("GET", "/cluster/sdn/vnets/{vnet}/subnets", sdn_subnets),
        ("GET", "/cluster/firewall/options", fw_options),
        ("GET", "/cluster/firewall/rules", rules_get),
        ("POST", "/cluster/firewall/rules", rules_post),
        ("PUT", "/cluster/firewall/rules/{pos}", rule_put),
        ("DELETE", "/cluster/firewall/rules/{pos}", rule_delete),
        ("GET", "/cluster/firewall/groups", groups),
        ("GET", "/cluster/firewall/groups/{group}", rules_get),
        ("POST", "/cluster/firewall/groups/{group}", rules_post),
        ("PUT", "/cluster/firewall/groups/{group}/{pos}", rule_put),
        ("DELETE", "/cluster/firewall/groups/{group}/{pos}", rule_delete),
        ("GET", "/cluster/firewall/ipset", ipsets),
        ("GET", "/cluster/firewall/ipset/{name}", ipset),
        ("GET", "/cluster/firewall/aliases", aliases),
        ("GET", "/cluster/firewall/macros", macros),
        ("GET", "/nodes/{node}/status", node_status),
        ("GET", "/nodes/{node}/network", node_network),
        ("GET", "/nodes/{node}/disks/list", disks),
        ("GET", "/nodes/{node}/disks/zfs", zfs),
        ("GET", "/nodes/{node}/services", services),
        ("GET", "/nodes/{node}/apt/update", updates),
        ("GET", "/nodes/{node}/subscription", subscription),
        ("GET", "/nodes/{node}/replication", node_replication),
        ("GET", "/nodes/{node}/rrddata", node_rrd),
        ("GET", "/nodes/{node}/storage/{storage}/rrddata", storage_rrd),
        ("GET", "/nodes/{node}/storage/{storage}/content", storage_content),
        ("GET", "/nodes/{node}/firewall/rules", rules_get),
        ("POST", "/nodes/{node}/firewall/rules", rules_post),
        ("PUT", "/nodes/{node}/firewall/rules/{pos}", rule_put),
        ("DELETE", "/nodes/{node}/firewall/rules/{pos}", rule_delete),
        ("GET", "/nodes/{node}/firewall/options", fw_options),
        ("GET", "/nodes/{node}/firewall/log", fw_log),
        ("GET", "/nodes/{node}/tasks/{upid}/status", task_status),
        ("GET", "/nodes/{node}/tasks/{upid}/log", task_log),
        ("GET", "/nodes/{node}/{kind}/{vmid}/config", guest_config),
        ("PUT", "/nodes/{node}/{kind}/{vmid}/config", guest_config_put),
        ("POST", "/nodes/{node}/{kind}/{vmid}/config", guest_config_put),
        ("GET", "/nodes/{node}/{kind}/{vmid}/status/current", guest_current),
        ("GET", "/nodes/{node}/{kind}/{vmid}/rrddata", guest_rrd),
        ("GET", "/nodes/{node}/{kind}/{vmid}/snapshot", guest_snapshots),
        ("GET", "/nodes/{node}/{kind}/{vmid}/pending", guest_pending),
        ("GET", "/nodes/{node}/{kind}/{vmid}/agent/network-get-interfaces", agent_net),
        ("GET", "/nodes/{node}/{kind}/{vmid}/interfaces", lxc_interfaces),
        ("POST", "/nodes/{node}/{kind}/{vmid}/status/{action}", power),
        ("GET", "/nodes/{node}/{kind}/{vmid}/firewall/rules", rules_get),
        ("POST", "/nodes/{node}/{kind}/{vmid}/firewall/rules", rules_post),
        ("PUT", "/nodes/{node}/{kind}/{vmid}/firewall/rules/{pos}", rule_put),
        ("DELETE", "/nodes/{node}/{kind}/{vmid}/firewall/rules/{pos}", rule_delete),
        ("GET", "/nodes/{node}/{kind}/{vmid}/firewall/options", fw_options),
        ("GET", "/nodes/{node}/{kind}/{vmid}/firewall/aliases", guest_aliases),
        ("GET", "/nodes/{node}/{kind}/{vmid}/firewall/ipset", guest_ipsets),
    ]
    for method, path, handler in routes:
        r.add_route(method, P + path, handler)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18006)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    web.run_app(build_app(MockCluster(args.seed)), port=args.port)


if __name__ == "__main__":
    main()
