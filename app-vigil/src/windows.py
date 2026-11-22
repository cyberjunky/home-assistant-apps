"""Windows Event Log records, as Fluent Bit's winevtlog input sends them to the JSON lines endpoint.

A record looks like (with event_data_as_map: true):
    {"Channel": "Security", "Computer": "pc1.home", "EventID": 4625, "Level": 0,
     "Keywords": "0x8010000000000000", "TimeCreated": "2026-11-22 14:03:05 +0100",
     "ProviderName": "Microsoft-Windows-Security-Auditing", "Message": "An account failed to log on. ..",
     "EventData": {"TargetUserName": "bob", "IpAddress": "192.168.1.20", "LogonType": 3, ..}}

It becomes a log line with the computer as host and the channel as app, a severity from the
Windows level (an audit failure is a warning), and attributes to search and alert on:
    event_id   the Windows event ID (attrs.event_id:4625)
    event      a name for the events below (attrs.event:"failed logon")
    category   what kind of event, so one search covers several IDs (attrs.category:account_change)
    mitre      the MITRE ATT&CK technique it can be a sign of (attrs.mitre:T1110)
    audit      success or failure, for Security audit events
    user, source_ip, logon_type, group, service, image, command, task, ..   from the event's data
and the rest of the event's data as data.<Name>.

The table only names events; it is written for Vigil (Microsoft's event IDs, MITRE's public
technique IDs), not taken from another product's rule set.
"""

from datetime import datetime

from .records import parse_time

# (channel, event ID) -> (name, category, MITRE technique or "", syslog severity or None)
# None keeps the severity from the record's level.
SECURITY = "security"
SYSTEM = "system"
DEFENDER = "microsoft-windows-windows defender/operational"
POWERSHELL = "microsoft-windows-powershell/operational"
SYSMON = "microsoft-windows-sysmon/operational"

EVENTS: dict[tuple[str, int], tuple[str, str, str, int | None]] = {
    (SECURITY, 4624): ("logon", "logon", "T1078", 6),
    (SECURITY, 4625): ("failed logon", "logon_failure", "T1110", 4),
    (SECURITY, 4648): ("logon with explicit credentials", "logon", "T1078", 6),
    (SECURITY, 4740): ("account locked out", "logon_failure", "T1110", 4),
    (SECURITY, 4720): ("user account created", "account_change", "T1136.001", 5),
    (SECURITY, 4722): ("user account enabled", "account_change", "T1098", 5),
    (SECURITY, 4724): ("password reset", "account_change", "T1098", 5),
    (SECURITY, 4726): ("user account deleted", "account_change", "T1531", 5),
    (SECURITY, 4728): ("member added to a global group", "account_change", "T1098", 5),
    (SECURITY, 4732): ("member added to a local group", "account_change", "T1098", 5),
    (SECURITY, 4756): ("member added to a universal group", "account_change", "T1098", 5),
    (SECURITY, 1102): ("security log cleared", "log_cleared", "T1070.001", 4),
    (SECURITY, 4697): ("service installed", "service_installed", "T1543.003", 5),
    (SECURITY, 4698): ("scheduled task created", "task_created", "T1053.005", 5),
    (SECURITY, 4719): ("audit policy changed", "policy_change", "T1562.002", 4),
    (SYSTEM, 104): ("event log cleared", "log_cleared", "T1070.001", 4),
    (SYSTEM, 7045): ("service installed", "service_installed", "T1543.003", 5),
    (DEFENDER, 1116): ("malware detected", "malware", "", 4),
    (DEFENDER, 1117): ("malware action taken", "malware", "", 5),
    (DEFENDER, 5001): ("real-time protection disabled", "defense_off", "T1562.001", 4),
    (DEFENDER, 5007): ("Defender settings changed", "policy_change", "T1562.001", 5),
    (POWERSHELL, 4104): ("PowerShell script block", "powershell", "T1059.001", 6),
    (SYSMON, 1): ("process created", "process", "", 6),
    (SYSMON, 3): ("network connection", "network", "", 6),
    (SYSMON, 11): ("file created", "file", "", 6),
    (SYSMON, 13): ("registry value set", "registry", "", 6),
    (SYSMON, 22): ("DNS query", "dns", "", 6),
}
# Event 104 in System is only the log-cleared event from this provider
PROVIDER_ONLY = {(SYSTEM, 104): "microsoft-windows-eventlog"}

# Windows levels: 1 critical, 2 error, 3 warning, 4 information, 5 verbose (0: log always)
LEVELS = {1: 2, 2: 3, 3: 4, 4: 6, 5: 7}
AUDIT_SUCCESS = 0x20000000000000
AUDIT_FAILURE = 0x10000000000000

# Event data fields under a short name (the first one present wins)
ALIASES = {
    "user": ("TargetUserName", "User", "AccountName"),
    "by_user": ("SubjectUserName",),
    "source_ip": ("IpAddress", "SourceIp"),
    "logon_type": ("LogonType",),
    "workstation": ("WorkstationName",),
    "service": ("ServiceName",),
    "image": ("Image", "NewProcessName", "ImagePath", "ServiceFileName", "Path"),
    "command": ("CommandLine",),
    "task": ("TaskName",),
    "dest_ip": ("DestinationIp",),
    "dest_port": ("DestinationPort",),
    "dns_query": ("QueryName",),
    "threat": ("Threat Name",),
}
# In a group membership event TargetUserName is the group and the member is MemberName
GROUP_EVENTS = {4728, 4732, 4756}
EMPTY = ("", "-", None)  # "-" is Windows' "nothing here"
MAX_DATA = 40


def is_windows_event(item: dict) -> bool:
    return "EventID" in item and isinstance(item.get("Channel"), str) and "Computer" in item


def _int(value) -> int | None:
    try:
        return int(str(value), 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        return None


def _time(value) -> datetime | None:
    # "2026-11-22 14:03:05 +0100": local time with an offset after a space
    if isinstance(value, str) and len(value) > 6 and value[-6] == " " and value[-5] in "+-":
        value = value[:-6] + value[-5:]
    return parse_time(value)


def _data(item: dict) -> dict:
    data = item.get("EventData")
    if isinstance(data, dict):
        return data
    # Some templates come as a list of {Name: value} maps
    if isinstance(data, list):
        merged: dict = {}
        for part in data:
            if isinstance(part, dict):
                merged.update(part)
        return merged
    return {}


def windows_fields(item: dict) -> dict:
    """time, host, app, severity, message and attrs for a winevtlog record."""
    channel = str(item.get("Channel") or "")
    event_id = _int(item.get("EventID"))
    provider = str(item.get("ProviderName") or "")
    key = (channel.lower(), event_id or 0)
    known = EVENTS.get(key)
    if known and key in PROVIDER_ONLY and provider.lower() != PROVIDER_ONLY[key]:
        known = None
    keywords = _int(item.get("Keywords")) or 0
    audit = "failure" if keywords & AUDIT_FAILURE else "success" if keywords & AUDIT_SUCCESS else None

    severity = LEVELS.get(_int(item.get("Level")) or 0, 6)
    if known and known[3] is not None:
        severity = known[3]
    if audit == "failure":
        severity = min(severity, 4)

    attrs: dict = {"event_id": event_id, "provider": provider or None}
    if known:
        attrs["event"], attrs["category"] = known[0], known[1]
        if known[2]:
            attrs["mitre"] = known[2]
    if audit:
        attrs["audit"] = audit
    if item.get("UserID"):
        attrs["account"] = item["UserID"]
    data = {k: v for k, v in _data(item).items() if v not in EMPTY and not isinstance(v, dict | list)}
    used = set()
    if event_id in GROUP_EVENTS and channel.lower() == SECURITY:
        for alias, name in (("group", "TargetUserName"), ("user", "MemberName"), ("by_user", "SubjectUserName")):
            if name in data:
                attrs[alias] = data[name]
                used.add(name)
        if "user" not in attrs and "MemberSid" in data:
            attrs["user"] = data["MemberSid"]
            used.add("MemberSid")
    for alias, names in ALIASES.items():
        if alias in attrs:
            continue
        for name in names:
            if name in data:
                attrs[alias] = data[name]
                used.add(name)
                break
    for name, value in list(data.items())[:MAX_DATA]:
        if name not in used:
            attrs[f"data.{name}"] = value

    message = item.get("Message")
    if not isinstance(message, str) or not message.strip():
        label = known[0] if known else f"event {event_id}"
        message = f"{label} ({provider or channel})"
    message = message.strip()
    return {
        "ts": _time(item.get("TimeCreated")),
        "host": item.get("Computer") if isinstance(item.get("Computer"), str) else None,
        "app": channel or None,
        "severity": severity,
        "message": message,
        # One pattern per kind of event: the first line says what happened ("An account failed
        # to log on."), the lines below hold this event's details
        "pattern_text": message.splitlines()[0].strip(),
        "attrs": {k: v for k, v in attrs.items() if v is not None},
    }
