"""Pack browser-extension/ into mybrain-browser-extension.zip, the download linked from the docs.

    python3 -m tools.make_extension_zip

Run it after changing the extension; a test fails while the zip is behind the folder. The files go
in a folder "mybrain-extension" inside the zip, so unpacking gives the folder Edge loads. Fixed dates
and order: the same files give the same zip.
"""

import zipfile
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
SOURCE = APP_DIR / "browser-extension"
TARGET = APP_DIR / "mybrain-browser-extension.zip"
FOLDER = "mybrain-extension"


def extension_files() -> list[Path]:
    return sorted(p for p in SOURCE.rglob("*") if p.is_file() and not p.name.startswith("."))


def make(target: Path = TARGET) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in extension_files():
            info = zipfile.ZipInfo(f"{FOLDER}/{path.relative_to(SOURCE).as_posix()}", date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes())
    return target


if __name__ == "__main__":
    print(f"{make()} ({len(extension_files())} files)")
