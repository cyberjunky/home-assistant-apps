"""The app keeps everything under its data folder, read from the environment when its modules load: point it at
a fresh temporary folder before any test imports them."""

import os
import tempfile
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="marshal-tests-"))
os.environ["MARSHAL_APP_DATA_DIR"] = str(_ROOT / "data")
os.environ["MARSHAL_APP_HA_CONFIG"] = str(_ROOT / "homeassistant")
os.environ.pop("SUPERVISOR_TOKEN", None)

HA_CONFIG = _ROOT / "homeassistant"
