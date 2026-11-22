"""The schema, the writer and the queries against a real TimescaleDB (VIGIL_TEST_DB)."""

import asyncio
import time
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from src.database import SCHEMA_VERSION, Database, Sources, Writer
from src.patterns import PatternMiner
from src.records import LogEntry, Sample

from .conftest import _connected


def _log(message, host="nas", app="sshd", severity=6, ago=0.0, **attrs):
    return LogEntry(
        ts=datetime.now(UTC) - timedelta(seconds=ago),
        source="syslog",
        host=host,
        app=app,
        severity=severity,
        facility=4,
        message=message,
        attrs=attrs,
    )


async def _write(database: Database, items: list) -> Writer:
    writer = Writer(database, PatternMiner(), Sources())
    task = asyncio.create_task(writer.run())
    for item in items:
        if isinstance(item, LogEntry):
            assert writer.put_log(item)
        else:
            writer.put_samples([item])
    await writer.stop(task, 20)
    assert writer.queue.empty() and writer.dropped == 0, writer.last_error
    return writer


async def test_schema_created_and_migrated(database):
    assert await database.fetchval("SELECT value FROM vigil.meta WHERE key = 'schema_version'") == str(SCHEMA_VERSION)
    names = [d["name"] for d in await database.dashboards()]
    assert names == ["Hosts", "Network"]
    extension = await database.fetchval("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'")
    assert bool(extension) == database.timescaledb
    if database.timescaledb:
        hypertables = await database.fetchval(
            "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_schema = 'vigil'"
        )
        assert hypertables == 2
        jobs = await database.fetch(
            """SELECT proc_name, hypertable_name, config FROM timescaledb_information.jobs
               WHERE hypertable_schema = 'vigil' ORDER BY hypertable_name, proc_name"""
        )
        procs = {(j["hypertable_name"], j["proc_name"]) for j in jobs}
        assert ("logs", "policy_retention") in procs and ("points", "policy_retention") in procs
        assert ("points", "policy_compression") in procs and ("logs", "policy_compression") in procs
    # Starting again changes nothing (and keeps a dashboard the user deleted deleted)
    await database.delete_dashboard((await database.dashboards())[0]["id"])
    await database.ensure_schema()
    assert [d["name"] for d in await database.dashboards()] == ["Network"]


async def test_newer_layout_is_refused(empty_server):
    conn = await asyncpg.connect(
        host=empty_server.db_host,
        port=empty_server.db_port,
        user=empty_server.db_user,
        password=empty_server.db_password,
        database=empty_server.db_name,
    )
    await conn.execute("CREATE SCHEMA vigil; CREATE TABLE vigil.meta (key text PRIMARY KEY, value text)")
    await conn.execute("INSERT INTO vigil.meta VALUES ('schema_version', '99')")
    await conn.close()
    database = Database(empty_server)
    task = asyncio.create_task(database.run())
    await asyncio.sleep(1.5)
    assert not database.connected and "newer" in database.last_error
    task.cancel()
    await database.close()


async def test_database_down_reports_the_error():
    from src.config import Config

    database = Database(Config(db_host="127.0.0.1", db_port=1, db_password="pw"))
    task = asyncio.create_task(database.run())
    await asyncio.sleep(0.5)
    assert not database.connected and "cannot reach" in database.last_error
    assert "pw" not in database.last_error
    task.cancel()
    await database.close()


async def test_write_and_search_round_trip(database):
    items = [
        _log("Accepted password for bob from 10.0.0.5 port 50022 ssh2", ago=30, user="bob"),
        _log("Accepted password for alice from 10.0.0.9 port 41234 ssh2", ago=20, user="alice"),
        _log("Failed password for root from 203.0.113.9 port 22 ssh2", severity=3, ago=10),
        _log("kernel: disk sda1 is 91% full", host="pi", app="kernel", severity=4, ago=5),
        _log("NUL \x00 inside", host="pi", app="x", ago=1, weird="a\x00b"),
        _log("from the future", ago=-10 * 86400),
    ]
    writer = await _write(database, items)
    assert writer.written_logs == 6
    now = time.time()

    async def search(q):
        result = await database.logs(q, now - 3600, now + 60, 100, None)
        return [r["message"] for r in result["rows"]]

    assert len(await search("")) == 6
    assert await search("accepted -alice") == ["Accepted password for bob from 10.0.0.5 port 50022 ssh2"]
    assert await search('"password for root"') == ["Failed password for root from 203.0.113.9 port 22 ssh2"]
    assert await search("severity:<=warning") == [
        "kernel: disk sda1 is 91% full",
        "Failed password for root from 203.0.113.9 port 22 ssh2",
    ]
    assert len(await search("host:p*")) == 2
    assert await search("10.0.0.9") == ["Accepted password for alice from 10.0.0.9 port 41234 ssh2"]
    assert await search("attrs.user:bob") == ["Accepted password for bob from 10.0.0.5 port 50022 ssh2"]
    assert await search("acc*") == await search("accepted")
    # Lines of one pattern
    rows = (await database.logs("accepted", now - 3600, now + 60, 100, None))["rows"]
    assert rows[0]["pattern"] == rows[1]["pattern"] is not None
    assert len(await search(f"pattern:{rows[0]['pattern']}")) == 2
    # The far-future line was kept at the time it arrived
    future = (await database.logs('"from the future"', now - 3600, now + 60, 10, None))["rows"][0]
    assert "original_ts" in future["attrs"]
    # Paging
    page = await database.logs("", now - 3600, now + 60, 2, None)
    assert page["more"] and len(page["rows"]) == 2
    rest = await database.logs("", now - 3600, now + 60, 10, page["rows"][-1]["ts"])
    assert len(rest["rows"]) == 4 and not rest["more"], (page["rows"], rest["rows"])
    # Tail and fields
    tail = await database.tail("", now - 25)
    assert len(tail["rows"]) == 5
    fields = await database.fields(now - 3600, now + 60)
    assert {h["value"]: h["count"] for h in fields["hosts"]} == {"nas": 4, "pi": 2}
    assert {"value": "err", "number": 3, "count": 1} in fields["severities"]
    # Patterns
    patterns = (await database.patterns(now - 3600, now + 60, "count"))["patterns"]
    top = patterns[0]
    assert top["template"] == "Accepted password for <*> from <*> port <*> ssh2" and top["count"] == 2
    assert top["new"] and len(top["spark"]) == 24 and sum(top["spark"]) == 2
    assert await database.new_patterns(now - 3600) == len(patterns)
    assert await database.count_logs("app:sshd", now - 3600, now + 60) == 4


async def test_histogram_and_overview(database):
    items = [_log(f"line {i}", host=f"h{i % 3}", severity=i % 8, ago=60 * (i % 50)) for i in range(200)]
    await _write(database, items)
    now = time.time()
    result = await database.histogram("", now - 3600, now + 1, 60)
    assert len(result["t"]) == 60 and result["total"] == 200
    assert set(result["by_severity"]) == {"err", "warning", "info", "debug"}
    assert sum(result["by_severity"]["err"]) == 100  # severities 0..3
    assert sum(sum(v) for v in result["by_severity"].values()) == 200
    grouped = await database.histogram("severity:<=3", now - 3600, now + 1, 30, "host")
    assert set(grouped["groups"]) == {"h0", "h1", "h2"} and grouped["total"] == 100
    overview = await database.overview(1)
    assert overview["tiles"]["logs"] == 200 and overview["tiles"]["errors"] == 100
    assert overview["tiles"]["warnings"] == 25 and overview["tiles"]["hosts"] == 3
    assert sum(sum(v) for v in overview["volume"]["by_severity"].values()) == 200
    assert len(overview["top_hosts"]) == 3 and overview["firing"] == []
    changes = await database.change_data(now - 1800, now + 1)
    assert {h["host"] for h in changes["hosts"]} == {"h0", "h1", "h2"}


async def test_metrics_query_with_rate(database):
    now = datetime.now(UTC).replace(microsecond=0)
    start = now - timedelta(minutes=30)
    items = []
    for i in range(31):
        t = start + timedelta(minutes=i)
        # A counter growing 60 per minute (1/s), reset half way
        value = 60.0 * i if i < 15 else 60.0 * (i - 14)
        items.append(Sample("bytes_total", {"job": "nas", "if": "eth0"}, value, t, "B", "counter"))
        items.append(Sample("bytes_total", {"job": "nas", "if": "eth1"}, 120.0 * i, t, "B", "counter"))
        items.append(Sample("temp", {"room": "attic"}, 20.0 + (i % 2), t, "C", "gauge"))
    await _write(database, items)
    names = {n["name"]: n for n in await database.metric_names("")}
    assert names["bytes_total"]["series"] == 2 and names["temp"]["unit"] == "C"
    assert await database.metric_names("emp") == [{"name": "temp", "series": 1, "unit": "C", "kind": "gauge"}]
    # A label narrows the list and the series count: job=nas has only bytes_total, both its series
    by_job = await database.metric_names("", {"job": "nas"})
    assert [(n["name"], n["series"]) for n in by_job] == [("bytes_total", 2)]
    assert [n["series"] for n in await database.metric_names("bytes", {"if": "eth0"})] == [1]
    assert await database.metric_names("", {"job": "nowhere"}) == []
    labels = await database.metric_labels("bytes_total")
    assert labels == {"if": ["eth0", "eth1"], "job": ["nas"]}
    t0, t1 = start.timestamp(), now.timestamp() + 1
    rate = await database.metric_query("bytes_total", {"if": "eth0"}, "rate", None, t0, t1, 300, False)
    assert rate["step"] == 300 and rate["unit"] == "B"
    values = [v for v in rate["series"][0]["values"] if v is not None]
    assert values and all(abs(v - 1.0) < 0.01 for v in values), values
    grouped = await database.metric_query("bytes_total", {}, "rate", "if", t0, t1, 300, False)
    by_label = {s["label"]: s["values"] for s in grouped["series"]}
    assert set(by_label) == {"eth0", "eth1"}
    assert all(abs(v - 2.0) < 0.01 for v in by_label["eth1"] if v is not None)
    total = await database.metric_query("bytes_total", {}, "rate", None, t0, t1, 300, False)
    assert all(abs(v - 3.0) < 0.01 for v in total["series"][0]["values"] if v is not None)
    avg = await database.metric_query("temp", {}, "avg", None, t0, t1, 600, True)
    assert len(avg["t"]) == len(avg["series"][0]["values"]) and "band" in avg["series"][0]
    assert await database.metric_value("temp", {"room": "attic"}, "max", 3600) == 21.0
    assert await database.metric_value("temp", {"room": "nowhere"}, "max", 3600) is None
    assert await database.last_point("temp", {}, 3600) is not None
    assert len(await database.metric_recent("temp", {}, "avg", 60, 3600)) >= 30
    # Points every minute: a short window (or a small asked step) still gets one-minute buckets,
    # not 15 s ones with empty buckets between every two values
    for asked in (None, 10):
        short = await database.metric_query("temp", {}, "avg", None, t0, t1, asked, False)
        assert short["step"] == 60, short["step"]
        values = short["series"][0]["values"]
        filled = [k for k, v in enumerate(values) if v is not None]
        assert all(values[k] is not None for k in range(filled[0], filled[-1] + 1)), values


async def test_rules_alerts_and_saved_things(database):
    rule_id = await database.save_rule(
        {
            "name": "Errors",
            "kind": "log_count",
            "query": {"q": "severity:<=err", "window": 300},
            "condition": {"op": ">", "threshold": 0},
            "for_seconds": 0,
            "severity": "warning",
            "enabled": True,
            "notify": True,
        }
    )
    await database.set_rule_state(rule_id, "firing", time.time(), 3.0, ("firing", 3.0, "Errors: 3 (> 0)"))
    firing = await database.firing_alerts()
    assert firing[0]["rule_name"] == "Errors" and firing[0]["severity"] == "warning"
    alerts = await database.alerts(time.time() - 60, time.time() + 60)
    assert alerts[0]["state"] == "firing" and alerts[0]["value"] == 3.0
    rule = await database.rule(rule_id)
    assert rule["state"] == "firing" and rule["query"]["q"] == "severity:<=err"
    # Cleared by hand: not firing any more, the notification dismissed, a row in the history
    from src.rules import RuleEngine

    class FakeHomeAssistant:
        dismissed: list = []

        async def dismiss(self, rule_id):
            self.dismissed.append(rule_id)

    home_assistant = FakeHomeAssistant()
    engine = RuleEngine(database, home_assistant)
    assert await engine.clear(rule_id)
    assert (await database.rule(rule_id))["state"] == "cleared" and home_assistant.dismissed == [rule_id]
    assert await database.firing_alerts() == [] and not await engine.clear(rule_id)
    states = [a["state"] for a in await database.alerts(time.time() - 60, time.time() + 60)]
    assert states == ["cleared", "firing"]
    # A changed condition starts from ok again
    await database.save_rule({**rule, "condition": {"op": ">", "threshold": 5}})
    assert (await database.rule(rule_id))["state"] == "ok"
    assert await database.delete_rule(rule_id) and not await database.alerts(0, time.time() + 60)

    search_id = await database.save_search(None, "ssh", "app:sshd")
    assert await database.save_search(None, "ssh", "app:sshd Accepted") == search_id
    assert (await database.searches())[0]["query"] == "app:sshd Accepted"
    dashboard_id = await database.save_dashboard(None, "Mine", [{"id": "a", "type": "stat"}])
    with pytest.raises(asyncpg.UniqueViolationError):
        await database.save_dashboard(None, "Mine", [])
    assert await database.delete_dashboard(dashboard_id) and not await database.delete_dashboard(dashboard_id)


async def test_sources_and_housekeeping(database):
    sources = Sources()
    writer = Writer(database, PatternMiner(), sources)
    sources.seen("syslog", "nas", "10.0.0.2", 5)
    sources.seen("scrape", "router", "router:9100", 0, "HTTP 500")
    await writer._write_sources()
    sources.seen("syslog", "nas", None, 2)
    await writer._write_sources()
    rows = {r["name"]: r for r in await database.sources()}
    assert rows["nas"]["received"] == 7 and rows["nas"]["host"] == "10.0.0.2"
    assert rows["router"]["errors"] == 1 and rows["router"]["last_error"] == "HTTP 500"
    assert rows["router"]["last_seen"] is None
    await database.housekeeping()
    assert await database.counts() == {"logs_24h": 0, "series": 0, "rules": 0, "firing": 0}


async def test_reconnects_after_the_connection_is_lost(database):
    config = database.config
    other = await _connected(config)
    try:
        await database.fetch(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name = 'vigil' AND pid <> pg_backend_pid()"
        )
        # The pool replaces dead connections; a query may fail once, then works
        for _ in range(3):
            try:
                assert await other.fetchval("SELECT 1") == 1
                break
            except Exception:
                other.ready.set()
                await asyncio.sleep(0.2)
        assert await other.fetchval("SELECT 1") == 1
    finally:
        other._test_task.cancel()
        await other.close()


async def test_both_layouts_are_tested(database, request):
    assert database.timescaledb == ("timescaledb" in request.node.callspec.id)


async def test_windows_events_are_searchable(database):
    import json

    from src.otlp import parse_json_lines

    now = datetime.now(UTC).replace(microsecond=0)
    stamp = now.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    records = [
        {
            "Channel": "Security",
            "Computer": "pc1",
            "EventID": 4625,
            "Level": 0,
            "Keywords": "0x8010000000000000",
            "TimeCreated": stamp,
            "ProviderName": "Microsoft-Windows-Security-Auditing",
            "Message": "An account failed to log on.",
            "EventData": {"TargetUserName": "bob", "LogonType": 10},
        },
        {
            "Channel": "Security",
            "Computer": "pc1",
            "EventID": 4732,
            "Level": 0,
            "Keywords": "0x8020000000000000",
            "TimeCreated": stamp,
            "ProviderName": "Microsoft-Windows-Security-Auditing",
            "Message": "A member was added to a security-enabled local group.",
            "EventData": {"TargetUserName": "Administrators", "MemberSid": "S-1-5-21-1"},
        },
    ]
    entries, rejected = parse_json_lines("\n".join(json.dumps(r) for r in records))
    assert rejected == 0
    await _write(database, entries)
    t = now.timestamp()

    async def count(q):
        return len((await database.logs(q, t - 60, t + 60, 100, None))["rows"])

    assert await count("attrs.event_id:4625") == 1
    assert await count("attrs.event_id:4625 attrs.logon_type:10") == 1
    assert await count("attrs.mitre:T*") == 2
    assert await count('attrs.event:"failed logon"') == 1
    assert await count("attrs.category:account_change attrs.group:Administrators") == 1
    assert await count("host:pc1 app:Security severity:<=warning") == 1
    assert await database.count_logs("attrs.mitre:T1110", t - 60, t + 60) == 1


async def test_windows_events_make_one_pattern_each(database):
    import json

    from src.otlp import parse_json_lines

    lines = []
    for i, app in enumerate(("esrv_svc.exe", "svchost.exe", "a much longer program name.exe")):
        message = (
            "The Windows Filtering Platform has permitted a connection.\r\n\r\nApplication Information:\r\n"
            f"\tProcess ID:\t\t{100 + i}\r\n\tApplication Name:\t\\device\\{app}\r\n"
            + "\tDirection:\t\tInbound\r\n"
            * i
        )
        lines.append(
            json.dumps({"Channel": "Security", "Computer": "pc1", "EventID": 5156, "Level": 0, "Message": message})
        )
    entries, _ = parse_json_lines("\n".join(lines))
    await _write(database, entries)
    rows = await database.fetch("SELECT template FROM vigil.patterns")
    assert [r["template"] for r in rows] == ["The Windows Filtering Platform has permitted a connection."]
