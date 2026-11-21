"""One shape for a telegram, wherever it came from: the app's own KNX connection or Home Assistant's.

    {ts, dir: in|out, src, dst, type: write|response|read, raw: hex, text, num, unit, dpt, secure, via: bus|ha}

``text`` is the decoded value as shown, ``num`` its chartable form (booleans as 0/1), ``raw`` the payload
as hex ('' for a read). A payload of up to 6 bits (DPT 1, 2, 3) is one hex byte; ``bits`` is then true.
"""

import struct
import time
from datetime import datetime
from enum import Enum
from typing import Any

from xknx.dpt import DPTArray, DPTBase, DPTBinary
from xknx.telegram import Telegram, TelegramDirection
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

TYPES = {"GroupValueWrite": "write", "GroupValueResponse": "response", "GroupValueRead": "read"}


def dpt_text(main: int | None, sub: int | None) -> str | None:
    """``9.001`` for main 9 sub 1, ``9`` when there is no sub type."""
    if main is None:
        return None
    return f"{main}.{sub:03d}" if sub is not None else str(main)


def dpt_main(dpt: str | None) -> int | None:
    try:
        return int(str(dpt).split(".")[0]) if dpt else None
    except ValueError:
        return None


def transcoder_for(dpt: str | None) -> type[DPTBase] | None:
    """The xknx class for ``9.001``; for a bare main type the first sub type, which reads the same payload."""
    if not dpt:
        return None
    found = DPTBase.parse_transcoder(dpt)
    if found is None and "." not in dpt:
        found = DPTBase.parse_transcoder(f"{dpt}.001")
    return found


def plain(value: Any) -> Any:
    """A decoded value as something JSON can carry."""
    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, Enum):
        return value.name.lower()
    if hasattr(value, "as_dict"):
        return {k: plain(v) for k, v in value.as_dict().items()}
    if isinstance(value, dict):
        return {str(k): plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [plain(v) for v in value]
    return str(value)


def as_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".") if abs(value) < 1e6 else f"{value:.6g}"
    if isinstance(value, dict):
        return ", ".join(f"{k}={as_text(v)}" for k, v in value.items())
    if isinstance(value, list):
        return ", ".join(str(as_text(v)) for v in value)
    return str(value)


def as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int | float):
        return float(value)
    return None


def decode(dpt: str | None, raw: str, bits: bool | None = None) -> tuple[str | None, float | None, str | None] | None:
    """(text, num, unit) of a hex payload read as ``dpt``; None when the type is unknown or the payload
    does not fit it. ``bits`` says whether the payload was a small one (up to 6 bits); when not known, the
    type decides."""
    transcoder = transcoder_for(dpt)
    if transcoder is None or not raw:
        return None
    small = transcoder.payload_type is DPTBinary
    if bits is not None and bits != small:
        return None
    try:
        data = bytes.fromhex(raw)
        payload: DPTBinary | DPTArray = DPTBinary(data[0]) if small else DPTArray(data)
        decoded = transcoder.from_knx(payload)
    except Exception:
        # A payload that does not match the type: a wrong length, a value out of range
        return None
    value = plain(decoded)
    # A switch reads as on/off, a mode as its name: the number behind it is what a chart can draw
    num = as_number(decoded.value if isinstance(decoded, Enum) else value)
    if num is None and small and transcoder.dpt_main_number == 1:
        num = float(data[0] & 1)
    return as_text(value), num, transcoder.unit or None


def guess(raw: str, bits: bool | None) -> str | None:
    """What a payload of an address without a known type most likely says, by its length. KNX types are
    bound to a size, and a few sizes are nearly always the same type: one bit a switch, two bytes a DPT 9
    measurement, four a DPT 14 one, fourteen text. Shown as a guess, never stored as the value."""
    if not raw:
        return None
    try:
        data = bytes.fromhex(raw)
    except ValueError:
        return None
    if bits or (bits is None and len(data) == 1 and data[0] <= 1):
        return "on" if data[0] & 1 else "off" if data[0] <= 1 else str(data[0] & 0x3F)
    if len(data) == 1:
        return f"{data[0]} ({round(data[0] / 255 * 100)} %)"
    if len(data) == 2:
        word = (data[0] << 8) | data[1]
        mantissa = word & 0x07FF
        if word & 0x8000:
            mantissa -= 0x800
        return as_text(0.01 * mantissa * (2 ** ((word & 0x7800) >> 11)))
    if len(data) == 4:
        value = struct.unpack(">f", data)[0]
        if value == value and abs(value) < 1e9 and (value == 0 or abs(value) > 1e-6):
            return as_text(value)
        return str(int.from_bytes(data, "big"))
    if len(data) == 14:
        text = data.rstrip(b"\x00 ")
        if text and all(32 <= c < 127 for c in text):
            return text.decode("ascii")
    return None


def from_xknx(telegram: Telegram, dpt: str | None, flags: Any = None) -> dict[str, Any] | None:
    """A group telegram from the app's own connection; None for anything that is not one. ``flags`` are
    the frame's own (xknx's CEMIFlags) when the connection kept them: ``rep`` is true for a frame that was
    sent again because nobody acknowledged it, ``hops`` how many couplers it may still pass (6 when it
    passed none yet)."""
    payload = telegram.payload
    kind = TYPES.get(type(payload).__name__)
    if kind is None:
        return None
    raw, bits, text, num, unit = "", None, None, None, None
    if isinstance(payload, GroupValueWrite | GroupValueResponse):
        value = payload.value
        bits = isinstance(value, DPTBinary)
        raw = f"{value.value:02x}" if bits else bytes(value.value).hex()
        if decoded := decode(dpt, raw, bits):
            text, num, unit = decoded
    elif not isinstance(payload, GroupValueRead):
        return None
    return {
        "ts": time.time(),
        "dir": "out" if telegram.direction is TelegramDirection.OUTGOING else "in",
        "src": str(telegram.source_address),
        "dst": str(telegram.destination_address),
        "type": kind,
        "raw": raw,
        "bits": bits,
        "text": text,
        "num": num,
        "unit": unit,
        "dpt": dpt,
        "secure": telegram.data_secure,
        "via": "bus",
        **(
            {"rep": bool(flags.repeat_on_error), "hops": int(flags.hop_count), "prio": flags.priority.name.lower()}
            if flags is not None and telegram.direction is TelegramDirection.INCOMING
            else {}
        ),
    }


def from_ha(t: dict[str, Any]) -> dict[str, Any] | None:
    """A telegram as Home Assistant's KNX integration reports it (its TelegramDict)."""
    kind = TYPES.get(str(t.get("telegramtype")))
    if kind is None or not t.get("destination"):
        return None
    payload = t.get("payload")
    bits = isinstance(payload, int)
    if payload is None:
        raw, bits = "", None
    elif bits:
        raw = f"{payload & 0xFF:02x}"
    else:
        try:
            raw = bytes(payload).hex()
        except (TypeError, ValueError):
            raw = ""
    try:
        ts = datetime.fromisoformat(str(t.get("timestamp"))).timestamp()
    except ValueError:
        ts = time.time()
    value = plain(t.get("value"))
    num = as_number(value)
    if num is None and bits and t.get("dpt_main") == 1:
        num = float(payload & 1)
    return {
        "ts": ts,
        "dir": "out" if str(t.get("direction")).lower().startswith("out") else "in",
        "src": str(t.get("source") or ""),
        "dst": str(t["destination"]),
        "type": kind,
        "raw": raw,
        "bits": bits,
        "text": as_text(value),
        "num": num,
        "unit": t.get("unit") or None,
        "dpt": dpt_text(t.get("dpt_main"), t.get("dpt_sub")),
        "secure": t.get("data_secure"),
        "via": "ha",
    }
