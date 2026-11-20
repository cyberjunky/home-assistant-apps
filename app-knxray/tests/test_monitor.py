"""What the monitor learns from the telegrams, and what the recorder keeps of them."""

import time

from src.monitor import Monitor
from src.recorder import Recorder, guess_numeric, parse_ga


def tg(ts, src, dst, kind="write", raw="01", text="on", num=1.0, dpt="1.001", bits=True, unit=None):
    return {"ts": ts, "dir": "in", "src": src, "dst": dst, "type": kind, "raw": raw if kind != "read" else "",
            "bits": bits, "text": text if kind != "read" else None, "num": num, "unit": unit, "dpt": dpt, "secure": False, "via": "bus"}  # fmt: skip


def test_reads_are_answered_unanswered_or_answered_twice():
    m = Monitor()
    m.add(tg(100.0, "1.0.250", "1/1/7", "read"))
    m.tick(102.0)
    assert m.addresses["1/1/7"]["unanswered"] == 0  # still waiting
    m.tick(103.5)
    assert m.addresses["1/1/7"]["unanswered"] == 1

    m.add(tg(200.0, "1.0.250", "1/1/6", "read"))
    m.add(tg(200.1, "1.1.1", "1/1/6", "response"))
    m.add(tg(200.2, "1.1.21", "1/1/6", "response"))
    m.tick(204.0)
    hall = m.addresses["1/1/6"]
    assert (hall["answered"], hall["unanswered"], hall["multi_answer"]) == (1, 0, 1)
    assert hall["responders"] == {"1.1.1": 1, "1.1.21": 1}

    m.add(tg(300.0, "1.0.250", "1/1/1", "read"))
    m.add(tg(300.1, "1.1.2", "1/1/1", "response"))
    # A second read settles the first
    m.add(tg(301.0, "1.0.250", "1/1/1", "read"))
    assert m.addresses["1/1/1"]["answered"] == 1 and m.addresses["1/1/1"]["multi_answer"] == 0


def test_counts_per_address_and_per_sender():
    m = Monitor()
    for i in range(40):
        m.add(tg(6000.0 + i, "1.2.10", "4/0/2", raw="0011", text="0.17", num=0.17, dpt="9.005", bits=False))
    m.add(tg(6100.0, "1.2.10", "4/0/1", raw="0343", text="8.35", num=8.35, dpt="9.001", bits=False))
    wind = m.addresses["4/0/2"]
    assert wind["count"] == 40 and wind["writes"] == 40 and wind["peak"] == 40
    assert wind["sources"] == {"1.2.10": 40} and wind["sizes"] == {"2": 40}
    assert wind["last_value"]["text"] == "0.17"
    assert wind["repeats"] == 39  # the same value from the same sender, a second apart
    station = m.sources["1.2.10"]
    assert station["count"] == 41 and set(station["addresses"]) == {"4/0/2", "4/0/1"}
    assert m.total == 41 and "_minute" not in m.public(wind)


def test_a_typed_address_with_an_unreadable_value_is_counted():
    m = Monitor()
    m.add(tg(10.0, "1.2.11", "3/0/6", raw="14", text=None, num=None, dpt="9.001", bits=False))
    m.add(tg(11.0, "1.1.4", "3/0/9", raw="01", text=None, num=None, dpt=None))
    assert m.addresses["3/0/6"]["undecoded"] == 1 and m.addresses["3/0/6"]["sizes"] == {"1": 1}
    # No type is not an error of the telegram
    assert m.addresses["3/0/9"]["undecoded"] == 0 and m.addresses["3/0/9"]["sizes"] == {"bits": 1}


def test_load_recent_and_waiters():
    m = Monitor(size=5)
    now = 600050.0
    for i in range(8):
        m.add(tg(now - 30 + i * 0.1, "1.1.1", "1/1/4"))
    load = m.load(3, now)
    assert len(load) == 3 and load[-1][1] == 8 and load[-1][2] == 8 and load[0][1] == 0
    assert len(m.telegrams) == 5 and [t["id"] for t in m.recent(after=6)] == [7, 8]
    assert [t["id"] for t in m.of("1/1/4", 2)] == [8, 7] and m.of("1.1.1", 1)[0]["id"] == 8
    waiter = m.wait_for("1/1/4")
    m.add(tg(now, "1.1.1", "1/1/4", "response"))
    m.add(tg(now, "1.1.1", "1/1/4", "write"))
    assert len(waiter) == 1
    m.done("1/1/4", waiter)
    assert m._waiters == {}
    m.seed(tg(1.0, "1.1.1", "1/1/4"))
    assert m.total == 10  # a seeded telegram is shown, not counted
    m.clear()
    assert m.total == 0 and not m.addresses and not m.telegrams


def test_group_addresses_and_guessed_numbers():
    assert parse_ga("1/2/3") == (1 << 11) | (2 << 8) | 3
    assert parse_ga("1/515") == (1 << 11) | 515 and parse_ga("2051") == 2051
    assert parse_ga("32/0/1") is None and parse_ga("1/8/1") is None and parse_ga("x") is None
    assert guess_numeric("80") == 128.0
    assert round(guess_numeric("0c1a"), 2) == 21.0
    assert guess_numeric("41a00000") == 20.0
    assert guess_numeric("") is None and guess_numeric("abc") is None and guess_numeric("010203") is None


async def test_recorder_keeps_and_answers(tmp_path):
    r = Recorder(tmp_path / "t.db", retain_days=0, retain_rows=0)
    r.start()
    r.event("connected")
    now = time.time()
    for i in range(100):
        r.add(
            tg(
                now - 3600 + i * 30,
                "1.1.20",
                "3/0/1",
                raw="0c1a",
                text="21",
                num=20.0 + i / 10,
                dpt="9.001",
                bits=False,
                unit="°C",
            )
        )
    for i in range(10):
        r.add(tg(now - 600 + i, "1.1.10", "1/0/1"))
    r.add(tg(now - 60, "1.0.250", "1/1/7", "read"))
    r.add(tg(now - 50, "1.1.4", "3/0/9", raw="14", text=None, num=None, dpt=None, bits=False))
    assert r.summary()["rows"] == 112

    page = r.archive({"since": now - 7200, "until": now}, limit=50)
    assert page["total"] == 112 and len(page["items"]) == 50 and page["next"]
    assert page["items"][0]["dst"] == "3/0/9"  # newest first
    rest = r.archive({"since": now - 7200, "until": now}, cursor=page["next"], limit=1000)
    assert len(rest["items"]) == 62 and rest["next"] is None
    assert r.archive({"ga": parse_ga("1/0/1")})["total"] == 10
    assert r.archive({"type": "read"})["total"] == 1
    assert r.archive({"src": "1.1.10"})["total"] == 10
    assert r.archive({"dpt_main": 9})["total"] == 100
    assert r.archive({"q": "1.1.2"})["total"] == 100
    # A name the project gives is found through the addresses it belongs to
    assert r.archive({"q": "ceiling", "q_gas": [parse_ga("1/0/1")]})["total"] == 10
    assert r.archive({"ga_prefix": "3/0/"})["total"] == 101

    raw = r.series(parse_ga("3/0/1"), now - 7200, now, points=600)
    assert raw["count"] == 100 and raw["bucket"] == 0 and raw["unit"] == "°C" and len(raw["points"]) == 100
    bucketed = r.series(parse_ga("3/0/1"), now - 7200, now, points=20)
    assert bucketed["bucket"] > 0 and len(bucketed["points"]) <= 21
    low, high = bucketed["points"][0][2], bucketed["points"][0][3]
    assert low <= bucketed["points"][0][1] <= high
    # No type: the payload is read by its length, and the answer says so
    guessed = r.series(parse_ga("3/0/9"), now - 7200, now)
    assert guessed["guessed"] and guessed["points"][0][1] == 20.0
    assert r.series(parse_ga("7/7/7"), now - 7200, now)["points"] == []

    stats = r.stats(now - 7200, now)
    assert stats["total"] == 112 and stats["addresses"] == 4 and stats["sources"] == 4
    assert stats["by_type"] == {"write": 111, "read": 1}
    assert stats["top_addresses"][0] == {
        "ga": "3/0/1",
        "count": 100,
        "sources": 1,
        "last": stats["top_addresses"][0]["last"],
    }
    assert stats["top_sources"][0]["src"] == "1.1.20" and sum(stats["histogram"]["counts"]) == 112
    assert sum(sum(day) for day in stats["heatmap"]) == 112 and stats["peak_minute"]["count"] >= 2

    addresses = r.addresses(now - 7200, now)
    assert addresses[0]["ga"] == "3/0/1" and addresses[0]["numeric"] == 100

    lines = "".join(r.csv_rows({"ga": parse_ga("1/0/1")}, {"1/0/1": "Ceiling", "1.1.10": "Push button"})).splitlines()
    assert len(lines) == 11 and lines[0].startswith("time,direction,source")
    assert ",1.1.10,Push button,1/0/1,Ceiling,write,on,,01" in lines[1]

    # The type becomes known later: what was recorded without a value gets one
    assert r.backfill(lambda dst, raw: ("20", 20.0, "%", "5.001") if dst == "3/0/9" else None) == 1
    assert r.archive({"ga": parse_ga("3/0/9")})["items"][0]["text"] == "20"
    assert r.backfill(lambda dst, raw: ("20", 20.0, "%", "5.001") if dst == "3/0/9" else None) == 0

    r.retain_rows = 12
    assert r.prune() == 100 and r.summary()["rows"] == 12
    r.clear()
    assert r.summary()["rows"] == 0
    await r.stop()


async def test_availability_explains_the_gaps(tmp_path):
    r = Recorder(tmp_path / "t.db", retain_days=0, retain_rows=0)
    now = time.time()
    events = [(now - 10000, "start"), (now - 9990, "connected"), (now - 6000, "disconnected"), (now - 5000, "connected"),
              (now - 3000, "stop"), (now - 2000, "start"), (now - 1990, "connected")]  # fmt: skip
    with r._lock:
        r._db.executemany("INSERT INTO events (ts, kind) VALUES (?, ?)", events)
    r.add(tg(now - 9000, "1.1.1", "1/1/4"))
    r.add(tg(now - 6500, "1.1.1", "1/1/4"))
    r.add(tg(now - 1900, "1.1.1", "1/1/4"))
    out = r.availability(now - 10000, now)
    assert [s["state"] for s in out["segments"]] == ["link_down", "recording", "link_down", "recording", "not_running", "link_down", "recording"]  # fmt: skip
    assert round(out["coverage"]["not_running"]) == 1000 and round(out["coverage"]["link_down"]) == 1020
    # The bus was quiet for more than half an hour between the first two telegrams, and again at the end
    assert len(out["quiet"]) >= 2

    # A run that never said stop is closed at its last heartbeat when the next one starts
    with r._lock:
        r._db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('alive', ?)", (str(now - 500),))
    r.start()
    with r._lock:
        kinds = [k for (k,) in r._db.execute("SELECT kind FROM events ORDER BY id").fetchall()]
    assert kinds[-2:] == ["stop", "start"]
    await r.stop()


def _wire(source, address, tpdu, repeated=False, status=0):
    """An L_Busmon.ind for a group frame: the status of the interface, then the frame as on the wire."""
    from src.busmon import checksum

    def raw(text, group=False):
        parts = [int(p) for p in text.replace("/", ".").split(".")]
        return (
            (parts[0] << 11 | parts[1] << 8 | parts[2]) if group else (parts[0] << 12 | parts[1] << 8 | parts[2])
        ).to_bytes(2, "big")

    body = (
        bytes([0x9C if repeated else 0xBC]) + raw(source) + raw(address, True) + bytes([0xE0 | (len(tpdu) - 1)]) + tpdu
    )
    return bytes([0x2B, 3, 0x03, 0x01, status]) + body + bytes([checksum(body)])


def test_frames_on_the_wire():
    from src.busmon import BusMonitor, parse_frame, parse_indication

    write = parse_indication(_wire("1.1.20", "3/0/1", bytes([0x00, 0x80, 0x0C, 0x1A])))
    assert (write["kind"], write["src"], write["dst"], write["type"], write["payload"], write["bits"]) == (
        "data", "1.1.20", "3/0/1", "write", "0c1a", False)  # fmt: skip
    assert write["checksum_ok"] and not write["repeated"] and write["hops"] == 6 and write["priority"] == "low"
    bit = parse_indication(_wire("1.1.10", "1/0/1", bytes([0x00, 0x81]), repeated=True, status=0x40))
    assert bit["payload"] == "01" and bit["bits"] and bit["repeated"] and bit["bit_error"]
    assert parse_indication(_wire("1.0.250", "1/1/7", bytes([0x00, 0x00])))["type"] == "read"
    assert parse_frame(bytes([0xCC]))["kind"] == "ack" and parse_frame(bytes([0xC0]))["kind"] == "busy"
    broken = bytearray(_wire("1.1.20", "3/0/1", bytes([0x00, 0x80, 0x0C, 0x1A]))[5:])
    broken[-1] ^= 0xFF
    assert parse_frame(bytes(broken))["checksum_ok"] is False
    # A connection to a device being read
    management = bytes([0xB0, 0x11, 0x02, 0x11, 0x05, 0x61, 0x43, 0x00])
    assert (
        parse_frame(management + bytes([0]))["service"] == "DeviceDescriptorRead"
        and not parse_frame(management + bytes([0]))["group"]
    )
    assert parse_indication(b"\x29\x00") is None and parse_frame(b"")["kind"] == "other"

    monitor = BusMonitor()
    monitor.decode = lambda frame: frame.update(text="21", unit="°C", dpt="9.001")
    ack = bytes([0x2B, 0, 0xCC])
    first = monitor.add(_wire("1.1.20", "3/0/1", bytes([0x00, 0x80, 0x0C, 0x1A])), now=10.0)
    assert monitor.add(ack) is None and first["ack"] == "ack" and first["text"] == "21"
    # Nobody takes it: sent again three times, no acknowledgement
    lonely = [
        monitor.add(_wire("1.1.99", "7/3/1", bytes([0x00, 0x81]), repeated=i > 0), now=11.0 + i) for i in range(4)
    ]
    busy = monitor.add(_wire("1.1.11", "1/0/8", bytes([0x00, 0x81])), now=20.0)
    monitor.add(bytes([0x2B, 0, 0xC0]))
    assert [f["ack"] for f in lonely] == ["none", "none", "none", "none"] and busy["ack"] == "busy"
    assert monitor.counts == {"frames": 8, "data": 6, "repeated": 3, "acked": 1, "nak": 0, "busy": 1, "unanswered": 4,
                              "errors": 0, "lost": 0, "bad_checksum": 0}  # fmt: skip
    assert [f["id"] for f in monitor.recent(after=4)] == [5, 6] and monitor.status()["state"] == "off"
