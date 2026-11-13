"""VDL2: dumpvdl2's JSON frames read into messages; link upkeep is left out."""

import json

import pytest

from src.decoders.vdl2 import Vdl2, parse
from src.errors import ValidationError

ACARS = {"vdl2": {"freq": 136975000, "sig_level": -5.086136, "avlc": {
    "src": {"addr": "390826", "type": "Aircraft", "status": "Airborne"},
    "dst": {"addr": "26B117", "type": "Ground station"}, "frame_type": "I",
    "acars": {"err": False, "crc_ok": True, "reg": ".F-GCBG", "mode": "2", "label": "2T", "flight": "AF0000",
              "msg_text": "VER/038/B747/M\r\nSCH/AFR6748/LFPG/OKBK/05JAN/1030\r\nROT"}}}}  # fmt: skip
XID = {"vdl2": {"freq": 136975000, "sig_level": -5.1, "avlc": {
    "src": {"addr": "2D4918", "type": "Ground station"}, "dst": {"addr": "FFFFFF", "type": "Aircraft"},
    "frame_type": "U", "cmd": "XID", "xid": {"type": "GSIF"}}}}  # fmt: skip


def test_acars_frame():
    m = parse(json.dumps(ACARS))
    assert m["kind"] == "ACARS" and m["registration"] == "F-GCBG" and m["flight"] == "AF0000"
    assert m["text"] == "VER/038/B747/M\nSCH/AFR6748/LFPG/OKBK/05JAN/1030\nROT"
    assert m["aircraft"] == "390826" and m["direction"] == "down" and m["frequency"] == 136_975_000 and m["crc"]


def test_link_upkeep_and_rubbish():
    assert parse(json.dumps(XID)) is None
    assert parse("dumpvdl2 2.7.0 starting") is None and parse("{broken") is None


def test_cpdlc_inside():
    frame = json.loads(json.dumps(XID))
    frame["vdl2"]["avlc"]["x25"] = {"clnp": {"cotp": {"cpdlc": {"msg": "CLIMB TO FL350"}}}}
    m = parse(json.dumps(frame))
    assert m["kind"] == "CPDLC" and "CLIMB TO FL350" in m["text"] and m["direction"] == "up"


def test_published_by_flight():
    out = []
    mode = Vdl2({}, out.append)
    mode._line(json.dumps(ACARS))
    mode._line(json.dumps(XID))
    assert [m["key"] for m in out] == ["AF0000"] and mode.frames == 2 and mode.count == 1


def test_channels_setting():
    clean = Vdl2.validate({"channels": [136_975_000, 136_725_000]})
    assert clean["channels"] == [136_725_000, 136_975_000] and clean["frequency"] == 136_975_000
    assert Vdl2.validate({})["frequency"] == 136_875_000
    with pytest.raises(ValidationError):
        Vdl2.validate({"channels": [131_725_000, 136_975_000]})
    with pytest.raises(ValidationError):
        Vdl2.validate({"channels": []})
