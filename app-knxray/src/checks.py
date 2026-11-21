"""What looks wrong: the ETS project, the telegrams on the bus, what devices answered and Home Assistant's
KNX configuration held against each other.

A finding is {id, code, severity, category, kind, subject, title, detail}; what a code means and what to do
about it is in CATALOG. ``kind`` and ``subject`` point at a group address, a device or an entity, so the
page can link to it. Pure functions over plain data: nothing here talks to a bus or to Home Assistant.
"""

from typing import Any

from .model import PENDING_NAMES, HAConfig, Project, main_differs
from .monitor import TP_TELEGRAMS_PER_SECOND, Monitor
from .telegrams import transcoder_for

ERROR, WARNING, INFO = "error", "warning", "info"
BUS, PROJECT, DEVICES, HA = "Bus", "Project", "Devices", "Home Assistant"

# code -> (severity, category, what it is, what to do)
CATALOG: dict[str, tuple[str, str, str, str]] = {
    "payload_mismatch": (
        ERROR,
        BUS,
        "Payload does not fit the datapoint type",
        "A device sends a value of another size or range than the type the project gives this group address. "
        "Compare the datapoint type of the sending object with the group address in ETS; one of them is wrong, "
        "or the device was programmed from another project.",
    ),
    "size_conflict": (
        WARNING,
        BUS,
        "Payloads of different sizes on one group address",
        "Senders disagree about what this address carries. Objects of different datapoint types are linked to "
        "it, or a device runs an older application. Check every sender listed.",
    ),
    "ga_unknown": (
        WARNING,
        BUS,
        "Group address on the bus that is not in the project",
        "Something sends to an address the project does not know: the project in Home Assistant is older than "
        "what was programmed, or a device still holds links from a previous project. Export the current "
        "project from ETS and upload it in Home Assistant's KNX settings, or reprogram the sender.",
    ),
    "src_unknown": (
        WARNING,
        BUS,
        "Device on the bus that is not in the project",
        "Telegrams come from an individual address the project has no device for: a device added after the "
        "export, a commissioning tool, or another IP interface or tunnel. If it is none of those, find it: "
        "the group addresses it sends to say what it does.",
    ),
    "src_unprogrammed": (
        ERROR,
        BUS,
        "Device sending from the factory address",
        "15.15.255 is the address a device has before it is programmed. A device was reset or replaced and "
        "never got its individual address. Program it from ETS.",
    ),
    "foreign_sender": (
        WARNING,
        BUS,
        "Device sends to a group address it is not linked to",
        "By the project this device has no object on this address, yet it sends to it. The device holds "
        "other links than the project: it was programmed from another version. Download the application "
        "again from ETS, or upload the current project in Home Assistant.",
    ),
    "read_unanswered": (
        WARNING,
        BUS,
        "Reads that nobody answers",
        "A read was sent to this address and no device responded. Exactly one object linked to the address "
        "needs the read flag (R) set in ETS; the status object of the actuator is the usual choice. Until "
        "then Home Assistant does not know the state after a restart.",
    ),
    "read_multi": (
        WARNING,
        BUS,
        "Several devices answer the same read",
        "More than one object on this address has the read flag. The answers may differ and the last one "
        "wins. Leave the read flag on one object only.",
    ),
    "chatty_ga": (
        WARNING,
        BUS,
        "Group address is very busy",
        "More telegrams a minute than the limit set in the app's options. Look for a cyclic send with a short "
        "interval, a sensor reporting every small change (raise its hysteresis), or two logics answering "
        "each other.",
    ),
    "chatty_src": (
        WARNING,
        BUS,
        "Device sends very often",
        "This device takes a large share of the bus. Check its cyclic sending and the minimum change for "
        "its measured values in ETS.",
    ),
    "repeats": (
        INFO,
        BUS,
        "The same value sent again and again",
        "The sender repeats an unchanged value within seconds. Usually a cyclic send that is set too fast, "
        "or a push button whose press is sent on by a logic.",
    ),
    "resent": (
        WARNING,
        BUS,
        "Device has to send its telegrams again",
        "Frames from this device arrive marked as repeated: nobody acknowledged them the first time. No device "
        "on its line listens to the address, the line is disturbed (cable, termination, power supply), or a "
        "coupler does not pass the address. Not every IP interface reports repeats.",
    ),
    "hops_exhausted": (
        WARNING,
        BUS,
        "Telegrams arrive with their hop count used up",
        "Every coupler a telegram passes lowers its hop count; at zero it is dropped. Telegrams from this "
        "device arrive with one hop or none left: it sits behind too many couplers, or telegrams go round in "
        "a loop between two of them.",
    ),
    "bus_load": (
        WARNING,
        BUS,
        "High bus load",
        "In the busiest second the bus carried at least half of what a twisted pair line can. Telegrams get "
        "delayed and repeated above that. See the busiest devices in the statistics.",
    ),
    "wire_errors": (
        ERROR,
        BUS,
        "Damaged frames on the line",
        "The interface saw frames that arrived broken. That is the wiring: a loose terminal, a cable too "
        "long or of the wrong kind, a second power supply without a choke, or a failing device that "
        "disturbs the line. Disconnect parts of the line to find where it stops.",
    ),
    "wire_repeats": (
        WARNING,
        BUS,
        "Many telegrams are sent again",
        "A telegram is sent again when nobody acknowledges it. A few are normal; many mean a disturbed "
        "line or senders talking to addresses no device on the line listens to. The Wire page shows which.",
    ),
    "wire_refused": (
        WARNING,
        BUS,
        "Devices answer busy or refuse telegrams",
        "A receiver could not take a telegram. Busy means it had no room: it is flooded, usually by a "
        "sender that is too fast. Look for the busiest group addresses.",
    ),
    "wire_unanswered": (
        INFO,
        BUS,
        "Telegrams nobody acknowledged",
        "No device on this line took these telegrams, so the sender repeats each of them three times. "
        "Normal for an address only used on another line or only by Home Assistant over IP; otherwise the "
        "receiver is missing or not programmed.",
    ),
    "secure_undecoded": (
        WARNING,
        BUS,
        "Data Secure telegrams that could not be read",
        "Telegrams arrived encrypted and the connection has no key for them. Home Assistant needs the "
        "keyring exported from ETS (KNX settings); this app reads their values through Home Assistant only.",
    ),
    "ga_no_dpt": (
        INFO,
        PROJECT,
        "Group address without a datapoint type",
        "Without a type the value cannot be decoded, charted or checked; when its objects agree on one, the "
        "app reads the value as that. Set the datapoint type on the group address in ETS and upload the "
        "project again.",
    ),
    "download_pending": (
        WARNING,
        PROJECT,
        "ETS has not downloaded every part of the device",
        "The project was changed after the device was last programmed, or a part was never downloaded: the "
        "device may not do what the project says. Download the missing parts from ETS (partial download), "
        "then export the project again and upload it in Home Assistant.",
    ),
    "ga_unused": (
        INFO,
        PROJECT,
        "Group address that nothing is linked to",
        "No object and no Home Assistant entity uses this address. A leftover, or a link that was forgotten.",
    ),
    "ga_no_listener": (
        INFO,
        PROJECT,
        "Group address that nothing listens to",
        "Objects send to this address, but no object can receive on it (no write or update flag) and Home "
        "Assistant does not use it. What is sent here has no effect.",
    ),
    "ga_no_sender": (
        INFO,
        PROJECT,
        "Group address that nothing sends to",
        "Objects listen on this address, but no object sends on it and Home Assistant does not write to it.",
    ),
    "read_flag_twice": (
        WARNING,
        PROJECT,
        "Several objects on the address have the read flag",
        "A read of this address will be answered by each of them, and the answers may differ. Leave the "
        "read flag on one object: the status object of the actuator.",
    ),
    "line_crowded": (
        WARNING,
        PROJECT,
        "More than 64 devices on one line",
        "A line segment carries 64 devices; more need line repeaters, each with a power supply of its own. "
        "Check that the line is built that way, or move devices to another line.",
    ),
    "line_no_supply": (
        INFO,
        PROJECT,
        "Line without a power supply in the project",
        "Every twisted pair line needs a KNX power supply with choke. The project has no device on this "
        "line that looks like one; it may simply not be entered, as a supply needs no programming.",
    ),
    "line_no_coupler": (
        INFO,
        PROJECT,
        "Line without a coupler in the project",
        "The project has several lines, and this one has no coupler or router (a device at address 0 of "
        "the line). Telegrams cannot reach the other lines unless the lines are in fact one.",
    ),
    "dpt_conflict": (
        WARNING,
        PROJECT,
        "Objects of another datapoint type linked to the group address",
        "The address and the objects on it disagree about the type of the value. The receivers will read "
        "something else than the sender meant. Correct the link or the type in ETS.",
    ),
    "device_no_links": (
        INFO,
        PROJECT,
        "Device without any linked object",
        "The device is in the project but none of its objects is linked to a group address, so it does "
        "nothing on the bus.",
    ),
    "address_conflict": (
        ERROR,
        PROJECT,
        "Interface uses the address of a project device",
        "The connection to the bus got an individual address that the project gives to a device. Two "
        "senders with one address confuse couplers and acknowledgements. Give the tunnel a free address "
        "in the interface's settings in ETS.",
    ),
    "device_unreachable": (
        ERROR,
        DEVICES,
        "Device does not answer",
        "The device was addressed directly and stayed silent: no bus voltage, not programmed with this "
        "address, behind a coupler that does not pass it, or broken. Check its programming LED and the line.",
    ),
    "device_not_in_project": (
        WARNING,
        DEVICES,
        "Device answers at an address the project does not have",
        "A scan found a device where the project has none. Add it to the project, or find out what it is.",
    ),
    "device_progmode": (
        WARNING,
        DEVICES,
        "Device is in programming mode",
        "Its programming LED is on. Left like that, the next download of an individual address in ETS can "
        "land on this device. Press its programming button.",
    ),
    "device_unloaded": (
        ERROR,
        DEVICES,
        "Device has no working application",
        "The device answers, but reports its application as not loaded: a download was interrupted or it "
        "was unloaded. Download the application again from ETS.",
    ),
    "device_address_clash": (
        ERROR,
        DEVICES,
        "Two devices use the same individual address",
        "The device reports that it saw telegrams from another sender with its own address. Two devices were "
        "programmed with one address, or a tunnel of an interface uses it. Find the other one (switch this "
        "device off and check the address again) and give it its own address in ETS.",
    ),
    "device_table_differs": (
        WARNING,
        DEVICES,
        "Device holds other group addresses than the project says",
        "The group addresses read from the device are not the ones the project links to it. The device was "
        "programmed from another version of the project, or the project in Home Assistant is out of date. "
        "Download the application again from ETS, or upload the current project.",
    ),
    "device_stopped": (
        ERROR,
        DEVICES,
        "Device has stopped its application",
        "The device answers, but reports that its application is not running. Restart it from ETS, or "
        "download the application again.",
    ),
    "device_objects_differ": (
        WARNING,
        DEVICES,
        "Device's objects are linked or flagged differently than the project says",
        "The links and flags read from the device's own tables are not the project's. The device was "
        "programmed from another version of the project. Download the application again from ETS, or upload "
        "the current project in Home Assistant.",
    ),
    "coupler_blocks": (
        WARNING,
        DEVICES,
        "Coupler blocks group telegrams",
        "The coupler is set to pass no group telegrams in one direction. Nothing crosses it that way. Set "
        "it to filter (or to route everything while commissioning) in ETS.",
    ),
    "coupler_open": (
        INFO,
        DEVICES,
        "Coupler passes every group telegram",
        "The coupler does not filter: every telegram of one side loads the other side too. That is a "
        "commissioning setting; set it to filter in ETS and download the coupler once the project is done.",
    ),
    "device_silent": (
        INFO,
        DEVICES,
        "Device not heard yet",
        "By the project this device has objects that send, but no telegram came from it while the app "
        "listened. That is normal for a device that only reports on change. Check it from the Devices page "
        "if you expect to hear from it.",
    ),
    "ha_missing": (
        ERROR,
        HA,
        "The KNX integration is not set up or not running",
        "The app reads the ETS project and Home Assistant's entities from the KNX integration. Add or "
        "repair it under Settings › Devices & services.",
    ),
    "ha_disconnected": (
        ERROR,
        HA,
        "Home Assistant is not connected to the bus",
        "The KNX integration runs but has no connection to its interface. Its entities do not work. Check "
        "the interface and the integration's connection settings.",
    ),
    "ha_no_project": (
        WARNING,
        HA,
        "No ETS project in Home Assistant",
        "Without the project the app knows no names, datapoint types or devices, and most checks cannot "
        "run. Upload the .knxproj under Settings › Devices & services › KNX › Configure › Project.",
    ),
    "ha_yaml_error": (
        ERROR,
        HA,
        "The KNX configuration in YAML has an error",
        "Home Assistant could not validate the knx: section of configuration.yaml. Entities from it may be missing.",
    ),
    "ha_ga_not_in_project": (
        WARNING,
        HA,
        "Entity uses a group address that is not in the project",
        "A typing mistake in the address, or the address was removed or moved in ETS. Correct the entity, "
        "or upload the current project.",
    ),
    "ha_dpt_mismatch": (
        WARNING,
        HA,
        "Entity expects another datapoint type than the project gives",
        "Home Assistant will send or read this address as another type than the devices use. Check the "
        "entity's address and type against the project.",
    ),
    "ha_state_no_answer": (
        INFO,
        HA,
        "State address that no device answers reads on",
        "Home Assistant asks this address for the state when it starts. By the project no object on it has "
        "the read flag, so the entity stays unknown until the value changes. Set the read flag on the status "
        "object in ETS.",
    ),
    "ha_write_no_listener": (
        WARNING,
        HA,
        "Entity writes to an address that no device listens to",
        "By the project no object can receive on this address, so the entity controls nothing. Use the "
        "address the actuator's object is linked to.",
    ),
    "ha_unavailable": (
        WARNING,
        HA,
        "KNX entity is unavailable",
        "The integration is connected, but this entity is not available. Reload the KNX integration; if it "
        "stays, look at Home Assistant's log for this entity.",
    ),
    "ha_stray_entity": (
        WARNING,
        HA,
        "Stray entity: Home Assistant still has it, KNX no longer provides it",
        "Its configuration was removed or its addresses changed, and the old entry stays in Home "
        "Assistant's registry, unavailable for ever. Remove it (here when removing is switched on in the "
        "app's options, or from the entity's settings in Home Assistant).",
    ),
    "ha_stray_device": (
        INFO,
        HA,
        "KNX device in Home Assistant without entities",
        "A device made for KNX entities that has none left.",
    ),
    "ha_duplicate": (
        INFO,
        HA,
        "Two entities use exactly the same addresses",
        "Probably the same thing configured twice, once in YAML and once in the UI.",
    ),
}


def _finding(code: str, kind: str, subject: str, detail: str, title: str = "") -> dict[str, Any]:
    severity, category, default_title, _fix = CATALOG[code]
    return {
        "id": f"{code}:{subject}",
        "code": code,
        "severity": severity,
        "category": category,
        "kind": kind,
        "subject": subject,
        "title": title or default_title,
        "detail": detail,
    }


def _names(project: Project, addresses: list[str], limit: int = 4) -> str:
    """``1.1.4 (MDT Switch actuator), 1.1.9`` for a list of device addresses."""
    parts = [f"{a} ({name})" if (name := project.name_of(a)) else a for a in addresses[:limit]]
    if len(addresses) > limit:
        parts.append(f"and {len(addresses) - limit} more")
    return ", ".join(parts)


def _n(count: int, word: str) -> str:
    """``1 telegram``, ``3 telegrams``."""
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _sizes(sizes: dict[str, int]) -> str:
    """``up to 6 bits, 2 bytes`` for the payload sizes seen on an address."""
    return ", ".join("up to 6 bits" if s == "bits" else "1 byte" if s == "1" else f"{s} bytes" for s in sizes)


def _size_of(dpt: str | None) -> str:
    transcoder = transcoder_for(dpt)
    if transcoder is None:
        return ""
    length = getattr(transcoder, "payload_length", None)
    if transcoder.payload_type.__name__ == "DPTBinary":
        return "up to 6 bits"
    return _sizes({str(length): 1}) if length else ""


# How a power supply is called in product names, in the languages ETS catalogs come in
SUPPLY_WORDS = (
    "power supply",
    "spannungsversorgung",
    "netzteil",
    "voeding",
    "stromversorgung",
    "alimentation",
    " ps/",
    "stv-",
)
NEEDED_TABLES = ("Address table", "Association table", "Application", "Group object table")
FLAG_LETTERS = (("communication", "C"), ("read", "R"), ("write", "W"), ("transmit", "T"), ("update", "U"))


def _object_differences(project: Project, address: str, held: list[dict[str, Any]] | None) -> list[str]:
    """Where the objects read from a device differ from the project's: links and flags, per object.

    Devices count their objects from 0 or from 1, and the project does not say which: both are tried, and
    the one that fits best is used. When neither fits at least half of the objects, the tables are read
    wrongly rather than the device programmed wrongly, and nothing is reported."""
    device = project.devices.get(address)
    if not held or device is None:
        return []
    mine = {
        project.objects[o]["number"]: project.objects[o]
        for o in device["objects"]
        if isinstance(project.objects[o]["number"], int) and project.objects[o]["group_addresses"]
    }
    if not mine:
        return []
    best: list[str] | None = None
    for offset in (0, 1):
        theirs = {item["number"] - offset: item for item in held}
        differences, same = [], 0
        for number, co in sorted(mine.items()):
            other = theirs.get(number)
            if other is None:
                differences.append(f"object {number} ({co['name']}) is not in the device")
                continue
            wanted, got = set(co["group_addresses"]), set(other["group_addresses"])
            flags = "".join(letter for key, letter in FLAG_LETTERS if co["flags"][key] != other["flags"].get(key))
            if wanted == got and not flags:
                same += 1
                continue
            parts = []
            if got - wanted:
                parts.append(f"also linked to {', '.join(sorted(got - wanted))}")
            if wanted - got:
                parts.append(f"not linked to {', '.join(sorted(wanted - got))}")
            if flags:
                parts.append(f"flags {flags} differ")
            differences.append(f"object {number} ({co['name']}) {', '.join(parts)}")
        if same * 2 >= len(mine) and (best is None or len(differences) < len(best)):
            best = differences
    return best or []


def run_checks(
    project: Project,
    ha: HAConfig,
    monitor: Monitor,
    reach: dict[str, dict[str, Any]],
    *,
    chatty_per_minute: int = 30,
    own_addresses: tuple[str, ...] = (),
    observed: float = 0.0,
    bus_issues: int = 0,
    wire: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Every finding, worst first. ``reach`` is what devices answered when asked directly (bus.py), and
    ``own_addresses`` the individual addresses of this app's and Home Assistant's connections."""
    out: list[dict[str, Any]] = []
    users = ha.users()
    own = {a for a in (*own_addresses, ha.address) if a and a != "0.0.0"}

    # ---- Home Assistant itself ----
    if not ha.present or not ha.loaded:
        out.append(_finding("ha_missing", "ha", "integration", ha.error or "The KNX integration did not answer."))
    else:
        if ha.connection and not ha.connection.get("connected"):
            out.append(_finding("ha_disconnected", "ha", "connection", "The integration reports no connection."))
        if not project.loaded:
            out.append(_finding("ha_no_project", "ha", "project", "The integration has no project loaded."))
        if ha.yaml_error:
            out.append(_finding("ha_yaml_error", "ha", "yaml", ha.yaml_error[:600]))

    # ---- the bus ----
    for address, stats in monitor.addresses.items():
        ga = project.group_addresses.get(address)
        senders = sorted(stats["sources"], key=lambda s: -stats["sources"][s])
        if project.loaded and ga is None:
            out.append(
                _finding(
                    "ga_unknown",
                    "ga",
                    address,
                    f"{_n(stats['count'], 'telegram')}, sent by {_names(project, senders)}.",
                )
            )
        if stats["undecoded"] and ga and ga["dpt"]:
            sizes = _sizes(stats["sizes"])
            out.append(
                _finding(
                    "payload_mismatch",
                    "ga",
                    address,
                    f"{stats['undecoded']} of {stats['writes'] + stats['responses']} values could not be read as "
                    f"DPT {ga['dpt']} ({_size_of(ga['dpt']) or 'size unknown'}); seen on the bus: {sizes}. "
                    f"Senders: {_names(project, senders)}.",
                )
            )
        elif len(stats["sizes"]) > 1:
            sizes = _sizes(stats["sizes"])
            out.append(_finding("size_conflict", "ga", address, f"Seen: {sizes}. Senders: {_names(project, senders)}."))
        if stats["unanswered"] and not stats["answered"]:
            out.append(
                _finding(
                    "read_unanswered",
                    "ga",
                    address,
                    f"{stats['unanswered']} of {stats['reads']} reads got no response."
                    + (" By the project no object on it has the read flag." if ga and not ga["answerers"] else ""),
                )
            )
        if stats["multi_answer"]:
            out.append(
                _finding(
                    "read_multi",
                    "ga",
                    address,
                    f"{_n(stats['multi_answer'], 'read')} answered by more than one device: "
                    f"{_names(project, sorted(stats['responders']))}.",
                )
            )
        if stats["peak"] > chatty_per_minute:
            out.append(
                _finding(
                    "chatty_ga",
                    "ga",
                    address,
                    f"Up to {stats['peak']} telegrams in one minute (limit {chatty_per_minute}). "
                    f"Mostly from {_names(project, senders, 2)}.",
                )
            )
        if stats["repeats"] >= 10:
            out.append(
                _finding("repeats", "ga", address, f"{stats['repeats']} repeats of an unchanged value within seconds.")
            )
        if ga:
            foreign = [
                s for s in senders if s in project.devices and s not in ga["devices"] and s not in own and ga["devices"]
            ]
            if foreign:
                out.append(
                    _finding(
                        "foreign_sender",
                        "ga",
                        address,
                        f"{_names(project, foreign)} sent to it; by the project it is linked to "
                        f"{_names(project, ga['devices'])}.",
                    )
                )

    for address, stats in monitor.sources.items():
        if address in own:
            continue
        if address == "15.15.255":
            out.append(
                _finding(
                    "src_unprogrammed",
                    "device",
                    address,
                    f"{_n(stats['count'], 'telegram')}, to {', '.join(list(stats['addresses'])[:5])}.",
                )
            )
        elif project.loaded and address not in project.devices:
            targets = list(stats["addresses"])
            names = ", ".join(f"{a} ({project.name_of(a)})" if project.name_of(a) else a for a in targets[:4])
            out.append(_finding("src_unknown", "device", address, f"{_n(stats['count'], 'telegram')}, to {names}."))
        if stats["resent"] >= 3:
            out.append(
                _finding(
                    "resent",
                    "device",
                    address,
                    f"{stats['resent']} of its {stats['count']} telegrams were repeats.",
                )
            )
        if stats["min_hops"] <= 1:
            out.append(
                _finding("hops_exhausted", "device", address, f"As few as {stats['min_hops']} hops left on arrival.")
            )
        if stats["peak"] > chatty_per_minute * 3:
            out.append(
                _finding(
                    "chatty_src",
                    "device",
                    address,
                    f"Up to {stats['peak']} telegrams in one minute, over {len(stats['addresses'])} group addresses.",
                )
            )

    busiest = max((second for _minute, _count, second in monitor.load(60)), default=0)
    if busiest >= TP_TELEGRAMS_PER_SECOND // 2:
        out.append(
            _finding(
                "bus_load",
                "bus",
                "load",
                f"{busiest} telegrams in the busiest second of the last hour; a twisted pair line carries about "
                f"{TP_TELEGRAMS_PER_SECOND}.",
            )
        )
    wire = wire or {}
    if wire.get("errors") or wire.get("bad_checksum"):
        out.append(
            _finding(
                "wire_errors",
                "bus",
                "wire",
                f"{wire.get('errors', 0)} frames with a frame, bit or parity error and {wire.get('bad_checksum', 0)} with a "
                f"wrong checksum, of {wire['frames']} seen in bus monitor mode.",
            )
        )
    if wire.get("repeated", 0) >= 3 and wire["repeated"] * 50 > wire.get("data", 0):
        out.append(
            _finding(
                "wire_repeats",
                "bus",
                "repeats",
                f"{wire['repeated']} of {wire['data']} telegrams seen in bus monitor mode were repeats.",
            )
        )
    if wire.get("nak") or wire.get("busy"):
        out.append(
            _finding(
                "wire_refused", "bus", "refused", f"{wire.get('busy', 0)} answered busy, {wire.get('nak', 0)} refused."
            )
        )
    if wire.get("unanswered"):
        where = f" Most to {', '.join(wire['unanswered_to'])}." if wire.get("unanswered_to") else ""
        out.append(
            _finding(
                "wire_unanswered", "bus", "unanswered", f"{wire['unanswered']} of {wire['data']} telegrams.{where}"
            )
        )
    if bus_issues:
        out.append(_finding("secure_undecoded", "bus", "secure", f"{bus_issues} telegrams since the app connected."))

    # ---- the project ----
    for address, ga in project.group_addresses.items():
        used = users.get(address, [])
        if not ga["dpt"]:
            sizes = (monitor.addresses.get(address) or {}).get("sizes") or {}
            seen = _sizes(sizes)
            parts = [f"Payloads seen: {seen}." if seen else ga["name"]]
            if ga.get("dpt_objects"):
                parts.append(f"Its objects are DPT {ga['dpt_objects']}: values are read as that.")
            out.append(_finding("ga_no_dpt", "ga", address, " ".join(parts)))
        if not ga["objects"] and not used:
            out.append(_finding("ga_unused", "ga", address, ga["name"]))
        elif ga["objects"]:
            ha_listens = any(u["use"] in ("state", "passive") for u in used)
            ha_writes = any(u["use"] == "write" for u in used)
            if ga["senders"] and not ga["listeners"] and not ha_listens:
                out.append(_finding("ga_no_listener", "ga", address, f"Sent to by {_names(project, ga['devices'])}."))
            elif not ga["senders"] and ga["listeners"] and not ha_writes and not ga["answerers"]:
                out.append(_finding("ga_no_sender", "ga", address, f"Listened to by {_names(project, ga['devices'])}."))
        if ga["answerers"] > 1:
            answering = [
                project.objects[o]["device"]
                for o in ga["objects"]
                if project.objects[o]["flags"]["read"] and project.objects[o]["group_addresses"][:1] == [address]
            ]
            out.append(_finding("read_flag_twice", "ga", address, f"On {_names(project, answering)}."))
        differing = [d for d in ga["object_dpts"] if main_differs(d, ga["dpt"])]
        if not differing and not ga["dpt"]:
            mains = {d.split(".")[0] for d in ga["object_dpts"]}
            differing = ga["object_dpts"] if len(mains) > 1 else []
        if differing:
            out.append(
                _finding(
                    "dpt_conflict",
                    "ga",
                    address,
                    f"The address is DPT {ga['dpt'] or 'not set'}; objects on it are DPT {', '.join(differing)}.",
                )
            )

    by_line: dict[str, list[dict[str, Any]]] = {}
    for device in project.devices.values():
        by_line.setdefault(device["line"], []).append(device)
    twisted = [line for line in by_line if (project.lines.get(line) or {}).get("medium", "TP") == "TP"]
    for line in twisted:
        devices = by_line[line]
        if len(devices) > 64:
            out.append(_finding("line_crowded", "line", line, f"{len(devices)} devices on line {line}."))
        text = " ".join(f"{d['name']} {d['product']} {d['application']}".lower() for d in devices)
        if len(devices) >= 3 and not any(word in text for word in SUPPLY_WORDS):
            out.append(_finding("line_no_supply", "line", line, f"Line {line}, {len(devices)} devices."))
        if len(twisted) > 1 and f"{line}.0" not in project.devices and not line.endswith(".0"):
            out.append(_finding("line_no_coupler", "line", line, f"Line {line}, {len(devices)} devices."))

    for address, device in project.devices.items():
        if device.get("pending"):
            missing = ", ".join(PENDING_NAMES[p] for p in device["pending"])
            out.append(
                _finding(
                    "download_pending",
                    "device",
                    address,
                    f"{device['maker']} {device['name']}. Not downloaded: {missing}.".strip(),
                )
            )
        linked = any(project.objects[o]["group_addresses"] for o in device["objects"])
        if device["objects"] and not linked:
            out.append(_finding("device_no_links", "device", address, f"{device['maker']} {device['name']}".strip()))
        if address in own:
            out.append(
                _finding(
                    "address_conflict",
                    "device",
                    address,
                    f"{address} is {device['maker']} {device['name']} in the project, and also the address of a "
                    "connection to the bus.",
                )
            )
        sends = any(
            project.objects[o]["flags"]["transmit"] and project.objects[o]["group_addresses"] for o in device["objects"]
        )
        if sends and observed >= 3600 and address not in monitor.sources and address not in reach:
            out.append(
                _finding("device_silent", "device", address, f"Listening for {int(observed // 3600)} h without it.")
            )

    # ---- devices that were asked directly ----
    for address, result in reach.items():
        name = project.name_of(address)
        if result.get("reachable") is False and address in project.devices:
            out.append(_finding("device_unreachable", "device", address, f"{name}. Checked without an answer."))
        if result.get("reachable") and project.loaded and address not in project.devices and address not in own:
            mask = f" Mask {result['mask']}." if result.get("mask") else ""
            out.append(_finding("device_not_in_project", "device", address, f"A device answers there.{mask}"))
        if result.get("progmode"):
            out.append(_finding("device_progmode", "device", address, name or "Programming mode is on."))
        if result.get("address_clash"):
            out.append(_finding("device_address_clash", "device", address, name or address))
        if result.get("stopped"):
            out.append(_finding("device_stopped", "device", address, name or address))
        held = result.get("group_addresses")
        if held is not None and address in project.devices:
            linked = {
                ga
                for o in project.devices[address]["objects"]
                if project.objects[o]["flags"]["communication"]
                for ga in project.objects[o]["group_addresses"]
            }
            extra = [ga for ga in held if ga not in linked]
            missing = sorted(linked - set(held))
            if extra or missing:
                parts = []
                if extra:
                    parts.append(f"in the device but not linked in the project: {', '.join(extra[:8])}")
                if missing:
                    parts.append(f"linked in the project but not in the device: {', '.join(missing[:8])}")
                out.append(_finding("device_table_differs", "device", address, f"{name}. {'; '.join(parts)}.".strip()))
        differences = _object_differences(project, address, result.get("held_objects"))
        if differences:
            out.append(
                _finding("device_objects_differ", "device", address, f"{name}. {'; '.join(differences[:6])}.".strip())
            )
        router = result.get("router") or {}
        sides = [("main_groups", "towards the main line"), ("sub_groups", "towards the line")]
        blocked = [
            where for key, where in sides if "blocks all" in (router.get(key + "_low"), router.get(key + "_high"))
        ]
        opened = [where for key, where in sides if router.get(key + "_low") == "passes all"]
        if blocked:
            out.append(
                _finding("coupler_blocks", "device", address, f"{name}. Blocked {' and '.join(blocked)}.".strip())
            )
        elif opened:
            out.append(
                _finding("coupler_open", "device", address, f"{name}. Unfiltered {' and '.join(opened)}.".strip())
            )
        # Only the tables a device cannot work without: an empty interface program or security object is normal
        bad = [
            o
            for o in result.get("objects") or []
            if o.get("type") in NEEDED_TABLES and o.get("load_state") not in (None, "loaded")
        ]
        if bad and not (result.get("load_state") and result["load_state"] != "loaded"):
            states = ", ".join(f"{o['type']}: {o['load_state']}" for o in bad)
            out.append(_finding("device_unloaded", "device", address, f"{name}. {states}.".strip()))
        if result.get("load_state") and result["load_state"] != "loaded":
            out.append(
                _finding("device_unloaded", "device", address, f"{name}. Application: {result['load_state']}.".strip())
            )

    # ---- Home Assistant's entities ----
    if ha.loaded:
        connected = bool(ha.connection.get("connected"))
        seen_sets: dict[tuple, str] = {}
        for entity in ha.entities:
            label = entity["entity_id"] or entity["name"]
            if entity["disabled"]:
                continue
            for address in entity["addresses"]:
                ga = project.group_addresses.get(address["ga"])
                role = address["role"] or "an address"
                subject = f"{label}|{address['ga']}"
                if not project.loaded:
                    continue
                if ga is None:
                    out.append(
                        _finding("ha_ga_not_in_project", "entity", subject, f"{label}: {role} is {address['ga']}.")
                    )
                    continue
                if main_differs(address.get("expects"), ga["dpt"]):
                    out.append(
                        _finding(
                            "ha_dpt_mismatch",
                            "entity",
                            subject,
                            f"{label}: {role} {address['ga']} ({ga['name']}) is DPT {ga['dpt']} in the project; the "
                            f"entity needs DPT {address['expects']}.",
                        )
                    )
                if not ga["objects"]:
                    continue
                if address["use"] == "state" and not ga["answerers"]:
                    out.append(
                        _finding(
                            "ha_state_no_answer", "entity", subject, f"{label}: {role} {address['ga']} ({ga['name']})."
                        )
                    )
                if address["use"] == "write" and not ga["listeners"]:
                    out.append(
                        _finding(
                            "ha_write_no_listener",
                            "entity",
                            subject,
                            f"{label}: {role} {address['ga']} ({ga['name']}).",
                        )
                    )
            if connected and entity["state"] == "unavailable":
                out.append(_finding("ha_unavailable", "entity", label, entity["name"]))
            key = (entity["platform"], tuple(sorted((a["ga"], a["use"]) for a in entity["addresses"])))
            if key[1] and key in seen_sets:
                out.append(_finding("ha_duplicate", "entity", label, f"{label} and {seen_sets[key]}."))
            elif key[1]:
                seen_sets[key] = label
        for entity_id, addresses in ha.exposes.items():
            for ga in addresses:
                if project.loaded and ga not in project.group_addresses:
                    out.append(
                        _finding(
                            "ha_ga_not_in_project",
                            "entity",
                            f"{entity_id}|{ga}",
                            f"{entity_id} is sent to {ga} (expose).",
                        )
                    )
        for entity in ha.stray_entities:
            out.append(
                _finding(
                    "ha_stray_entity",
                    "entity",
                    str(entity["entity_id"]),
                    f"{entity['name']} ({entity['platform']}), state {entity['state'] or 'none'}.",
                )
            )
        for device in ha.stray_devices:
            out.append(_finding("ha_stray_device", "ha_device", device["id"], device["name"]))

    order = {ERROR: 0, WARNING: 1, INFO: 2}
    out.sort(key=lambda f: (order[f["severity"]], f["category"], f["code"], f["subject"]))
    return out
