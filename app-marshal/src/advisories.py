"""Security advisories an integration's own repository has published on GitHub.

The vulnerability database knows packages, not Home Assistant integrations: a hole in an integration is
announced in its repository. What is sent: the repository's name. Nothing about your installation.

GitHub answers 60 questions an hour to someone who is not logged in, so an answer is kept for three days
and a scan asks for at most 40 repositories; the rest follows with the next scans.
"""

import contextlib
import json
import logging
import os
import time
from pathlib import Path

import aiohttp
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

URL = "https://api.github.com/repos/{}/security-advisories?per_page=50"
KEEP = 3 * 86400
PER_SCAN = 40


def covers(version_range: str, version: str) -> bool | None:
    """Does a range as GitHub writes it ("< 1.4.2", ">= 1.0, < 1.2") hold the version? None: cannot tell."""
    try:
        return SpecifierSet(version_range.replace(" ", "")).contains(Version(version.lstrip("vV")), prereleases=True)
    except (InvalidSpecifier, InvalidVersion):
        return None


class Advisories:
    def __init__(self, cache_dir: Path):
        self.file = cache_dir / "advisories.json"
        self.known: dict[str, dict] = {}  # repository (lower case): {at, items: [{id, summary, severity, ranges}]}
        with contextlib.suppress(OSError, ValueError):
            stored = json.loads(self.file.read_text())
            self.known = stored if isinstance(stored, dict) else {}

    def _save(self) -> None:
        self.file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_name(self.file.name + ".tmp")
        tmp.write_text(json.dumps(self.known))
        os.replace(tmp, self.file)

    async def refresh(self, session: aiohttp.ClientSession, repositories: list[str]) -> str | None:
        """Ask GitHub for the repositories whose answer is old or missing. Returns what went wrong, if anything."""
        now = time.time()
        due = sorted({r.lower() for r in repositories if now - (self.known.get(r.lower()) or {}).get("at", 0) > KEEP})
        problem = None
        asked = 0
        for repository in due[:PER_SCAN]:
            try:
                async with session.get(
                    URL.format(repository),
                    headers={"Accept": "application/vnd.github+json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    if response.status in (403, 429):
                        problem = "GitHub answers no more questions this hour; the security advisories follow later"
                        break
                    # 404: the repository is gone, or has no advisories page
                    data = await response.json() if response.status == 200 else []
                    if response.status not in (200, 404):
                        problem = f"GitHub answered {response.status} for the security advisories"
                        continue
            except (aiohttp.ClientError, TimeoutError, ValueError) as e:
                problem = f"The security advisories could not be fetched: {type(e).__name__} {e}".strip()
                break
            asked += 1
            items = []
            for advisory in data if isinstance(data, list) else []:
                if not isinstance(advisory, dict) or advisory.get("state") != "published":
                    continue
                ranges = [
                    {
                        "range": str(v.get("vulnerable_version_range") or "")[:80],
                        "fixed": str(v.get("patched_versions") or "")[:40],
                    }
                    for v in advisory.get("vulnerabilities") or []
                    if isinstance(v, dict)
                ]
                items.append(
                    {
                        "id": str(advisory.get("cve_id") or advisory.get("ghsa_id") or "")[:40],
                        "summary": str(advisory.get("summary") or "")[:200],
                        "severity": str(advisory.get("severity") or "").lower(),
                        "ranges": ranges,
                    }
                )
            self.known[repository] = {"at": now, "items": items}
        if asked:
            self._save()
        if len(due) > PER_SCAN and not problem:
            problem = (
                f"Security advisories were fetched for {PER_SCAN} repositories; the other {len(due) - PER_SCAN} follow"
            )
        return problem

    def covering(self, repository: str | None, version: str) -> list[dict]:
        """The repository's advisories that cover this version: [{id, summary, severity, fixed}].
        One whose versions cannot be compared is left out: no alarm on a guess."""
        found = []
        for advisory in (self.known.get((repository or "").lower()) or {}).get("items") or []:
            hit = next((r for r in advisory["ranges"] if r["range"] and covers(r["range"], version)), None)
            if hit and advisory["id"]:
                found.append(
                    {
                        "id": advisory["id"],
                        "summary": advisory["summary"],
                        "severity": advisory["severity"],
                        "fixed": hit["fixed"],
                    }
                )
        return found
