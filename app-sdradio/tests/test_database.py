"""Channels, CSV, hits, finds, settings and backups."""

import time

import pytest

from src.backups import Backups
from src.database import Database, ValidationError


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "sdradio.db")
    yield database
    database.close()


def test_default_bands(db):
    names = [b["name"] for b in db.bands()]
    assert "2 m" in names and "Air" in names


def test_channel_crud(db):
    a = db.save_channel({"name": "Calling", "frequency": 145_500_000, "mode": "nfm"})
    assert a["number"] == 1
    assert a["band"] == "2 m"
    assert a["bandwidth"] == 12_500
    b = db.save_channel({"name": "Guard", "frequency": 121_500_000, "mode": "am", "favourite": True})
    assert b["number"] == 2 and b["band"] == "Air" and b["favourite"] == 1
    with pytest.raises(ValidationError, match="already in use"):
        db.save_channel({"number": 1, "frequency": 145_000_000, "mode": "nfm"})
    db.save_channel({"name": "Calling channel"}, a["id"])
    assert db.channel(a["id"])["name"] == "Calling channel"
    assert db.channel_at(145_500_200)["id"] == a["id"]
    db.delete_channel(a["id"])
    assert db.next_number() == 1
    with pytest.raises(ValidationError):
        db.save_channel({"frequency": "abc", "mode": "nfm"})
    with pytest.raises(ValidationError, match="Unknown mode"):
        db.save_channel({"frequency": 1e8, "mode": "xyz"})


def test_csv_roundtrip(db):
    db.save_channel({"name": "Calling", "frequency": 145_500_000, "mode": "nfm", "tags": "amateur", "favourite": 1})
    db.save_channel({"name": "20 m SSTV", "frequency": 14_230_000, "mode": "usb", "scan": 0})
    text = db.export_csv()
    assert text.splitlines()[0].startswith("number,name,frequency_mhz")
    db.delete_channel(db.channels()[0]["id"])
    result = db.import_csv(text)
    assert result == {"added": 1, "updated": 1}
    channels = {c["name"]: c for c in db.channels()}
    assert channels["Calling"]["frequency"] == 145_500_000 and channels["Calling"]["favourite"] == 1
    assert channels["20 m SSTV"]["mode"] == "usb" and channels["20 m SSTV"]["scan"] == 0


def test_csv_from_other_programs(db):
    chirp = "Location,Name,Frequency,Duplex,Offset,Tone,Mode,TStep,Comment\n0,PI2NOS,145.6125,-,0.6,Tone,FM,12.5,repeater\n1,AIR,121.5,,0,,AM,8.33,\n"
    assert db.import_csv(chirp) == {"added": 2, "updated": 0}
    channels = {c["name"]: c for c in db.channels()}
    assert channels["PI2NOS"]["mode"] == "nfm" and channels["PI2NOS"]["step"] == 12_500
    assert channels["PI2NOS"]["notes"] == "repeater"
    assert channels["AIR"]["number"] == 1


def test_csv_errors_import_nothing(db):
    with pytest.raises(ValidationError, match="line 3"):
        db.import_csv("name,frequency,mode\nA,145.5,nfm\nB,abc,nfm\n")
    assert db.channels() == []
    with pytest.raises(ValidationError, match="frequency column"):
        db.import_csv("name,mode\nA,nfm\n")


def test_csv_replace(db):
    db.save_channel({"name": "Old", "frequency": 100_000_000, "mode": "wfm"})
    db.import_csv("name,frequency_mhz,mode\nNew,145.5,nfm\n", replace=True)
    assert [c["name"] for c in db.channels()] == ["New"]


def test_hits_finds_and_stats(db):
    now = time.time()
    channel = db.save_channel({"name": "Calling", "frequency": 145_500_000, "mode": "nfm"})
    db.add_hit(now - 60, 5, 145_500_000, "nfm", -40, False)
    for i in range(3):
        db.add_hit(now - 30 + i, 2, 146_400_000, "nfm", -50, True)
    db.add_hit(now - 10, 1, 145_000_000, "nfm", -55, True)
    finds = db.finds(min_hits=3)
    assert [f["frequency"] for f in finds] == [146_400_000]
    db.ignore(146_400_000)
    assert db.finds(min_hits=1)[0]["frequency"] == 145_000_000
    # Storing a channel claims its earlier hits
    db.save_channel({"name": "New", "frequency": 145_000_000, "mode": "nfm"})
    assert db.finds(min_hits=1) == []
    stats = db.stats(now)
    assert stats["channels"] == 2 and stats["hits_24h"] == 5
    assert stats["heard_today"] >= 1
    assert sum(h["hits"] for h in stats["hours"]) == 5
    assert stats["top"][0]["id"] == channel["id"] or stats["top"][0]["hits"] >= 1
    assert db.recent_hits(2)[0]["frequency"] == 145_000_000


def test_settings(db):
    assert db.settings()["fft_size"] == 2048
    db.update_settings({"fft_size": 4096, "backup_time": "4:5"})
    assert db.settings()["fft_size"] == 4096 and db.settings()["backup_time"] == "04:05"
    with pytest.raises(ValidationError):
        db.update_settings({"fft_size": 1000})
    with pytest.raises(ValidationError):
        db.update_settings({"scan_speed": 99})
    with pytest.raises(ValidationError, match="Unknown"):
        db.update_settings({"nope": 1})
    rng = db.update_settings({"scan_range": {"start": 1e8, "stop": 1.1e8, "step": 1e5, "mode": "wfm"}})["scan_range"]
    assert rng == {"start": 100_000_000, "stop": 110_000_000, "step": 100_000, "mode": "wfm"}


def test_bands(db):
    band = db.save_band({"name": "6 m", "start": 50e6, "stop": 52e6, "mode": "usb", "step": 1000})
    assert band["bandwidth"] == 2_700
    with pytest.raises(ValidationError, match="above"):
        db.save_band({"name": "x", "start": 5, "stop": 1, "mode": "am"})
    db.delete_band(band["id"])
    assert len(db.reset_bands()) > 5


def test_backup_and_restore(db, tmp_path):
    backups = Backups(db, tmp_path / "backups")
    db.save_channel({"name": "Keep", "frequency": 145_500_000, "mode": "nfm"})
    item = backups.create()
    db.save_channel({"name": "Later", "frequency": 145_600_000, "mode": "nfm"})
    backups.restore(item["name"])
    assert [c["name"] for c in db.channels()] == ["Keep"]
    names = [b["name"] for b in backups.list()]
    assert any(n.endswith("-before-restore.db") for n in names)
    backups.prune(1)
    assert len(backups.list()) == 1
    with pytest.raises(ValidationError):
        backups.add_upload(b"not a database")
    upload = backups.add_upload((tmp_path / "backups" / backups.list()[0]["name"]).read_bytes())
    assert upload["name"].endswith("-uploaded.db")
    with pytest.raises(KeyError):
        backups.path("../sdradio.db")
