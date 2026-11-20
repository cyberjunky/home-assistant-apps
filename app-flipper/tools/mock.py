"""The app outside Home Assistant, with a stand-in Flipper, for development and for watching the screens.

    python3 -m tools.mock                       # http://localhost:8099, a stand-in Flipper
    python3 -m tools.mock --device /dev/ttyACM0 # a real Flipper on this machine

Projects and the SDK go to a temporary folder (or --data). Building C apps needs ufbt on this machine
(pip install ufbt); JavaScript projects, the screen, files and the console work without.
"""

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--device", help="a real Flipper's serial port instead of the stand-in")
    parser.add_argument("--data", type=Path, help="where projects and the SDK are kept")
    args = parser.parse_args()
    root = args.data or Path(tempfile.mkdtemp(prefix="flipper-mock-"))
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("FLIPPER_APP_DATA_DIR", str(root))
    os.environ.pop("SUPERVISOR_TOKEN", None)

    from src.__main__ import run
    from src.config import WEB_PORT, Config
    from tools.fake_flipper import FakeFlipper

    async def serve() -> int:
        device = args.device
        if not device:
            fake = FakeFlipper(root / "fake-flipper")
            fake.start()
            device = fake.path
            (fake.root / "ext" / "subghz").mkdir(exist_ok=True)
            (fake.root / "ext" / "infrared").mkdir(exist_ok=True)
            (fake.root / "ext" / "apps" / "Tools" / "hello.fap").write_bytes(b"\x7fELF" + bytes(4000))
            (fake.root / "ext" / "infrared" / "tv.ir").write_text("Filetype: IR signals file\nVersion: 1\n")
        print(f"Flipper on http://localhost:{WEB_PORT} (device {device}, data in {root})")
        return await run(Config(device=device))

    return asyncio.run(serve())


if __name__ == "__main__":
    sys.exit(main())
