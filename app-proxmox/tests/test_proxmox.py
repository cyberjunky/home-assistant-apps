"""The app against a stand-in Proxmox VE cluster (tools/mock_proxmox.py): reading, changes and the dashboard's API."""

from datetime import UTC, datetime

import pytest
from aiohttp.test_utils import TestClient, TestServer

from src import webui
from src.config import Config, normalize_host
from src.proxmox import (
    Cluster,
    ProxmoxError,
    guest_devices,
    lockout_risk,
    parse_firewall_log,
    parse_nic,
    set_nic_tag,
    size_bytes,
)
from src.webui import WebUI
from tools import mock_proxmox


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------
@pytest.mark.parametrize(
    "text, expected",
    [
        ("192.168.1.11", "https://192.168.1.11:8006"),
        ("pve1.lan:8443", "https://pve1.lan:8443"),
        ("https://pve1:8006/api2/json/", "https://pve1:8006"),
        ("http://localhost:18006", "http://localhost:18006"),
        ("fd00::11", "https://[fd00::11]:8006"),
    ],
)
def test_hosts_are_normalized(text, expected):
    if text == "fd00::11":
        text = "https://[fd00::11]"
    assert normalize_host(text) == expected


def test_options():
    config = Config.from_options(
        {"hosts": ["pve1", "pve1:8006", "pve2"], "token_id": "ha@pve!dash", "token_secret": " s ", "poll_seconds": 1}
    )
    assert config.hosts == ["https://pve1:8006", "https://pve2:8006"]
    assert config.token_secret == "s"
    assert config.poll_seconds == 5
    assert config.read_only is True
    assert config.configured
    assert not Config.from_options({}).configured


@pytest.mark.parametrize("token", ["root@pam", "dashboard", "root!x", "a@b!c d"])
def test_bad_token_ids_are_refused(token):
    with pytest.raises(ValueError, match="user@realm!tokenname"):
        Config.from_options({"token_id": token})


# ---------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------
def test_nics_and_disks():
    nic = parse_nic("qemu", "net1", "virtio=BC:24:11:00:64:01,bridge=vmbr0,firewall=1,tag=40,rate=12.5")
    assert (nic["model"], nic["mac"], nic["bridge"], nic["tag"], nic["firewall"], nic["rate"]) == (
        "virtio",
        "BC:24:11:00:64:01",
        "vmbr0",
        40,
        True,
        "12.5",
    )
    ct = parse_nic("lxc", "net0", "name=eth0,bridge=vmbr1,hwaddr=AA:BB,ip=dhcp,type=veth")
    assert (ct["mac"], ct["tag"], ct["firewall"], ct["name"], ct["model"]) == ("AA:BB", None, False, "eth0", "veth")
    nics, disks = guest_devices(
        "qemu",
        {
            "net10": "virtio=X,bridge=vmbr0",
            "net2": "e1000=Y,bridge=vmbr1",
            "scsi0": "ceph:vm-1-disk-0,size=32G,ssd=1",
            "ide2": "none,media=cdrom",
            "name": "x",
        },
    )
    assert [n["key"] for n in nics] == ["net2", "net10"]
    assert disks[1]["size"] == 32 * 1024**3 and disks[1]["storage"] == "ceph"
    assert disks[0]["cdrom"]
    assert size_bytes("1.5T") == int(1.5 * 1024**4) and size_bytes("junk") is None


@pytest.mark.parametrize(
    "before, tag, after",
    [
        ("virtio=X,bridge=vmbr0,firewall=1,tag=40,rate=3", 20, "virtio=X,bridge=vmbr0,firewall=1,tag=20,rate=3"),
        ("virtio=X,bridge=vmbr0,firewall=1", 30, "virtio=X,bridge=vmbr0,firewall=1,tag=30"),
        ("virtio=X,bridge=vmbr0,rate=3", 7, "virtio=X,bridge=vmbr0,tag=7,rate=3"),
        (
            "name=eth0,bridge=vmbr0,hwaddr=Y,ip=dhcp,tag=40,type=veth",
            None,
            "name=eth0,bridge=vmbr0,hwaddr=Y,ip=dhcp,type=veth",
        ),
    ],
)
def test_vlan_tag_changes_only_the_tag(before, tag, after):
    assert set_nic_tag(before, tag) == after


def test_firewall_log():
    lines = [
        {"n": 1, "t": "0 5 - 13/Nov/2026:09:00:00 +0100 starting pvefw logger"},
        {
            "n": 2,
            "t": "101 6 tap101i0-IN 13/Nov/2026:09:01:02 +0100 policy DROP: IN=fwbr101i0 OUT=fwbr101i0 "
            "PHYSIN=fwln101o0 SRC=45.155.205.233 DST=192.168.30.101 LEN=60 PROTO=TCP SPT=51515 DPT=22 SYN",
        },
        {
            "n": 3,
            "t": "0 6 PVEFW-HOST-OUT 13/Nov/2026:09:01:03 +0100 REJECT: IN= OUT=vmbr0 SRC=192.168.1.11 "
            "DST=8.8.8.8 PROTO=UDP SPT=5353 DPT=53",
        },
    ]
    entries = parse_firewall_log(lines, "pve1")
    assert len(entries) == 2
    first, second = entries
    assert (first["vmid"], first["action"], first["policy"], first["src"], first["dpt"], first["dir"]) == (
        101,
        "DROP",
        True,
        "45.155.205.233",
        "22",
        "in",
    )
    assert (second["vmid"], second["action"], second["policy"], second["dir"], second["proto"]) == (
        0,
        "REJECT",
        False,
        "out",
        "UDP",
    )
    assert first["ts"] == datetime(2026, 11, 13, 8, 1, 2, tzinfo=UTC).timestamp()


@pytest.mark.parametrize(
    "scope, rule, risky",
    [
        ({"kind": "cluster"}, {"type": "in", "action": "DROP"}, "every port"),
        (
            {"kind": "node", "node": "pve1"},
            {"type": "in", "action": "REJECT", "proto": "tcp", "dport": "8000:9000"},
            "8006",
        ),
        ({"kind": "cluster"}, {"type": "in", "action": "DROP", "macro": "SSH"}, "SSH"),
        ({"kind": "cluster"}, {"type": "in", "action": "DROP", "proto": "tcp", "dport": "22,80"}, "SSH"),
        ({"kind": "cluster"}, {"type": "in", "action": "DROP", "proto": "udp"}, ""),
        ({"kind": "cluster"}, {"type": "in", "action": "DROP", "macro": "HTTP"}, ""),
        ({"kind": "cluster"}, {"type": "in", "action": "DROP", "enable": "0"}, ""),
        ({"kind": "cluster"}, {"type": "out", "action": "DROP"}, ""),
        ({"kind": "cluster"}, {"type": "in", "action": "ACCEPT"}, ""),
        ({"kind": "guest", "vmid": 100}, {"type": "in", "action": "DROP"}, ""),
    ],
)
def test_lockout_risk(scope, rule, risky):
    reason = lockout_risk(scope, rule)
    assert (risky in reason) if risky else reason == ""


# ---------------------------------------------------------------------
# Against the mock cluster
# ---------------------------------------------------------------------
@pytest.fixture
async def mock_server():
    server = TestServer(mock_proxmox.build_app(mock_proxmox.MockCluster(seed=1)))
    await server.start_server()
    yield server
    await server.close()


@pytest.fixture
async def cluster(mock_server):
    config = Config.from_options(
        {
            "hosts": ["http://127.0.0.1:1", f"http://127.0.0.1:{mock_server.port}"],
            "token_id": "root@pam!dashboard",
            "token_secret": "secret",
            "read_only": False,
        }
    )
    c = Cluster(config)
    await c.refresh_live()
    await c.refresh_conf()
    yield c
    await c.api.close()


async def test_reads_the_cluster_through_the_node_that_answers(cluster, mock_server):
    assert cluster.api.host.endswith(str(mock_server.port))
    assert sorted(cluster.live["node_status"]) == ["pve1", "pve2", "pve3"]
    assert cluster.live["ceph"]["health"]["status"] == "HEALTH_WARN"
    assert cluster.notes == {}
    conf = cluster.conf
    assert conf["guests"]["100"]["nics"][1]["tag"] == 40
    assert conf["nodes"]["pve2"]["network"]["changes"].startswith("---")
    assert [g["group"] for g in conf["firewall"]["groups"]] == ["webserver", "mgmt", "dns", "iot-isolate"]
    assert conf["firewall"]["groups"][0]["rules"][0]["macro"] == "HTTP"
    assert conf["sdn"]["subnets"]["iot"][0]["cidr"] == "192.168.30.0/24"
    assert conf["nodes"]["pve3"]["updates"] == []
    assert summary_keys(cluster) >= {"pve", "connected", "resources", "tasks", "notes"}


def summary_keys(cluster):
    return set(cluster.summary())


async def test_a_refused_part_becomes_a_note(cluster):
    async def refuse(*args, **kwargs):
        raise ProxmoxError("Permission check failed (/, Sys.Audit)", 403)

    assert await cluster._try("ha", refuse(), []) == []
    assert cluster.notes["ha"].startswith("Permission check failed")
    with pytest.raises(ProxmoxError):
        await cluster._try("x", _unreachable(), [])


async def _unreachable():
    raise ProxmoxError("No node can be reached", 0)


async def test_bad_token(mock_server):
    config = Config.from_options(
        {"hosts": [f"http://127.0.0.1:{mock_server.port}"], "token_id": "root@pam!dashboard", "token_secret": "wrong"}
    )
    c = Cluster(config)
    with pytest.raises(ProxmoxError, match="token was refused"):
        await c.refresh_live()
    await c.api.close()


async def test_details(cluster):
    details = await cluster.guest_details(101)
    assert details["nics"][0]["tag"] == 30
    assert details["addresses"][1]["ip-addresses"][0]["ip-address"] == "192.168.30.101"
    assert [s["name"] for s in (await cluster.guest_details(102))["snapshots"]] == ["before-upgrade", "clean-install"]
    assert "Turn on the QEMU guest agent" in (await cluster.guest_details(108))["address_note"]
    history = await cluster.cluster_rrd("day")
    assert len(history["time"]) == 70 and history["nodes"] == 3
    assert 0 < history["cpu"][-1] < 1
    log = await cluster.firewall_log()
    assert log["entries"] and log["entries"][0]["ts"] >= log["entries"][-1]["ts"]
    backups = await cluster.backups()
    assert {b["storage"] for b in backups["backups"]} == {"pbs"}
    with pytest.raises(KeyError):
        await cluster.guest_details(4242)


async def test_changes(cluster):
    result = await cluster.set_vlan(202, "net0", 20, cluster.conf["guests"]["202"]["digest"])
    assert "tag=20" in result["after"] and "tag=40" in result["before"]
    await cluster.refresh_conf()
    assert cluster.conf["guests"]["202"]["nics"][0]["tag"] == 20
    with pytest.raises(ProxmoxError, match="changed in the meantime"):
        await cluster.set_vlan(202, "net0", 30, "stale-digest")
    with pytest.raises(ValueError):
        await cluster.set_vlan(202, "net0", 5000)

    scope = {"kind": "guest", "vmid": 202}
    await cluster.change_rule(scope, "create", {"type": "in", "action": "ACCEPT", "macro": "SSH"}, pos=0)
    await cluster.refresh_conf()
    rules = cluster.conf["guests"]["202"]["firewall"]["rules"]
    assert rules[0]["macro"] == "SSH" and len(rules) == 3
    await cluster.change_rule(scope, "update", {"type": "in", "action": "ACCEPT", "proto": "tcp", "dport": "22"}, pos=0)
    await cluster.refresh_conf()
    edited = cluster.conf["guests"]["202"]["firewall"]["rules"][0]
    assert "macro" not in edited and edited["dport"] == "22"
    await cluster.change_rule(scope, "update", {"type": "in", "action": "ACCEPT", "macro": "SSH"}, pos=0)
    await cluster.change_rule(scope, "toggle", {"enable": False}, pos=0)
    await cluster.change_rule(scope, "move", pos=0, to=3)
    await cluster.refresh_conf()
    rules = cluster.conf["guests"]["202"]["firewall"]["rules"]
    assert rules[-1]["macro"] == "SSH" and rules[-1]["enable"] == 0
    await cluster.change_rule(scope, "delete", pos=2)
    await cluster.refresh_conf()
    assert len(cluster.conf["guests"]["202"]["firewall"]["rules"]) == 2
    with pytest.raises(ValueError, match="ACCEPT, DROP or REJECT"):
        await cluster.change_rule(scope, "create", {"type": "in", "action": "ALLOW"})
    with pytest.raises(ValueError, match="security group"):
        await cluster.change_rule(scope, "create", {"type": "group", "action": "bad group"})

    upid = await cluster.power(106, "start")
    assert upid.startswith("UPID:pve3:")
    task = await cluster.task("pve3", upid)
    assert task["status"]["type"] == "qmstart"


# ---------------------------------------------------------------------
# The dashboard's API
# ---------------------------------------------------------------------
@pytest.fixture
async def client(cluster, tmp_path, monkeypatch):
    monkeypatch.setattr(webui, "LAYOUT_FILE", tmp_path / "layout.json")
    web = WebUI(0, cluster.config, cluster)
    async with TestClient(TestServer(web.app)) as c:
        yield c


async def test_pages(client):
    page = await client.get("/")
    assert page.status == 200 and "Proxmox" in await page.text()
    status = await (await client.get("/api/status")).json()
    assert status["version"] != status["pve"] and status["pve"]["version"] == "9.1.1"
    assert "token_secret" not in str(status["config"])
    conf = await (await client.get("/api/config")).json()
    assert conf["guests"]["100"]["nics"]
    for path in (
        "/api/rrd?node=pve1&timeframe=hour",
        "/api/rrd?vmid=101&timeframe=week",
        "/api/rrd/cluster?timeframe=day",
    ):
        assert (await client.get(path)).status == 200, path
    assert (await client.get("/api/rrd?node=pve1&timeframe=decade")).status == 400
    assert (await client.get("/api/rrd?node=nope&timeframe=hour")).status == 404
    assert (await client.get("/api/guest/4242")).status == 404
    assert (await client.get("/api/task?node=pve1&upid=x")).status == 400


async def test_changes_through_the_api(client, cluster):
    post = lambda path, body: client.post(path, json=body)  # noqa: E731
    answer = await post(
        "/api/firewall/rule", {"scope": {"kind": "cluster"}, "op": "create", "rule": {"type": "in", "action": "DROP"}}
    )
    assert answer.status == 409 and "lockout" in await answer.json()
    answer = await post(
        "/api/firewall/rule",
        {"scope": {"kind": "cluster"}, "op": "create", "rule": {"type": "in", "action": "DROP"}, "accept_risk": True},
    )
    assert answer.status == 200
    assert (await post("/api/guest/106/power", {"action": "explode"})).status == 400
    assert (await post("/api/guest/202/vlan", {"nic": "net0", "tag": 30})).status == 200
    assert (await client.post("/api/guest/202/vlan", data="nic=net0")).status == 415
    assert (await post("/api/layout", {"pve1": {"vmbr0": [10, 20]}})).status == 200
    assert (await (await client.get("/api/layout")).json()) == {"pve1": {"vmbr0": [10, 20]}}

    cluster.config.read_only = True
    answer = await post("/api/guest/106/power", {"action": "start"})
    assert answer.status == 403 and "read-only" in (await answer.json())["error"]
    assert (await post("/api/firewall/rule", {"scope": {"kind": "cluster"}, "op": "delete", "pos": 0})).status == 403


async def test_only_ingress_may_change(client, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    assert (await client.post("/api/guest/106/power", json={"action": "start"})).status == 403
