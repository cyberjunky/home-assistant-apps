"""Known vulnerabilities in the exact versions a manifest pins, from the OSV database (osv.dev).

What is sent: package names and versions from manifests. Nothing about your installation.
"""

import logging

import aiohttp

logger = logging.getLogger(__name__)

BATCH_URL = "https://api.osv.dev/v1/querybatch"
DETAIL_URL = "https://api.osv.dev/v1/vulns/"
MAX_DETAILS = 150


async def lookup(
    session: aiohttp.ClientSession, packages: set[tuple[str, str]], details: dict[str, dict]
) -> dict[tuple[str, str], list[dict]]:
    """{(name, version): [{id, summary, severity}]} for the packages that have any. `details` is a cache of
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
        }
    return {
        package: [details.get(i) or {"id": i, "summary": "", "severity": ""} for i in ids]
        for package, ids in found.items()
    }
