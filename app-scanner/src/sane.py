"""SANE through its own command, scanimage: the scanners, every option a scanner has, and scanning.

scanimage runs as a separate process for every question: a scanner driver that crashes or hangs
then can't take the app with it, and a scan is cancelled by a signal. What scanimage prints with
--all-options is read here into a form the browser can show (see parse_options).
"""

import asyncio
import contextlib
import logging
import re
import signal
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

SCANIMAGE = "scanimage"
LIST_TIMEOUT = 60
OPTIONS_TIMEOUT = 45
# The scan area: scanimage names these with one letter
GEOMETRY = {"l": "Left", "t": "Top", "x": "Width", "y": "Height"}
# What a driver says, in plain words
PROBLEMS = (
    ("out of documents", "The feeder is empty"),
    ("feeder jammed", "The paper is jammed in the feeder"),
    ("cover is open", "The scanner's cover is open"),
    ("device busy", "The scanner is busy: another program or a scan from its own buttons uses it"),
    ("access to resource has been denied", "The app may not use this scanner (access denied)"),
    ("out of memory", "The scanner or the app ran out of memory: try a lower resolution"),
    ("error during device i/o", "The scanner stopped answering: is it still connected and switched on?"),
    ("invalid argument", "The scanner did not accept these settings"),
    ("operation was cancelled", "Cancelled"),
    ("assertion", "The scanner's driver stopped on these settings: try other ones"),
)


class ScanError(Exception):
    pass


def friendly(message: str) -> str:
    """A driver's message in plain words, with the driver's own words kept when they say more."""
    text = message.strip()
    text = re.sub(r"^scanimage:\s*", "", text)
    lower = text.lower()
    for needle, plain in PROBLEMS:
        if needle in lower:
            return plain
    return text[:1].upper() + text[1:] if text else "The scan failed"


# =====================================================================
# The scanners
# =====================================================================
def parse_devices(text: str) -> list[dict]:
    """The output of scanimage -f "%d|%v|%m|%t%n"."""
    devices = []
    for line in text.splitlines():
        parts = line.split("|")
        if len(parts) < 4 or (":" not in parts[0] and not parts[0]):
            continue
        name, vendor, model, kind = (p.strip() for p in parts[:4])
        backend = name.split(":", 1)[0]
        label = " ".join(x for x in (vendor, model) if x and x.lower() != "noname") or name
        if backend == "test":
            label = f"Test scanner {name.rsplit(':', 1)[-1]}"
        devices.append({"id": name, "vendor": vendor, "model": model, "type": kind, "backend": backend, "label": label})
    return devices


async def _run(args: list[str], timeout: float) -> tuple[str, str]:
    """Run scanimage and return what it printed. Raises ScanError when it can't run or takes too long."""
    try:
        proc = await asyncio.create_subprocess_exec(
            SCANIMAGE, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
    except OSError as e:
        raise ScanError(f"Cannot start scanimage: {e}") from e
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise ScanError("The scanner did not answer in time") from None
    return out.decode(errors="replace"), err.decode(errors="replace")


async def list_devices() -> list[dict]:
    out, err = await _run(["-f", "%d|%v|%m|%t%n"], LIST_TIMEOUT)
    devices = parse_devices(out)
    if not devices and err.strip():
        logger.debug(f"scanimage -f said: {err.strip()[:500]}")
    return devices


# =====================================================================
# A scanner's options
# =====================================================================
_OPTION = re.compile(r"^ {4}(-[a-zA-Z]|--[A-Za-z0-9][\w-]*)(.*)$")
_GROUP = re.compile(r"^ {2}(\S.*):\s*$")
_RANGE = re.compile(
    r"^(-?[\d.]+(?:e[+-]?\d+)?)\.\.(-?[\d.]+(?:e[+-]?\d+)?)([^\s,(]*)(,\.\.\.)?(?: \(in steps of ([\d.e+-]+)\))?$"
)
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$")
_NUMBER_UNIT = re.compile(r"^(-?\d+(?:\.\d+)?)([^\d.].*)$")
_FLAGS = (" [advanced]", " [inactive]", " [hardware]", " [read-only]")


def _number(text: str) -> float | int:
    value = float(text)
    return int(value) if value == int(value) and "." not in text and "e" not in text.lower() else value


def _split_value(rest: str, known: Callable[[str, str], bool]) -> tuple[str, str | None]:
    """Split "constraint [value]" at the right " [": a value may hold brackets itself."""
    if not rest.endswith("]"):
        return rest, None
    at = len(rest)
    while True:
        at = rest.rfind(" [", 0, at)
        if at < 0:
            return rest, None
        constraint, value = rest[:at], rest[at + 2 : -1]
        if known(constraint, value) or " [" not in rest[:at]:
            return constraint, value


def _parse_option(flag: str, rest: str) -> dict:
    name = flag.lstrip("-")
    option: dict = {
        "name": name,
        "flag": flag,
        "title": GEOMETRY.get(name) if flag[1] != "-" else name.replace("-", " ").capitalize(),
        "type": "string",
        "active": True,
        "readonly": False,
        "advanced": False,
        "auto": False,
        "array": False,
        "unit": "",
        "value": None,
        "description": "",
    }
    # The marks scanimage puts last
    stripped = True
    while stripped:
        stripped = False
        for mark in _FLAGS:
            if rest.endswith(mark):
                rest = rest[: -len(mark)]
                stripped = True
                if mark == " [advanced]":
                    option["advanced"] = True
                elif mark == " [inactive]":
                    option["active"] = False
                else:
                    option["readonly"] = True

    if rest.startswith("[=("):
        option["type"] = "bool"
        end = rest.find(")]")
        choices = rest[3:end].split("|") if end > 0 else ["yes", "no"]
        option["auto"] = "auto" in choices
        value = rest[end + 2 :].strip() if end > 0 else ""
        if value.startswith("[") and value.endswith("]"):
            option["value"] = value[1:-1]
        return option
    if not rest.strip():
        # No value to give: a button, such as "eject the page"
        option["type"] = "button"
        return option

    def known(constraint: str, value: str) -> bool:
        choices = constraint.strip().split("|")
        return len(choices) > 1 and (
            value in choices or (bool(_NUMBER_UNIT.match(choices[-1])) and value in constraint)
        )

    constraint, value = _split_value(rest.strip(), known)
    option["value"] = value
    if constraint.startswith("auto|"):
        option["auto"] = True
        constraint = constraint[5:].lstrip("|")
    if constraint.endswith(",..."):
        # A list of numbers (a gamma table): not something to type in a form
        option["array"] = True
        constraint = constraint[:-4]
    if constraint in ("<int>", "<float>", "<string>"):
        option["type"] = constraint.strip("<>")
        return option
    match = _RANGE.match(constraint)
    if match:
        low, high, unit, array, step = match.groups()
        option.update(type="number", min=_number(low), max=_number(high), unit=unit or "")
        option["array"] = option["array"] or bool(array)
        if step:
            option["step"] = _number(step)
        return option
    choices = constraint.split("|")
    # Numbers carry their unit after the last one: 75|150|300dpi
    last = _NUMBER_UNIT.match(choices[-1])
    if last and all(_NUMBER.match(c) for c in choices[:-1]):
        choices[-1] = last.group(1)
        option["unit"] = last.group(2)
    option["type"] = "choice"
    option["choices"] = choices
    option["numeric"] = all(_NUMBER.match(c) for c in choices)
    return option


def parse_options(text: str) -> list[dict]:
    """What scanimage --all-options prints, as groups of options.

    Each option: name, flag (--mode, -x), type (choice, number, bool, int, float, string, button),
    its choices or range with the unit, its value now, and whether it can be set now.
    """
    start = text.find("ptions specific to device")
    if start < 0:
        return []
    groups: list[dict] = []
    current: dict | None = None
    option: dict | None = None
    for line in text[start:].splitlines()[1:]:
        if not line.strip():
            continue
        match = _GROUP.match(line)
        if match and not line.startswith("    "):
            current = {"title": match.group(1), "options": []}
            groups.append(current)
            option = None
            continue
        match = _OPTION.match(line)
        if match:
            if current is None:
                current = {"title": "", "options": []}
                groups.append(current)
            option = _parse_option(match.group(1), match.group(2))
            current["options"].append(option)
            continue
        if option is not None and line.startswith("        "):
            option["description"] = (option["description"] + " " + line.strip()).strip()
    return [g for g in groups if g["options"]]


def option_args(values: dict[str, str], groups: list[dict] | None = None) -> list[str]:
    """scanimage's arguments for the chosen values. With groups: only what the scanner has, in its order."""
    if groups is None:
        names = [(name, ("-" if len(name) == 1 else "--") + name) for name in values]
    else:
        names = [(o["name"], o["flag"]) for g in groups for o in g["options"] if o["name"] in values]
    args: list[str] = []
    for name, flag in names:
        value = str(values[name])
        if flag.startswith("--"):
            args.append(f"{flag}={value}")
        else:
            args.extend([flag, value])
    return args


def settable(groups: list[dict], values: dict[str, str]) -> dict[str, str]:
    """The chosen values the scanner takes now: an option it has, switched on, not read-only, no list."""
    out = {}
    for g in groups:
        for o in g["options"]:
            if o["name"] in values and o["active"] and not o["readonly"] and not o["array"] and o["type"] != "button":
                out[o["name"]] = str(values[o["name"]])
    return out


_REJECTED = re.compile(
    r"attempted to set (?:inactive|readonly|read-only) option (\S+)|setting of option --?(\S+) failed"
)
_UNKNOWN = re.compile(r"unrecogni[sz]ed option '--?([^'=]+)|invalid option -- '(\w)'")


async def read_options(device: str, values: dict[str, str] | None = None) -> dict:
    """The scanner's options with the chosen values applied.

    Choosing one option changes others (the feeder has another paper size, colour another depth),
    so the scanner is asked again with what was chosen. A value it refuses is left out and named in
    "dropped"; a value it changed (rounded) shows in the option's own value.
    """
    values = {k: str(v) for k, v in (values or {}).items()}
    out, err = await _run(["-d", device, "-A"], OPTIONS_TIMEOUT)
    groups = parse_options(out)
    if not groups:
        raise ScanError(friendly(_last_message(err) or "The scanner gave no options"))
    dropped: dict[str, str] = {}
    applied: dict[str, str] = {}
    for _ in range(8):
        wanted = {k: v for k, v in settable(groups, values).items() if k not in dropped}
        if wanted == applied:
            break
        out, err = await _run(["-d", device, *option_args(wanted, groups), "-A"], OPTIONS_TIMEOUT)
        refused = _refused(err)
        if refused:
            for name in refused:
                dropped[name] = friendly(_message_about(err, name))
            continue
        parsed = parse_options(out)
        if not parsed:
            raise ScanError(friendly(_last_message(err) or "The scanner gave no options"))
        groups = parsed
        applied = wanted
    return {"device": device, "groups": groups, "applied": applied, "dropped": dropped, "notes": _notes(err)}


def _messages(err: str) -> list[str]:
    return [line.strip() for line in err.splitlines() if line.startswith("scanimage:")]


def _last_message(err: str) -> str:
    messages = [m for m in _messages(err) if "rounded value" not in m]
    return messages[-1] if messages else ""


def _message_about(err: str, name: str) -> str:
    for m in _messages(err):
        if name in m:
            return m
    return ""


def _notes(err: str) -> list[str]:
    """What the scanner changed by itself: "rounded value of resolution from 5000 to 1200"."""
    return [friendly(m) for m in _messages(err) if "rounded value" in m]


def _refused(err: str) -> list[str]:
    names = []
    for m in _messages(err) + [line for line in err.splitlines() if "option" in line and "scanimage" in line]:
        for match in (*_REJECTED.finditer(m), *_UNKNOWN.finditer(m)):
            name = next((g for g in match.groups() if g), "")
            if name and name not in names:
                names.append(name)
    return names


def current_values(groups: list[dict]) -> dict[str, str]:
    """Every option's value now, by name."""
    return {o["name"]: o["value"] for g in groups for o in g["options"] if o["value"] is not None and o["active"]}


def find_option(groups: list[dict], name: str) -> dict | None:
    for g in groups:
        for o in g["options"]:
            if o["name"] == name:
                return o
    return None


def preview_values(groups: list[dict], values: dict[str, str], dpi: int = 75) -> dict[str, str]:
    """The values for a preview: the whole glass, at the lowest resolution that still shows the page."""
    out = {
        k: v for k, v in values.items() if k not in GEOMETRY and k not in ("resolution", "x-resolution", "y-resolution")
    }
    resolution = find_option(groups, "resolution")
    if resolution is not None and resolution["active"]:
        if resolution["type"] == "choice" and resolution.get("numeric"):
            numbers = sorted(float(c) for c in resolution["choices"])
            pick = next((n for n in numbers if n >= dpi), numbers[-1])
            out["resolution"] = str(int(pick) if pick == int(pick) else pick)
        elif resolution["type"] == "number":
            out["resolution"] = str(int(min(max(dpi, resolution["min"]), resolution["max"])))
    for name in ("l", "t"):
        o = find_option(groups, name)
        if o is not None and o["active"] and o["type"] == "number":
            out[name] = str(o["min"])
    for name, origin in (("x", "l"), ("y", "t")):
        o = find_option(groups, name)
        if o is None or not o["active"] or o["type"] != "number":
            continue
        # scanimage shows the width that is left from the chosen left edge: the glass is both together
        start = find_option(groups, origin)
        offset = float(start["value"]) if start is not None and start["active"] and _is_number(start["value"]) else 0
        out[name] = _trim(o["max"] + offset)
    return out


def _is_number(text: str | None) -> bool:
    return bool(text) and bool(_NUMBER.match(str(text)))


def _trim(value: float) -> str:
    return str(int(value)) if value == int(value) else f"{value:.3f}".rstrip("0").rstrip(".")


def bed_size(groups: list[dict]) -> tuple[float, float, str] | None:
    """The size of the glass (or the feeder's widest page) and its unit, when the scanner tells it."""
    size = []
    unit = "mm"
    for name, origin in (("x", "l"), ("y", "t")):
        o = find_option(groups, name)
        if o is None or o["type"] != "number":
            return None
        start = find_option(groups, origin)
        offset = float(start["value"]) if start is not None and start["active"] and _is_number(start["value"]) else 0
        size.append(o["max"] + offset)
        unit = o["unit"] or unit
    return size[0], size[1], unit


# =====================================================================
# Scanning
# =====================================================================
_PROGRESS = re.compile(r"Progress: ([\d.]+)%")
_PAGE = re.compile(r"Scanning page (\d+)")


class Scan:
    """One run of scanimage: a page, or every page in the feeder."""

    def __init__(self) -> None:
        self.proc: asyncio.subprocess.Process | None = None
        self.cancelled = False

    async def run(
        self,
        device: str,
        args: list[str],
        folder: Path,
        batch: bool,
        count: int,
        on_progress: Callable[[int, float], None],
        on_page: Callable[[Path], Awaitable[None]],
    ) -> int:
        """Scan into folder as PNM files and hand every finished page to on_page. Returns the number of pages.

        Raises ScanError with what went wrong; the pages before that are kept.
        """
        folder.mkdir(parents=True, exist_ok=True)
        command = ["-d", device, *args, "--format=pnm", "-p"]
        if batch:
            command += [f"--batch={folder}/page-%04d.pnm", "--batch-print"]
            if count > 0:
                command.append(f"--batch-count={count}")
        else:
            command += ["-o", str(folder / "page-0001.pnm")]
        try:
            self.proc = await asyncio.create_subprocess_exec(
                SCANIMAGE, *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
        except OSError as e:
            raise ScanError(f"Cannot start scanimage: {e}") from e
        pages = 0
        messages: list[str] = []
        page = 1

        async def read_stderr() -> None:
            nonlocal page
            assert self.proc is not None and self.proc.stderr is not None
            buffer = ""
            while True:
                chunk = await self.proc.stderr.read(512)
                if not chunk:
                    break
                buffer += chunk.decode(errors="replace")
                # Progress lines end in a carriage return, the rest in a newline
                parts = re.split(r"[\r\n]", buffer)
                buffer = parts.pop()
                for part in parts:
                    match = _PAGE.search(part)
                    if match:
                        page = int(match.group(1))
                        on_progress(page, 0.0)
                    found = _PROGRESS.findall(part)
                    if found:
                        on_progress(page, float(found[-1]))
                    if part.startswith("scanimage:"):
                        messages.append(part.strip())
            if buffer.startswith("scanimage:"):
                messages.append(buffer.strip())

        async def read_stdout() -> None:
            nonlocal pages
            assert self.proc is not None and self.proc.stdout is not None
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    break
                name = line.decode(errors="replace").strip()
                if batch and name.endswith(".pnm") and Path(name).is_file():
                    pages += 1
                    await on_page(Path(name))

        on_progress(1, 0.0)
        await asyncio.gather(read_stderr(), read_stdout())
        code = await self.proc.wait()
        self.proc = None
        if not batch:
            single = folder / "page-0001.pnm"
            # A scan that failed halfway leaves a part of the page: not a page
            failed = [m for m in messages if "rounded value" not in m]
            if single.is_file() and single.stat().st_size > 0 and not failed and not self.cancelled and code == 0:
                pages = 1
                await on_page(single)
        if self.cancelled:
            raise ScanError("Cancelled")
        problems = [m for m in messages if "rounded value" not in m]
        if batch:
            # The feeder running empty is how a batch ends, not a problem - unless it was empty from the start
            ended = [m for m in problems if "out of documents" in m.lower()]
            problems = [m for m in problems if m not in ended]
            if ended and pages == 0 and not problems:
                raise ScanError("The feeder is empty")
        if problems:
            raise ScanError(friendly(problems[-1]))
        if code != 0 and pages == 0:
            raise ScanError(f"The scan failed (scanimage stopped with code {code})")
        return pages

    async def cancel(self) -> None:
        """Stop the scan: scanimage tells the scanner to stop on the first signal."""
        self.cancelled = True
        proc = self.proc
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            proc.send_signal(signal.SIGTERM)
        try:
            await asyncio.wait_for(asyncio.shield(proc.wait()), 8)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
