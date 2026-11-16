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
    assert f["kind"] == "vulnerability" and f["host"] == "10.0.0.5"
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
    a = fingerprint("device_tracker.nas", "10.0.0.5", 443, "CVE-1", "https://10.0.0.5/x")
    assert a == fingerprint("device_tracker.nas", "10.0.0.5", 443, "CVE-1", "https://10.0.0.5/x")
    assert a != fingerprint("device_tracker.nas", "10.0.0.5", 80, "CVE-1", "https://10.0.0.5/x")
    assert a != fingerprint("device_tracker.tv", "10.0.0.5", 443, "CVE-1", "https://10.0.0.5/x")


def test_a_new_address_keeps_the_same_issue():
    before = fingerprint("device_tracker.nas", "10.0.0.5", 443, "CVE-1", "https://10.0.0.5/x")
    assert before == fingerprint("device_tracker.nas", "10.0.0.9", 443, "CVE-1", "https://10.0.0.9/x")


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
