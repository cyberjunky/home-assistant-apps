"""Running the scanners: naabu for open ports, then nuclei for vulnerabilities.

The flow for one target:
  1. naabu finds open ports (skipped for a URL target, which already names host and port).
  2. nuclei runs against host:port pairs. It carries the whole community template set, so every
     applicable CVE and exposure template is tried; its matchers mean only real hits are reported,
     and -automatic-scan fingerprints each host so only templates relevant to the detected technology
     run. Templates are kept current in /data (see update_templates).
  3. nuclei's JSONL output becomes findings (tickets).

Everything the scanners emit is parsed as data: one bad line can't stop the scan. The templates and
their matchers come from ProjectDiscovery; this module only runs the binaries and reads their output.
"""

import asyncio
import hashlib
import json
import logging
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field

from .config import DATA_DIR, SEVERITIES, Config, nuclei_env
from .custom_templates import CustomTemplates

logger = logging.getLogger(__name__)

NUCLEI = "nuclei"
NAABU = "naabu"
TEMPLATES_DIR = DATA_DIR / "nuclei-templates"
# Written whenever the community templates are updated, so "last updated" survives a restart
TEMPLATES_UPDATED_FILE = DATA_DIR / "templates-updated-at"
# Ports naabu checks by default: its "top 100", enough for the usual exposed services without a full sweep
DEFAULT_TOP_PORTS = "100"
# A big network takes hours: completeness matters more than speed. This only stops a scanner that hangs.
PROCESS_TIMEOUT = 8 * 3600
# nuclei can print a JSON line with a whole HTTP response in it; the default 64 KB line limit would stop it
STREAM_LIMIT = 16 * 1024 * 1024
STDERR_KEEP = 20


class ScannerError(Exception):
    """A scanner failed to run. The scan is marked failed with this message, never reported as clean."""


@dataclass
class HostResult:
    host: str
    open_ports: list[int] = field(default_factory=list)


@dataclass
class ScanProgress:
    hosts_total: int = 0
    hosts_done: int = 0
    open_ports: int = 0
    findings: int = 0


def fingerprint(host: str, port, template_id: str, matched_at: str) -> str:
    """Identifies the same issue on the same host across scans, so a repeat updates one ticket."""
    raw = f"{host}|{port or ''}|{template_id or ''}|{matched_at or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def severity_at_least(severity: str, minimum: str) -> bool:
    try:
        return SEVERITIES.index(severity) >= SEVERITIES.index(minimum)
    except ValueError:
        return True


def _text(value) -> str:
    """Nuclei gives some fields as a list (extracted-results); the database stores text."""
    if isinstance(value, list | tuple):
        return "\n".join(str(v) for v in value)
    return str(value)


def parse_nuclei_line(line: str) -> dict | None:
    """One JSONL finding from nuclei into a finding dict, or None when the line isn't a finding."""
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    info = row.get("info") or {}
    classification = info.get("classification") or {}
    cve_ids = classification.get("cve-id") or []
    cvss = classification.get("cvss-score")
    host = row.get("ip") or row.get("host") or ""
    port = row.get("port")
    try:
        port = int(port) if port not in (None, "") else None
    except (TypeError, ValueError):
        port = None
    template_id = row.get("template-id") or ""
    matched = row.get("matched-at") or row.get("matched") or host
    severity = str(info.get("severity") or "info").lower()
    if severity not in SEVERITIES:
        severity = "info"
    refs = info.get("reference") or []
    return {
        "fingerprint": fingerprint(host, port, template_id, matched),
        "host": host,
        "port": port,
        "kind": "vulnerability",
        "template_id": template_id,
        "name": info.get("name") or template_id,
        "severity": severity,
        "cve": ",".join(cve_ids) if isinstance(cve_ids, list) else str(cve_ids or "") or None,
        "cvss": float(cvss) if isinstance(cvss, int | float) else None,
        "matched_at": matched,
        "proof": _text(row.get("extracted-results") or row.get("curl-command") or matched or "")[:2000],
        "description": (info.get("description") or "")[:4000],
        "remediation": (info.get("remediation") or "")[:4000],
        "reference": ", ".join(refs) if isinstance(refs, list) else str(refs or ""),
    }


def parse_naabu_line(line: str) -> tuple[str, int] | None:
    """One JSONL host:port from naabu, or None."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        row = json.loads(line)
    except ValueError:
        return None
    host = row.get("ip") or row.get("host")
    port = row.get("port")
    if host and isinstance(port, int):
        return str(host), port
    return None


# Ports that speak HTTP, and ports that certainly don't. Anything else could be either, so it gets both kinds of
# templates: a slow miss is better than a missed finding.
WEB_PORTS = frozenset(
    {
        80,
        81,
        443,
        591,
        631,
        2375,
        2376,
        3000,
        3001,
        5000,
        5001,
        5985,
        5986,
        7443,
        8000,
        8001,
        8006,
        8008,
        8009,
        8080,
        8081,
        8088,
        8123,
        8443,
        8444,
        8888,
        9000,
        9090,
        9200,
        9443,
        10000,
        32400,
    }
)
NON_WEB_PORTS = frozenset(
    {
        21,
        22,
        23,
        25,
        53,
        69,
        110,
        111,
        123,
        135,
        137,
        139,
        143,
        161,
        389,
        445,
        465,
        512,
        513,
        514,
        548,
        554,
        587,
        636,
        873,
        993,
        995,
        1433,
        1521,
        1723,
        1883,
        2049,
        3306,
        3389,
        5060,
        5432,
        5900,
        6379,
        8883,
        9100,
        11211,
        27017,
    }
)
WEB_PROTOCOLS = ("http", "ssl")
PROTOCOL_PROTOCOLS = ("tcp", "javascript", "ssl")


def split_targets(targets: list[str]) -> tuple[list[str], list[str]]:
    """Targets for the web pass and for the protocol pass. A URL is always web; host:port goes by its port."""
    web: list[str] = []
    protocol: list[str] = []
    for target in targets:
        if "://" in target:
            web.append(target)
            continue
        try:
            port = int(target.rsplit(":", 1)[1])
        except (IndexError, ValueError):
            web.append(target)
            protocol.append(target)
            continue
        if port in WEB_PORTS:
            web.append(target)
        elif port in NON_WEB_PORTS:
            protocol.append(target)
        else:
            web.append(target)
            protocol.append(target)
    return web, protocol


class Scanner:
    """Runs naabu and nuclei. Binaries are resolved on PATH; missing ones raise at use."""

    def __init__(self, config: Config, run=None):
        self.config = config
        # A seam for tests: run(argv, on_line) -> returncode, instead of launching a real process
        self._run = run or self._run_process

    @staticmethod
    def available() -> dict[str, bool]:
        return {NAABU: bool(shutil.which(NAABU)), NUCLEI: bool(shutil.which(NUCLEI))}

    async def _run_process(self, argv: list[str], on_line) -> tuple[int, str]:
        """Run a scanner, feeding its stdout lines to on_line. Returns (exit code, the end of its stderr)."""
        proc = await asyncio.create_subprocess_exec(
            *argv, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT, env=nuclei_env()
        )
        assert proc.stdout is not None and proc.stderr is not None
        tail: list[str] = []

        async def read_stderr() -> None:
            # Read it as it comes, or a chatty scanner fills the pipe and stalls
            async for raw in proc.stderr:
                tail.append(raw.decode("utf-8", "replace").rstrip())
                del tail[:-STDERR_KEEP]

        reader = asyncio.create_task(read_stderr())
        try:
            async with asyncio.timeout(PROCESS_TIMEOUT):
                async for raw in proc.stdout:
                    on_line(raw.decode("utf-8", "replace"))
                await proc.wait()
            await reader
        except TimeoutError:
            proc.kill()
            reader.cancel()
            raise ScannerError(f"{argv[0]} did not finish within {PROCESS_TIMEOUT} seconds") from None
        return proc.returncode or 0, "\n".join(line for line in tail if line.strip())

    async def _checked(self, argv: list[str], on_line) -> None:
        """Run a scanner and raise ScannerError if it failed. Whatever it printed on stderr is the reason."""
        code, stderr = await self._run(argv, on_line)
        if code != 0:
            reason = stderr.strip().splitlines()[-1] if stderr.strip() else "no output"
            raise ScannerError(f"{argv[0]} exited with {code}: {reason[:300]}")

    @staticmethod
    def template_count() -> int:
        """How many community templates are installed."""
        return sum(1 for _ in TEMPLATES_DIR.rglob("*.yaml")) if TEMPLATES_DIR.exists() else 0

    @staticmethod
    def templates_updated_at() -> int | None:
        """When the community templates were last successfully updated, or None if never."""
        try:
            return int(TEMPLATES_UPDATED_FILE.read_text().strip())
        except (OSError, ValueError):
            return None

    @staticmethod
    def _mark_templates_updated() -> None:
        tmp = TEMPLATES_UPDATED_FILE.with_name(TEMPLATES_UPDATED_FILE.name + ".tmp")
        try:
            tmp.write_text(str(int(time.time())))
            os.replace(tmp, TEMPLATES_UPDATED_FILE)
        except OSError as e:
            logger.warning("Could not record the template update time: %s", e)

    async def update_templates(self) -> bool:
        """Pull the latest templates so every scan has the newest CVEs. Returns whether it succeeded. A failed
        update (no internet, say) keeps the templates already installed, but there must be some.

        Do not add -disable-update-check here: it turns the update into a silent no-op (checked on v3.11.1)."""
        try:
            await self._checked(
                [NUCLEI, "-update-templates", "-update-template-dir", str(TEMPLATES_DIR)], lambda _l: None
            )
            if not self.template_count():
                raise ScannerError("the update finished but installed no templates")
            self._mark_templates_updated()
            return True
        except ScannerError as e:
            if not self.template_count():
                raise ScannerError(f"no nuclei templates are installed and they could not be downloaded: {e}") from e
            logger.warning("Template update failed, scanning with the templates already installed: %s", e)
            return False

    def require_templates(self) -> None:
        """Refuse to scan with no templates: an empty run would look like a clean result."""
        if not self.template_count():
            raise ScannerError("no nuclei templates are installed; turn on Update templates and check internet access")

    async def discover_ports(self, hosts: list[str]) -> list[HostResult]:
        """Open ports per host, from naabu. A host with none is left out."""
        found: dict[str, list[int]] = {}

        def line(raw: str) -> None:
            if hp := parse_naabu_line(raw):
                found.setdefault(hp[0], []).append(hp[1])

        argv = [
            NAABU,
            "-silent",
            "-json",
            "-top-ports",
            DEFAULT_TOP_PORTS,
            "-rate",
            str(self.config.requests_per_second),
            "-c",
            str(self.config.max_concurrent_hosts),
            "-host",
            ",".join(hosts),
        ]
        await self._checked(argv, line)
        return [HostResult(host, sorted(set(ports))) for host, ports in found.items()]

    def _nuclei_argv(self, targets_file: str, protocols: tuple[str, ...]) -> list[str]:
        """The nuclei command for one pass. Flag names checked against nuclei v3.11.1: -t runs templates from a
        folder, -pt limits them to protocol types, -duc skips nuclei's own update check (templates are updated
        separately). No -s: every severity runs, because the informational templates are what tell devices
        apart; the minimum severity only decides which findings become tickets."""
        exclude = [t for t in self.config.nuclei_exclude_tags.split(",") if t.strip()]
        if not self.config.default_credentials and "default-login" not in exclude:
            exclude.append("default-login")
        argv = [
            NUCLEI,
            "-jsonl",
            "-silent",
            "-no-color",
            "-duc",
            "-l",
            targets_file,
            "-pt",
            ",".join(protocols),
            "-rl",
            str(self.config.requests_per_second),
            "-t",
            str(TEMPLATES_DIR),
        ]
        if custom := CustomTemplates.dir_for_scan():
            # Your own templates run alongside the community set
            argv += ["-t", custom]
        if self.config.nuclei_tags:
            argv += ["-tags", self.config.nuclei_tags]
        if exclude:
            argv += ["-etags", ",".join(exclude)]
        return argv

    def scan_summary(self, targets: list[str]) -> str:
        """A short, human-readable line of what a nuclei run on these targets will do: which passes run,
        on how many targets, and whether the default-credential check is active. Shown on the scan so
        it is possible to tell, without reading the container log, whether the credential check ran."""
        web, protocol = split_targets(targets)
        parts = []
        if web:
            parts.append(f"web pass: {len(web)} target{'s' if len(web) != 1 else ''}")
        if protocol:
            parts.append(f"protocol pass: {len(protocol)} target{'s' if len(protocol) != 1 else ''}")
        parts.append(f"credential check: {'on' if self.config.default_credentials else 'off'}")
        return " · ".join(parts)

    async def scan_targets(self, targets: list[str], on_finding) -> int:
        """Run nuclei against host:port (or URL) targets, in two passes so every target gets the templates
        that fit it: web templates (http and TLS) on web ports, and protocol templates (ssh, smb, ftp,
        databases, TLS and so on) on the others. A port that is neither known to be web nor known not to be
        gets both. Every finding goes to on_finding; the caller decides which become tickets. Returns the count."""
        web, protocol = split_targets(targets)
        count = 0

        def line(raw: str) -> None:
            nonlocal count
            if finding := parse_nuclei_line(raw):
                count += 1
                on_finding(finding)

        cred_check = "on" if self.config.default_credentials else "off"
        for batch, protocols, label in ((web, WEB_PROTOCOLS, "web"), (protocol, PROTOCOL_PROTOCOLS, "protocol")):
            if not batch:
                continue
            logger.info("nuclei %s pass: %d target(s), credential check %s", label, len(batch), cred_check)
            with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=True) as handle:
                handle.write("\n".join(batch))
                handle.flush()
                await self._checked(self._nuclei_argv(handle.name, protocols), line)
        return count
