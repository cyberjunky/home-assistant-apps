"""The app outside Home Assistant, with a stand-in Home Assistant and a made-up KNX installation, for
development and for looking at the screens.

    python3 -m tools.mock                 # http://localhost:8023, telegrams through the stand-in Home Assistant
    python3 -m tools.mock --bus           # also a virtual KNX bus with an interface to find and connect to
    python3 -m tools.mock --history 7     # start with seven days of recorded telegrams

The app's data goes to a temporary folder (or --data).
"""

import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, help="where the app keeps its data")
    parser.add_argument("--bus", action="store_true", help="run a virtual KNX bus too (multicast on port 13671)")
    parser.add_argument("--history", type=float, default=0, help="days of recorded telegrams to start with")
    parser.add_argument("--remove", action="store_true", help="switch on removing stray data")
    parser.add_argument("--ha-port", type=int, default=18523)
    args = parser.parse_args()
    root = args.data or Path(tempfile.mkdtemp(prefix="knxray-mock-"))
    root.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("KNXRAY_DATA_DIR", str(root))
    os.environ["KNXRAY_SUPERVISOR_URL"] = f"http://127.0.0.1:{args.ha_port}"
    os.environ.setdefault("SUPERVISOR_TOKEN", "mock")
    os.environ.setdefault("KNXRAY_MULTICAST_PORT", "13671")

    from src.__main__ import run
    from src.config import WEB_PORT, Config
    from tools.mock_ha import MockHA

    async def serve() -> int:
        if args.history:
            from tools.fake_history import write_history

            count = write_history(root / "telegrams.db", args.history)
            print(f"Wrote {count} telegrams of history")
        # With the virtual bus the devices talk there; Home Assistant then only reports what it hears
        await MockHA(simulate=not args.bus).start(args.ha_port)
        if args.bus:
            from tools.fake_bus import FakeBus

            await FakeBus().start()
        print(f"KNX-Ray on http://localhost:{WEB_PORT} (data in {root})")
        return await run(Config(allow_remove=args.remove))

    return asyncio.run(serve())


if __name__ == "__main__":
    sys.exit(main())
