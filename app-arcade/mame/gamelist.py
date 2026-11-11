"""Make games.json: the machines in the MAME drivers this app's MAME build includes.

Reads the GAME()/GAMEL() lines and ROM_START ... ROM_END blocks of the driver sources,
so the app can name the games, show their year and maker, and recognise an uploaded zip
by the CRC32 of the files in it (zip files store those, nothing has to be unpacked).

Usage: python3 gamelist.py <mame source dir> <driver .cpp> [...] > games.json
"""

import json
import re
import sys
from pathlib import Path

GAME_RE = re.compile(r"^\s*(GAMEL?|GAME_CUSTOM)\s*\((.*)\)\s*(//.*)?$")
ROM_START_RE = re.compile(r"^\s*ROM_START\s*\(\s*(\w+)\s*\)")
ROM_LOAD_RE = re.compile(
    r"ROMX?_LOAD\w*\s*\(\s*\"([^\"]+)\"\s*,\s*[^,]+,\s*([^,]+),\s*(?:CRC\(\s*([0-9a-fA-F]{8})\s*\)|(NO_DUMP))"
)
FLAG_NAMES = {
    "MACHINE_NOT_WORKING": "not working",
    "MACHINE_IMPERFECT_SOUND": "imperfect sound",
    "MACHINE_NO_SOUND": "no sound",
    "MACHINE_IMPERFECT_GRAPHICS": "imperfect graphics",
    "MACHINE_IMPERFECT_COLORS": "imperfect colours",
    "MACHINE_WRONG_COLORS": "wrong colours",
    "MACHINE_UNEMULATED_PROTECTION": "protection not emulated",
    "MACHINE_IMPERFECT_CONTROLS": "imperfect controls",
    "MACHINE_MECHANICAL": "mechanical",
    "MACHINE_IS_BIOS_ROOT": "bios",
}


def split_args(text: str) -> list[str]:
    """Split macro arguments on top-level commas, keeping quoted strings whole."""
    args, depth, quote, cur = [], 0, False, ""
    for i, ch in enumerate(text):
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            quote = not quote
        if not quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                args.append(cur.strip())
                cur = ""
                continue
        cur += ch
    args.append(cur.strip())
    return args


def unquote(s: str) -> str:
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s.replace('\\"', '"')


def parse(path: Path) -> tuple[dict, dict]:
    games, roms = {}, {}
    current = None
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        m = ROM_START_RE.match(line)
        if m:
            current = m.group(1)
            roms[current] = []
            continue
        if current:
            if line.strip().startswith("ROM_END"):
                current = None
                continue
            for name, size, crc, nodump in ROM_LOAD_RE.findall(line):
                if nodump:
                    continue
                try:
                    size_int = int(size.strip(), 0)
                except ValueError:
                    size_int = None
                roms[current].append({"name": name, "crc": crc.lower(), "size": size_int})
            continue
        m = GAME_RE.match(line)
        if not m or m.group(1) == "GAME_CUSTOM":
            continue
        a = split_args(m.group(2))
        if len(a) < 11:
            continue
        year, name, parent, _machine, _inp, _cls, _init, monitor, company, fullname, flags = a[:11]
        flag_list = [FLAG_NAMES[f] for f in re.findall(r"MACHINE_\w+", flags) if f in FLAG_NAMES]
        if "bios" in flag_list:
            continue
        rotate = {"ROT90": 90, "ROT180": 180, "ROT270": 270}.get(monitor.split("|")[0].strip(), 0)
        games[name] = {
            "description": unquote(fullname),
            "year": unquote(year),
            "company": unquote(company),
            "parent": None if parent.strip() == "0" else parent.strip(),
            "rotate": rotate,
            "flags": [f for f in flag_list if f != "bios"],
            "driver": path.name,
        }
    return games, roms


def main() -> None:
    src, drivers = Path(sys.argv[1]), sys.argv[2:]
    games, roms = {}, {}
    for d in drivers:
        g, r = parse(src / d)
        games.update(g)
        roms.update(r)
    for name, game in games.items():
        game["roms"] = roms.get(name, [])
    json.dump({"games": games}, sys.stdout, indent=None, separators=(",", ":"), sort_keys=True)


if __name__ == "__main__":
    main()
