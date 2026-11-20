"""Code quality and security lint, measured with Ruff (docs.astral.sh/ruff).

Ruff reads Python as text, like Marshal's own analyzer: nothing is imported or run. Each file is handed to it
on standard input, so nothing is written to disk either. The integration's own `# noqa` comments and Ruff
settings are ignored: every integration is held to the same list.

Ruff is a separate program (installed with pip in the image). Where it is missing, lint() returns None and
the report says the quality was not measured.
"""

import hashlib
import json
import logging
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

from .analyzer import Files
from .analyzer.lint_rules import RULE_FOR

logger = logging.getLogger(__name__)

# The limit Home Assistant holds its own code to
MAX_COMPLEXITY = 25
MAX_FILE_BYTES = 2 * 1024 * 1024
WORKERS = 4
TIMEOUT = 60
KEEP = 300  # results kept in memory, by what the files hold

_program: str | None = None
_version = ""
_kept: dict[str, dict] = {}


def _command(name: str) -> list[str]:
    return [
        _program,
        "check",
        "--isolated",
        "--no-cache",
        "--exit-zero",
        "--ignore-noqa",
        "--output-format=json",
        "--target-version=py314",
        f"--select={','.join(RULE_FOR)}",
        f"--config=lint.mccabe.max-complexity={MAX_COMPLEXITY}",
        # Only its name is used, to tell an __init__.py; given in one piece so a strange name is never an option
        f"--stdin-filename={name}",
        "-",
    ]


def program() -> str | None:
    """Where Ruff is, or None: not installed, or it does not take the list of checks (a version that
    renamed one). Tried once, on an empty file."""
    global _program, _version
    if _program is None:
        _program = shutil.which("ruff") or ""
        why = "not found"
        if _program:
            try:
                out = subprocess.run([_program, "--version"], capture_output=True, timeout=TIMEOUT, check=False)
                _version = out.stdout.decode(errors="replace").strip().removeprefix("ruff").strip()
                out = subprocess.run(_command("empty.py"), input=b"", capture_output=True, timeout=TIMEOUT, check=False)
                if out.returncode:
                    why = out.stderr.decode(errors="replace").strip()[:300]
                    _program = ""
            except (OSError, subprocess.SubprocessError) as e:
                why = str(e)
                _program = ""
        if not _program:
            logger.warning("Ruff cannot be used, code quality is not measured: %s", why)
    return _program or None


def _one(path: str, data: bytes) -> list[dict]:
    try:
        out = subprocess.run(
            _command(path.rsplit("/", 1)[-1]), input=data, capture_output=True, timeout=TIMEOUT, check=False
        )
        found = json.loads(out.stdout or b"[]")
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        logger.debug("Ruff could not read %s: %s", path, e)
        return []
    issues = []
    for item in found if isinstance(found, list) else []:
        code = str(item.get("code") or "")
        # What an __init__.py imports and does not use, it usually passes on
        if code not in RULE_FOR or (code == "F401" and path.endswith("__init__.py")):
            continue
        issues.append(
            {
                "code": code,
                "file": path,
                "line": int((item.get("location") or {}).get("row") or 0),
                "message": str(item.get("message") or "")[:160],
            }
        )
    return issues


def lint(files: Files) -> dict | None:
    """What Ruff says of an integration's Python: {tool, issues: [{code, file, line, message}]}, or None
    without Ruff. Blocking: run it in a worker thread."""
    if not program():
        return None
    python = sorted(
        (path, data)
        for path, (size, data) in files.items()
        if path.lower().endswith(".py") and size == len(data) and size <= MAX_FILE_BYTES
    )
    digest = hashlib.sha256()
    for path, data in python:
        digest.update(path.encode() + b"\0" + hashlib.sha256(data).digest())
    key = digest.hexdigest()
    if key not in _kept:
        with ThreadPoolExecutor(WORKERS) as pool:
            issues = [issue for found in pool.map(lambda item: _one(*item), python) for issue in found]
        if len(_kept) >= KEEP:
            _kept.clear()
        _kept[key] = {"tool": f"Ruff {_version}".strip(), "issues": issues}
    return _kept[key]
