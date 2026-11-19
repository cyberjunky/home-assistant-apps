"""The app outside Home Assistant, for development and for watching the screens.

    python3 -m tools.mock --custom-components ~/dev/my-integration/custom_components   # http://localhost:8099

Scans the given custom_components folder (default: an empty one) with a stand-in for Home Assistant that
reports believable setup times. The catalog and the vulnerability database are the real ones; --offline
turns them off.
"""

import argparse
import asyncio
import os
import random
import sys
import tempfile
from pathlib import Path


class StandIn:
    """Home Assistant's answers: every integration set up in a second or so, none known to the Marketplace."""

    def __init__(self, custom_components: Path):
        self.custom_components = custom_components

    async def snapshot(self, session) -> dict:
        domains = (
            [p.name for p in self.custom_components.iterdir() if p.is_dir()] if self.custom_components.is_dir() else []
        )
        return {
            "setup": {d: round(random.Random(d).uniform(0.1, 3.0), 2) for d in domains},
            "log": {},
            "marketplace": None,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--custom-components", type=Path)
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="marshal-mock-"))
    config_dir = root / "homeassistant"
    config_dir.mkdir()
    if args.custom_components:
        (config_dir / "custom_components").symlink_to(args.custom_components.resolve(), target_is_directory=True)
    os.environ.setdefault("MARSHAL_APP_DATA_DIR", str(root / "data"))
    os.environ["MARSHAL_APP_HA_CONFIG"] = str(config_dir)
    os.environ.pop("SUPERVISOR_TOKEN", None)

    from src.__main__ import run
    from src.config import WEB_PORT, Config

    config = Config(catalog_lookups=not args.offline, vulnerability_lookups=not args.offline)
    print(f"Marshal on http://localhost:{WEB_PORT}")
    return asyncio.run(run(config, home_assistant=StandIn(config_dir / "custom_components")))


if __name__ == "__main__":
    sys.exit(main())
