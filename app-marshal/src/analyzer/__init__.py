"""Marshal's analyzer: reads the files of one integration and says what deserves a look.

    report = analyze(files)          # what the code itself says
    add_context(report, ...)         # what the catalog, the vulnerability database and Home Assistant add
    finish(report)                   # scores and grade

Only the standard library, and nothing from the integration is imported or run: Python is parsed to a
syntax tree, the manifest is parsed as JSON, and every other file is only measured.
"""

import json
import re
import time
from datetime import datetime

from . import hosts as host_rules
from .pyscan import Facts, scan
from .rules import CATEGORIES, CATEGORY_WEIGHT, RULES, SEVERITIES, WEIGHT

__all__ = ["CATEGORIES", "RULES", "SEVERITIES", "add_context", "analyze", "finish", "grade", "parse_requirement"]

# files: {path inside the integration: (size in bytes, the content, or its first bytes for what is not read)}
Files = dict[str, tuple[int, bytes]]

MAX_PYTHON_BYTES = 2 * 1024 * 1024
MAX_OCCURRENCES = 20
BINARY_SUFFIXES = (".so", ".dll", ".dylib", ".pyd", ".exe", ".elf", ".o", ".a")
BINARY_MAGIC = (b"\x7fELF", b"MZ\x90\x00", b"\xcf\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xca\xfe\xba\xbe")
REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")


def grade(score: float) -> str:
    return "A" if score >= 90 else "B" if score >= 80 else "C" if score >= 65 else "D" if score >= 50 else "F"


def parse_requirement(text: str) -> dict:
    """A manifest requirement: its name, whether it is one exact version, and whether it comes from a URL."""
    text = str(text).strip()
    if "://" in text or text.startswith("git+") or " @ " in text:
        name = re.split(r"\s*@\s*|[#&]egg=", text)[0 if " @ " in text else -1]
        return {"name": name.strip()[:80] or "?", "spec": "", "version": None, "pinned": False, "url": True}
    match = REQUIREMENT.match(text.split(";")[0])
    if not match:
        return {"name": text[:80], "spec": "", "version": None, "pinned": False, "url": False}
    spec = match.group(3).strip()
    pinned = re.fullmatch(r"==\s*([A-Za-z0-9][A-Za-z0-9.!+_-]*)", spec)
    return {
        "name": match.group(1),
        "spec": spec[:60],
        "version": pinned.group(1) if pinned and "*" not in spec else None,
        "pinned": bool(pinned) and "*" not in spec,
        "url": False,
    }


class _Hits:
    """Findings as they come in: one per rule, with the places it was seen."""

    def __init__(self, skip: tuple[str, ...] = ()) -> None:
        self.found: dict[str, dict] = {}
        self.skip = skip

    def add(self, rule: str, file: str = "", line: int = 0, note: str = "", snippet: str = "") -> None:
        if self.skip and rule.startswith(self.skip):
            return
        finding = self.found.setdefault(rule, {"rule": rule, "count": 0, "where": []})
        if any(w["file"] == file and w["line"] == line for w in finding["where"]):
            return
        finding["count"] += 1
        if len(finding["where"]) < MAX_OCCURRENCES:
            finding["where"].append({"file": file, "line": line, "note": str(note)[:160], "snippet": snippet[:200]})


def _manifest(files: Files) -> dict | None:
    entry = files.get("manifest.json")
    if not entry:
        return None
    try:
        manifest = json.loads(entry[1].decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        return None
    return manifest if isinstance(manifest, dict) else None


def analyze(files: Files, package: bool = False) -> dict:
    """Everything the files of one integration say. Add context and call finish() for the scores.

    package: the files are a library from PyPI, not an integration. It has no manifest to judge, a compiled
    part is normal, and whether its calls block is for the integration that makes them.
    """
    hits = _Hits(skip=("performance.", "upkeep.") if package else ())
    facts = Facts()
    manifest = _manifest(files)
    stats = {"files": len(files), "python_files": 0, "lines": 0, "bytes": sum(size for size, _ in files.values())}
    frontend_bytes = 0

    for path, (size, data) in sorted(files.items()):
        lower = path.lower()
        if lower.endswith(".py"):
            stats["python_files"] += 1
            if size > MAX_PYTHON_BYTES:
                hits.add("code.unparseable", path, 0, "larger than 2 MB")
                continue
            source = data.decode("utf-8", errors="replace")
            lines = source.splitlines()

            def hit(rule: str, line: int, note: str, path: str = path, lines: list[str] = lines) -> None:
                snippet = lines[line - 1].strip() if 0 < line <= len(lines) else ""
                if rule == "data.hardcoded_secret":
                    snippet = ""  # the report must not carry the secret further
                hits.add(rule, path, line, note, snippet)

            if not scan(path, source, hit, facts):
                hits.add("code.unparseable", path, 0, "syntax error")
        elif lower.endswith(BINARY_SUFFIXES) or data[:4] in BINARY_MAGIC:
            hits.add("code.compiled" if package else "code.binary", path, 0, f"{size // 1024} kB")
        elif lower.endswith(".pyc") and "__pycache__" not in lower:
            if lower[:-1] not in {p.lower() for p in files}:
                hits.add("code.bytecode_only", path, 0, "")
        elif lower.endswith((".sh", ".bash")):
            hits.add("code.shell_script", path, 0, "")
        elif lower.endswith((".js", ".mjs")):
            frontend_bytes += size
    stats["lines"] = facts.lines
    if frontend_bytes and not package:
        hits.add("code.frontend", "", 0, f"{frontend_bytes // 1024} kB")
    if stats["lines"] > 25000 or stats["bytes"] > 20 * 1024 * 1024:
        hits.add("performance.large", "", 0, f"{stats['lines']} lines, {stats['bytes'] // 1024} kB")
    if facts.diagnostics and not facts.diagnostics_redacted and not package:
        hits.add("data.diagnostics_unredacted", "diagnostics.py", 0, "")

    report = {
        "domain": (manifest or {}).get("domain") or "",
        "name": (manifest or {}).get("name") or "",
        "version": str((manifest or {}).get("version") or ""),
        "iot_class": (manifest or {}).get("iot_class") or "",
        "config_flow": bool((manifest or {}).get("config_flow")),
        "codeowners": [str(o) for o in (manifest or {}).get("codeowners") or [] if isinstance(o, str)][:10],
        "documentation": str((manifest or {}).get("documentation") or "")[:300],
        "stats": stats,
        "requirements": [],
        "hosts": [],
        "scanned_at": int(time.time()),
        "_hits": hits,
        "_facts": facts,
    }
    if not package:
        _manifest_rules(report, manifest, hits)
    _network_rules(report, facts, hits)
    _interval_rules(report, facts, hits)
    return report


def _manifest_rules(report: dict, manifest: dict | None, hits: _Hits) -> None:
    if manifest is None:
        hits.add("upkeep.no_manifest", "manifest.json")
        return
    if not manifest.get("version"):
        hits.add("upkeep.no_version", "manifest.json")
    if not manifest.get("codeowners"):
        hits.add("upkeep.no_owner", "manifest.json")
    if not manifest.get("issue_tracker"):
        hits.add("upkeep.no_issue_tracker", "manifest.json")
    if not manifest.get("iot_class") and manifest.get("integration_type") not in ("helper", "system", "virtual"):
        hits.add("upkeep.no_iot_class", "manifest.json")
    if not manifest.get("config_flow"):
        hits.add("upkeep.yaml_only", "manifest.json")
    requirements = manifest.get("requirements")
    for index, text in enumerate(requirements if isinstance(requirements, list) else []):
        requirement = parse_requirement(text)
        report["requirements"].append(requirement)
        if requirement["url"]:
            hits.add("code.url_requirement", "manifest.json", index + 1, requirement["name"])
        elif not requirement["pinned"]:
            hits.add(
                "upkeep.unpinned_requirement", "manifest.json", index + 1, f"{requirement['name']}{requirement['spec']}"
            )


def _network_rules(report: dict, facts: Facts, hits: _Hits) -> None:
    seen: dict[str, dict] = {}
    for scheme, rest, file, line in facts.urls:
        address = host_rules.classify(scheme, rest)
        if address is None:
            continue
        host = seen.setdefault(address["host"], {**address, "count": 0, "file": file, "line": line})
        host["count"] += 1
        host["insecure"] = host["insecure"] or address["insecure"]
        host["download"] = host["download"] or address["download"]
        rule = {"dropbox": "network.dropbox_host", "chat": "network.chat_webhook", "telemetry": "data.telemetry"}.get(
            address["kind"]
        )
        if address["kind"] == "ip":
            rule = "network.ip_literal"
        if rule:
            hits.add(rule, file, line, address["host"])
        if address["download"]:
            hits.add("code.downloads", file, line, address["host"])
        if address["insecure"] and address["kind"] not in ("reference",):
            hits.add("network.plain_http", file, line, address["host"])
    order = {"dropbox": 0, "ip": 1, "chat": 2, "telemetry": 3, "external": 4, "reference": 5, "local": 6}
    report["hosts"] = sorted(seen.values(), key=lambda h: (order[h["kind"]], h["host"]))
    outside = [h for h in report["hosts"] if h["kind"] not in ("reference", "local")]
    sites = sorted({h["site"] for h in outside})
    if sites:
        first = outside[0]
        hits.add("network.external_hosts", first["file"], first["line"], ", ".join(sites[:12]))
    if len(sites) > 6:
        hits.add("network.many_hosts", "", 0, f"{len(sites)} services")
    if str(report["iot_class"]).startswith("local") and sites:
        first = outside[0]
        hits.add("network.local_claims_cloud", first["file"], first["line"], ", ".join(sites[:8]))
    if facts.reads_all_states and sites:
        file, line = facts.reads_all_states
        hits.add("data.states_egress", file, line, ", ".join(sites[:6]))


def _interval_rules(report: dict, facts: Facts, hits: _Hits) -> None:
    cloud = str(report["iot_class"]).startswith("cloud")
    for seconds, file, line in facts.intervals:
        if cloud and seconds < 30:
            hits.add("performance.fast_poll", file, line, f"every {seconds:g} s")
        elif seconds < 5:
            hits.add("performance.tight_interval", file, line, f"every {seconds:g} s")


def add_context(
    report: dict,
    *,
    catalog: dict | None = None,
    removed: dict | None = None,
    marketplace: dict | None = None,
    installed: bool = False,
    vulnerabilities: dict[tuple[str, str], list[dict]] | None = None,
    runtime: dict | None = None,
    packages: dict[tuple[str, str], dict] | None = None,
    package_reports: dict[str, dict] | None = None,
    now: float | None = None,
) -> None:
    """What the code cannot say: the catalog's record, known vulnerabilities, and how it behaves in Home Assistant.

    catalog: the Marketplace catalog's entry; removed: its removal record; marketplace: what Home Assistant's
    Marketplace says of the install ({custom, installed_version, available_version, pending_upgrade});
    vulnerabilities: {(requirement name, version): [{id, summary, severity}]}; runtime: {setup_seconds, errors, warnings};
    packages: {(requirement name, spec): {status, version, key, reason}} and package_reports: {key: report}, what
    reading the requirements from PyPI gave.
    """
    hits: _Hits = report["_hits"]
    now = now or time.time()
    if removed:
        critical = removed.get("removal_type") == "critical"
        hits.add("upkeep.removed" if critical else "upkeep.delisted", "", 0, str(removed.get("reason") or "")[:160])
    if catalog:
        for key in ("full_name", "stargazers_count", "downloads", "last_updated", "last_version", "open_issues"):
            if catalog.get(key) is not None:
                report[{"full_name": "repository", "stargazers_count": "stars"}.get(key, key)] = catalog[key]
        try:
            updated = datetime.fromisoformat(str(catalog.get("last_updated")).replace("Z", "+00:00")).timestamp()
        except ValueError:
            updated = None
        if updated:
            years = (now - updated) / (365 * 86400)
            if years > 2:
                hits.add("upkeep.abandoned", "", 0, f"{years:.1f} years")
            elif years > 1:
                hits.add("upkeep.stale", "", 0, f"{years:.1f} years")
    if marketplace:
        report["repository"] = marketplace.get("full_name") or report.get("repository")
        if marketplace.get("custom") and not removed:
            hits.add("upkeep.custom_repository", "", 0, str(marketplace.get("full_name") or ""))
        if marketplace.get("pending_upgrade"):
            versions = f"{marketplace.get('installed_version') or '?'} → {marketplace.get('available_version') or '?'}"
            hits.add("upkeep.update_available", "", 0, versions)
    elif installed and not catalog:
        hits.add("upkeep.manual")
    known_hosts = {h["host"] for h in report["hosts"]}
    for number, requirement in enumerate(report["requirements"]):
        package = (packages or {}).get((requirement["name"].lower(), requirement["spec"])) or {}
        requirement["status"] = package.get("status")
        requirement["resolved"] = package.get("version") or requirement["version"]
        read = (package_reports or {}).get(package.get("key") or "")
        if package.get("status") == "missing":
            hits.add("code.dependency_missing", "manifest.json", number + 1, requirement["name"])
        elif package.get("status") == "error":
            requirement["reason"] = package.get("reason")
            hits.add(
                "code.dependency_unread", "manifest.json", number + 1, f"{requirement['name']}: {package.get('reason')}"
            )
        if read:
            requirement.update(package=read["key"], grade=read["grade"], score=read["score"], counts=read["counts"])
            # Only what is serious in a library counts against the integration that brings it in
            for severity, rule in (("critical", "code.dependency_critical"), ("high", "code.dependency_findings")):
                for finding in [f for f in read["findings"] if f["severity"] == severity][:6]:
                    note = f"{requirement['name']} {read['version']}: {finding['title']}"
                    place = finding["where"][0] if finding["where"] else {}
                    hits.add(rule, f"{requirement['name']}/{place.get('file', '')}", place.get("line", 0), note)
            # What the library talks to, the integration talks to
            for host in read["hosts"]:
                if host["host"] not in known_hosts and host["kind"] not in ("reference", "local"):
                    known_hosts.add(host["host"])
                    report["hosts"].append({**host, "via": requirement["name"]})
        found = (vulnerabilities or {}).get((requirement["name"].lower(), requirement["resolved"])) or []
        requirement["vulnerabilities"] = found
        for index, vulnerability in enumerate(found):
            note = f"{requirement['name']} {requirement['resolved']}: {vulnerability['id']}"
            hits.add(
                "code.vulnerable_dependency", "manifest.json", 1000 + index, note, vulnerability.get("summary") or ""
            )
    if runtime:
        report["runtime"] = runtime
        seconds = runtime.get("setup_seconds")
        if seconds is not None and seconds >= 10:
            hits.add("performance.slow_setup", "", 0, f"{seconds:.1f} s")
        errors, warnings = runtime.get("errors") or 0, runtime.get("warnings") or 0
        if errors >= 20:
            hits.add("performance.log_errors", "", 0, f"{errors} errors, {warnings} warnings")
        elif errors or warnings:
            hits.add("performance.log_warnings", "", 0, f"{errors} errors, {warnings} warnings")


def finish(report: dict) -> dict:
    """Turn the hits into findings, scores and a grade. Returns the report, ready to store as JSON."""
    hits: _Hits = report.pop("_hits")
    report.pop("_facts", None)
    local = str(report["iot_class"]).startswith("local")
    findings = []
    penalties = dict.fromkeys(CATEGORIES, 0.0)
    for rule_id, finding in hits.found.items():
        rule = RULES[rule_id]
        severity = rule.severity
        # A device on your own network with its own certificate: the usual reason, and a small risk
        if rule_id == "network.tls_off" and local:
            severity = "low"
        # More places weigh more, up to twice: thirty of the same do not make a package thirty times worse
        penalty = WEIGHT[severity] * (1 + 0.25 * min(finding["count"] - 1, 4))
        penalties[rule.category] += penalty
        findings.append(
            {**finding, "category": rule.category, "severity": severity, "title": rule.title, "why": rule.why}
        )
    findings.sort(key=lambda f: (SEVERITIES.index(f["severity"]), f["category"], f["rule"]))
    scores = {category: max(0, round(100 - penalty)) for category, penalty in penalties.items()}
    weighted = sum(scores[c] * w for c, w in CATEGORY_WEIGHT.items())
    # One bad category is not averaged away by four good ones
    score = round(min(weighted, min(scores.values()) + 15))
    # Nothing with a critical finding passes
    if any(f["severity"] == "critical" for f in findings):
        score = min(score, 45)
    report.update(
        findings=findings,
        scores=scores,
        score=score,
        grade=grade(score),
        counts={s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES},
    )
    return report
