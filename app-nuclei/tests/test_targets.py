import pytest

from src import targets


def test_classify():
    assert targets.classify("10.0.0.1") == "ip"
    assert targets.classify("10.0.0.0/24") == "cidr"
    assert targets.classify("host.example.com") == "host"
    assert targets.classify("https://host.example") == "url"
    assert targets.classify("AS12345") == "asn"
    for bad in ("", "not valid!", "999.1.1.1/8 x"):
        with pytest.raises(targets.TargetError):
            targets.classify(bad)


def test_private_detection():
    assert targets.is_private("192.168.1.1")
    assert targets.is_private("10.0.0.0/8")
    assert targets.is_private("127.0.0.1")
    assert not targets.is_private("8.8.8.8")
    assert not targets.is_private("1.1.1.0/24")
    assert not targets.is_private("host.example")


def test_hosts_of_expands_and_bounds():
    assert targets.hosts_of("10.0.0.0/30") == ["10.0.0.1", "10.0.0.2"]
    assert targets.hosts_of("10.0.0.5") == ["10.0.0.5"]
    with pytest.raises(targets.TargetError):
        targets.hosts_of("10.0.0.0/8")  # millions of hosts
    with pytest.raises(targets.TargetError):
        targets.hosts_of("host.example")


def test_normalise():
    assert targets.normalise("AS1234") == "AS1234"
    assert targets.normalise("as1234") == "AS1234"
    assert targets.normalise("HOST.Example") == "host.example"
    assert targets.normalise("10.0.0.0/24") == "10.0.0.0/24"
