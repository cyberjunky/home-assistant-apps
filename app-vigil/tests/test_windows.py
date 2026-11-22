"""Windows Event Log records from Fluent Bit's winevtlog input, through the JSON lines endpoint."""

import json
from datetime import UTC, datetime

from src.otlp import parse_json_lines
from src.query import to_where
from src.windows import EVENTS, windows_fields

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def _record(**extra) -> dict:
    record = {
        "ProviderName": "Microsoft-Windows-Security-Auditing",
        "ProviderGuid": "{54849625-5478-4994-A5BA-3E3B0328C30D}",
        "Qualifiers": 0,
        "EventID": 4625,
        "Version": 0,
        "Level": 0,
        "Task": 12544,
        "Opcode": 0,
        "Keywords": "0x8010000000000000",
        "TimeCreated": "2026-11-22 14:03:05 +0100",
        "EventRecordID": 123456,
        "ActivityID": "{00000000-0000-0000-0000-000000000000}",
        "RelatedActivityID": "{00000000-0000-0000-0000-000000000000}",
        "ProcessID": 812,
        "ThreadID": 1460,
        "Channel": "Security",
        "Computer": "pc1.home",
        "UserID": "",
        "Message": "An account failed to log on.\r\n\r\nSubject:\r\n\tSecurity ID:\t\tS-1-0-0\r\n",
        "EventData": {
            "SubjectUserSid": "S-1-0-0",
            "SubjectUserName": "-",
            "TargetUserName": "bob",
            "TargetDomainName": "PC1",
            "Status": "0xc000006d",
            "LogonType": 3,
            "WorkstationName": "LAPTOP",
            "IpAddress": "192.168.1.20",
            "IpPort": "51234",
        },
    }
    record.update(extra)
    return record


def _parse(record: dict):
    entries, rejected = parse_json_lines(json.dumps(record), NOW)
    assert rejected == 0 and len(entries) == 1
    return entries[0]


def test_failed_logon():
    entry = _parse(_record())
    assert entry.host == "pc1.home" and entry.app == "Security" and entry.source == "json"
    assert entry.ts == datetime(2026, 11, 22, 13, 3, 5, tzinfo=UTC)  # +0100 after a space
    assert entry.severity == 4  # an audit failure is a warning
    assert entry.message.startswith("An account failed to log on.")
    a = entry.attrs
    assert a["event_id"] == 4625 and a["event"] == "failed logon" and a["category"] == "logon_failure"
    assert a["mitre"] == "T1110" and a["audit"] == "failure"
    assert a["user"] == "bob" and a["source_ip"] == "192.168.1.20" and a["logon_type"] == 3
    assert a["workstation"] == "LAPTOP" and a["data.Status"] == "0xc000006d"
    # Aliased fields are not stored twice; "-" is Windows' empty value; the noise is left out
    assert "data.TargetUserName" not in a and "by_user" not in a and "data.SubjectUserName" not in a
    assert not any(k in a for k in ("ActivityID", "ProviderGuid", "ThreadID", "Keywords"))


def test_group_membership_names_the_group_and_the_member():
    entry = _parse(
        _record(
            EventID=4732,
            Keywords="0x8020000000000000",
            Message="A member was added to a security-enabled local group.",
            EventData={
                "MemberName": "-",
                "MemberSid": "S-1-5-21-1-2-3-1001",
                "TargetUserName": "Administrators",
                "SubjectUserName": "ron",
            },
        )
    )
    a = entry.attrs
    assert a["group"] == "Administrators" and a["user"] == "S-1-5-21-1-2-3-1001" and a["by_user"] == "ron"
    assert a["category"] == "account_change" and a["mitre"] == "T1098" and a["audit"] == "success"
    assert entry.severity == 5


def test_levels_and_unknown_events():
    entry = _parse(
        _record(
            Channel="System",
            ProviderName="Disk",
            EventID=7,
            Level=2,
            Keywords="0x80000000000000",
            Message="The device has a bad block.",
            EventData={},
        )
    )
    assert entry.severity == 3 and entry.app == "System"
    assert "event" not in entry.attrs and "mitre" not in entry.attrs and "audit" not in entry.attrs
    assert entry.attrs["event_id"] == 7 and entry.attrs["provider"] == "Disk"
    for level, severity in ((1, 2), (3, 4), (4, 6), (5, 7), (0, 6)):
        record = _record(Channel="Application", EventID=1000, Level=level, Keywords="0x80000000000000")
        assert windows_fields(record)["severity"] == severity


def test_event_104_only_from_the_event_log_provider():
    cleared = windows_fields(_record(Channel="System", EventID=104, ProviderName="Microsoft-Windows-Eventlog"))
    assert cleared["attrs"]["category"] == "log_cleared" and cleared["attrs"]["mitre"] == "T1070.001"
    other = windows_fields(_record(Channel="System", EventID=104, ProviderName="Something-Else"))
    assert "category" not in other["attrs"]


def test_without_a_message_or_time():
    entry = _parse(
        _record(
            Message="",
            TimeCreated="not a time",
            EventID=7045,
            Channel="System",
            ProviderName="Service Control Manager",
            Keywords="0x8080000000000000",
            EventData={"ServiceName": "evil", "ImagePath": "C:\\evil.exe"},
        )
    )
    assert entry.ts == NOW and entry.message == "service installed (Service Control Manager)"
    assert entry.attrs["service"] == "evil" and entry.attrs["image"] == "C:\\evil.exe"


def test_other_json_lines_are_unchanged():
    entries, _ = parse_json_lines('{"host": "nas", "EventID": 1, "message": "hi"}', NOW)
    assert entries[0].host == "nas" and entries[0].attrs == {"EventID": 1}


def test_the_attributes_are_searchable():
    for q in ("attrs.event_id:4625", "attrs.mitre:T*", 'attrs.event:"failed logon"', "attrs.category:account_change"):
        assert to_where(q).sql
    assert all(len(v) == 4 for v in EVENTS.values())
