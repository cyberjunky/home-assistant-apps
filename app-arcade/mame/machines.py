"""Make machines.json: a small index of every machine in MAME, also those this app's build leaves out.

The app recognises the games it can run by the CRC32 of their files (games.json). For any
other archive it can only go by the file name: racherod.7z is the MAME set "racherod", Racing
Hero (bootleg), of the driver sega/segaxbd.cpp. With this index the app tells which driver
an unrecognised file needs, whether that machine works in MAME, and whether it is a slot
machine, a pinball machine (mechanical) or a console, so you know what to ask for.

Reads the GAME()/GAMEL()/CONS()/COMP()/SYST() lines of every driver in src/mame. One row per
machine: [description, company, year, driver ("sega/segaxbd"), parent, BIOS, flags], where flags
holds a letter per fact: b BIOS set, n not working, m mechanical, g gambling (slot machine,
poker, pachinko ...), p pinball, c console or computer, s supports save states.

Usage: python3 machines.py <mame source dir> > machines.json
"""

import json
import re
import sys
from pathlib import Path

from gamelist import GAME_RE, split_args, unquote

# Driver folders of gambling machine makers (fruit machines, slots, pachinko)
GAMBLING_FOLDERS = {
    "ainsworth",
    "aristocrat",
    "barcrest",
    "bfm",
    "igt",
    "jpm",
    "maygay",
    "novomatic",
    "cirsa",
    "subsino",
    "astrocorp",
    "ausnz",
}
# ... or machines whose controls are gambling ones (bet, deal, hold, take, stop reel ...)
INPUT_RE = re.compile(r"INPUT_PORTS_START\s*\(\s*(\w+)\s*\)(.*?)INPUT_PORTS_END", re.S)
INCLUDE_RE = re.compile(r"PORT_INCLUDE\s*\(\s*(\w+)\s*\)")
GAMBLE_RE = re.compile(r"\bIPT_(GAMBLE_\w+|POKER_\w+|SLOT_STOP\w*)")


def gambling_inputs(text: str) -> set[str]:
    """The INPUT_PORTS of a driver that have gambling controls (also through PORT_INCLUDE)."""
    blocks = {m.group(1): m.group(2) for m in INPUT_RE.finditer(text)}
    found: dict[str, bool] = {}

    def check(name: str, seen: frozenset) -> bool:
        if name in found:
            return found[name]
        body = blocks.get(name, "")
        result = bool(GAMBLE_RE.search(body)) or any(
            check(inc, seen | {name}) for inc in INCLUDE_RE.findall(body) if inc not in seen
        )
        found[name] = result
        return result

    return {name for name in blocks if check(name, frozenset())}


def main() -> None:
    src = Path(sys.argv[1]) / "src" / "mame"
    rows: dict[str, list] = {}
    for path in sorted(src.rglob("*.cpp")):
        text = path.read_text(encoding="utf-8", errors="replace")
        if not re.search(r"^\s*(?:/\*.*?\*/\s*)?(GAMEL?|CONS|COMP|SYST)\s*\(", text, re.M):
            continue
        driver = path.relative_to(src).with_suffix("").as_posix()
        folder = driver.split("/", 1)[0]
        gambling_folder = folder in GAMBLING_FOLDERS
        gamble_inputs = gambling_inputs(text)
        # Pinball machines: the pinball folder, and mechanical machines elsewhere (Williams WPC,
        # Gottlieb System 80, Stern Whitestar) whose driver says pinball
        pinball_words = "pinball" in text[:4000].lower()
        for line in text.splitlines():
            m = GAME_RE.match(line)
            if not m or m.group(1) == "GAME_CUSTOM":
                continue
            a = split_args(m.group(2))
            if len(a) < 11:
                continue
            kind = m.group(1)
            # GAME( year, name, parent, machine, input, class, init, monitor, company, fullname, flags )
            # CONS( year, name, parent, compat, machine, input, class, init, company, fullname, flags )
            year, name, parent, company, fullname, flags = a[0], a[1], a[2], a[8], a[9], a[10]
            name = name.strip()
            if not re.fullmatch(r"\w+", name):
                continue
            letters = ""
            if "MACHINE_IS_BIOS_ROOT" in flags:
                letters += "b"
            if "MACHINE_NOT_WORKING" in flags:
                letters += "n"
            if "MACHINE_MECHANICAL" in flags:
                letters += "m"
            if kind in ("GAME", "GAMEL") and (gambling_folder or a[4].strip() in gamble_inputs):
                letters += "g"
            if folder == "pinball" or (pinball_words and "MACHINE_MECHANICAL" in flags):
                letters += "p"
            if kind not in ("GAME", "GAMEL"):
                letters += "c"
            if "MACHINE_SUPPORTS_SAVE" in flags:
                letters += "s"
            parent = parent.strip()
            rows[name] = [
                unquote(fullname),
                unquote(company),
                unquote(year),
                driver,
                "" if parent == "0" else parent,
                "",
                letters,
            ]
    # The BIOS set a machine needs: a BIOS root up its parent chain (przfight -> aristmk5)
    for name, row in rows.items():
        seen, cur = {name}, row[4]
        while cur and cur in rows and cur not in seen:
            if "b" in rows[cur][6]:
                row[5] = cur
                if row[4] == cur:
                    row[4] = ""
                break
            seen.add(cur)
            cur = rows[cur][4]
    json.dump({"machines": rows}, sys.stdout, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


if __name__ == "__main__":
    main()
