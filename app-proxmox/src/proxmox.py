"""Proxmox VE's REST API: the client, and the cluster's state as this app keeps it.

Three speeds. The live state (nodes, guests, storage, tasks, HA, Ceph) is read every poll_seconds.
The configuration (network, firewall, SDN, disks, services, updates, backup jobs) every 5 minutes,
and right after a change from the dashboard. Details (charts, a guest's snapshots, the firewall log,
backups, a task's log) are read when a page asks for them.
"""

import asyncio
import contextlib
import logging
import re
import time
from datetime import datetime
from urllib.parse import quote

import aiohttp

from .config import Config

logger = logging.getLogger(__name__)

CONFIG_SECONDS = 300
TIMEOUT = aiohttp.ClientTimeout(total=20, connect=6)
GUEST_TYPES = ("qemu", "lxc")
TIMEFRAMES = ("hour", "day", "week", "month", "year")
POWER_ACTIONS = ("start", "shutdown", "reboot", "stop", "suspend", "resume")
QEMU_NIC_MODELS = (
    "virtio",
    "e1000",
    "e1000e",
    "rtl8139",
    "vmxnet3",
    "ne2k_pci",
    "pcnet",
    "i82551",
    "i82557b",
    "i82559er",
)
DISK_KEY = re.compile(r"^(scsi|virtio|sata|ide|efidisk|tpmstate|rootfs|mp|unused)\d*$")
RULE_FIELDS = (
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
    "enable",
)
RULE_TYPES = ("in", "out", "group", "forward")
RULE_ACTIONS = ("ACCEPT", "DROP", "REJECT")
# What an edited rule may leave out; Proxmox only clears a field that is named in "delete"
RULE_OPTIONAL = ("macro", "source", "dest", "proto", "dport", "sport", "iface", "log", "comment")
LOG_LINE = re.compile(r"^(\d+) (\d+) (\S+) (\d{1,2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2} [+-]\d{4}) (.*)$")
PACKET_FIELD = re.compile(r"\b([A-Z]+)=(\S*)")
BACKUP_DAYS = 14
BACKUP_START = re.compile(r"Starting Backup of VM (\d+) \((qemu|lxc|openvz)\)")
BACKUP_AT = re.compile(r"Backup started at (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
BACKUP_DONE = re.compile(r"Finished Backup of VM (\d+) \((\d+):(\d\d):(\d\d)\)")
BACKUP_FAILED = re.compile(r"ERROR: Backup of VM (\d+) failed - (.*)")
BACKUP_TRANSFERRED = re.compile(r"transferred ([\d.]+) ([KMGT]i?B|B) in (\d+) seconds")
BACKUP_ARCHIVE_SIZE = re.compile(r"archive file size: ([\d.]+)([KMGT]?)B")


class ProxmoxError(Exception):
    """The API answered with an error, or no node of the cluster could be reached."""

    def __init__(self, message: str, status: int = 0):
        super().__init__(message)
        self.status = status


def _quote(part) -> str:
    return quote(str(part), safe="")


# ----------------------------------------------------------------------
# Small parsers, shared with the tests
# ----------------------------------------------------------------------
def split_props(value: str) -> list[tuple[str, str]]:
    """'virtio=AA:BB,bridge=vmbr0,tag=20' -> [('virtio', 'AA:BB'), ('bridge', 'vmbr0'), ('tag', '20')].

    A part without '=' (a disk's volume) gets the key ''."""
    out = []
    for part in str(value).split(","):
        if not part:
            continue
        key, sep, val = part.partition("=")
        out.append((key, val) if sep else ("", key))
    return out


def join_props(props: list[tuple[str, str]]) -> str:
    return ",".join(val if key == "" else f"{key}={val}" for key, val in props)


def parse_nic(kind: str, key: str, value: str) -> dict:
    """One network device of a guest's configuration."""
    props = split_props(value)
    nic = {"key": key, "raw": value, "bridge": "", "tag": None, "firewall": False, "mac": "", "model": ""}
    for k, v in props:
        if kind == "qemu" and k in QEMU_NIC_MODELS:
            nic["model"], nic["mac"] = k, v
        elif k == "hwaddr":
            nic["mac"] = v
        elif k == "tag":
            nic["tag"] = int(v) if v.isdigit() else None
        elif k == "firewall":
            nic["firewall"] = v == "1"
        elif k == "link_down":
            nic["link_down"] = v == "1"
        elif k in ("bridge", "name", "ip", "ip6", "gw", "gw6", "rate", "mtu", "trunks", "type", "queues"):
            nic[k] = v
    if kind == "lxc":
        nic["model"] = nic.get("type") or "veth"
    return nic


def set_nic_tag(value: str, tag: int | None) -> str:
    """The same network device with another VLAN tag; None takes the tag off (untagged).

    An existing tag is changed where it stands; a new one goes after firewall (or bridge), as Proxmox writes it."""
    props = split_props(value)
    at = next((i for i, (k, _) in enumerate(props) if k == "tag"), None)
    if at is not None:
        props.pop(at)
    elif tag is not None:
        at = next((i + 1 for i, (k, _) in enumerate(props) if k == "firewall"), None)
        if at is None:
            at = next((i + 1 for i, (k, _) in enumerate(props) if k == "bridge"), len(props))
    if tag is not None:
        props.insert(at, ("tag", str(tag)))
    return join_props(props)


def size_bytes(text: str) -> int | None:
    """'32G' -> bytes. Proxmox sizes use K, M, G and T (powers of 1024)."""
    match = re.match(r"^(\d+(?:\.\d+)?)([KMGT]?)$", str(text or "").strip())
    if not match:
        return None
    return int(float(match.group(1)) * 1024 ** " KMGT".index(match.group(2) or " "))


def parse_disk(key: str, value: str) -> dict:
    props = split_props(value)
    volume = next((v for k, v in props if k in ("", "file", "volume")), "")
    info = {"key": key, "volume": volume, "storage": volume.split(":", 1)[0] if ":" in volume else ""}
    for k, v in props:
        if k == "size":
            info["size"] = size_bytes(v)
        elif k in ("media", "mp", "backup", "ssd", "discard", "iothread", "cache", "replicate"):
            info[k] = v
    if info.get("media") == "cdrom":
        info["cdrom"] = True
    return info


def guest_devices(kind: str, cfg: dict) -> tuple[list[dict], list[dict]]:
    """The network devices and disks of a guest's configuration, in key order."""
    nics, disks = [], []
    for key in sorted(cfg, key=lambda k: (re.sub(r"\d+$", "", k), int(re.sub(r"^\D+", "", k) or 0))):
        value = cfg[key]
        if not isinstance(value, str):
            continue
        if re.fullmatch(r"net\d+", key):
            nics.append(parse_nic(kind, key, value))
        elif DISK_KEY.match(key):
            disks.append(parse_disk(key, value))
    return nics, disks


def parse_firewall_log(lines: list, node: str) -> list[dict]:
    """pve-firewall's log lines: '100 6 tap100i0-IN 13/Nov/2026:10:00:00 +0100 policy DROP: IN=... SRC=...'.

    VMID 0 is the host itself. Lines that aren't about a packet (the logger starting) are left out."""
    entries = []
    for item in lines:
        text = item.get("t", "") if isinstance(item, dict) else str(item)
        match = LOG_LINE.match(text)
        if not match:
            continue
        vmid, _level, chain, stamp, rest = match.groups()
        head, sep, packet = rest.partition(": ")
        if not sep or "SRC=" not in packet:
            continue
        policy = head.startswith("policy ")
        action = head.split()[-1] if head else ""
        try:
            ts = datetime.strptime(stamp, "%d/%b/%Y:%H:%M:%S %z").timestamp()
        except ValueError:
            continue
        fields = dict(PACKET_FIELD.findall(packet))
        entries.append(
            {
                "ts": ts,
                "node": node,
                "vmid": int(vmid),
                "chain": chain,
                "dir": "out" if chain.endswith("-OUT") else "in",
                "action": action,
                "policy": policy,
                "src": fields.get("SRC", ""),
                "dst": fields.get("DST", ""),
                "proto": fields.get("PROTO", ""),
                "spt": fields.get("SPT", ""),
                "dpt": fields.get("DPT", ""),
                "iface": fields.get("PHYSIN") or fields.get("IN", ""),
            }
        )
    return entries


def _amount(value: str, unit: str) -> int:
    """'12.4', 'GiB' -> bytes."""
    return int(float(value) * 1024 ** " KMGT".index(unit[:1] if unit[:1] in "KMGT" else " "))


def parse_backup_log(lines: list, task: dict) -> list[dict]:
    """What a vzdump task did per guest: one run each, ok or failed, with how long it took and what it moved.

    A job task covers many guests; a manual backup one. A guest that started but neither finished nor
    failed (the task was stopped) counts as failed."""
    runs: list[dict] = []
    current: dict | None = None
    for item in lines:
        text = item.get("t", "") if isinstance(item, dict) else str(item)
        if m := BACKUP_START.search(text):
            if current:
                current.update(ok=False, error=current.get("error") or "did not finish")
                runs.append(current)
            current = {
                "vmid": int(m.group(1)),
                "ts": task.get("starttime", 0),
                "ok": None,
                "duration": None,
                "error": "",
                "transferred": None,
                "node": task.get("node", ""),
                "upid": task.get("upid", ""),
            }
            continue
        if current is None:
            continue
        if m := BACKUP_AT.search(text):
            with contextlib.suppress(ValueError):
                current["ts"] = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
        elif (m := BACKUP_TRANSFERRED.search(text)) or (m := BACKUP_ARCHIVE_SIZE.search(text)):
            current["transferred"] = _amount(m.group(1), m.group(2))
        elif (m := BACKUP_DONE.search(text)) and int(m.group(1)) == current["vmid"]:
            current.update(ok=True, duration=int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4)))
            runs.append(current)
            current = None
        elif (m := BACKUP_FAILED.search(text)) and int(m.group(1)) == current["vmid"]:
            current.update(ok=False, error=m.group(2).strip())
            runs.append(current)
            current = None
    if current:
        running = not task.get("endtime") and not task.get("status")
        current.update(ok=None if running else False, error="" if running else "did not finish")
        runs.append(current)
    return runs


def _port_in(spec: str, port: int) -> bool:
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        low, sep, high = part.partition(":")
        if not low.isdigit():
            continue
        if (int(low) <= port <= int(high)) if sep and high.isdigit() else int(low) == port:
            return True
    return False


def lockout_risk(scope: dict, rule: dict, options: dict | None = None) -> str:
    """Why a rule on the host firewall may cut off the web interface (port 8006) or SSH, or ''.

    Only for the datacenter and the nodes: a guest's rules never filter the host."""
    if scope.get("kind") not in ("cluster", "node"):
        return ""
    if str(rule.get("enable", "1")) in ("0", "") or rule.get("type") != "in":
        return ""
    if rule.get("action") not in ("DROP", "REJECT"):
        return ""
    macro = (rule.get("macro") or "").upper()
    proto = (rule.get("proto") or "").lower()
    dport = rule.get("dport") or ""
    if macro:
        # Of Proxmox's macros only SSH covers a management port; none covers 8006
        if macro != "SSH":
            return ""
        what = "SSH"
    elif proto and proto not in ("tcp", "6"):
        return ""
    elif not dport:
        what = "every port, including the web interface (8006) and SSH (22)"
    elif _port_in(dport, 8006):
        what = "the web interface and this app (port 8006)"
    elif _port_in(dport, 22):
        what = "SSH (port 22)"
    else:
        return ""
    source = rule.get("source") or "any address"
    return (
        f"This {rule['action']} rule matches {what} from {source}. If the firewall is on, it can lock you "
        "and this app out of the node. Proxmox still lets the management network in unless a rule above "
        "this one says otherwise."
    )


# ----------------------------------------------------------------------
# The API client
# ----------------------------------------------------------------------
class ProxmoxAPI:
    """Talks to one cluster over any of its nodes, with an API token."""

    def __init__(self, config: Config):
        self.config = config
        self.hosts = list(config.hosts)
        self.current = 0
        self._session: aiohttp.ClientSession | None = None

    @property
    def host(self) -> str:
        return self.hosts[self.current] if self.hosts else ""

    async def session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=TIMEOUT,
                headers={"Authorization": f"PVEAPIToken={self.config.token_id}={self.config.token_secret}"},
                connector=aiohttp.TCPConnector(ssl=None if self.config.verify_ssl else False, limit=16),
            )
        return self._session

    async def close(self) -> None:
        if self._session:
            await self._session.close()

    async def request(self, method: str, path: str, params: dict | None = None, data: dict | None = None, full=False):
        """The answer's data (or the whole answer with full). Tries the next node when one can't be reached."""
        if not self.hosts:
            raise ProxmoxError("No Proxmox node configured")
        session = await self.session()
        last = ""
        for attempt in range(len(self.hosts)):
            index = (self.current + attempt) % len(self.hosts)
            url = f"{self.hosts[index]}/api2/json{path}"
            try:
                async with session.request(method, url, params=params, data=data) as response:
                    body = None
                    with contextlib.suppress(aiohttp.ContentTypeError, ValueError):
                        body = await response.json()
                    if response.status != 200:
                        raise ProxmoxError(self._error_text(response, body), response.status)
                    if index != self.current:
                        logger.info(f"Now reading the cluster through {self.hosts[index]}")
                        self.current = index
                    body = body or {}
                    return body if full else body.get("data")
            except (aiohttp.ClientConnectionError, TimeoutError) as e:
                last = f"{self.hosts[index]}: {e or type(e).__name__}"
                continue
        raise ProxmoxError(f"No node can be reached ({last})", 0)

    @staticmethod
    def _error_text(response: aiohttp.ClientResponse, body) -> str:
        reason = (response.reason or "").strip()
        if response.status == 401:
            return "The API token was refused: check token_id and token_secret"
        if isinstance(body, dict):
            errors = body.get("errors")
            if isinstance(errors, dict) and errors:
                detail = "; ".join(f"{k}: {str(v).strip()}" for k, v in errors.items())
                return f"{reason} ({detail})" if reason else detail
            if body.get("message"):
                return str(body["message"]).strip()
        return reason or f"HTTP {response.status}"

    async def get(self, path: str, **params):
        return await self.request("GET", path, params=params or None)

    async def post(self, path: str, data: dict | None = None):
        return await self.request("POST", path, data=data or {})

    async def put(self, path: str, data: dict | None = None):
        return await self.request("PUT", path, data=data or {})

    async def delete(self, path: str, **params):
        return await self.request("DELETE", path, params=params or None)


# ----------------------------------------------------------------------
# The cluster as the app keeps it
# ----------------------------------------------------------------------
class Cluster:
    """Reads the cluster on a schedule and keeps the latest answer of every part."""

    def __init__(self, config: Config, api: ProxmoxAPI | None = None):
        self.config = config
        self.api = api or ProxmoxAPI(config)
        self.live: dict = {}
        self.conf: dict = {}
        self.live_at = 0.0
        self.conf_at = 0.0
        self.connected = False
        self.error = ""
        # What the token may not read, or what this cluster doesn't have: {part: message}
        self.notes: dict[str, str] = {}
        self._conf_due = asyncio.Event()
        self._cache: dict[str, tuple[float, object]] = {}
        self._sem = asyncio.Semaphore(8)

    # -------------------------------------------------- scheduling
    async def run(self) -> None:
        if not self.config.configured:
            self.error = "Not configured: enter hosts, token_id and token_secret in the app's configuration"
            logger.warning(self.error)
            await asyncio.Event().wait()
        try:
            while True:
                started = time.monotonic()
                try:
                    await self.refresh_live()
                    if not self.connected:
                        logger.info(f"Connected to the cluster through {self.api.host}")
                    self.connected, self.error = True, ""
                except ProxmoxError as e:
                    if self.connected or self.error != str(e):
                        logger.warning(f"Cannot read the cluster: {e}")
                    self.connected, self.error = False, str(e)
                if self.connected and (self._conf_due.is_set() or time.time() - self.conf_at >= CONFIG_SECONDS):
                    self._conf_due.clear()
                    try:
                        await self.refresh_conf()
                    except ProxmoxError as e:
                        logger.warning(f"Cannot read the cluster's configuration: {e}")
                wait = max(1.0, self.config.poll_seconds - (time.monotonic() - started))
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self._conf_due.wait(), wait)
        finally:
            await self.api.close()

    def reread_config(self) -> None:
        """Read the configuration again at once, after a change."""
        self._conf_due.set()

    async def _try(self, part: str, coro, default=None):
        """One part of the state: a refusal or a missing feature becomes a note, not a failure."""
        async with self._sem:
            try:
                result = await coro
                self.notes.pop(part, None)
                return result
            except ProxmoxError as e:
                if e.status == 0:
                    raise
                self.notes[part] = str(e)
                return default

    # -------------------------------------------------- live state
    async def refresh_live(self) -> None:
        api = self.api
        status, resources = await asyncio.gather(api.get("/cluster/status"), api.get("/cluster/resources"))
        if self.live.get("version") is None or time.time() - self.conf_at >= CONFIG_SECONDS:
            version = await self._try("version", api.get("/version"), {})
        else:
            version = self.live["version"]
        online = [n["name"] for n in status or [] if n.get("type") == "node" and n.get("online")]
        node_status, tasks, ha, ceph = await asyncio.gather(
            asyncio.gather(*(self._try(f"node:{n}", api.get(f"/nodes/{_quote(n)}/status"), {}) for n in online)),
            self._try("tasks", api.get("/cluster/tasks"), []),
            self._try("ha", api.get("/cluster/ha/status/current"), []),
            self._ceph(),
        )
        self.live = {
            "version": version or {},
            "status": status or [],
            "resources": resources or [],
            "node_status": dict(zip(online, node_status, strict=True)),
            "tasks": sorted(tasks or [], key=lambda t: t.get("starttime", 0), reverse=True)[:200],
            "ha": ha or [],
            "ceph": ceph,
        }
        self.live_at = time.time()

    async def _ceph(self):
        """Ceph's status when the cluster runs Ceph; asked again with the configuration when it doesn't."""
        if self.live.get("ceph") is None and "ceph" in self.notes and time.time() - self.conf_at < CONFIG_SECONDS:
            return None
        return await self._try("ceph", self.api.get("/cluster/ceph/status"), None)

    # -------------------------------------------------- configuration
    async def refresh_conf(self) -> None:
        api = self.api
        nodes = sorted(self.live.get("node_status", {}))
        guests = [r for r in self.live.get("resources", []) if r.get("type") in GUEST_TYPES and r.get("node") in nodes]

        def per_node(part: str, path: str, default=None, **params):
            return asyncio.gather(
                *(self._try(f"{part}:{n}", api.get(f"/nodes/{_quote(n)}{path}", **params), default) for n in nodes)
            )

        async def network(node: str):
            answer = await self._try(
                f"network:{node}", api.request("GET", f"/nodes/{_quote(node)}/network", full=True), {}
            )
            return {"ifaces": (answer or {}).get("data") or [], "changes": (answer or {}).get("changes") or ""}

        async def group_rules(groups):
            return {
                g["group"]: await self._try(
                    f"fwgroup:{g['group']}", api.get(f"/cluster/firewall/groups/{_quote(g['group'])}"), []
                )
                for g in groups or []
            }

        async def ipset_entries(ipsets):
            return {
                s["name"]: await self._try(
                    f"ipset:{s['name']}", api.get(f"/cluster/firewall/ipset/{_quote(s['name'])}"), []
                )
                for s in ipsets or []
            }

        async def guest_conf(g):
            base = f"/nodes/{_quote(g['node'])}/{g['type']}/{g['vmid']}"
            cfg, rules, options, aliases, ipsets = await asyncio.gather(
                self._try(f"config:{g['vmid']}", api.get(f"{base}/config"), {}),
                self._try(f"fw:{g['vmid']}", api.get(f"{base}/firewall/rules"), []),
                self._try(f"fwopt:{g['vmid']}", api.get(f"{base}/firewall/options"), {}),
                self._try(f"fwalias:{g['vmid']}", api.get(f"{base}/firewall/aliases"), []),
                self._try(f"fwipset:{g['vmid']}", api.get(f"{base}/firewall/ipset"), []),
            )
            nics, disks = guest_devices(g["type"], cfg or {})
            return {
                "vmid": g["vmid"],
                "type": g["type"],
                "node": g["node"],
                "nics": nics,
                "disks": disks,
                "onboot": str((cfg or {}).get("onboot", "0")) == "1",
                "agent": str((cfg or {}).get("agent", "0")).split(",")[0] in ("1", "enabled=1"),
                "ostype": (cfg or {}).get("ostype", ""),
                "cores": (cfg or {}).get("cores"),
                "sockets": (cfg or {}).get("sockets"),
                "digest": (cfg or {}).get("digest", ""),
                "firewall": {
                    "rules": rules or [],
                    "options": options or {},
                    "aliases": aliases or [],
                    "ipsets": ipsets or [],
                },
            }

        (
            net,
            disks,
            zfs,
            services,
            updates,
            subscription,
            node_fw_rules,
            node_fw_options,
            replication_status,
        ) = await asyncio.gather(
            asyncio.gather(*(network(n) for n in nodes)),
            per_node("disks", "/disks/list", []),
            per_node("zfs", "/disks/zfs", []),
            per_node("services", "/services", []),
            per_node("updates", "/apt/update", None),
            per_node("subscription", "/subscription", {}),
            per_node("fwnode", "/firewall/rules", []),
            per_node("fwnodeopt", "/firewall/options", {}),
            per_node("replication", "/replication", []),
        )
        fw_options, fw_rules, groups, ipsets, aliases = await asyncio.gather(
            self._try("fwopt", api.get("/cluster/firewall/options"), {}),
            self._try("fwrules", api.get("/cluster/firewall/rules"), []),
            self._try("fwgroups", api.get("/cluster/firewall/groups"), []),
            self._try("fwipsets", api.get("/cluster/firewall/ipset"), []),
            self._try("fwaliases", api.get("/cluster/firewall/aliases"), []),
        )
        group_map, ipset_map, guest_list = await asyncio.gather(
            group_rules(groups), ipset_entries(ipsets), asyncio.gather(*(guest_conf(g) for g in guests))
        )
        macros = self.conf.get("firewall", {}).get("macros") or await self._try(
            "fwmacros", api.get("/cluster/firewall/macros"), []
        )
        zones, vnets, backup_jobs, not_backed_up, replication, ha_resources, ha_groups, pools = await asyncio.gather(
            self._try("sdn", api.get("/cluster/sdn/zones"), []),
            self._try("sdn", api.get("/cluster/sdn/vnets"), []),
            self._try("backup", api.get("/cluster/backup"), []),
            self._try("backupinfo", api.get("/cluster/backup-info/not-backed-up"), []),
            self._try("replication", api.get("/cluster/replication"), []),
            self._try("haresources", api.get("/cluster/ha/resources"), []),
            self._try("hagroups", api.get("/cluster/ha/groups"), []),
            self._try("pools", api.get("/pools"), []),
        )
        subnets = {}
        for vnet in vnets or []:
            subnets[vnet["vnet"]] = await self._try(
                f"sdn:{vnet['vnet']}", api.get(f"/cluster/sdn/vnets/{_quote(vnet['vnet'])}/subnets"), []
            )
        self.conf = {
            "nodes": {
                n: {
                    "network": net[i],
                    "disks": disks[i] or [],
                    "zfs": zfs[i] or [],
                    "services": services[i] or [],
                    "updates": updates[i],
                    "subscription": subscription[i] or {},
                    "replication": replication_status[i] or [],
                }
                for i, n in enumerate(nodes)
            },
            "firewall": {
                "options": fw_options or {},
                "rules": fw_rules or [],
                "groups": [{**g, "rules": group_map.get(g["group"], [])} for g in groups or []],
                "ipsets": [{**s, "entries": ipset_map.get(s["name"], [])} for s in ipsets or []],
                "aliases": aliases or [],
                "macros": macros or [],
                "nodes": {
                    n: {"rules": node_fw_rules[i] or [], "options": node_fw_options[i] or {}}
                    for i, n in enumerate(nodes)
                },
            },
            "guests": {str(g["vmid"]): g for g in guest_list},
            "sdn": {"zones": zones or [], "vnets": vnets or [], "subnets": subnets},
            "backup_jobs": backup_jobs or [],
            "not_backed_up": not_backed_up or [],
            "replication": replication or [],
            "ha_resources": ha_resources or [],
            "ha_groups": ha_groups or [],
            "pools": pools or [],
        }
        self.conf_at = time.time()

    # -------------------------------------------------- what the pages get
    def summary(self) -> dict:
        # The cluster's own version goes out as "pve": "version" is the app's
        live = {k: v for k, v in self.live.items() if k != "version"}
        return {
            "pve": self.live.get("version") or {},
            "connected": self.connected,
            "error": self.error,
            "host": self.api.host,
            "live_at": self.live_at,
            "conf_at": self.conf_at,
            "notes": self.notes,
            **live,
        }

    def guest(self, vmid) -> dict | None:
        return next(
            (
                r
                for r in self.live.get("resources", [])
                if r.get("type") in GUEST_TYPES and str(r.get("vmid")) == str(vmid)
            ),
            None,
        )

    async def cached(self, key: str, seconds: float, make):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < seconds:
            return hit[1]
        value = await make()
        self._cache[key] = (time.time(), value)
        return value

    def online_nodes(self) -> list[str]:
        return sorted(self.live.get("node_status", {}))

    # -------------------------------------------------- details, when asked
    async def rrd(self, node: str, timeframe: str, guest: dict | None = None, storage: str = "") -> list:
        if timeframe not in TIMEFRAMES:
            raise ValueError("timeframe must be hour, day, week, month or year")
        path = f"/nodes/{_quote(node)}"
        if guest:
            path += f"/{guest['type']}/{guest['vmid']}"
        elif storage:
            path += f"/storage/{_quote(storage)}"
        return await self.cached(
            f"rrd:{path}:{timeframe}",
            20 if timeframe == "hour" else 120,
            lambda: self.api.get(f"{path}/rrddata", timeframe=timeframe, cf="AVERAGE"),
        )

    async def cluster_rrd(self, timeframe: str) -> dict:
        """Every online node's history added up: CPU as a share of all cores, memory and network summed."""
        nodes = self.online_nodes()
        series = await asyncio.gather(*(self.rrd(n, timeframe) for n in nodes), return_exceptions=True)
        by_time: dict[int, dict] = {}
        for rows in series:
            if isinstance(rows, BaseException):
                continue
            for row in rows or []:
                t = int(row.get("time", 0))
                if row.get("cpu") is None:
                    continue
                slot = by_time.setdefault(
                    t,
                    {
                        "cpu": 0.0,
                        "maxcpu": 0,
                        "memused": 0,
                        "memtotal": 0,
                        "netin": 0,
                        "netout": 0,
                        "iowait": 0.0,
                        "n": 0,
                    },
                )
                cores = row.get("maxcpu") or 1
                slot["cpu"] += (row.get("cpu") or 0) * cores
                slot["maxcpu"] += cores
                slot["memused"] += row.get("memused") or 0
                slot["memtotal"] += row.get("memtotal") or 0
                slot["netin"] += row.get("netin") or 0
                slot["netout"] += row.get("netout") or 0
                slot["iowait"] += row.get("iowait") or 0
                slot["n"] += 1
        times = sorted(by_time)
        rows = [by_time[t] for t in times]
        return {
            "time": times,
            "cpu": [r["cpu"] / r["maxcpu"] if r["maxcpu"] else None for r in rows],
            "memused": [r["memused"] for r in rows],
            "memtotal": [r["memtotal"] for r in rows],
            "netin": [r["netin"] for r in rows],
            "netout": [r["netout"] for r in rows],
            "iowait": [r["iowait"] / r["n"] if r["n"] else None for r in rows],
            "nodes": len(nodes),
        }

    async def guest_details(self, vmid) -> dict:
        g = self.guest(vmid)
        if g is None:
            raise KeyError(vmid)
        api = self.api
        base = f"/nodes/{_quote(g['node'])}/{g['type']}/{g['vmid']}"
        running = g.get("status") == "running"
        cfg, current, snapshots, pending = await asyncio.gather(
            self._try(f"config:{vmid}", api.get(f"{base}/config"), {}),
            self._try(f"current:{vmid}", api.get(f"{base}/status/current"), {}),
            self._try(f"snapshots:{vmid}", api.get(f"{base}/snapshot"), []),
            self._try(f"pending:{vmid}", api.get(f"{base}/pending"), []),
        )
        addresses, address_note = [], ""
        if running and g["type"] == "qemu" and str((cfg or {}).get("agent", "0")).split(",")[0] in ("1", "enabled=1"):
            try:
                answer = await api.get(f"{base}/agent/network-get-interfaces")
                addresses = (answer or {}).get("result") or []
            except ProxmoxError as e:
                address_note = f"Guest agent: {e}"
        elif running and g["type"] == "lxc":
            try:
                addresses = await api.get(f"{base}/interfaces") or []
            except ProxmoxError as e:
                address_note = f"Container interfaces: {e}"
        elif g["type"] == "qemu":
            address_note = "Turn on the QEMU guest agent to see the addresses inside the VM"
        nics, disks = guest_devices(g["type"], cfg or {})
        changes = [p for p in pending or [] if "pending" in p or p.get("delete")]
        return {
            "guest": g,
            "config": cfg or {},
            "current": current or {},
            "nics": nics,
            "disks": disks,
            "snapshots": [s for s in snapshots or [] if s.get("name") != "current"],
            "pending": changes,
            "addresses": addresses,
            "address_note": address_note,
        }

    async def firewall_log(self, limit: int = 1000) -> dict:
        async def read(node):
            lines = await self._try(
                f"fwlog:{node}", self.api.get(f"/nodes/{_quote(node)}/firewall/log", limit=limit), []
            )
            return parse_firewall_log(lines or [], node)

        async def make():
            parts = await asyncio.gather(*(read(n) for n in self.online_nodes()))
            entries = sorted((e for part in parts for e in part), key=lambda e: e["ts"], reverse=True)
            notes = {k: v for k, v in self.notes.items() if k.startswith("fwlog:")}
            return {"entries": entries, "notes": notes, "read_at": time.time()}

        return await self.cached("fwlog", 20, make)

    async def backups(self) -> dict:
        """Every backup on every storage that holds backups. A shared storage is read once."""

        async def make():
            seen, jobs = set(), []
            for r in self.live.get("resources", []):
                if r.get("type") != "storage" or r.get("status") != "available":
                    continue
                if "backup" not in str(r.get("content", "")).split(","):
                    continue
                key = r["storage"] if r.get("shared") else f"{r['node']}/{r['storage']}"
                if key in seen:
                    continue
                seen.add(key)
                jobs.append((r["node"], r["storage"]))

            async def read(node, storage):
                items = await self._try(
                    f"backups:{storage}",
                    self.api.get(f"/nodes/{_quote(node)}/storage/{_quote(storage)}/content", content="backup"),
                    [],
                )
                return [{**i, "storage": storage, "node": node} for i in items or []]

            parts = await asyncio.gather(*(read(n, s) for n, s in jobs))
            return {"backups": [b for part in parts for b in part], "read_at": time.time()}

        return await self.cached("backups", 60, make)

    async def backup_history(self, days: int = BACKUP_DAYS) -> dict:
        """The backup tasks of the last days on every node, and per guest what each run did, from their logs."""

        async def make():
            since = int(time.time() - days * 86400)
            nodes = self.online_nodes()
            lists = await asyncio.gather(
                *(
                    self._try(
                        f"backuptasks:{n}",
                        self.api.get(
                            f"/nodes/{_quote(n)}/tasks", typefilter="vzdump", since=since, limit=500, source="all"
                        ),
                        [],
                    )
                    for n in nodes
                )
            )
            tasks = sorted(
                ({**t, "node": t.get("node") or n} for n, part in zip(nodes, lists, strict=True) for t in part or []),
                key=lambda t: t.get("starttime", 0),
                reverse=True,
            )

            async def runs_of(task):
                lines = await self._try(
                    f"backuplog:{task['node']}",
                    self.api.get(f"/nodes/{_quote(task['node'])}/tasks/{_quote(task['upid'])}/log", limit=50000),
                    [],
                )
                return parse_backup_log(lines or [], task)

            parts = await asyncio.gather(*(runs_of(t) for t in tasks[:200]))
            runs = sorted((r for part in parts for r in part), key=lambda r: r["ts"], reverse=True)
            keep = ("upid", "node", "id", "user", "starttime", "endtime", "status")
            return {
                "days": days,
                "tasks": [{k: t.get(k) for k in keep} for t in tasks],
                "runs": runs,
                "read_at": time.time(),
            }

        return await self.cached("backuphistory", 300, make)

    async def task(self, node: str, upid: str) -> dict:
        base = f"/nodes/{_quote(node)}/tasks/{_quote(upid)}"
        status, log = await asyncio.gather(self.api.get(f"{base}/status"), self.api.get(f"{base}/log", limit=1000))
        return {"status": status or {}, "log": [line.get("t", "") for line in log or []]}

    # -------------------------------------------------- changes
    async def power(self, vmid, action: str) -> str:
        if action not in POWER_ACTIONS:
            raise ValueError("unknown action")
        g = self.guest(vmid)
        if g is None:
            raise KeyError(vmid)
        upid = await self.api.post(f"/nodes/{_quote(g['node'])}/{g['type']}/{g['vmid']}/status/{action}")
        self._cache.clear()
        return upid

    def rules_path(self, scope: dict) -> str:
        kind = scope.get("kind")
        if kind == "cluster":
            return "/cluster/firewall/rules"
        if kind == "group":
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]+", str(scope.get("group", ""))):
                raise ValueError("unknown security group")
            return f"/cluster/firewall/groups/{_quote(scope['group'])}"
        if kind == "node":
            if scope.get("node") not in self.live.get("node_status", {}):
                raise ValueError("unknown or offline node")
            return f"/nodes/{_quote(scope['node'])}/firewall/rules"
        if kind == "guest":
            g = self.guest(scope.get("vmid"))
            if g is None:
                raise ValueError("unknown guest")
            return f"/nodes/{_quote(g['node'])}/{g['type']}/{g['vmid']}/firewall/rules"
        raise ValueError("scope must be cluster, group, node or guest")

    @staticmethod
    def clean_rule(rule: dict) -> dict:
        out = {}
        for key in RULE_FIELDS:
            value = rule.get(key)
            if value is None or value == "":
                continue
            value = str(value).strip()
            if "\n" in value or len(value) > 512:
                raise ValueError(f"{key} is not valid")
            out[key] = value
        if out.get("type") not in RULE_TYPES:
            raise ValueError("type must be in, out, group or forward")
        if out["type"] == "group":
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]+", out.get("action", "")):
                raise ValueError("a group rule needs the security group as its action")
        elif out.get("action") not in RULE_ACTIONS:
            raise ValueError("action must be ACCEPT, DROP or REJECT")
        out["enable"] = "1" if out.get("enable", "1") in ("1", "true", "True") else "0"
        return out

    async def change_rule(self, scope: dict, op: str, rule: dict | None = None, pos=None, to=None, digest=""):
        path = self.rules_path(scope)
        extra = {"digest": digest} if digest else {}
        if op == "create":
            data = self.clean_rule(rule or {})
            if pos is not None:
                data["pos"] = str(int(pos))
            result = await self.api.post(path, {**data, **extra})
        elif op == "update":
            data = self.clean_rule(rule or {})
            cleared = [key for key in RULE_OPTIONAL if key not in data]
            if cleared:
                data["delete"] = ",".join(cleared)
            result = await self.api.put(f"{path}/{int(pos)}", {**data, **extra})
        elif op == "toggle":
            result = await self.api.put(
                f"{path}/{int(pos)}", {"enable": "1" if rule and rule.get("enable") else "0", **extra}
            )
        elif op == "move":
            result = await self.api.put(f"{path}/{int(pos)}", {"moveto": str(int(to)), **extra})
        elif op == "delete":
            result = await self.api.delete(f"{path}/{int(pos)}", **extra)
        else:
            raise ValueError("unknown change")
        self.reread_config()
        return result

    async def set_vlan(self, vmid, nic_key: str, tag: int | None, digest: str = "") -> dict:
        """Put one network device of a guest on another VLAN (or untagged), keeping the rest of it."""
        g = self.guest(vmid)
        if g is None:
            raise KeyError(vmid)
        if not re.fullmatch(r"net\d+", nic_key):
            raise ValueError("unknown network device")
        if tag is not None and not 1 <= int(tag) <= 4094:
            raise ValueError("a VLAN tag is 1 to 4094")
        base = f"/nodes/{_quote(g['node'])}/{g['type']}/{g['vmid']}/config"
        cfg = await self.api.get(base)
        if digest and cfg.get("digest") and digest != cfg["digest"]:
            raise ProxmoxError("The guest's configuration changed in the meantime; look again and retry", 409)
        if nic_key not in cfg:
            raise ValueError(f"the guest has no {nic_key}")
        before = cfg[nic_key]
        after = set_nic_tag(before, None if tag is None else int(tag))
        data = {nic_key: after, "digest": cfg.get("digest", "")}
        # PUT changes the configuration at once (QEMU's POST would do it as a task)
        await self.api.put(base, data)
        self.reread_config()
        return {"before": before, "after": after}
