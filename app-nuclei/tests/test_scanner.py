import json

from src.scanner import fingerprint, parse_naabu_line, parse_nuclei_line, severity_at_least


def test_parse_nuclei_finding():
    line = (
        '{"template-id":"CVE-2021-1234","info":{"name":"Thing RCE","severity":"critical",'
        '"description":"bad","remediation":"patch","reference":["https://x"],'
        '"classification":{"cve-id":["CVE-2021-1234"],"cvss-score":9.8}},'
        '"host":"10.0.0.5","ip":"10.0.0.5","port":443,"matched-at":"https://10.0.0.5/x"}'
    )
    f = parse_nuclei_line(line)
    assert f["name"] == "Thing RCE" and f["severity"] == "critical"
    assert f["cve"] == "CVE-2021-1234" and f["cvss"] == 9.8 and f["port"] == 443
    assert f["kind"] == "vulnerability" and len(f["fingerprint"]) == 32
    assert parse_nuclei_line("not json") is None
    assert parse_nuclei_line("") is None


def test_unknown_severity_falls_back_to_info():
    f = parse_nuclei_line('{"template-id":"x","info":{"name":"n","severity":"weird"},"host":"h"}')
    assert f["severity"] == "info"


def test_parse_naabu():
    assert parse_naabu_line('{"ip":"10.0.0.5","port":22}') == ("10.0.0.5", 22)
    assert parse_naabu_line("garbage") is None
    assert parse_naabu_line('{"ip":"10.0.0.5"}') is None


def test_fingerprint_is_stable_and_specific():
    a = fingerprint("h", 443, "CVE-1", "url")
    assert a == fingerprint("h", 443, "CVE-1", "url")
    assert a != fingerprint("h", 80, "CVE-1", "url")


def test_severity_filter():
    assert severity_at_least("high", "medium")
    assert not severity_at_least("low", "high")
    assert severity_at_least("critical", "critical")


def test_extracted_results_list_becomes_text_proof():
    line = json.dumps(
        {
            "template-id": "ssh-auth-methods",
            "host": "10.0.0.5",
            "port": "22",
            "matched-at": "10.0.0.5:22",
            "extracted-results": ["password", "publickey"],
            "info": {"name": "SSH Auth Methods", "severity": "info"},
        }
    )
    finding = parse_nuclei_line(line)
    assert finding["proof"] == "password\npublickey"


def test_scan_summary_reports_passes_and_credential_check():
    from src.config import Config
    from src.scanner import Scanner

    on = Scanner(Config(default_credentials=True))
    off = Scanner(Config(default_credentials=False))
    both = ["https://a.example/", "10.0.0.5:22"]

    assert on.scan_summary(both) == "web pass: 1 target · protocol pass: 1 target · credential check: on"
    assert off.scan_summary(both) == "web pass: 1 target · protocol pass: 1 target · credential check: off"
    assert on.scan_summary(["https://a.example/", "https://b.example/"]) == "web pass: 2 targets · credential check: on"
    assert on.scan_summary(["10.0.0.5:22"]) == "protocol pass: 1 target · credential check: on"
    assert on.scan_summary([]) == "credential check: on"
