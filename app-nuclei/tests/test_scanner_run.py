"""How the scanner runs its binaries: failures are reported, never mistaken for a clean scan."""

import pytest

from src import scanner as scanner_module
from src.config import Config
from src.database import Database
from src.engine import Engine
from src.scanner import Scanner, ScannerError


def scanner_with(code=0, stderr="", lines=()):
    async def run(argv, on_line):
        run.calls.append(argv)
        for line in lines:
            on_line(line)
        return code, stderr

    run.calls = []
    s = Scanner(Config(update_templates=False), run=run)
    s.calls = run.calls
    return s


@pytest.fixture
def templates(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_module, "TEMPLATES_DIR", tmp_path / "templates")
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "a.yaml").write_text("id: a")


async def test_nuclei_failure_is_an_error_not_a_clean_scan(templates):
    s = scanner_with(code=2, stderr="flag provided but not defined: -nonsense")
    with pytest.raises(ScannerError, match="flag provided but not defined"):
        await s.scan_targets(["10.0.0.5:80"], lambda f: None)


async def test_naabu_failure_is_an_error(templates):
    with pytest.raises(ScannerError, match="naabu exited with 1"):
        await scanner_with(code=1, stderr="boom").discover_ports(["10.0.0.5"])


async def test_findings_are_collected_on_success(templates):
    line = '{"template-id":"t","info":{"name":"n","severity":"high"},"host":"10.0.0.5","port":"80"}'
    seen = []
    count = await scanner_with(lines=[line, "garbage"]).scan_targets(["10.0.0.5:80"], seen.append)
    assert count == 1 and seen[0]["severity"] == "high"


async def test_nuclei_flags_are_the_ones_nuclei_has(templates):
    """These names were checked against nuclei v3.11.1 (-h and real runs). -templates-directory is not one."""
    s = scanner_with()
    await s.scan_targets(["10.0.0.5:80"], lambda f: None)
    argv = s.calls[0]
    for flag in ("-jsonl", "-silent", "-duc", "-l", "-pt", "-rl", "-t", "-etags"):
        assert flag in argv, flag
    assert "-templates-directory" not in argv and "-severity" not in argv
    # No -s and no -as: every severity must run (the informational templates identify devices), and
    # -as only runs templates for detected web technology
    assert "-s" not in argv and "-as" not in argv
    assert argv[argv.index("-t") + 1] == str(scanner_module.TEMPLATES_DIR)
    # default-login is excluded unless the credential check is on
    assert "default-login" in argv[argv.index("-etags") + 1]


def test_targets_are_split_by_what_they_speak():
    web, protocol = scanner_module.split_targets(
        ["10.0.0.5:8000", "10.0.0.5:22", "10.0.0.5:445", "10.0.0.5:7777", "https://host.example", "10.0.0.5:443"]
    )
    assert web == ["10.0.0.5:8000", "10.0.0.5:7777", "https://host.example", "10.0.0.5:443"]
    assert protocol == ["10.0.0.5:22", "10.0.0.5:445", "10.0.0.5:7777"]  # an unknown port gets both


async def test_each_pass_gets_its_own_templates(templates):
    s = scanner_with()
    await s.scan_targets(["10.0.0.5:80", "10.0.0.5:22"], lambda f: None)
    types = [argv[argv.index("-pt") + 1] for argv in s.calls]
    assert types == ["http,ssl", "tcp,javascript,ssl"]


async def test_a_pass_with_no_targets_does_not_run(templates):
    s = scanner_with()
    await s.scan_targets(["10.0.0.5:22"], lambda f: None)
    assert [argv[argv.index("-pt") + 1] for argv in s.calls] == ["tcp,javascript,ssl"]


async def test_every_finding_reaches_the_caller_whatever_its_severity(templates):
    """The engine decides what becomes a ticket; info findings are how devices are recognised."""
    lines = [
        '{"template-id":"a","info":{"name":"n","severity":"info"},"host":"h"}',
        '{"template-id":"b","info":{"name":"n","severity":"high"},"host":"h"}',
    ]
    seen = []
    s = scanner_with(lines=lines)
    s.config.min_severity = "critical"
    await s.scan_targets(["10.0.0.5:80"], seen.append)
    assert [f["severity"] for f in seen] == ["info", "high"]


async def test_update_uses_update_template_dir(templates):
    s = scanner_with()
    assert await s.update_templates() is True
    argv = s.calls[0]
    assert argv[:2] == ["nuclei", "-update-templates"] and "-update-template-dir" in argv
    # -duc / -disable-update-check makes nuclei skip the update and exit 0 (checked on v3.11.1)
    assert "-duc" not in argv and "-disable-update-check" not in argv


async def test_failed_update_keeps_installed_templates_but_needs_some(tmp_path, monkeypatch, templates):
    assert await scanner_with(code=1, stderr="no route to host").update_templates() is False
    monkeypatch.setattr(scanner_module, "TEMPLATES_DIR", tmp_path / "empty")
    with pytest.raises(ScannerError, match="no nuclei templates are installed"):
        await scanner_with(code=1, stderr="no route to host").update_templates()


def test_no_templates_means_no_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_module, "TEMPLATES_DIR", tmp_path / "none")
    with pytest.raises(ScannerError, match="no nuclei templates"):
        Scanner(Config()).require_templates()


async def test_a_failing_scanner_marks_the_scan_failed_and_keeps_tickets(tmp_path, templates):
    db = Database(tmp_path / "n.db")
    oid = db.add_object("10.0.0.5", "ip", "", None, authorised=True)
    f = {
        "fingerprint": "x",
        "object_id": oid,
        "host": "10.0.0.5",
        "port": 80,
        "kind": "vulnerability",
        "template_id": "t",
        "name": "n",
        "severity": "high",
        "cve": None,
        "cvss": None,
        "matched_at": "u",
        "proof": "",
        "description": "",
        "remediation": "",
        "reference": "",
    }
    db.record_finding(f)
    engine = Engine(db, Config(update_templates=False), scanner_with(code=2, stderr="crashed"))
    await engine.scan_object(oid)
    scan = db.scans()[0]
    assert scan["status"] == "failed" and "crashed" in scan["error"]
    # A scan that never ran must not resolve what earlier scans found
    assert db.finding(db.findings()[0]["id"])["status"] == "open"
    db.close()


def test_nuclei_keeps_its_config_in_data(monkeypatch):
    from src.config import DATA_DIR, nuclei_env

    env = nuclei_env()
    assert env["XDG_CONFIG_HOME"].startswith(str(DATA_DIR))
    assert env["XDG_CACHE_HOME"].startswith(str(DATA_DIR))


async def test_an_update_that_installs_nothing_is_not_a_success(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner_module, "TEMPLATES_DIR", tmp_path / "none")
    with pytest.raises(ScannerError, match=r"installed no templates|could not be downloaded"):
        await scanner_with(code=0).update_templates()
