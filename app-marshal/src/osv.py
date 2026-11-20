"""Known vulnerabilities in the package versions that get installed, from the OSV database (osv.dev).

What is sent: package names and versions from manifests. Nothing about your installation.
"""

import logging
import re

import aiohttp
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

BATCH_URL = "https://api.osv.dev/v1/querybatch"
DETAIL_URL = "https://api.osv.dev/v1/vulns/"
MAX_DETAILS = 300


def _fixes(data: dict) -> dict[str, list[str]]:
    """{package name: the versions a vulnerability is fixed in}, from an OSV record."""
    fixes: dict[str, list[str]] = {}
    for affected in data.get("affected") or []:
        package = affected.get("package") or {}
        if package.get("ecosystem") != "PyPI":
            continue
        name = re.sub(r"[-_.]+", "-", str(package.get("name") or "")).lower()
        for span in affected.get("ranges") or []:
            for event in span.get("events") or []:
                if event.get("fixed"):
                    fixes.setdefault(name, []).append(str(event["fixed"])[:40])
    return fixes


def fixed_after(fixes: list[str], version: str) -> str:
    """The first version after this one that has the fix, or ""."""
    try:
        have = Version(version)
    except InvalidVersion:
        return ""
    later = []
    for text in fixes:
        try:
            if Version(text) > have:
                later.append((Version(text), text))
        except InvalidVersion:
            continue
    return min(later)[1] if later else ""


async def lookup(
    session: aiohttp.ClientSession, packages: set[tuple[str, str]], details: dict[str, dict]
) -> dict[tuple[str, str], list[dict]]:
    """{(name, version): [{id, summary, severity, fixed}]} for the packages that have any. `details` is a cache of
    what is known per vulnerability, filled as needed. Raises aiohttp.ClientError or TimeoutError."""
    ordered = sorted(packages)
    found: dict[tuple[str, str], list[str]] = {}
    for start in range(0, len(ordered), 500):
        chunk = ordered[start : start + 500]
        body = {"queries": [{"package": {"name": n, "ecosystem": "PyPI"}, "version": v} for n, v in chunk]}
        async with session.post(BATCH_URL, json=body, timeout=aiohttp.ClientTimeout(total=60)) as response:
            if response.status != 200:
                raise aiohttp.ClientError(f"OSV answered {response.status}")
            results = (await response.json()).get("results") or []
        for package, result in zip(chunk, results, strict=False):
            ids = [str(v["id"]) for v in (result or {}).get("vulns") or [] if v.get("id")]
            if ids:
                found[package] = ids
    fetched = 0
    for vulnerability in sorted({i for ids in found.values() for i in ids}):
        if vulnerability in details or fetched >= MAX_DETAILS:
            continue
        fetched += 1
        try:
            async with session.get(DETAIL_URL + vulnerability, timeout=aiohttp.ClientTimeout(total=30)) as response:
                data = await response.json() if response.status == 200 else {}
        except (aiohttp.ClientError, TimeoutError, ValueError):
            continue
        aliases = [a for a in data.get("aliases") or [] if isinstance(a, str)]
        details[vulnerability] = {
            "id": next((a for a in aliases if a.startswith("CVE-")), vulnerability),
            "summary": str(data.get("summary") or "")[:200],
            "severity": str((data.get("database_specific") or {}).get("severity") or "").lower(),
            "fixes": _fixes(data),
        }
    out = {}
    for (name, version), ids in found.items():
        normal = re.sub(r"[-_.]+", "-", name).lower()
        out[(name, version)] = [
            {
                "id": known.get("id") or i,
                "summary": known.get("summary") or "",
                "severity": known.get("severity") or "",
                "fixed": fixed_after((known.get("fixes") or {}).get(normal) or [], version),
            }
            for i in ids
            for known in [details.get(i) or {}]
        ]
    return out
