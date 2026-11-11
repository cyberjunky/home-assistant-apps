"""What MAME writes per game, kept on the server so it survives the browser and follows you.

The browser copies these files into MAME's in-memory file system before a game starts and
sends them back while it runs and when it stops: high scores (hiscore/), NVRAM (nvram/), the
game's settings and inputs (cfg/) and save states (sta/).
"""

import re
from pathlib import Path

KINDS = {"hiscore", "nvram", "cfg", "sta"}
# kind/<game>... ; a few levels deep at most, plain names only
PATH_RE = re.compile(r"^(hiscore|nvram|cfg|sta)/[A-Za-z0-9_.\-]{1,64}(/[A-Za-z0-9_.\-]{1,64}){0,2}$")
MAX_FILE = 32 * 1024 * 1024


class Saves:
    def __init__(self, root: Path):
        self.root = root

    @staticmethod
    def valid(game: str, rel: str) -> bool:
        if not PATH_RE.match(rel) or ".." in rel:
            return False
        _kind, rest = rel.split("/", 1)
        # Only this game's own files: hiscore/<game>.hi, cfg/<game>.cfg, nvram/<game>/..., sta/<game>/...
        return rest == f"{game}.hi" or rest == f"{game}.cfg" or rest.startswith(f"{game}/")

    def list(self, game: str) -> list[dict]:
        out = []
        for kind in sorted(KINDS):
            base = self.root / kind
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*")):
                if p.is_file():
                    rel = p.relative_to(self.root).as_posix()
                    if self.valid(game, rel):
                        st = p.stat()
                        out.append({"path": rel, "size": st.st_size, "mtime": st.st_mtime})
        return out

    def path(self, game: str, rel: str) -> Path | None:
        return self.root / rel if self.valid(game, rel) else None

    def write(self, game: str, rel: str, data: bytes) -> None:
        p = self.path(game, rel)
        if p is None:
            raise ValueError("invalid path")
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)

    def delete_game(self, game: str) -> int:
        n = 0
        for item in self.list(game):
            (self.root / item["path"]).unlink(missing_ok=True)
            n += 1
        return n

    def high_score_games(self) -> set[str]:
        base = self.root / "hiscore"
        return {p.stem for p in base.glob("*.hi")} if base.is_dir() else set()
