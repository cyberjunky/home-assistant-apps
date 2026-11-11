# SPDX-License-Identifier: GPL-2.0-or-later
"""High score tables: the high scores MAME keeps for a game, as a table you can read.

MAME's hiscore plugin saves a game's high scores as raw memory (hiscore/<game>.hi); some
games keep them in their own NVRAM (nvram/<game>/...). Either way it is a layout of the
game's own. The hi2txt project by GreatStone (GPL-2.0, github.com/GreatStoneEx/hi2txt-xml)
describes those layouts for about 3000 games, one XML file per game. This module decodes
the saved files with them, after the hi2txt XML reference
(greatstoneex.github.io/hi2txt-doc/doc/xml.html) and following MAME's own decoder for it,
plugins/data/data_hiscore.lua, which is why this file is under the GPL like those two.

It covers what the definitions for the games in this app use; a definition that needs
something it doesn't do gives no table (the app then only says the scores are kept).
"""

import contextlib
import logging
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

logger = logging.getLogger(__name__)

SAFE_NAME = re.compile(r"^[a-z0-9_]{1,32}$")
SAMEAS_RE = re.compile(r"<sameas\s+id\s*=\s*\"([a-z0-9_]{1,32})\"")
MAX_ROWS = 100
DEFAULT_ORDER = "byte-swap;bit-swap;byte-skip;endianness;byte-trim;byte-trunc;nibble-skip;bitmask"
PROFILES = {
    "base-40": {"src-unit-size": "16", "base": "40", "dst-unit-size": "3", "ascii-offset": "64"},
    "base-32": {"src-unit-size": "16", "base": "32", "dst-unit-size": "3", "ascii-offset": "64"},
    "bcd": {"endianness": "big_endian", "nibble-skip": "odd", "base": "16"},
    "bcd-le": {"endianness": "little_endian", "nibble-skip": "odd", "base": "16"},
}
# The named characters hi2txt uses in its charsets (as data_hiscore.lua knows them)
ENTITIES = {
    "big-mid-dot": "•",
    "copyright": "©",
    "mid-dot": "·",
    "one-on-two": "½",
    "ring": "Ö",
    "acute": "á",
    "y-strike": "ɏ",
    "bridge1": "˹",
    "bridge2": "˽",
    "bridge3": "˾",
    "alpha": "\u03b1",
    "beta": "\u03b2",
    "gamma": "\u03b3",
    "delta": "\u03b4",
    "epsilon": "\u03b5",
    "zeta": "\u03b6",
    "eta": "\u03b7",
    "theta": "\u03b8",
    "iota": "\u03b9",
    "mens-symbol": "♂",
    "womens-symbol": "♀",
    "feet": "🐾",
}
OPERATORS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    "<": lambda a, b: a < b,
    ">": lambda a, b: a > b,
    "<=": lambda a, b: a <= b,
    ">=": lambda a, b: a >= b,
}


class Unsupported(Exception):
    """A part of hi2txt this decoder doesn't do."""


def number(text):
    """A number from an attribute ("12", "0x1f", "-3", "1.5"), or None."""
    if text is None:
        return None
    t = str(text).strip()
    try:
        return int(t, 0)
    except ValueError:
        pass
    try:
        return int(t)
    except ValueError:
        pass
    try:
        return float(t)
    except ValueError:
        return None


def tidy(v):
    """1.0 -> 1; numbers and text as text for the table."""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return v


def as_number(v):
    if isinstance(v, int | float):
        return v
    n = number(v)
    if n is None:
        raise Unsupported(f"not a number: {v!r}")
    return n


def load_xml(text: str) -> ET.Element:
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text)

    def entity(m):
        name = m.group(1)
        if name in ("amp", "lt", "gt", "quot", "apos") or name.startswith("#"):
            return m.group(0)
        return ENTITIES.get(name, "?")

    text = re.sub(r"&([#\w-]+);", entity, text)
    return ET.fromstring(text)


class Definition:
    def __init__(self, root: ET.Element):
        self.root = root
        self._last = None  # the index the previous elt of a loop step got (table-index="last")
        self.formats = {f.get("id"): f for f in root.iter("format") if f.get("id")}
        self.charsets = {}
        for cs in root.iter("charset"):
            table, default = {}, None
            for ch in cs.iter("char"):
                dst = ch.get("dst", "")
                src = number(ch.get("src"))
                if src is not None:
                    table[src] = dst
                if ch.get("default") == "yes":
                    default = dst
            self.charsets[cs.get("id")] = (table, default)

    # ------------------------------------------------------------ structure
    def size(self, node: ET.Element) -> int:
        total = 0
        for child in node:
            if child.tag == "elt":
                total += int(child.get("size", "0"))
            elif child.tag == "loop":
                total += int(child.get("skip-first-bytes", "0")) + int(child.get("skip-last-bytes", "0"))
                total += int(child.get("count", "0")) * self.size(child)
        return total

    def read(self, structure: ET.Element, data: bytes) -> dict:
        size = self.size(structure)
        if len(data) < size:
            data = data + bytes(size - len(data))
        swap = number(structure.get("byte-swap"))
        if swap in (2, 4):
            data = b"".join(data[i : i + swap][::-1] for i in range(0, len(data) - len(data) % swap, swap))
        arr: dict = {}
        self._walk(structure, data, [0], arr, None, None)
        return arr

    def _walk(self, node, data, offset, arr, loop, i):
        for child in node:
            if child.tag == "loop":
                offset[0] += int(child.get("skip-first-bytes", "0"))
                for n in range(int(child.get("count", "0"))):
                    self._last = None
                    self._walk(child, data, offset, arr, child, n)
                offset[0] += int(child.get("skip-last-bytes", "0"))
            elif child.tag == "elt":
                size = int(child.get("size", "0"))
                raw = data[offset[0] : offset[0] + size]
                offset[0] += size
                value = self.decode(child, raw)
                eid = child.get("id")
                if loop is None:
                    arr[eid] = value
                    continue
                index = self.index(child, loop, i, value, arr)
                arr.setdefault(eid, {})
                if not isinstance(arr[eid], dict):
                    arr[eid] = {}
                arr[eid][index] = value
                self._last = index

    def index(self, elt, loop, i, value, arr):
        how = elt.get("table-index") or "loop_index"
        start, step, count = int(loop.get("start", "0")), int(loop.get("step", "1")), int(loop.get("count", "0"))
        fixed = number(how)
        if fixed is not None:
            index = fixed
        elif how == "loop_index":
            index = i * step + start
        elif how == "loop_reverse_index":
            index = (count - i - 1) * step + start
        elif how == "itself":
            index = value
        elif how == "last":
            index = self._last if self._last is not None else i
        elif ":" in how:
            ref, kind = how.rsplit(":", 1)
            col = arr.get(ref.strip(), {})
            if kind == "index_from_value":
                index = next((k for k, v in col.items() if v == i), i) if isinstance(col, dict) else i
            elif kind == "value_from_index":
                index = col.get(i, i) if isinstance(col, dict) else i
            else:
                raise Unsupported(f"table-index {how}")
        else:
            raise Unsupported(f"table-index {how}")
        if elt.get("table-index-format"):
            index = self.format(index, elt.get("table-index-format"), index)
        return tidy(index)

    def decode(self, elt: ET.Element, raw: bytes):
        a = dict(PROFILES.get(elt.get("decoding-profile", ""), {}))
        a.update(elt.attrib)
        if a.get("bitmask"):
            raise Unsupported("bitmask")
        unit = int(a.get("src-unit-size", "8")) // 8 or 1
        units = [int.from_bytes(raw[k : k + unit], "big") for k in range(0, len(raw), unit)]
        for step in a.get("swap-skip-order", DEFAULT_ORDER).split(";"):
            if step in a:
                units = self.byte_op(step, a[step], units)
        if a.get("type") == "int":
            value = 0
            for u in units:
                value = (value << (8 * unit)) | u
            if a.get("base") == "16":
                value = frombcd(value)
        else:
            value = self.text(a, units)
        if a.get("format"):
            value = self.format(value, a["format"], 0)
        return value

    @staticmethod
    def byte_op(op, arg, b):
        if op == "byte-swap":
            n = int(arg)
            out = []
            for k in range(len(b)):
                off = k + n - 1 - 2 * (k % n)
                if off >= len(b):
                    break
                out.append(b[off])
            return out
        if op == "bit-swap":
            return [int(f"{x:08b}"[::-1], 2) for x in b] if arg == "yes" else b
        if op == "byte-skip":
            picks = {"odd": (1, 2), "even": (0, 2), "1000": (0, 4), "0100": (1, 4), "0010": (2, 4), "0001": (3, 4)}
            if arg in picks:
                first, every = picks[arg]
                return b[first::every]
            skip = number(arg)
            return [x for x in b if x != skip]
        if op == "endianness":
            return b[::-1] if arg == "little_endian" else b
        if op == "byte-trim":
            val = number(arg)
            k = 0
            while k < len(b) and b[k] == val:
                k += 1
            return b[k:]
        if op == "byte-trunc":
            val = number(arg)
            return b[: b.index(val)] if val in b else b
        if op == "nibble-skip":
            out = []
            for k in range(0, len(b) - 1, 2):
                if arg == "odd":
                    out.append(((b[k] & 0x0F) << 4) | (b[k + 1] & 0x0F))
                elif arg == "even":
                    out.append((b[k] & 0xF0) | ((b[k + 1] & 0xF0) >> 4))
                else:
                    raise Unsupported(f"nibble-skip {arg}")
            return out
        raise Unsupported(op)

    def text(self, a, units):
        base = a.get("base")
        if base in ("32", "40"):
            n = int(base)
            chars = []
            for u in units:
                chars += [(u // (n * n)) % n, (u // n) % n, u % n]
            units = chars
        elif base not in (None, "10", "16"):
            raise Unsupported(f"text base {base}")
        step = number(a.get("ascii-step"))
        if step:
            units = [u // step for u in units]
        offset = number(a.get("ascii-offset")) or 0
        out = []
        charsets = [c for c in (a.get("charset") or "").split(";") if c]
        for u in units:
            ch = None
            for cs in charsets:
                if cs.startswith("CS_"):
                    m = re.match(r"CS_(\w+?)(?:\[(-?\d+)(?:,(\d+))?\])?$", cs)
                    if m and m.group(1) == "NUMBER":
                        d = u - int(m.group(2) or 0)
                        if 0 <= d <= 9:
                            ch = str(d)
                    elif not m or m.group(1) != "NUMBER":
                        raise Unsupported(cs)
                elif cs in self.charsets:
                    table, default = self.charsets[cs]
                    if u in table:
                        ch = table[u]
                    elif default is not None:
                        ch = default
                if ch is not None:
                    break
            if ch is None:
                code = u + offset
                ch = chr(code) if 0 < code < 0x110000 else ""
            out.append(ch)
        return "".join(out)

    # --------------------------------------------------------------- format
    def format(self, val, spec: str, index):
        for item in spec.replace("&gt;", ">").split(";"):
            item = item.strip()
            if item:
                val = self.format_one(val, item, index)
        return tidy(val)

    def format_one(self, val, f, index):
        if f in self.formats:
            return self.named(val, self.formats[f], index)
        first, rest = f[0], f[1:]
        n = number(rest)
        if n is not None and first in "*/dD-+%>":
            v = as_number(val)
            return {
                "*": lambda: v * n,
                "/": lambda: v / n,
                "d": lambda: math.floor(v / n),
                "D": lambda: math.floor(v / n + 0.5),
                "-": lambda: v - n,
                "+": lambda: v + n,
                "%": lambda: v % n,
                ">": lambda: int(v) << int(n),
            }[first]()
        if f in ("LC", "Lowercase"):
            return str(val).lower()
        if f in ("UC", "Uppercase"):
            return str(val).upper()
        if f == "Capitalize":
            s = str(val)
            return s[:1].upper() + s[1:]
        if f in ("R", "Round"):
            return math.floor(as_number(val) + 0.5)
        if f in ("hexadecimal_string", "0x"):
            return f"0x{int(as_number(val)):x}"
        if f == "LoopIndex":
            return index
        m = re.match(r"Trim(L?R?)(.)$", f)
        if m:
            s = str(val)
            if m.group(1) in ("", "L"):
                s = s.lstrip(m.group(2))
            if m.group(1) in ("", "R"):
                s = s.rstrip(m.group(2))
            return s
        m = re.match(r"Pad(L|R)(\d+)(.)$", f)
        if m:
            s, width = str(val), int(m.group(2))
            return s.rjust(width, m.group(3)) if m.group(1) == "L" else s.ljust(width, m.group(3))
        if f.startswith("Prefix"):
            return f[6:] + str(val)
        if f.startswith("Suffix"):
            return str(val) + f[6:]
        raise Unsupported(f"format {f}")

    def named(self, val, form: ET.Element, index):
        if form.get("apply-to") == "char":
            return "".join(str(self.ops(ord(c), form, index, char=True)) for c in str(val))
        out = self.ops(val, form, index)
        if form.get("formatter"):
            try:
                out = form.get("formatter") % (out,)
            except (TypeError, ValueError) as e:
                raise Unsupported(f"formatter {form.get('formatter')}") from e
        return out

    def ops(self, val, form, index, char=False):
        temp, default = val, None
        for op in form:
            t, text = op.tag, (op.text or "")
            if t == "case":
                src, dst = op.get("src"), op.get("dst", "")
                srcn, dstn = number(src), number(dst)
                if op.get("default") == "yes":
                    default = dstn if dstn is not None else dst
                compare = OPERATORS.get((op.get("operator") or "==").replace("&lt;", "<").replace("&gt;", ">"))
                if compare is None:
                    raise Unsupported(f"operator {op.get('operator')}")
                left = temp
                if srcn is not None and not isinstance(left, int | float):
                    left = number(left) if number(left) is not None else left
                right = srcn if srcn is not None and isinstance(left, int | float) else src
                with contextlib.suppress(TypeError):
                    if compare(left, right):
                        result = dstn if dstn is not None else dst
                        return chr(result) if char and isinstance(result, int) else result
                continue
            val = self.op(t, text, op, val)
        if default is not None:
            return default
        return chr(val) if char and isinstance(val, int) else val

    @staticmethod
    def op(t, text, op, val):
        n = number(text)
        if t == "add":
            return as_number(val) + n
        if t == "substract":
            return as_number(val) - n
        if t == "multiply":
            return as_number(val) * n
        if t == "divide" and n is not None:
            return as_number(val) / n
        if t == "remainder":
            return as_number(val) % n
        if t == "divide_trunc":
            return math.floor(as_number(val) / n)
        if t == "divide_round":
            return math.floor(as_number(val) / n + 0.5)
        if t == "trunc":
            return math.floor(as_number(val))
        if t == "round":
            return math.floor(as_number(val) + 0.5)
        if t == "shift":
            return int(as_number(val)) << int(n)
        if t == "prefix":
            return text + str(tidy(val))
        if t == "suffix":
            return str(tidy(val)) + text
        if t == "lowercase":
            return str(val).lower()
        if t == "uppercase":
            return str(val).upper()
        if t == "capitalize":
            s = str(val)
            return s[:1].upper() + s[1:]
        if t == "replace":
            return str(val).replace(op.get("src", ""), op.get("dst", ""))
        if t == "pad":
            s, width, ch = str(tidy(val)), int(op.get("max", "0")), text or " "
            return s.rjust(width, ch) if op.get("direction") == "left" else s.ljust(width, ch)
        if t == "trim":
            s, ch = str(val), text or " "
            if op.get("direction") in ("left", "both"):
                s = s.lstrip(ch)
            if op.get("direction") in ("right", "both"):
                s = s.rstrip(ch)
            return s
        raise Unsupported(f"format operation {t}")

    # --------------------------------------------------------------- output
    def output(self, structure: ET.Element, arr: dict) -> dict:
        outputs = self.root.findall("output")
        want = structure.get("output")
        out = next((o for o in outputs if want and o.get("id") == want), outputs[0] if outputs else None)
        result = {"tables": [], "fields": {}}
        if out is None:
            return result
        for node in out:
            if node.get("display") not in (None, "always"):
                continue
            if node.tag == "field":
                src = node.get("src") or node.get("id")
                v = arr.get(src)
                if isinstance(v, dict):
                    v = v[min(v)] if v else None
                if v is None:
                    continue
                with contextlib.suppress(Unsupported, ValueError, ZeroDivisionError):
                    result["fields"][node.get("id")] = str(
                        tidy(self.format(v, node.get("format"), 0) if node.get("format") else v)
                    )
            elif node.tag == "table":
                table = self.table(node, arr)
                if table and table["rows"]:
                    result["tables"].append(table)
        return result

    def table(self, node: ET.Element, arr: dict) -> dict | None:
        cols = [c for c in node if c.tag in ("column", "field") and c.get("display") in (None, "always")]
        if not cols:
            return None
        srcs = [c.get("src") or c.get("id") for c in cols]
        keys = set()
        for src in srcs:
            if isinstance(arr.get(src), dict):
                keys |= set(arr[src])
        try:
            keys = sorted(keys)
        except TypeError:
            keys = sorted(keys, key=str)
        rows = []
        for pos, k in enumerate(keys):
            raw = {}
            for c, src in zip(cols, srcs, strict=True):
                v = arr.get(src)
                raw[c.get("id")] = v.get(k) if isinstance(v, dict) else v
            rows.append((pos, k, raw))
        sort = node.get("sort")
        if sort:
            sort_src = sort
            key_of = {}
            for pos, k, raw in rows:
                v = (
                    raw.get(sort)
                    if sort in raw
                    else (arr.get(sort_src, {}) or {}).get(k)
                    if isinstance(arr.get(sort_src), dict)
                    else None
                )
                if node.get("sort-format") and v is not None:
                    with contextlib.suppress(Unsupported, ValueError, ZeroDivisionError):
                        v = self.format(v, node.get("sort-format"), pos)
                n = number(v) if not isinstance(v, int | float) else v
                key_of[pos] = (0, n) if n is not None else (1, str(v))
            rows.sort(key=lambda r: key_of[r[0]], reverse=node.get("sort-order") == "desc")
        ignore = node.get("line-ignore")
        ignore_col, _, ignore_val = (ignore or "").partition(":")
        compare = OPERATORS.get(
            (node.get("line-ignore-operator") or "==").replace("&lt;", "<").replace("&gt;", ">"), OPERATORS["=="]
        )
        out_rows = []
        for place, (pos, _k, raw) in enumerate(rows):
            line = []
            skip = False
            for c, src in zip(cols, srcs, strict=True):
                v = place if src == "index" else pos if src == "unsorted_index" else raw.get(c.get("id"))
                if v is None:
                    v = ""
                if c.get("format") and v != "":
                    with contextlib.suppress(Unsupported, ValueError, ZeroDivisionError):
                        v = self.format(v, c.get("format"), place)
                v = tidy(v)
                if ignore and c.get("id") == ignore_col:
                    a, b = number(v) if not isinstance(v, int | float) else v, number(ignore_val)
                    with contextlib.suppress(TypeError):
                        skip = compare(a, b) if a is not None and b is not None else compare(str(v), ignore_val)
                line.append(str(v))
            if not skip:
                out_rows.append(line)
        lines_max = number(node.get("lines-max"))
        out_rows = out_rows[: int(lines_max) if lines_max else MAX_ROWS]
        return {"columns": [c.get("id") for c in cols], "rows": out_rows}


def frombcd(val: int) -> int:
    result, mul = 0, 1
    while val:
        result += (val % 16) * mul
        val >>= 4
        mul *= 10
    return result


class HiScores:
    def __init__(self, root: Path, games: dict[str, dict], saves_root: Path):
        self.xml = root / "xml"
        self.games = games
        self.saves_root = saves_root
        self._known: dict[str, str | None] = {}
        self._cache: dict[str, tuple[tuple, dict | None]] = {}

    def definition_name(self, game: str) -> str | None:
        """The hi2txt file for a game: its own or its parent's, following <sameas id="..."/>."""
        if game not in self._known:
            found = None
            for name in (game, self.games.get(game, {}).get("parent")):
                if name and SAFE_NAME.match(name) and (self.xml / f"{name}.xml").is_file():
                    found = name
                    break
            if found:
                m = SAMEAS_RE.search((self.xml / f"{found}.xml").read_text(encoding="utf-8", errors="replace"))
                if m and (self.xml / f"{m.group(1)}.xml").is_file():
                    found = m.group(1)
            self._known[game] = found
        return self._known[game]

    def _source(self, game: str, file: str) -> Path:
        if file == ".hi":
            return self.saves_root / "hiscore" / f"{game}.hi"
        return self.saves_root / "nvram" / game / Path(file).name

    def table(self, game: str) -> dict | None:
        """The decoded high scores of a game: {tables: [{columns, rows}], fields, updated}; None
        without saved scores or a definition."""
        name = self.definition_name(game)
        if not SAFE_NAME.match(game) or not name:
            return None
        try:
            root = load_xml((self.xml / f"{name}.xml").read_text(encoding="utf-8", errors="replace"))
        except (OSError, ET.ParseError) as e:
            logger.debug(f"hi2txt {name}: {e}")
            return None
        structures = root.findall("structure")
        files = {s.get("file", ".hi") for s in structures}
        stamp = tuple(
            (f, p.stat().st_mtime, p.stat().st_size) for f in sorted(files) if (p := self._source(game, f)).is_file()
        )
        if not stamp:
            return None
        cached = self._cache.get(game)
        if cached and cached[0] == stamp:
            return cached[1]
        result = None
        try:
            result = self.decode(game, root, structures)
        except (Unsupported, ValueError, KeyError, IndexError, ZeroDivisionError, OverflowError) as e:
            logger.debug(f"hi2txt {name} for {game}: {e}")
        if result is not None:
            result["updated"] = max(s[1] for s in stamp)
            result["definition"] = name
        self._cache[game] = (stamp, result)
        return result

    def decode(self, game: str, root: ET.Element, structures: list) -> dict | None:
        d = Definition(root)
        candidates = []
        for s in structures:
            p = self._source(game, s.get("file", ".hi"))
            if p.is_file():
                data = p.read_bytes()
                candidates.append((d.size(s) != len(data), s, data))
        if not candidates:
            return None
        # The structure whose size fits the saved file, else the first one there is a file for
        _mismatch, structure, data = sorted(candidates, key=lambda c: c[0])[0]
        result = d.output(structure, d.read(structure, data))
        return result if result["tables"] or result["fields"] else None

    def all(self) -> dict[str, dict]:
        """The decoded tables of every game with saved high scores or NVRAM."""
        names = set()
        if (self.saves_root / "hiscore").is_dir():
            names |= {p.stem for p in (self.saves_root / "hiscore").glob("*.hi")}
        if (self.saves_root / "nvram").is_dir():
            names |= {p.name for p in (self.saves_root / "nvram").iterdir() if p.is_dir()}
        out = {}
        for g in sorted(names):
            if g in self.games:
                t = self.table(g)
                if t:
                    out[g] = t
        return out
