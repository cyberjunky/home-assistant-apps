"""Log patterns: masking variable words, merging similar templates, spikes."""

from src.patterns import PatternMiner, is_spike, mask_token, tokenize


def test_masking():
    assert tokenize("Accepted password for bob from 10.0.0.5 port 50022 ssh2") == [
        "Accepted",
        "password",
        "for",
        "bob",
        "from",
        "<*>",
        "port",
        "<*>",
        "ssh2",
    ]
    assert mask_token("fe80::1ff:fe23:4567:890a") == "<*>"
    assert mask_token("aa:bb:cc:dd:ee:01") == "<*>"
    assert mask_token("550e8400-e29b-41d4-a716-446655440000") == "<*>"
    assert mask_token("bob2@example.com") == "<*>"
    assert mask_token("0xdeadbeef,") == "<*>,"
    assert mask_token("(42)") == "(<*>)"
    assert mask_token("user=bob") == "user=<*>"
    assert mask_token("sshd[1234]:") == "sshd[<*>]:"
    assert mask_token("/var/log/app.1") == "<*>"
    assert mask_token("port:22") == "port:<*>"
    assert mask_token("12.5ms") == "<*>"
    assert mask_token("10:23:45") == "<*>"
    # Words without digits stay; so do names with a digit (eth0, ssh2)
    assert mask_token("eth0") == "eth0"
    assert mask_token("error:") == "error:"
    assert tokenize('open "C:\\some file.txt" failed') == ["open", "<*>", "failed"]


def test_merging_widens_a_template():
    miner = PatternMiner()
    first = miner.match("Accepted password for bob from 10.0.0.5 port 50022 ssh2")
    second = miner.match("Accepted password for alice from 10.0.0.9 port 41234 ssh2")
    assert first.template == "Accepted password for bob from <*> port <*> ssh2"
    assert second.template == "Accepted password for <*> from <*> port <*> ssh2"
    assert second.renamed_from == first.template
    # Now covered without a change
    third = miner.match("Accepted password for carol from 10.0.0.1 port 1 ssh2")
    assert third.template == second.template and third.renamed_from is None
    assert len(miner) == 1


def test_different_messages_stay_apart():
    miner = PatternMiner()
    a = miner.match("Connection closed by peer")
    b = miner.match("Connection reset by peer")  # 1 of 4 words differ: 25 %, merged
    c = miner.match("Disk full on volume")  # another first word
    d = miner.match("Connection refused")  # another length
    assert a.template != c.template != d.template
    assert b.template == "Connection <*> by peer"
    assert len(miner) == 3


def test_ids_follow_a_rename():
    miner = PatternMiner()
    first = miner.match("user bob logged in now")
    miner.remember(first.template, 7)
    second = miner.match("user alice logged in now")
    assert second.renamed_from == first.template
    assert miner.known_id(second.template) == 7


def test_lru_bound_and_seed():
    miner = PatternMiner(size=3)
    for i in range(10):
        miner.match(f"message{i} kind{i} unique")
    assert len(miner) == 3
    seeded = PatternMiner()
    seeded.seed(["Accepted password for <*> from <*> port <*> ssh2"])
    match = seeded.match("Accepted password for dave from 10.0.0.2 port 22 ssh2")
    assert match.template == "Accepted password for <*> from <*> port <*> ssh2" and match.renamed_from is None


def test_long_messages_are_cut():
    tokens = tokenize(" ".join(["word"] * 100))
    assert len(tokens) == 65 and tokens[-1] == "<*>"


def test_spike():
    assert is_spike(100, 10)
    assert not is_spike(100, 30)
    assert not is_spike(10, 0)  # too few hits
    assert is_spike(20, 0)
