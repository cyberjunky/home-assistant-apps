"""The ROM library: which games the uploaded archives hold, and whether they are complete.

A game is a .zip or .7z archive, as MAME reads both. Both formats store the CRC32 of every
file in them, so an archive is recognised by its contents (not its name) against the ROM
lists of the drivers in this app's MAME build (games.json), without unpacking anything.
A clone game needs its own files plus those it shares with its parent, which MAME also
finds in the parent's archive.

Some systems need a BIOS set besides the game: every Neo Geo game needs neogeo.zip, an IGS
PGM game pgm.zip, an Aristocrat MK5 slot machine aristmk5.zip. A BIOS archive is recognised
by its contents as well: the files the games on the system need, plus at least one of the
alternative BIOS ROMs when the system has those (Neo Geo: Europe, US, Japan ... ; one is enough).

An archive the build has no game for is explained as well as possible: the game it comes
closest to by its files, the game its name says it is, or, from the index of every machine
in MAME (machines.json.gz), the MAME driver it needs that this build doesn't include.
"""

import contextlib
import gzip
import json
import logging
import re
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SAFE_NAME = re.compile(r"^[a-z0-9_]{1,32}$")
# MAME looks for <game>.zip first, then <game>.7z
EXTENSIONS = (".zip", ".7z")
ZIP_MAGIC = (b"PK\x03\x04", b"PK\x05\x06")
SEVEN_ZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
SEVEN_ZIP_TIMEOUT = 60


def split_name(file: str) -> tuple[str, str] | None:
    """("jumpcoas", ".7z") for "jumpcoas.7z"; None for a name that isn't an archive."""
    lower = file.lower()
    for ext in EXTENSIONS:
        if lower.endswith(ext) and len(file) > len(ext):
            return file[: -len(ext)], ext
    return None


def plain_file(file: str) -> bool:
    """A file name directly in the ROM folder (no path, not hidden)."""
    return bool(file) and "/" not in file and "\\" not in file and not file.startswith(".")


@dataclass
class ArchiveInfo:
    file: str
    size: int
    crcs: set[str] = field(default_factory=set)
    game: str | None = None
    bios: str | None = None
    error: str | None = None
    # Why it is no game of this build, and the MAME driver it needs (see Library.diagnose)
    diagnosis: dict | None = None


# How a BIOS set is called in the app; others by the machine's own description
BIOS_LABELS = {"neogeo": "Neo Geo BIOS", "pgm": "IGS PGM BIOS", "aristmk5": "Aristocrat MK5 BIOS"}
# Uploads that weren't recognised, remembered for the Info tab's list of drivers to ask for
MAX_REJECTED = 200
# Files named in a diagnosis, at most
SHOW_FILES = 4


def set_name(stem: str) -> str:
    """The MAME set name a file name stands for: "Racherod (1).7z" -> "racherod"."""
    m = re.match(r"[a-z0-9_]+", stem.strip().lower())
    return m.group(0) if m else ""


def some(names: list[str]) -> str:
    """ "a, b, c" or "a, b, c and 5 more" """
    if len(names) <= SHOW_FILES:
        return ", ".join(names)
    return f"{', '.join(names[: SHOW_FILES - 1])} and {len(names) - SHOW_FILES + 1} more"


class Library:
    def __init__(self, games_file: Path, rom_folder: Path, rejected_file: Path | None = None):
        self.rom_folder = rom_folder
        try:
            data = json.loads(games_file.read_text(encoding="utf-8"))
            self.games: dict[str, dict] = data["games"]
            self.bios: dict[str, dict] = data.get("bios", {})
        except (OSError, ValueError, KeyError) as e:
            logger.error(f"Cannot read the game list {games_file}: {e}")
            self.games, self.bios = {}, {}
        self.mame_dir = games_file.parent
        self.mame_version = "?"
        with contextlib.suppress(OSError, ValueError, KeyError):
            self.mame_version = json.loads((self.mame_dir / "build.json").read_text(encoding="utf-8"))["version"]
        self._machines: dict[str, list] | None = None
        self.rejected_file = rejected_file
        self.rejected: dict[str, dict] = {}
        if rejected_file:
            with contextlib.suppress(OSError, ValueError):
                self.rejected = json.loads(rejected_file.read_text(encoding="utf-8"))
        self.by_crc: dict[str, set[str]] = {}
        for name, game in self.games.items():
            for rom in game["roms"]:
                self.by_crc.setdefault(rom["crc"], set()).add(name)
        self._cache: dict[str, tuple[float, int, ArchiveInfo]] = {}
        logger.info(f"MAME build knows {len(self.games)} games, {len(self.bios)} BIOS sets; ROM folder {rom_folder}")

    def bios_label(self, name: str) -> str:
        return BIOS_LABELS.get(name) or f"{self.bios.get(name, {}).get('description', name)} BIOS"

    def bios_state(self, name: str, crcs: set[str], needed: list[str] | None = None) -> dict:
        """What of BIOS set `name` these files hold: the missing required files, and which
        alternative BIOS ROMs (by their short names, in MAME's order) are there. `needed`: the
        CRCs of the BIOS files one game uses, when not all (games.json "bios_roms")."""
        roms = self.bios[name]["roms"]
        required = [r for r in roms if not r.get("alt") and (needed is None or r["crc"] in needed)]
        missing = sorted({r["name"] for r in required if r["crc"] not in crcs})
        present = []
        for r in roms:
            if r.get("alt") and r["crc"] in crcs and r["bios"] not in present:
                present.append(r["bios"])
        # A system without alternative BIOS ROMs (Aristocrat MK5) needs only the required files
        has_alts = any(r.get("alt") for r in roms)
        return {"missing": missing, "present": present, "complete": not missing and (bool(present) or not has_alts)}

    def identify_bios(self, crcs: set[str]) -> str | None:
        """The BIOS set this archive holds: its required files and at least one alternative,
        or (incomplete) mostly files of that set."""
        best, best_hits = None, 0
        for name, bios in self.bios.items():
            hits = len(crcs & {r["crc"] for r in bios["roms"]})
            if hits > best_hits:
                best, best_hits = name, hits
        if best and (self.bios_state(best, crcs)["complete"] or best_hits * 2 >= len(crcs)):
            return best
        return None

    # -------------------------------------------------------------- archives
    @staticmethod
    def kind(path: Path) -> str | None:
        """ ".zip" or ".7z" from the file's first bytes, whatever it is called."""
        with open(path, "rb") as f:
            head = f.read(6)
        if head.startswith(ZIP_MAGIC):
            return ".zip"
        if head == SEVEN_ZIP_MAGIC:
            return ".7z"
        return None

    @staticmethod
    def read_crcs(path: Path) -> set[str]:
        """The CRC32s of the files in a zip or 7z archive, read from its index."""
        kind = Library.kind(path)
        if kind == ".zip":
            try:
                with zipfile.ZipFile(path) as z:
                    return {f"{i.CRC:08x}" for i in z.infolist() if not i.is_dir()}
            except zipfile.BadZipFile as e:
                raise ValueError(f"a damaged or incomplete zip file, was the download or copy cut off? ({e})") from e
        if kind == ".7z":
            try:
                result = subprocess.run(
                    ["7z", "l", "-slt", "-ba", str(path)],
                    capture_output=True,
                    text=True,
                    timeout=SEVEN_ZIP_TIMEOUT,
                    check=False,
                )
            except FileNotFoundError as e:
                raise ValueError("7z archives need the 7z program, which is missing") from e
            except subprocess.TimeoutExpired as e:
                raise ValueError("reading the 7z archive took too long") from e
            if result.returncode != 0:
                raise ValueError(f"a damaged 7z file ({(result.stderr or result.stdout).strip()[:200]})")
            crcs, crc, is_dir = set(), None, False
            # One block of "Key = value" lines per file, separated by an empty line
            for line in [*result.stdout.splitlines(), ""]:
                if not line.strip():
                    if crc and not is_dir:
                        crcs.add(crc.lower())
                    crc, is_dir = None, False
                elif line.startswith("CRC = "):
                    crc = line[6:].strip() or None
                elif line.startswith("Attributes = "):
                    is_dir = "D" in line[13:].split(" ")[0]
            return crcs
        raise ValueError("not a zip or 7z file")

    def identify(self, crcs: set[str]) -> str | None:
        """The game whose own ROM set this archive covers best (a parent wins a tie with its clones)."""
        candidates: dict[str, int] = {}
        for crc in crcs:
            for name in self.by_crc.get(crc, ()):
                candidates[name] = candidates.get(name, 0) + 1
        best, best_key = None, None
        for name, hits in candidates.items():
            roms = {r["crc"] for r in self.games[name]["roms"]}
            if not roms:
                continue
            # Files of this game in the archive, then how much of the game is covered, then parents first
            key = (hits / len(roms), hits, self.games[name]["parent"] is None, -len(roms))
            if best_key is None or key > best_key:
                best, best_key = name, key
        if best_key is None or best_key[0] < 0.25:
            return None
        return best

    # ------------------------------------------------------------ diagnosis
    def machines(self) -> dict[str, list]:
        """Every machine in MAME (machines.json.gz, made with the build): name -> [description,
        company, year, driver, parent, BIOS, flags]; loaded the first time a file needs it."""
        if self._machines is None:
            self._machines = {}
            path = self.mame_dir / "machines.json.gz"
            try:
                with gzip.open(path, "rt", encoding="utf-8") as f:
                    self._machines = json.load(f)["machines"]
            except (OSError, ValueError, KeyError) as e:
                logger.warning(f"No index of MAME's machines ({path}: {e}): unrecognised files are explained less well")
        return self._machines

    def closest(self, crcs: set[str]) -> tuple[str, int] | None:
        """The game with the most of these files (relative to its size), and how many it has."""
        counts: dict[str, int] = {}
        for crc in crcs:
            for name in self.by_crc.get(crc, ()):
                counts[name] = counts.get(name, 0) + 1
        if not counts:
            return None
        name = max(counts, key=lambda n: (counts[n] / len(self.games[n]["roms"]), counts[n], self.games[n]["parent"] is None))
        return name, counts[name]

    def _match_text(self, roms: list[dict], crcs: set[str]) -> str:
        hits = sum(1 for r in roms if r["crc"] in crcs)
        missing = [r["name"] for r in roms if r["crc"] not in crcs]
        return f"{hits} of {len(roms)} files match" + (f" (missing {some(missing)})" if missing and hits else "")

    def diagnose(self, file: str, crcs: set[str]) -> dict:
        """Why an archive is no game this build can run, in a line or two, and what it is: the
        game its name says, the game its files come closest to, or the MAME driver it needs."""
        v = self.mame_version
        parts = split_name(file)
        stem = set_name(parts[0] if parts else file)
        if not crcs:
            return {"error": "the archive is empty: there are no files in it", "set": stem}
        known = set(self.by_crc)
        for b in self.bios.values():
            known |= {r["crc"] for r in b["roms"]}
        stray = len(crcs - known)
        none_match = f"none of its {len(crcs)} files" if stray == len(crcs) else f"{stray} of its {len(crcs)} files"
        stray_text = f"; {none_match} belong to any game of this app" if stray else ""
        if stem in self.games:
            g = self.games[stem]
            return {
                "error": f"named like {g['description']} ({stem}), but {self._match_text(g['roms'], crcs)} of MAME {v}'s set"
                f"{stray_text}: a set for another MAME version, or a damaged file?",
                "set": stem,
                "description": g["description"],
            }
        if stem in self.bios:
            required = [r for r in self.bios[stem]["roms"] if not r.get("alt")]
            return {
                "error": f"named like the {self.bios_label(stem)} ({stem}), but {self._match_text(required, crcs)} of MAME {v}'s set"
                f"{stray_text}: a set for another MAME version, or a damaged file?",
                "set": stem,
            }
        machine = self.machines().get(stem)
        if machine:
            desc, company, year, source, _parent, bios, flags = machine
            driver = source.rsplit("/", 1)[-1]
            kind = (
                "pinball machine"
                if "p" in flags
                else "slot machine or other gambling game"
                if "g" in flags
                else "console or computer"
                if "c" in flags
                else None
            )
            about = ", ".join(x for x in (company, year, kind) if x)
            if "p" in flags and "m" in flags:
                text = f"{desc} ({about}): a real pinball machine, MAME can't emulate its playfield (driver {driver})"
            elif "c" in flags:
                text = f"{desc} ({about}): not an arcade game; MAME driver {driver}, which this app doesn't include"
            else:
                text = f"{desc} ({about}): needs MAME driver {driver}, which this app doesn't include (yet)"
                if "n" in flags:
                    text += f"; it doesn't work in MAME {v} either"
                if bios:
                    text += f"; it also needs the BIOS set {bios}.zip"
            return {
                "error": text,
                "set": stem,
                "description": desc,
                "driver": driver,
                "source": source,
                "not_working": "n" in flags,
            }
        best = self.closest(crcs)
        if best:
            name, _hits = best
            g = self.games[name]
            return {
                "error": f"comes closest to {g['description']} ({name}): {self._match_text(g['roms'], crcs)}"
                f"{stray_text}; not enough to run it",
                "set": stem,
                "closest": name,
            }
        return {
            "error": f"not a game this app's MAME {v} can run: none of its {len(crcs)} files belong to one, and "
            f"{stem or file} is not the name of a MAME {v} machine",
            "set": stem,
        }

    def remember(self, file: str, diagnosis: dict | None) -> None:
        """Keep an unrecognised upload (or forget it again: None) for the Info tab's list."""
        if diagnosis is None:
            if self.rejected.pop(file, None) is None:
                return
        else:
            self.rejected[file] = {**diagnosis, "file": file, "at": time.time()}
            while len(self.rejected) > MAX_REJECTED:
                self.rejected.pop(min(self.rejected, key=lambda f: self.rejected[f]["at"]))
        if self.rejected_file:
            try:
                self.rejected_file.write_text(json.dumps(self.rejected), encoding="utf-8")
            except OSError as e:
                logger.warning(f"Cannot write {self.rejected_file}: {e}")

    def forget_rejected(self) -> int:
        n = len(self.rejected)
        self.rejected = {}
        if self.rejected_file:
            self.rejected_file.unlink(missing_ok=True)
        return n

    def unrecognised(self, archives: list[ArchiveInfo]) -> list[dict]:
        """Files in the folder and uploads that are no game of this build, with their diagnosis."""
        rows = {a.file: {**a.diagnosis, "file": a.file, "in_folder": True} for a in archives if a.diagnosis}
        for file, r in self.rejected.items():
            rows.setdefault(file, {**r, "in_folder": False})
        return sorted(rows.values(), key=lambda r: (r.get("driver") or "~", r["file"]))

    def scan(self, path: Path) -> ArchiveInfo:
        st = path.stat()
        cached = self._cache.get(path.name)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return cached[2]
        info = ArchiveInfo(file=path.name, size=st.st_size)
        try:
            info.crcs = self.read_crcs(path)
            info.game = self.identify(info.crcs)
            if not info.game:
                info.bios = self.identify_bios(info.crcs)
            if not info.game and not info.bios:
                info.diagnosis = self.diagnose(path.name, info.crcs)
                info.error = info.diagnosis["error"]
        except (OSError, ValueError) as e:
            info.error = f"not readable: {e}"
        self._cache[path.name] = (st.st_mtime, st.st_size, info)
        return info

    def archives(self) -> list[ArchiveInfo]:
        if not self.rom_folder.is_dir():
            return []
        return [
            self.scan(p)
            for p in sorted(self.rom_folder.iterdir())
            if p.is_file() and plain_file(p.name) and split_name(p.name)
        ]

    # ----------------------------------------------------------------- games
    def status(self) -> dict:
        """Every game that an archive in the folder is for, with what it still misses."""
        archives = self.archives()
        # One archive per game: a zip wins from a 7z of the same name, as in MAME
        by_name: dict[str, ArchiveInfo] = {}
        doubles = []
        for a in sorted(archives, key=lambda a: EXTENSIONS.index(split_name(a.file)[1].lower())):
            stem = split_name(a.file)[0]
            if stem in by_name:
                doubles.append((a, by_name[stem]))
            else:
                by_name[stem] = a
        listed: dict[str, dict] = {}

        def available(name: str) -> set[str]:
            crcs = set(by_name[name].crcs) if name in by_name else set()
            parent = self.games.get(name, {}).get("parent")
            if parent and parent in by_name:
                crcs |= by_name[parent].crcs
            return crcs

        names = set()
        for stem, a in by_name.items():
            # An archive counts for the game it is named after (a misnamed one is only a problem below)
            if a.game and stem in self.games:
                names.add(stem)
        # Clones that a parent archive (a merged set) already holds completely
        for name, game in self.games.items():
            if game["parent"] and game["parent"] in by_name and name not in names:
                needed = {r["crc"] for r in game["roms"]}
                if needed and needed <= available(name):
                    names.add(name)
        # BIOS sets: under their own name (MAME looks for neogeo.zip), as for games
        bios_list: dict[str, dict] = {}
        for name, bios in self.bios.items():
            a = by_name.get(name)
            if a is None or a.bios != name:
                continue
            state = self.bios_state(name, a.crcs)
            missing = list(state["missing"])
            first = next((r["name"] for r in bios["roms"] if r.get("alt")), None)
            if not state["present"] and first:
                missing.append(f"a BIOS ROM (one of the alternatives, such as {first})")
            bios_list[name] = {
                "name": name,
                "label": self.bios_label(name),
                "description": bios["description"],
                "file": a.file,
                "complete": state["complete"],
                "missing": missing,
                "present": state["present"],
            }
        for name in sorted(names):
            game = self.games[name]
            needed = {r["crc"]: r["name"] for r in game["roms"]}
            have = available(name)
            missing = sorted(n for c, n in needed.items() if c not in have)
            own = name in by_name or (game["parent"] in by_name if game["parent"] else False)
            bios = game.get("bios")
            bios_missing = None
            bios_info = {}
            if bios and bios in self.bios:
                # MAME finds the BIOS files in the BIOS set, or in the game's own (a non-merged set)
                bios_have = have | (by_name[bios].crcs if bios in by_name and by_name[bios].bios == bios else set())
                state = self.bios_state(bios, bios_have, game.get("bios_roms"))
                if not state["complete"]:
                    bios_missing = f"{self.bios_label(bios)} ({bios}.zip)"
                bios_info = {
                    "bios_zip": bios_list[bios]["file"] if bios in bios_list else None,
                    "bios_default": game.get("default_bios") or self.bios[bios].get("default"),
                    "bios_present": state["present"],
                }
                if bios not in bios_list and bios_missing:
                    bios_list[bios] = {
                        "name": bios,
                        "label": self.bios_label(bios),
                        "description": self.bios[bios]["description"],
                        "file": None,
                        "complete": False,
                        "missing": [f"{bios}.zip"],
                        "present": [],
                    }
            listed[name] = {
                "name": name,
                "description": game["description"],
                "year": game["year"],
                "company": game["company"],
                "parent": game["parent"],
                "rotate": game["rotate"],
                "flags": game["flags"],
                "files": len(needed),
                "missing": missing + ([bios_missing] if bios_missing else []),
                "playable": own and not missing and not bios_missing,
                "zip": by_name[name].file if name in by_name else None,
                "parent_zip": by_name[game["parent"]].file if game["parent"] and game["parent"] in by_name else None,
                "bios": bios,
                "bios_missing": bios_missing,
                "bios_zip": None,
                **bios_info,
            }
        problems = [{"file": a.file, "error": a.error, "driver": (a.diagnosis or {}).get("driver")} for a in archives if a.error]
        for a in archives:
            stem, ext = split_name(a.file)
            if a.game and stem != a.game and stem not in self.games:
                problems.append(
                    {
                        "file": a.file,
                        "error": f"holds {a.game} ({self.games[a.game]['description']}); rename it to {a.game}{ext.lower()}",
                        "rename_to": f"{a.game}{ext.lower()}",
                    }
                )
            elif a.bios and stem != a.bios and stem not in self.games and stem not in self.bios:
                problems.append(
                    {
                        "file": a.file,
                        "error": f"holds the {self.bios_label(a.bios)}; rename it to {a.bios}{ext.lower()}",
                        "rename_to": f"{a.bios}{ext.lower()}",
                    }
                )
        for extra, used in doubles:
            problems.append({"file": extra.file, "error": f"{used.file} is used for this game; remove one of the two"})
        return {
            "games": list(listed.values()),
            "bios": sorted(bios_list.values(), key=lambda b: b["name"]),
            "problems": problems,
            "unrecognised": self.unrecognised(archives),
            "known": len(self.games),
        }

    # --------------------------------------------------------------- changes
    def add(self, upload: Path, original_name: str) -> dict:
        """Recognise an uploaded zip or 7z and store it under the game's own name."""
        try:
            kind = self.kind(upload)
            crcs = self.read_crcs(upload)
        except (OSError, ValueError) as e:
            raise ValueError(f"{original_name}: {e}") from e
        game = self.identify(crcs)
        bios = None if game else self.identify_bios(crcs)
        if not game and not bios:
            diagnosis = self.diagnose(original_name, crcs)
            self.remember(original_name, diagnosis)
            raise ValueError(f"{original_name}: {diagnosis['error']}")
        self.remember(original_name, None)
        if bios:
            # A BIOS set is stored under its own name too (neogeo.zip), where MAME looks for it
            game = bios
        self.rom_folder.mkdir(parents=True, exist_ok=True)
        target = self.rom_folder / f"{game}{kind}"
        replaced = []
        # The new upload replaces this game's archive in the other format
        for ext in EXTENSIONS:
            other = self.rom_folder / f"{game}{ext}"
            if ext != kind and other.is_file():
                other.unlink()
                self._cache.pop(other.name, None)
                replaced.append(other.name)
        shutil.move(str(upload), target)
        target.chmod(0o644)
        self._cache.pop(target.name, None)
        return {
            "game": game,
            "description": self.bios_label(bios) if bios else self.games[game]["description"],
            "bios": bool(bios),
            "file": target.name,
            "replaced": replaced,
        }

    def path(self, file: str) -> Path | None:
        parts = split_name(file)
        if not parts or not SAFE_NAME.match(parts[0]) or parts[1] != parts[1].lower():
            return None
        p = self.rom_folder / file
        return p if p.is_file() else None

    def remove(self, file: str) -> bool:
        # Any archive in the folder, also one dropped in with an odd name
        p = self.rom_folder / file
        if not plain_file(file) or not split_name(file) or not p.is_file():
            return False
        p.unlink()
        self._cache.pop(file, None)
        return True

    def rename(self, file: str, new: str) -> None:
        src = self.rom_folder / file
        old_parts, new_parts = split_name(file), split_name(new)
        if (
            not plain_file(file)
            or not old_parts
            or not src.is_file()
            or not new_parts
            or not SAFE_NAME.match(new_parts[0])
            or new_parts[1].lower() != old_parts[1].lower()
            or new_parts[1] != new_parts[1].lower()
        ):
            raise ValueError("invalid file name")
        dst = self.rom_folder / new
        if dst.exists():
            raise ValueError(f"{new} already exists")
        src.rename(dst)
        self._cache.pop(file, None)
