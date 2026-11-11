"""The ROM library: which games the uploaded zips hold, and whether they are complete.

A zip stores the CRC32 of every file in it, so a zip is recognised by its contents (not
its name) against the ROM lists of the drivers in this app's MAME build (games.json).
A clone game needs its own files plus those it shares with its parent, which MAME also
finds in the parent's zip.
"""

import json
import logging
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

SAFE_NAME = re.compile(r"^[a-z0-9_]{1,32}$")


@dataclass
class ZipInfo:
    file: str
    size: int
    crcs: set[str] = field(default_factory=set)
    game: str | None = None
    error: str | None = None


class Library:
    def __init__(self, games_file: Path, rom_folder: Path):
        self.rom_folder = rom_folder
        try:
            self.games: dict[str, dict] = json.loads(games_file.read_text(encoding="utf-8"))["games"]
        except (OSError, ValueError, KeyError) as e:
            logger.error(f"Cannot read the game list {games_file}: {e}")
            self.games = {}
        self.by_crc: dict[str, set[str]] = {}
        for name, game in self.games.items():
            for rom in game["roms"]:
                self.by_crc.setdefault(rom["crc"], set()).add(name)
        self._cache: dict[str, tuple[float, int, ZipInfo]] = {}
        logger.info(f"MAME build knows {len(self.games)} games; ROM folder {rom_folder}")

    # ------------------------------------------------------------------ zips
    @staticmethod
    def read_crcs(path: Path) -> set[str]:
        with zipfile.ZipFile(path) as z:
            return {f"{i.CRC:08x}" for i in z.infolist() if not i.is_dir()}

    def identify(self, crcs: set[str]) -> str | None:
        """The game whose own ROM set this zip covers best (a parent wins a tie with its clones)."""
        candidates: dict[str, int] = {}
        for crc in crcs:
            for name in self.by_crc.get(crc, ()):
                candidates[name] = candidates.get(name, 0) + 1
        best, best_key = None, None
        for name, hits in candidates.items():
            roms = {r["crc"] for r in self.games[name]["roms"]}
            if not roms:
                continue
            # Files of this game in the zip, then how much of the game is covered, then parents first
            key = (hits / len(roms), hits, self.games[name]["parent"] is None, -len(roms))
            if best_key is None or key > best_key:
                best, best_key = name, key
        if best_key is None or best_key[0] < 0.25:
            return None
        return best

    def scan_zip(self, path: Path) -> ZipInfo:
        st = path.stat()
        cached = self._cache.get(path.name)
        if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
            return cached[2]
        info = ZipInfo(file=path.name, size=st.st_size)
        try:
            info.crcs = self.read_crcs(path)
            info.game = self.identify(info.crcs)
            if not info.game:
                info.error = "not a game this app's MAME can run"
        except (OSError, zipfile.BadZipFile) as e:
            info.error = f"not a valid zip file ({e})"
        self._cache[path.name] = (st.st_mtime, st.st_size, info)
        return info

    def zips(self) -> list[ZipInfo]:
        if not self.rom_folder.is_dir():
            return []
        return [self.scan_zip(p) for p in sorted(self.rom_folder.glob("*.zip")) if p.is_file()]

    # ----------------------------------------------------------------- games
    def status(self) -> dict:
        """Every game that a zip in the folder is for, with what it still misses."""
        zips = self.zips()
        by_name = {z.file[:-4]: z for z in zips}
        listed: dict[str, dict] = {}

        def available(name: str) -> set[str]:
            crcs = set(by_name[name].crcs) if name in by_name else set()
            parent = self.games.get(name, {}).get("parent")
            if parent and parent in by_name:
                crcs |= by_name[parent].crcs
            return crcs

        names = set()
        for z in zips:
            # A zip counts for the game it is named after (a misnamed one is only a problem below)
            if z.game and z.file[:-4] in self.games:
                names.add(z.file[:-4])
        # Clones that a parent zip (a merged set) already holds completely
        for name, game in self.games.items():
            if game["parent"] and game["parent"] in by_name and name not in names:
                needed = {r["crc"] for r in game["roms"]}
                if needed and needed <= available(name):
                    names.add(name)
        for name in sorted(names):
            game = self.games[name]
            needed = {r["crc"]: r["name"] for r in game["roms"]}
            have = available(name)
            missing = sorted(n for c, n in needed.items() if c not in have)
            own_zip = name in by_name or (game["parent"] in by_name if game["parent"] else False)
            listed[name] = {
                "name": name,
                "description": game["description"],
                "year": game["year"],
                "company": game["company"],
                "parent": game["parent"],
                "rotate": game["rotate"],
                "flags": game["flags"],
                "files": len(needed),
                "missing": missing,
                "playable": own_zip and not missing,
                "zip": f"{name}.zip" if name in by_name else None,
                "parent_zip": f"{game['parent']}.zip" if game["parent"] and game["parent"] in by_name else None,
            }
        problems = [{"file": z.file, "error": z.error} for z in zips if z.error] + [
            {
                "file": z.file,
                "error": f"holds {z.game} ({self.games[z.game]['description']}); rename it to {z.game}.zip",
                "rename_to": f"{z.game}.zip",
            }
            for z in zips
            if z.game and z.file[:-4] != z.game and z.file[:-4] not in self.games
        ]
        return {"games": list(listed.values()), "problems": problems, "known": len(self.games)}

    # --------------------------------------------------------------- changes
    def add(self, upload: Path, original_name: str) -> dict:
        """Recognise an uploaded zip and store it under the game's own name."""
        try:
            crcs = self.read_crcs(upload)
        except (OSError, zipfile.BadZipFile) as e:
            raise ValueError(f"{original_name} is not a valid zip file ({e})") from e
        game = self.identify(crcs)
        if not game:
            raise ValueError(f"{original_name} is not a game this app's MAME build can run")
        self.rom_folder.mkdir(parents=True, exist_ok=True)
        target = self.rom_folder / f"{game}.zip"
        shutil.move(str(upload), target)
        target.chmod(0o644)
        self._cache.pop(target.name, None)
        return {"game": game, "description": self.games[game]["description"], "file": target.name}

    def path(self, file: str) -> Path | None:
        name = file[:-4] if file.endswith(".zip") else ""
        if not SAFE_NAME.match(name):
            return None
        p = self.rom_folder / file
        return p if p.is_file() else None

    def remove(self, file: str) -> bool:
        # Any zip in the folder, also one dropped in with an odd name
        p = self.rom_folder / file
        if "/" in file or "\\" in file or file.startswith(".") or not file.endswith(".zip") or not p.is_file():
            return False
        p.unlink()
        self._cache.pop(file, None)
        return True

    def rename(self, file: str, new: str) -> None:
        src = self.rom_folder / file
        if (
            "/" in file
            or "\\" in file
            or file.startswith(".")
            or not src.is_file()
            or not SAFE_NAME.match(new[:-4] if new.endswith(".zip") else "")
        ):
            raise ValueError("invalid file name")
        dst = self.rom_folder / new
        if dst.exists():
            raise ValueError(f"{new} already exists")
        src.rename(dst)
        self._cache.pop(file, None)
