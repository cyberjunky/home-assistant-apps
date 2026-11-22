"""Syslog parsing: RFC 3164, RFC 5424, MikroTik, messages without PRI, and TCP framing."""

from datetime import UTC, datetime

from src.syslog import Framer, parse

NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


def test_rfc3164_with_host_and_tag():
    entry = parse(b"<34>Oct 11 22:14:15 mymachine su[123]: 'su root' failed on /dev/pts/8", "10.0.0.1", NOW)
    assert (entry.host, entry.app, entry.severity, entry.facility) == ("mymachine", "su", 2, 4)
    assert entry.message == "'su root' failed on /dev/pts/8"
    assert entry.attrs == {"procid": "123"}
    assert entry.ts.year == 2026 and entry.ts.month == 10


def test_rfc3164_without_host_falls_back_to_the_sender():
    entry = parse(b"<14>Nov  2 10:00:00 dhcpd: DHCPACK on 10.0.0.5", "192.168.1.20", NOW)
    assert (entry.host, entry.app, entry.message) == ("192.168.1.20", "dhcpd", "DHCPACK on 10.0.0.5")
    assert entry.ts.day == 2


def test_rfc3164_december_line_in_january_is_last_year():
    january = datetime(2027, 1, 1, 0, 5, tzinfo=UTC)
    entry = parse(b"<13>Dec 31 23:59:00 host app: late", "1.2.3.4", january)
    assert entry.ts.year == 2026


def test_rfc5424_with_structured_data():
    entry = parse(
        b"<165>1 2003-10-11T22:14:15.003Z mymachine.example.com evntslog - ID47 "
        b'[exampleSDID@32473 iut="3" eventSource="App\\]lication"] \xef\xbb\xbfAn event',
        "10.0.0.1",
        NOW,
    )
    assert entry.host == "mymachine.example.com"
    assert entry.app == "evntslog"
    assert (entry.severity, entry.facility) == (5, 20)
    assert entry.message == "An event"
    assert entry.attrs["exampleSDID@32473.eventSource"] == "App]lication"
    assert entry.attrs["msgid"] == "ID47"
    assert entry.ts == datetime(2003, 10, 11, 22, 14, 15, 3000, tzinfo=UTC)


def test_rfc5424_with_nil_fields():
    entry = parse(b"<13>1 2026-11-22T10:00:00+01:00 host - 12 - - hello world", "10.0.0.1", NOW)
    assert (entry.host, entry.app, entry.message) == ("host", None, "hello world")
    assert entry.ts == datetime(2026, 11, 22, 9, 0, tzinfo=UTC)
    assert entry.attrs == {"procid": "12"}


def test_mikrotik_topics():
    entry = parse(b"<30>Nov 22 10:00:00 MikroTik system,info,account user admin logged in via winbox", "x", NOW)
    assert (entry.host, entry.app) == ("MikroTik", "system")
    assert entry.attrs["topics"] == ["system", "info", "account"]
    assert entry.message == "user admin logged in via winbox"
    # Without a host name in the message
    entry = parse(b"<134>Nov 22 10:00:00 firewall,info input: in:ether1 out:(unknown 0)", "192.168.88.1", NOW)
    assert (entry.host, entry.app, entry.message) == ("192.168.88.1", "firewall", "input: in:ether1 out:(unknown 0)")


def test_without_pri():
    entry = parse(b"just some text\n", "10.0.0.7", NOW)
    assert (entry.severity, entry.facility, entry.host, entry.app) == (5, None, "10.0.0.7", None)
    assert entry.message == "just some text"
    assert entry.ts == NOW


def test_empty_and_binary():
    assert parse(b"\r\n\x00", "x", NOW) is None
    entry = parse(b"<11>bad \xff\xfe bytes", "x", NOW)
    assert entry.severity == 3 and "bad" in entry.message


def test_framer_octet_counted_and_lines_mixed():
    framer = Framer()
    assert framer.feed(b"12 <13>hello yo<14>line one\n<15>li") == [b"<13>hello yo", b"<14>line one"]
    assert framer.feed(b"ne two\r\n5 <1>a") == [b"<15>line two\r"]
    assert framer.feed(b"b") == [b"<1>ab"]
    assert framer.feed(b"<1>last") == []
    assert framer.rest() == b"<1>last"


def test_framer_caps_long_lines():
    framer = Framer(max_frame=10)
    frames = framer.feed(b"x" * 25)
    assert frames == [b"x" * 10] and framer.oversized == 1
