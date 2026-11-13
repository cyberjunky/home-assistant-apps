"""The app's own parts: PBS's log, the mount units, the disk watcher and the app's systemctl.

PBS itself, the disk guard and real mounts are tested by running the image (see DOCS.md, Development);
these tests need neither PBS nor root."""

import struct
from pathlib import Path

import pytest

from src import journal, mounts, systemctl

UNIT = """[Unit]
Description=Mount datatstore 'store1' under '/mnt/datastore/store1'

[Mount]
Options=defaults
Type=ext4
What=/dev/disk/by-uuid/0f0e-1234
Where=/mnt/datastore/store1

[Install]
WantedBy=multi-user.target
"""

DATASTORE_CFG = """datastore: store1
\tpath /mnt/datastore/store1
\tgc-schedule daily

datastore: other
\tpath /srv/other
\tmaintenance-mode offline,message=Disk not connected

datastore: deep
\tpath /mnt/datastore/store1/deep
\tmaintenance-mode read-only,message=by hand
"""

MOUNTINFO = """22 1 0:21 / / rw,relatime - overlay overlay rw
36 22 8:17 / /mnt/datastore/store1 rw,relatime shared:1 - ext4 /dev/sdb1 rw
"""

parse_datastores = mounts.datastores


# ---------------------------------------------------------------------
# PBS's log, from the journal's native format
# ---------------------------------------------------------------------
def _field(name: str, value: str) -> bytes:
    raw = value.encode()
    return name.encode() + b"\n" + struct.pack("<Q", len(raw)) + raw + b"\n"


def test_journal_entries_are_read():
    data = (
        _field("PRIORITY", "4") + _field("SYSLOG_IDENTIFIER", "proxmox-backup-proxy") + _field("MESSAGE", "two\nlines")
    )
    data += b"PLAIN=value\n"
    entry = journal.parse_entry(data)
    assert entry == {
        "PRIORITY": "4",
        "SYSLOG_IDENTIFIER": "proxmox-backup-proxy",
        "MESSAGE": "two\nlines",
        "PLAIN": "value",
    }


def test_journal_cut_off_entry_does_not_raise():
    journal.parse_entry(_field("MESSAGE", "hello")[:-4])


def test_journal_messages_reach_the_log(caplog):
    proto = journal._Protocol()
    with caplog.at_level("INFO", logger="pbs"):
        proto.datagram_received(
            _field("PRIORITY", "3") + _field("SYSLOG_IDENTIFIER", "proxmox-backup-api") + _field("MESSAGE", "bad"), None
        )
        proto.datagram_received(_field("MESSAGE", "catching reload signal"), None)
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [("ERROR", "[api] bad")]


# ---------------------------------------------------------------------
# Mount units, the mount table and the datastores
# ---------------------------------------------------------------------
def test_unit_as_pbs_writes_it():
    unit = mounts.parse_unit("mnt-datastore-store1.mount", UNIT)
    assert (unit.what, unit.where, unit.type, unit.options) == (
        "/dev/disk/by-uuid/0f0e-1234",
        "/mnt/datastore/store1",
        "ext4",
        "defaults",
    )


@pytest.mark.parametrize("text", ["", "[Unit]\nDescription=x\n", "[Mount]\nWhat=/dev/x\nWhere=relative\n", "not ini"])
def test_bad_units_are_skipped(text):
    assert mounts.parse_unit("x.mount", text) is None


def test_units_in_a_folder(tmp_path):
    (tmp_path / "mnt-datastore-store1.mount").write_text(UNIT)
    (tmp_path / "other.service").write_text("[Service]\n")
    (tmp_path / "broken.mount").write_text("[Mount]\n")
    assert [u.name for u in mounts.units(tmp_path)] == ["mnt-datastore-store1.mount"]
    assert mounts.find_unit("mnt-datastore-store1.mount", tmp_path).where == "/mnt/datastore/store1"
    assert mounts.find_unit("../etc/passwd", tmp_path) is None


def test_a_missing_device_is_not_connected():
    assert mounts.parse_unit("x.mount", UNIT).device == ""


def test_mount_table():
    table = mounts.mount_table(MOUNTINFO)
    assert table["/mnt/datastore/store1"] == {"device": "8:17", "fstype": "ext4", "source": "/dev/sdb1"}
    assert "/" in table


def test_datastores_on_a_disk():
    stores = mounts.datastores(DATASTORE_CFG)
    assert [(s["name"], s["path"]) for s in stores] == [
        ("store1", "/mnt/datastore/store1"),
        ("other", "/srv/other"),
        ("deep", "/mnt/datastore/store1/deep"),
    ]
    assert [s["name"] for s in mounts.stores_on("/mnt/datastore/store1", stores)] == ["store1", "deep"]
    # A folder that merely starts with the same name is another disk
    assert mounts.stores_on("/mnt/datastore/store", stores) == []
    assert mounts.ours(stores[1]["maintenance"]) and not mounts.ours(stores[2]["maintenance"])


# ---------------------------------------------------------------------
# The watcher
# ---------------------------------------------------------------------
class Fake:
    """What the watcher sees and does, without disks or PBS."""

    def __init__(self, monkeypatch, tmp_path, connected: bool, mounted: bool, cfg: str):
        (tmp_path / "mnt-datastore-store1.mount").write_text(UNIT)
        monkeypatch.setattr(mounts, "UNIT_DIR", tmp_path)
        self.calls: list[tuple] = []
        self.mounted = mounted
        monkeypatch.setattr(mounts.Unit, "device", property(lambda u: "/dev/sdb1" if connected else ""))
        monkeypatch.setattr(mounts, "datastores", lambda: parse_datastores(cfg))
        monkeypatch.setattr(
            mounts, "mount_table", lambda text=None: {"/mnt/datastore/store1": {}} if self.mounted else {}
        )
        monkeypatch.setattr(mounts, "mount", self._mount)
        monkeypatch.setattr(mounts, "unmount", lambda where: self.calls.append(("unmount", where)) or (True, ""))
        monkeypatch.setattr(mounts, "lock", lambda where: self.calls.append(("lock", where)))
        monkeypatch.setattr(mounts, "set_offline", lambda s: self.calls.append(("offline", s)) or True)
        monkeypatch.setattr(mounts, "set_online", lambda s: self.calls.append(("online", s)) or True)

    def _mount(self, unit):
        self.calls.append(("mount", unit.where))
        self.mounted = True
        return True, "mounted"


def test_a_connected_disk_is_mounted_and_its_datastore_brought_back(monkeypatch, tmp_path):
    cfg = "datastore: store1\n\tpath /mnt/datastore/store1\n\tmaintenance-mode offline,message=Disk not connected\n"
    fake = Fake(monkeypatch, tmp_path, connected=True, mounted=False, cfg=cfg)
    mounts.Watcher().check()
    assert fake.calls == [("mount", "/mnt/datastore/store1"), ("online", "store1")]


def test_maintenance_set_by_hand_stays(monkeypatch, tmp_path):
    cfg = "datastore: store1\n\tpath /mnt/datastore/store1\n\tmaintenance-mode read-only,message=moving\n"
    fake = Fake(monkeypatch, tmp_path, connected=True, mounted=True, cfg=cfg)
    mounts.Watcher().check()
    assert fake.calls == []


def test_a_disconnected_disk_goes_offline_before_it_is_unmounted(monkeypatch, tmp_path):
    fake = Fake(
        monkeypatch, tmp_path, connected=False, mounted=True, cfg="datastore: store1\n\tpath /mnt/datastore/store1\n"
    )
    mounts.Watcher().check()
    assert fake.calls == [("offline", "store1"), ("unmount", "/mnt/datastore/store1")]


def test_an_absent_disk_keeps_its_folder_locked(monkeypatch, tmp_path):
    cfg = "datastore: store1\n\tpath /mnt/datastore/store1\n\tmaintenance-mode offline,message=Disk not connected\n"
    fake = Fake(monkeypatch, tmp_path, connected=False, mounted=False, cfg=cfg)
    mounts.Watcher().check()
    assert fake.calls == [("lock", "/mnt/datastore/store1")]


# ---------------------------------------------------------------------
# The app's systemctl
# ---------------------------------------------------------------------
def test_systemctl_accepts_what_needs_no_systemd():
    for command in (["daemon-reload"], ["enable", "x.mount"], ["disable", "x.mount"], ["--quiet", "reset-failed"]):
        assert systemctl.main(command) == 0


def test_systemctl_shows_pbs_services(capsys):
    assert systemctl.main(["show", "proxmox-backup-proxy", "-p", "ActiveState,SubState"]) == 0
    assert capsys.readouterr().out.strip() == "ActiveState=active\nSubState=running"
    systemctl.main(["show", "postfix.service", "--property=LoadState"])
    assert capsys.readouterr().out.strip() == "LoadState=not-found"


def test_systemctl_mounts_a_unit(monkeypatch, tmp_path):
    (tmp_path / "mnt-datastore-store1.mount").write_text(UNIT)
    monkeypatch.setattr(mounts, "UNIT_DIR", tmp_path)
    done = []
    monkeypatch.setattr(mounts, "mount", lambda u: done.append(("mount", u.where)) or (True, "mounted"))
    monkeypatch.setattr(mounts, "unmount", lambda w: done.append(("unmount", w)) or (True, ""))
    assert systemctl.main(["start", "mnt-datastore-store1.mount"]) == 0
    assert systemctl.main(["stop", "mnt-datastore-store1.mount"]) == 0
    assert done == [("mount", "/mnt/datastore/store1"), ("unmount", "/mnt/datastore/store1")]
    assert systemctl.main(["start", "missing.mount"]) == 5


def test_systemctl_reports_a_failed_mount(monkeypatch, tmp_path, capsys):
    (tmp_path / "mnt-datastore-store1.mount").write_text(UNIT)
    monkeypatch.setattr(mounts, "UNIT_DIR", tmp_path)
    monkeypatch.setattr(mounts, "mount", lambda u: (False, "/dev/disk/by-uuid/0f0e-1234 is not connected"))
    assert systemctl.main(["start", "mnt-datastore-store1.mount"]) == 1
    assert "not connected" in capsys.readouterr().err


def test_guard_script_refuses_home_assistants_disk():
    guard = Path(__file__).resolve().parent.parent / "rootfs/usr/local/lib/pbsapp/disk-guard"
    text = guard.read_text()
    assert "hassos" in text and "exec -a" in text
