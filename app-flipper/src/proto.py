"""The Flipper's RPC messages, encoded and decoded as protobuf without a protobuf library.

The messages are those of github.com/flipperdevices/flipperzero-protobuf (version 0.25, the one firmware 1.x
is built with). A message is a plain dict here; the tables below give each field its number and type. Only what
the app uses is listed: a field or message that is not in a table is skipped when read.

Types: u = unsigned number, i = signed number, b = bool, s = text, y = bytes, a name = that message,
and a leading * = repeated.
"""

EMPTY: dict = {}

SCHEMA: dict[str, dict[int, tuple[str, str]]] = {
    "Empty": {},
    "Main": {
        1: ("command_id", "u"),
        2: ("command_status", "u"),
        3: ("has_next", "b"),
        4: ("empty", "Empty"),
        19: ("stop_session", "Empty"),
        5: ("system_ping_request", "Ping"),
        6: ("system_ping_response", "Ping"),
        31: ("system_reboot_request", "Reboot"),
        32: ("system_device_info_request", "Empty"),
        33: ("system_device_info_response", "KeyValue"),
        35: ("system_get_datetime_request", "Empty"),
        36: ("system_get_datetime_response", "DateTimeBox"),
        37: ("system_set_datetime_request", "DateTimeBox"),
        38: ("system_play_audiovisual_alert_request", "Empty"),
        39: ("system_protobuf_version_request", "Empty"),
        40: ("system_protobuf_version_response", "ProtobufVersion"),
        44: ("system_power_info_request", "Empty"),
        45: ("system_power_info_response", "KeyValue"),
        28: ("storage_info_request", "Path"),
        29: ("storage_info_response", "StorageInfo"),
        24: ("storage_stat_request", "Path"),
        25: ("storage_stat_response", "FileBox"),
        7: ("storage_list_request", "ListRequest"),
        8: ("storage_list_response", "ListResponse"),
        9: ("storage_read_request", "Path"),
        10: ("storage_read_response", "FileBox"),
        11: ("storage_write_request", "WriteRequest"),
        12: ("storage_delete_request", "DeleteRequest"),
        13: ("storage_mkdir_request", "Path"),
        14: ("storage_md5sum_request", "Path"),
        15: ("storage_md5sum_response", "Md5sum"),
        30: ("storage_rename_request", "RenameRequest"),
        16: ("app_start_request", "AppStart"),
        17: ("app_lock_status_request", "Empty"),
        18: ("app_lock_status_response", "Locked"),
        63: ("app_get_error_request", "Empty"),
        64: ("app_get_error_response", "AppError"),
        20: ("gui_start_screen_stream_request", "Empty"),
        21: ("gui_stop_screen_stream_request", "Empty"),
        22: ("gui_screen_frame", "ScreenFrame"),
        23: ("gui_send_input_event_request", "InputEvent"),
        66: ("desktop_is_locked_request", "Empty"),
        67: ("desktop_unlock_request", "Empty"),
        70: ("desktop_status", "Locked"),
    },
    "Ping": {1: ("data", "y")},
    "Reboot": {1: ("mode", "u")},
    "KeyValue": {1: ("key", "s"), 2: ("value", "s")},
    "DateTime": {
        1: ("hour", "u"),
        2: ("minute", "u"),
        3: ("second", "u"),
        4: ("day", "u"),
        5: ("month", "u"),
        6: ("year", "u"),
        7: ("weekday", "u"),
    },
    "DateTimeBox": {1: ("datetime", "DateTime")},
    "ProtobufVersion": {1: ("major", "u"), 2: ("minor", "u")},
    "Path": {1: ("path", "s")},
    "StorageInfo": {1: ("total_space", "u"), 2: ("free_space", "u")},
    "File": {1: ("type", "u"), 2: ("name", "s"), 3: ("size", "u"), 4: ("data", "y"), 5: ("md5sum", "s")},
    "FileBox": {1: ("file", "File")},
    "ListRequest": {1: ("path", "s"), 2: ("include_md5", "b"), 3: ("filter_max_size", "u")},
    "ListResponse": {1: ("file", "*File")},
    "WriteRequest": {1: ("path", "s"), 2: ("file", "File")},
    "DeleteRequest": {1: ("path", "s"), 2: ("recursive", "b")},
    "Md5sum": {1: ("md5sum", "s")},
    "RenameRequest": {1: ("old_path", "s"), 2: ("new_path", "s")},
    "AppStart": {1: ("name", "s"), 2: ("args", "s")},
    "Locked": {1: ("locked", "b")},
    "AppError": {1: ("code", "u"), 2: ("text", "s")},
    "ScreenFrame": {1: ("data", "y"), 2: ("orientation", "u")},
    "InputEvent": {1: ("key", "u"), 2: ("type", "u")},
}

# PB.CommandStatus
STATUS = {
    0: "OK",
    1: "ERROR",
    2: "ERROR_DECODE",
    3: "ERROR_NOT_IMPLEMENTED",
    4: "ERROR_BUSY",
    14: "ERROR_CONTINUOUS_COMMAND_INTERRUPTED",
    15: "ERROR_INVALID_PARAMETERS",
    5: "ERROR_STORAGE_NOT_READY",
    6: "ERROR_STORAGE_EXIST",
    7: "ERROR_STORAGE_NOT_EXIST",
    8: "ERROR_STORAGE_INVALID_PARAMETER",
    9: "ERROR_STORAGE_DENIED",
    10: "ERROR_STORAGE_INVALID_NAME",
    11: "ERROR_STORAGE_INTERNAL",
    12: "ERROR_STORAGE_NOT_IMPLEMENTED",
    13: "ERROR_STORAGE_ALREADY_OPEN",
    18: "ERROR_STORAGE_DIR_NOT_EMPTY",
    16: "ERROR_APP_CANT_START",
    17: "ERROR_APP_SYSTEM_LOCKED",
    21: "ERROR_APP_NOT_RUNNING",
    22: "ERROR_APP_CMD_ERROR",
    19: "ERROR_VIRTUAL_DISPLAY_ALREADY_STARTED",
    20: "ERROR_VIRTUAL_DISPLAY_NOT_STARTED",
}
STATUS_CODE = {name: code for code, name in STATUS.items()}
# What a status means to the person at the screen
STATUS_TEXT = {
    "ERROR": "the Flipper reported an error",
    "ERROR_BUSY": "the Flipper is busy",
    "ERROR_INVALID_PARAMETERS": "the Flipper did not accept the request",
    "ERROR_STORAGE_NOT_READY": "the SD card is not ready",
    "ERROR_STORAGE_EXIST": "that name already exists",
    "ERROR_STORAGE_NOT_EXIST": "no such file or folder",
    "ERROR_STORAGE_DENIED": "access denied",
    "ERROR_STORAGE_INVALID_NAME": "not a valid name",
    "ERROR_STORAGE_INTERNAL": "storage error on the Flipper",
    "ERROR_STORAGE_ALREADY_OPEN": "the file is in use on the Flipper",
    "ERROR_STORAGE_DIR_NOT_EMPTY": "the folder is not empty",
    "ERROR_APP_CANT_START": "the Flipper could not start the app",
    "ERROR_APP_SYSTEM_LOCKED": "another app is running on the Flipper",
    "ERROR_APP_NOT_RUNNING": "no app is running",
}

KEYS = {"up": 0, "down": 1, "right": 2, "left": 3, "ok": 4, "back": 5}
INPUT_PRESS, INPUT_RELEASE, INPUT_SHORT, INPUT_LONG, INPUT_REPEAT = 0, 1, 2, 3, 4
FILE, DIR = 0, 1
REBOOT_OS = 0

_HEAD = ("command_id", "command_status", "has_next")
_MASK64 = (1 << 64) - 1


class ProtoError(ValueError):
    """Bytes that are not a valid message."""


def encode_varint(value: int) -> bytes:
    value &= _MASK64
    out = bytearray()
    while value > 0x7F:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value)
    return bytes(out)


def decode_varint(data: bytes | bytearray | memoryview, pos: int = 0) -> tuple[int, int] | None:
    """The number at pos and the position after it; None when the data ends inside the number."""
    value = shift = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7
        if shift > 63:
            raise ProtoError("number too long")
    return None


def encode(kind: str, message: dict) -> bytes:
    out = bytearray()
    for number, (name, ftype) in sorted(SCHEMA[kind].items()):
        if name not in message:
            continue
        value = message[name]
        repeated = ftype.startswith("*")
        ftype = ftype.lstrip("*")
        for item in value if repeated else [value]:
            if ftype in ("u", "i", "b"):
                if not item:
                    continue  # proto3 leaves a zero out
                out += encode_varint(number << 3) + encode_varint(int(item))
            else:
                if ftype == "s":
                    body = str(item).encode()
                elif ftype == "y":
                    body = bytes(item)
                else:
                    body = encode(ftype, item)
                if not body and ftype in ("s", "y"):
                    continue
                # A message is written even when empty: its presence is what says which request this is
                out += encode_varint(number << 3 | 2) + encode_varint(len(body)) + body
    return bytes(out)


def decode(kind: str, data: bytes | bytearray | memoryview) -> dict:
    fields = SCHEMA[kind]
    message: dict = {}
    pos = 0
    data = bytes(data)
    while pos < len(data):
        head = decode_varint(data, pos)
        if head is None:
            raise ProtoError("cut off")
        tag, pos = head
        number, wire = tag >> 3, tag & 7
        if wire == 0:
            got = decode_varint(data, pos)
            if got is None:
                raise ProtoError("cut off")
            raw, pos = got
        elif wire == 2:
            got = decode_varint(data, pos)
            if got is None or got[1] + got[0] > len(data):
                raise ProtoError("cut off")
            raw, pos = data[got[1] : got[1] + got[0]], got[1] + got[0]
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
            if pos + size > len(data):
                raise ProtoError("cut off")
            raw, pos = None, pos + size
        else:
            raise ProtoError(f"wire type {wire}")
        if number not in fields or raw is None:
            continue
        name, ftype = fields[number]
        repeated = ftype.startswith("*")
        ftype = ftype.lstrip("*")
        if ftype in ("u", "i", "b"):
            if not isinstance(raw, int):
                raise ProtoError(f"{kind}.{name}: not a number")
            value = bool(raw) if ftype == "b" else raw - (1 << 64) if ftype == "i" and raw >> 63 else raw
        else:
            if isinstance(raw, int):
                raise ProtoError(f"{kind}.{name}: not bytes")
            value = raw.decode("utf-8", "replace") if ftype == "s" else raw if ftype == "y" else decode(ftype, raw)
        if repeated:
            message.setdefault(name, []).append(value)
        else:
            message[name] = value
    return message


def frame(message: dict) -> bytes:
    """A PB.Main ready to send: its length, then the message."""
    body = encode("Main", message)
    return encode_varint(len(body)) + body


def take_frame(buffer: bytearray) -> dict | None:
    """Take one whole PB.Main off the front of the buffer; None when it has not all arrived yet."""
    head = decode_varint(buffer)
    if head is None:
        return None
    size, start = head
    if size > 65536:
        raise ProtoError("message too large")
    if len(buffer) < start + size:
        return None
    message = decode("Main", buffer[start : start + size])
    del buffer[: start + size]
    return message


def content(message: dict) -> tuple[str, dict]:
    """Which request or response a PB.Main carries, and its fields."""
    for name, value in message.items():
        if name not in _HEAD:
            return name, value
    return "empty", EMPTY


def status_name(message: dict) -> str:
    code = message.get("command_status", 0)
    return STATUS.get(code, f"ERROR_{code}")
