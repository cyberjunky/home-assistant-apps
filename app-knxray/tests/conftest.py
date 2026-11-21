"""The app keeps everything under its data folder, read from the environment when its modules load: point it at
a fresh temporary folder before any test imports them."""

import os
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="knxray-tests-"))
os.environ["KNXRAY_DATA_DIR"] = str(_ROOT / "data")
os.environ.pop("SUPERVISOR_TOKEN", None)
os.environ.pop("KNXRAY_SUPERVISOR_URL", None)
(_ROOT / "data").mkdir()

ROOT = _ROOT
