"""Your own nuclei templates, pasted in the app and stored in /data.

Each is a YAML file under /data/custom-templates. They're added to every scan alongside the community
set. Before a template is saved, nuclei validates it (-validate), so a broken one can't quietly do
nothing. Names are constrained to a safe slug so a template can only ever land inside that one folder.
"""

import asyncio
import re
import shutil
from pathlib import Path

from .config import DATA_DIR, nuclei_env

CUSTOM_DIR = DATA_DIR / "custom-templates"
_SLUG = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class TemplateError(ValueError):
    """A template name or body is not acceptable."""


def _path(name: str) -> Path:
    if not _SLUG.match(name):
        raise TemplateError("name must be lower-case letters, digits, hyphen or underscore")
    path = (CUSTOM_DIR / f"{name}.yaml").resolve()
    if path.parent != CUSTOM_DIR.resolve():
        raise TemplateError("invalid name")
    return path


class CustomTemplates:
    def __init__(self, validate=None):
        CUSTOM_DIR.mkdir(parents=True, exist_ok=True)
        # Seam for tests: validate(path) -> (ok, message)
        self._validate = validate or self._validate_with_nuclei

    @staticmethod
    async def _validate_with_nuclei(path: Path) -> tuple[bool, str]:
        if not shutil.which("nuclei"):
            return True, "not validated (nuclei not available)"
        proc = await asyncio.create_subprocess_exec(
            "nuclei",
            "-validate",
            "-t",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=nuclei_env(),
        )
        out, _ = await proc.communicate()
        return proc.returncode == 0, out.decode("utf-8", "replace")[-2000:]

    def list(self) -> list[dict]:
        items = []
        for path in sorted(CUSTOM_DIR.glob("*.yaml")):
            items.append({"name": path.stem, "size": path.stat().st_size})
        return items

    def read(self, name: str) -> str:
        return _path(name).read_text(encoding="utf-8")

    async def save(self, name: str, body: str) -> dict:
        if not body.strip():
            raise TemplateError("the template is empty")
        path = _path(name)
        tmp = path.with_suffix(".yaml.checking")
        tmp.write_text(body, encoding="utf-8")
        try:
            ok, message = await self._validate(tmp)
            if not ok:
                raise TemplateError(f"nuclei rejected the template: {message.strip()[:500]}")
            tmp.replace(path)
        finally:
            tmp.unlink(missing_ok=True)
        return {"name": name, "size": path.stat().st_size}

    def delete(self, name: str) -> None:
        _path(name).unlink(missing_ok=True)

    @staticmethod
    def dir_for_scan() -> str | None:
        """The folder to hand nuclei, or None when there are no custom templates."""
        return str(CUSTOM_DIR) if any(CUSTOM_DIR.glob("*.yaml")) else None
