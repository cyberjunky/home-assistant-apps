"""Scan integrations that are not installed here, and write the dashboard as one file.

    python3 -m tools.survey --local ~/dev --top 100 --out /tmp/survey

--local DIR   every DIR/*/custom_components/<domain> (your own work in progress), as the group "Mine"
--top N       the N integrations of the Marketplace catalog with the most installations, read from GitHub
--out DIR     where report.html and report.json go; the catalog is cached there too

Nothing is installed, imported or run. What is printed is a line per integration with its grade; the code
lines the findings quote go to the report only.
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import aiohttp

from src import osv, payload, sources
from src.analyzer import add_context, analyze, finish
from src.catalog import Catalog
from src.config import USER_AGENT

GROUPS = [{"id": "mine", "label": "Mine"}, {"id": "top", "label": "Most installed"}]
SAFE = re.compile(r"[^A-Za-z0-9_./ -]")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--local", type=Path)
    parser.add_argument("--top", type=int, default=0)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    catalog = Catalog(args.out / "catalog")
    pending: list[dict] = []  # {group, key, files, repository, entry, ref}
    failed: list[str] = []

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}) as session:
        await catalog.refresh(session)
        if args.local:
            for manifest in sorted(args.local.glob("*/custom_components/*/manifest.json")):
                files = sources.read_folder(manifest.parent)
                domain = manifest.parent.name
                pending.append(
                    {"group": "mine", "key": f"mine:{domain}", "files": files, "entry": catalog.entry(domain=domain)}
                )
        gate = asyncio.Semaphore(6)

        async def fetch(entry: dict) -> None:
            async with gate:
                try:
                    files, ref = await sources.fetch_repository(session, entry["full_name"], entry.get("last_version"))
                except sources.SourceError as e:
                    failed.append(f"{entry['full_name']}: {e}")
                    return
            pending.append(
                {"group": "top", "key": f"top:{entry['full_name'].lower()}", "files": files, "entry": entry, "ref": ref}
            )

        await asyncio.gather(*(fetch(e) for e in catalog.top(args.top)))

        firsts = [analyze(p["files"]) for p in pending]
        packages = {(r["name"].lower(), r["version"]) for f in firsts for r in f["requirements"] if r["version"]}
        try:
            vulnerable = await osv.lookup(session, packages, {}) if packages else {}
        except (aiohttp.ClientError, TimeoutError) as e:
            print(f"vulnerability lookup failed: {type(e).__name__}", file=sys.stderr)
            vulnerable = {}

    reports = []
    for item in pending:
        entry = item["entry"] or {}
        report = analyze(item["files"])
        add_context(
            report, catalog=item["entry"], removed=catalog.removed(entry.get("full_name")), vulnerabilities=vulnerable
        )
        finish(report)
        domain = report["domain"] or item["key"].split(":", 1)[1]
        report.update(
            key=item["key"],
            group=item["group"],
            domain=domain,
            name=report["name"] or domain,
            ref=item.get("ref"),
            installs=catalog.installs(domain),
        )
        reports.append(report)
    reports.sort(key=lambda r: (r["group"], r["score"]))
    for report in reports:
        counts = " ".join(f"{s[0]}{report['counts'][s]}" for s in ("critical", "high", "medium", "low"))
        print(
            f"{report['group']:5} {report['grade']} {report['score']:3}  {counts:16} {SAFE.sub('?', report['domain'])[:40]}"
        )
    for line in failed:
        print("failed", SAFE.sub("?", line)[:120], file=sys.stderr)

    document = payload.build(reports, GROUPS, history=[], status={})
    (args.out / "report.json").write_text(json.dumps(document))
    (args.out / "report.html").write_text(payload.render_static(document, "Marshal survey"), encoding="utf-8")
    print(f"{len(reports)} integrations, {len(failed)} failed -> {args.out / 'report.html'}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
