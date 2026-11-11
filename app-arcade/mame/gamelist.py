"""Make games.json: the machines in the MAME drivers this app's MAME build includes.

Reads the GAME()/GAMEL() lines and ROM_START ... ROM_END blocks of the driver sources,
so the app can name the games, show their year and maker, and recognise an uploaded zip
by the CRC32 of the files in it (zip files store those, nothing has to be unpacked).

ROM blocks may use #define macros of the driver (Neo Geo: NEO_SFIX_128K, NEO_BIOS_AUDIO_128K,
NEOGEO_BIOS ...); those are expanded first. A BIOS machine (MACHINE_IS_BIOS_ROOT, such as
"neogeo") is listed under "bios", with the ROMs every game on it needs and the alternative
BIOS ROMs (ROM_SYSTEM_BIOS: "euro", "us", "japan" ...), of which one is enough. A game on such
a system gets "bios": "<name>" and lists only its own ROMs.

Usage: python3 gamelist.py <mame source dir> <driver .cpp> [...] > games.json
"""

import json
import re
import sys
from pathlib import Path

# Some drivers number their games in a comment first: /* 739 */ GAMEL( 1978, invaders, ...
# and some end in more than one comment: ... ) /* need internal ROM */  // V0008 04/27/99
GAME_RE = re.compile(
    r"^\s*(?:/\*.*?\*/\s*)?(GAMEL?|GAME_CUSTOM|CONS|COMP|SYST)\s*\((.*)\)\s*(?:(?://.*|/\*.*?\*/)\s*)*$"
)
ROM_START_RE = re.compile(r"ROM_START\s*\(\s*(\w+)\s*\)(.*?)ROM_END", re.S)
DEFINE_RE = re.compile(r"^[ \t]*#[ \t]*define[ \t]+(\w+)(\([^)]*\))?(.*)$", re.S)
CALL_RE = re.compile(r"\b([A-Za-z_]\w*)\s*\(")
CRC_RE = re.compile(r"CRC\(\s*([0-9a-fA-F]{8})\s*\)")
ROM_BIOS_RE = re.compile(r"\bROM_BIOS\s*\(([^()]*)\)")
IDENT_RE = re.compile(r"\b[A-Za-z_]\w*\b")
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
MAX_EXPANSION = 16


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


def closing_paren(text: str, start: int) -> int:
    """Index of the ")" matching the "(" at text[start - 1], skipping quoted strings; -1 if none."""
    depth, quote = 1, False
    for i in range(start, len(text)):
        ch = text[i]
        if ch == '"' and text[i - 1] != "\\":
            quote = not quote
        elif not quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    return i
    return -1


def unquote(s: str) -> str:
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s.replace('\\"', '"')


def strip_comments(text: str) -> str:
    """Remove /* */ and // comments outside quoted strings."""
    out, i, quote = [], 0, False
    while i < len(text):
        ch = text[i]
        if quote:
            out.append(ch)
            if ch == '"' and text[i - 1] != "\\":
                quote = False
            i += 1
        elif ch == '"':
            quote = True
            out.append(ch)
            i += 1
        elif text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            out.append(" ")
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def read_defines(text: str) -> dict[str, tuple[list[str] | None, str]]:
    """The #define macros whose body holds ROM definitions: name -> (parameters or None, body)."""
    macros = {}
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        # A macro continues on the next line after a backslash
        while line.rstrip().endswith("\\") and i + 1 < len(lines):
            i += 1
            line = line.rstrip()[:-1] + "\n" + lines[i]
        i += 1
        m = DEFINE_RE.match(line)
        if not m:
            continue
        name, params = m.group(1), m.group(2)
        body = strip_comments(m.group(3))
        if "ROM_" not in body:
            continue
        plist = [p.strip() for p in params[1:-1].split(",")] if params else None
        macros[name] = ([p for p in plist if p] if plist is not None else None, body)
    return macros


def expand(text: str, macros: dict, depth: int = 0) -> str:
    """Replace the macro calls in text by their bodies, recursively."""
    if depth > MAX_EXPANSION or not macros:
        return text
    out, pos = [], 0
    for m in IDENT_RE.finditer(text):
        if m.start() < pos or m.group(0) not in macros:
            continue
        params, body = macros[m.group(0)]
        end = m.end()
        if params is not None:
            rest = text[end:]
            lead = len(rest) - len(rest.lstrip())
            if not rest[lead:].startswith("("):
                continue
            close = closing_paren(text, end + lead + 1)
            if close < 0:
                continue
            args = split_args(text[end + lead + 1 : close])
            values = dict(zip(params, args, strict=False))
            body = IDENT_RE.sub(lambda t, v=values: v.get(t.group(0), t.group(0)), body)
            end = close + 1
        out.append(text[pos : m.start()])
        out.append(" " + expand(body, macros, depth + 1) + " ")
        pos = end
    out.append(text[pos:])
    return "".join(out)


def parse_roms(block: str) -> tuple[list[dict], str | None]:
    """The ROM files of one expanded ROM_START block, and its ROM_DEFAULT_BIOS."""
    roms, system_bios, default_bios = [], {}, None
    pos = 0
    while True:
        m = CALL_RE.search(block, pos)
        if not m:
            break
        close = closing_paren(block, m.end())
        if close < 0:
            break
        pos = close + 1
        macro, args = m.group(1), split_args(block[m.end() : close])
        if macro == "ROM_SYSTEM_BIOS" and len(args) >= 2:
            system_bios[re.sub(r"\s+", "", args[0])] = unquote(args[1])
            continue
        if macro == "ROM_DEFAULT_BIOS" and args:
            default_bios = unquote(args[0])
            continue
        # A *_BIOS load names the system BIOS it belongs to first: ROM_LOAD16_WORD_SWAP_BIOS( 0, "sp-s2.sp1", ...)
        bios_index = None
        if macro.endswith("_BIOS") and len(args) > 1:
            bios_index, args = re.sub(r"\s+", "", args[0]), args[1:]
        # Any call with a quoted file name followed by its CRC (or NO_DUMP)
        if not args or not args[0].startswith('"'):
            continue
        hash_at = next((i for i, a in enumerate(args) if CRC_RE.search(a) or a.strip() == "NO_DUMP"), None)
        if hash_at is None or hash_at == 0:
            continue
        crc = CRC_RE.search(args[hash_at])
        if not crc:
            continue  # NO_DUMP: nobody has the file (a BAD_DUMP file is in the set, and MAME needs it)
        try:
            size = int(args[hash_at - 1].strip(), 0) if hash_at >= 3 else None
        except ValueError:
            size = None
        rom = {"name": unquote(args[0]), "crc": crc.group(1).lower(), "size": size}
        # ROMX_LOAD( ..., ROM_BIOS(n) ): what the *_BIOS macros of a driver usually expand to
        for a in args[hash_at + 1 :]:
            m_bios = ROM_BIOS_RE.search(a)
            if m_bios:
                bios_index = re.sub(r"\s+", "", m_bios.group(1))
        if bios_index is not None:
            rom["alt"] = True
            rom["bios"] = system_bios.get(bios_index, bios_index)
        roms.append(rom)
    return roms, default_bios


def parse(path: Path) -> tuple[dict, dict, dict]:
    text = path.read_text(encoding="utf-8", errors="replace")
    macros = read_defines(text)
    games, bioses, roms = {}, {}, {}
    for m in ROM_START_RE.finditer(text):
        roms[m.group(1)] = parse_roms(expand(strip_comments(m.group(2)), macros))
    for line in text.splitlines():
        m = GAME_RE.match(line)
        if not m or m.group(1) == "GAME_CUSTOM":
            continue
        a = split_args(m.group(2))
        if len(a) < 11:
            continue
        if m.group(1) != "GAME" and m.group(1) != "GAMEL":
            # CONS( year, name, parent, compat, machine, input, class, init, company, fullname, flags )
            year, name, parent, _compat, _machine, _inp, _cls, _init, company, fullname, flags = a[:11]
            monitor = "ROT0"
        else:
            year, name, parent, _machine, _inp, _cls, _init, monitor, company, fullname, flags = a[:11]
        flag_list = [FLAG_NAMES[f] for f in re.findall(r"MACHINE_\w+", flags) if f in FLAG_NAMES]
        if "bios" in flag_list:
            bioses[name] = {
                "description": unquote(fullname),
                "year": unquote(year),
                "company": unquote(company),
                "driver": path.name,
            }
            continue
        if m.group(1) != "GAME" and m.group(1) != "GAMEL":
            continue  # other consoles (such as the Neo Geo's cabinets) are no games
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
    return games, bioses, roms


def main() -> None:
    src, drivers = Path(sys.argv[1]), sys.argv[2:]
    games, bioses, roms = {}, {}, {}
    for d in drivers:
        g, b, r = parse(src / d)
        games.update(g)
        bioses.update(b)
        roms.update(r)
    for name, bios in bioses.items():
        bios_roms, default = roms.get(name, ([], None))
        unique, seen = [], set()
        for rom in bios_roms:
            if (rom["name"], rom["crc"]) not in seen:
                seen.add((rom["name"], rom["crc"]))
                unique.append(rom)
        bios["roms"] = unique
        alts = [r["bios"] for r in unique if r.get("alt")]
        bios["default"] = default or (alts[0] if alts else None)

    def bios_of(name: str, seen: set) -> str | None:
        parent = games.get(name, {}).get("parent")
        if not parent or parent in seen:
            return None
        if parent in bioses:
            return parent
        return bios_of(parent, seen | {name})

    # Before any parent is changed below: a clone finds the BIOS through its parent
    bios_for = {name: bios_of(name, set()) for name in games}
    for name, game in games.items():
        game_roms, default = roms.get(name, ([], None))
        bios = bios_for[name]
        if bios:
            game["bios"] = bios
            if game["parent"] == bios:
                game["parent"] = None
            # The BIOS files come from the BIOS set (neogeo.zip), not from the game's own
            from_bios = {r["crc"] for r in bioses[bios]["roms"]}
            required = {r["crc"] for r in bioses[bios]["roms"] if not r.get("alt")}
            # The BIOS files this game uses, when not all of them: an Aristocrat MK5 game outside
            # the US needs the GALs of aristmk5.zip, not its US set chips
            uses = {r["crc"] for r in game_roms if r["crc"] in required}
            if uses != required:
                game["bios_roms"] = sorted(uses)
            game_roms = [r for r in game_roms if r["crc"] not in from_bios and not r.get("alt")]
            if default:
                game["default_bios"] = default
        game["roms"] = game_roms
    json.dump({"bios": bioses, "games": games}, sys.stdout, indent=None, separators=(",", ":"), sort_keys=True)


if __name__ == "__main__":
    main()
