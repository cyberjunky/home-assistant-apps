"""A stand-in for Vigil's web API, to work on the UI without a database or the real backend.

Serves src/web/index.html the way webui.py does (``__BASE__`` from X-Ingress-Path, ``__ADDON_VERSION__``)
and answers every /api/... endpoint of DESIGN.md with generated data: a week of logs from a few hosts
(mikrotik, fritz.box, freqle, ...), patterns, metric series with an anomaly, rules in every state, two
dashboards. New log lines keep arriving, for the live tail.

    python3 tests/web_mock.py --port 18098
    python3 tests/web_mock.py --state empty          # connected, nothing received yet
    python3 tests/web_mock.py --state disconnected   # the database unreachable

Only aiohttp is needed. Not a test: pytest doesn't collect it (no test_ prefix).
"""

import argparse
import bisect
import hashlib
import json
import math
import random
import re
import time
from pathlib import Path

from aiohttp import web

WEB_DIR = Path(__file__).resolve().parent.parent / "src" / "web"
VERSION = "0.1.0-mock"
SEVERITIES = ["emerg", "alert", "crit", "err", "warning", "notice", "info", "debug"]
SEV_ALIASES = {"error": 3, "warn": 4, "critical": 2, "fatal": 2}
WEEK = 7 * 86400

# (host, app, severity, template with {} for the variable parts, weight, source)
TEMPLATES = [
    (
        "mikrotik",
        "firewall",
        6,
        "input: in:ether1 out:(unknown 0), connection-state:new proto TCP (SYN), {ip}:{port}->{ip}:22, len 60",
        10,
        "syslog",
    ),
    ("mikrotik", "dhcp", 6, "defconf assigned {ip} to {mac}", 4, "syslog"),
    ("mikrotik", "interface", 5, "ether{n} link up (speed 1G, full duplex)", 1, "syslog"),
    ("mikrotik", "interface", 4, "ether{n} link down", 1, "syslog"),
    ("mikrotik", "system", 3, "login failure for user admin from {ip} via ssh", 2, "syslog"),
    ("mikrotik", "wireless", 6, "{mac}@wlan1: connected, signal strength -{n}", 3, "syslog"),
    (
        "fritz.box",
        "fritzbox",
        6,
        "WLAN-Gerät angemeldet (5 GHz), {n} Mbit/s, Name: phone-{n}, MAC: {mac}",
        3,
        "fritzbox",
    ),
    ("fritz.box", "fritzbox", 5, "Internetverbindung wurde erfolgreich hergestellt. IP-Adresse: {ip}", 1, "fritzbox"),
    ("fritz.box", "fritzbox", 4, "DSL antwortet nicht (Keine DSL-Synchronisation).", 1, "fritzbox"),
    (
        "freqle",
        "sshd",
        6,
        "Accepted publickey for freqle from {ip} port {port} ssh2: ED25519 SHA256:{hex}",
        2,
        "syslog",
    ),
    ("freqle", "sshd", 6, "Disconnected from user freqle {ip} port {port}", 2, "syslog"),
    ("freqle", "sshd", 4, "Invalid user {word} from {ip} port {port}", 3, "syslog"),
    ("freqle", "kernel", 4, "[UFW BLOCK] IN=eth0 OUT= SRC={ip} DST={ip} PROTO=TCP SPT={port} DPT={port}", 6, "syslog"),
    ("freqle", "dockerd", 6, "container {hex} health_status: healthy", 5, "otlp"),
    ("freqle", "postgres", 3, 'ERROR:  duplicate key value violates unique constraint "runs_pkey" (id={n})', 1, "otlp"),
    ("freqle", "CRON", 6, "(root) CMD (/usr/local/bin/backup.sh --target /mnt/nas/{word})", 1, "syslog"),
    ("nas", "smbd", 6, "connect to service {word} initially as user freqle (uid={n})", 3, "syslog"),
    (
        "nas",
        "smartd",
        4,
        "Device: /dev/sda [SAT], SMART Usage Attribute: 194 Temperature_Celsius changed from {n} to {n}",
        1,
        "syslog",
    ),
    ("sw-netgear-1", "", 5, "Port g{n} link up", 1, "syslog"),
    ("sw-netgear-1", "", 7, "STP: port g{n} state forwarding", 2, "syslog"),
    ("homeassistant", "homeassistant", 4, "Update for sensor.power_meter_{n} fails: timeout", 2, "json"),
    ("homeassistant", "homeassistant", 6, "Setup of domain {word} took {n} seconds", 1, "json"),
]
# Arrives only in the last half hour: a NEW pattern, and a SPIKE of mikrotik errors
NEW_TEMPLATE = (
    "mikrotik",
    "system",
    2,
    "ipsec: phase1 negotiation failed due to time up {ip}[500]<=>{ip}[500]",
    0,
    "syslog",
)
WORDS = ["oracle", "admin", "media", "backups", "test", "ubuntu", "photos", "zigbee", "mqtt", "esphome"]


def template_text(t: str) -> str:
    return re.sub(r"\{\w+\}", "<*>", t)


class Data:
    def __init__(self, empty: bool = False) -> None:
        self.empty = empty
        self.rng = random.Random(7)
        self.now = time.time()
        self.logs: list[dict] = []
        self.ts: list[float] = []
        self.patterns: dict[int, dict] = {}
        self.templates = [*TEMPLATES, NEW_TEMPLATE]
        for i, t in enumerate(self.templates):
            self.patterns[i + 1] = {
                "id": i + 1,
                "template": template_text(t[3]),
                "host": t[0],
                "app": t[1],
                "first_seen": None,
            }
        self.last_generated = self.now - WEEK
        if not empty:
            self.generate(self.now)
        self.rules = [] if empty else make_rules(self.now)
        self.next_rule = max([r["id"] for r in self.rules] + [0]) + 1
        self.alerts = [] if empty else make_alerts(self.now, self.rules)
        self.dashboards = make_dashboards()
        self.next_dash = 3
        self.searches = [
            {
                "id": 1,
                "name": "Router errors",
                "query": "host:mikrotik severity:<=err",
                "created": self.now - 86400 * 3,
            },
            {"id": 2, "name": "SSH logins", "query": "app:sshd Accepted", "created": self.now - 86400},
        ]
        self.next_search = 3
        self.dropped = 0

    def fill(self, t: str) -> str:
        r = self.rng
        repl = {
            "ip": lambda: f"{r.choice([192, 10, 185, 45])}.{r.randint(0, 255)}.{r.randint(0, 255)}.{r.randint(1, 254)}",
            "port": lambda: str(r.randint(1024, 65535)),
            "mac": lambda: ":".join(f"{r.randint(0, 255):02X}" for _ in range(6)),
            "n": lambda: str(r.randint(1, 48)),
            "hex": lambda: "".join(r.choice("0123456789abcdef") for _ in range(12)),
            "word": lambda: r.choice(WORDS),
        }
        return re.sub(r"\{(\w+)\}", lambda m: repl[m.group(1)](), t)

    def add(self, ts: float, idx: int) -> None:
        host, app, sev, tmpl, _w, source = self.templates[idx]
        msg = self.fill(tmpl)
        attrs: dict = {}
        if source == "otlp":
            attrs = {
                "service.name": app,
                "container.id": msg[-12:] if app == "dockerd" else None,
                "k8s": {"ns": "default"},
            }
            attrs = {k: v for k, v in attrs.items() if v is not None}
        elif source == "json":
            attrs = {"logger": f"homeassistant.components.{self.rng.choice(WORDS)}", "thread": self.rng.randint(1, 9)}
        elif source == "syslog":
            attrs = {"procid": str(self.rng.randint(100, 30000))} if app in ("sshd", "CRON", "smbd") else {}
        if "<script" not in msg and self.rng.random() < 0.002:
            msg += ' <script>alert("xss")</script> & "quotes"'  # escaping check
        row = {
            "ts": round(ts, 3),
            "host": host,
            "app": app or None,
            "source": source,
            "severity": sev,
            "facility": 16 if source == "syslog" else None,
            "message": msg,
            "attrs": attrs,
            "pattern": idx + 1,
        }
        self.logs.append(row)
        self.ts.append(row["ts"])
        p = self.patterns[idx + 1]
        if p["first_seen"] is None:
            p["first_seen"] = row["ts"]
        p["last_seen"] = row["ts"]
        p["sample"] = msg

    def generate(self, until: float) -> None:
        """Lines from last_generated to until: ~1 per 12 s on average, more by day, bursts lately."""
        if self.empty:
            return
        weights = [t[4] for t in TEMPLATES]
        t = self.last_generated
        while True:
            hour = time.localtime(t).tm_hour
            rate = 1 / 12 * (1.6 if 8 <= hour <= 23 else 0.5)
            recent = until - t < 2400 and self.now - t < 2400
            t += self.rng.expovariate(rate * (3 if recent else 1))
            if t >= until:
                break
            if recent and self.rng.random() < 0.35:
                self.add(t, 4 if self.rng.random() < 0.6 else len(self.templates) - 1)  # spike + new pattern
            else:
                self.add(t, self.rng.choices(range(len(TEMPLATES)), weights)[0])
        self.last_generated = until

    def window(self, frm: float, to: float) -> list[dict]:
        a = bisect.bisect_left(self.ts, frm)
        b = bisect.bisect_right(self.ts, to)
        return self.logs[a:b]


# =====================================================================
# The query language (enough of it)
# =====================================================================
TOKEN = re.compile(r'-?[\w.]+:(?:[<>]=?|=)?(?:"(?:[^"\\]|\\.)*"|\S+)|-?"(?:[^"\\]|\\.)*"|\S+')
FIELDS = {"host", "app", "source", "severity", "pattern"}


class QueryError(ValueError):
    pass


def sev_number(v: str) -> int:
    v = v.lower()
    if v.isdigit():
        return int(v)
    if v in SEVERITIES:
        return SEVERITIES.index(v)
    if v in SEV_ALIASES:
        return SEV_ALIASES[v]
    raise QueryError(f"Unknown severity {v!r}")


def parse_query(q: str):
    preds = []
    for tok in TOKEN.findall(q or ""):
        neg = tok.startswith("-") and len(tok) > 1
        if neg:
            tok = tok[1:]
        m = re.match(r"^([\w.]+):([<>]=?|=)?(.*)$", tok)
        if m and (m.group(1) in FIELDS or m.group(1).startswith("attrs.")):
            field, op, value = m.group(1), m.group(2) or "", m.group(3).strip('"')
            if field == "severity":
                n = sev_number(value)
                # Compared as syslog numbers: severity:<=err (<=3) is err and worse
                ops = {
                    "": lambda s, n=n: s == n,
                    "=": lambda s, n=n: s == n,
                    ">=": lambda s, n=n: s >= n,
                    ">": lambda s, n=n: s > n,
                    "<=": lambda s, n=n: s <= n,
                    "<": lambda s, n=n: s < n,
                }
                f = ops[op]
                pred = lambda r, f=f: f(r["severity"])  # noqa: E731
            elif field == "pattern":
                if not value.isdigit():
                    raise QueryError("pattern: takes a number")
                pred = lambda r, v=int(value): r["pattern"] == v  # noqa: E731
            else:

                def get(r, field=field):
                    if field.startswith("attrs."):
                        v = r["attrs"].get(field[6:])
                        return None if v is None else str(v)
                    return r.get(field)

                if value.endswith("*"):
                    pred = lambda r, g=get, p=value[:-1].lower(): (g(r) or "").lower().startswith(p)  # noqa: E731
                else:
                    pred = lambda r, g=get, v=value.lower(): (g(r) or "").lower() == v  # noqa: E731
        elif (
            m
            and ":" in tok
            and not tok.startswith('"')
            and re.match(r"^[a-z_]+$", m.group(1))
            and m.group(1) not in ("http", "https")
        ):
            raise QueryError(f"Unknown field {m.group(1)!r}: use host, app, source, severity, pattern or attrs.<key>")
        else:
            text = tok.strip('"').lower()
            pred = lambda r, t=text: t in f"{r['host'] or ''} {r['app'] or ''} {r['message']}".lower()  # noqa: E731
        preds.append((neg, pred))
    return lambda r: all(p(r) != neg for neg, p in preds)


# =====================================================================
# Metrics
# =====================================================================
def noise(*parts) -> float:
    h = hashlib.blake2b(repr(parts).encode(), digest_size=8).digest()
    return int.from_bytes(h, "big") / 2**64 * 2 - 1


SERIES = []  # {name, labels, unit, kind, fn(t) -> value, base(t) -> expected, sigma}


def diurnal(t: float, peak_hour: float = 20) -> float:
    h = time.localtime(t).tm_hour + time.localtime(t).tm_min / 60
    return math.cos((h - peak_hour) / 24 * 2 * math.pi)


def add_series(name, labels, unit, kind, base, sigma, spike=None):
    def fn(t, base=base, sigma=sigma, spike=spike, key=(name, tuple(sorted(labels.items())))):
        v = base(t) + sigma * noise(key, int(t // 60))
        if spike:
            v = spike(t, v)
        return v

    SERIES.append({"name": name, "labels": labels, "unit": unit, "kind": kind, "fn": fn, "base": base, "sigma": sigma})


def build_series(now: float) -> None:
    add_series(
        "fritz.wan_bytes_received_rate",
        {"host": "fritz.box"},
        "bytes/s",
        "gauge",
        lambda t: 2.2e6 + 1.6e6 * diurnal(t),
        4e5,
    )
    add_series(
        "fritz.wan_bytes_sent_rate", {"host": "fritz.box"}, "bytes/s", "gauge", lambda t: 4e5 + 2.5e5 * diurnal(t), 8e4
    )
    add_series(
        "fritz.wan_link_up",
        {"host": "fritz.box"},
        "",
        "gauge",
        lambda t: 1.0,
        0,
        spike=lambda t, v: 0.0 if now - 3 * 86400 < t < now - 3 * 86400 + 600 else v,
    )
    add_series("fritz.uptime_seconds", {"host": "fritz.box"}, "s", "gauge", lambda t: t - (now - 12 * 86400), 0)
    add_series("fritz.max_bitrate_down", {"host": "fritz.box"}, "bits/s", "gauge", lambda t: 1.0e8, 0)
    # The anomaly: the router's CPU since 90 minutes ago
    add_series(
        "mikrotik_cpu_load",
        {"host": "mikrotik", "job": "mikrotik"},
        "%",
        "gauge",
        lambda t: 22 + 9 * diurnal(t),
        3,
        spike=lambda t, v: v + 62 * min(1.0, (t - (now - 5400)) / 600) if t > now - 5400 else v,
    )
    for i in range(1, 6):
        add_series(
            "mikrotik_interface_rx_bytes_total",
            {"host": "mikrotik", "interface": f"ether{i}"},
            "bytes",
            "counter",
            lambda t, i=i: (6 - i) * 4e5 * (1.3 + diurnal(t)),
            1e5,
        )
        add_series(
            "mikrotik_interface_tx_bytes_total",
            {"host": "mikrotik", "interface": f"ether{i}"},
            "bytes",
            "counter",
            lambda t, i=i: (6 - i) * 1e5 * (1.3 + diurnal(t)),
            3e4,
        )
    for inst in ("freqle:9100", "nas:9100"):
        add_series(
            "node_load1",
            {"instance": inst, "job": "node"},
            "",
            "gauge",
            lambda t, k=len(inst): 0.6 + 0.4 * diurnal(t, 14) + k / 40,
            0.15,
        )
        add_series(
            "node_memory_MemAvailable_bytes",
            {"instance": inst, "job": "node"},
            "bytes",
            "gauge",
            lambda t: 6.2e9 - 1e9 * diurnal(t),
            1e8,
        )
        for mode in ("user", "system", "iowait"):
            add_series(
                "node_cpu_seconds_total",
                {"instance": inst, "job": "node", "mode": mode},
                "s",
                "counter",
                lambda t, m=mode: {"user": 0.8, "system": 0.3, "iowait": 0.05}[m] * (1.2 + diurnal(t, 14)),
                0.05,
            )
    add_series("vigil.queue_size", {}, "", "gauge", lambda t: 40 + 30 * diurnal(t), 20)
    add_series("vigil.dropped_total", {}, "", "counter", lambda t: 0.0, 0)
    add_series("vigil.write_seconds", {}, "s", "gauge", lambda t: 0.012, 0.004)


def nice_step(span: float, points: int = 150) -> int:
    raw = span / points
    for s in (15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600, 43200, 86400):
        if s >= raw:
            return s
    return 86400


def metric_query(name, flt, agg, group, frm, to, step, anomaly):
    matching = [s for s in SERIES if s["name"] == name and all(s["labels"].get(k) == v for k, v in flt.items())]
    if not matching:
        return None
    step = int(step) if step else nice_step(to - frm)
    t0 = math.floor(frm / step) * step
    ts = [t0 + k * step for k in range(int((to - t0) // step) + 1)]
    ts = [t for t in ts if t <= time.time()]
    groups: dict[str, list] = {}
    for s in matching:
        key = s["labels"].get(group, "") if group else ""
        groups.setdefault(key, []).append(s)
    counter = matching[0]["kind"] == "counter"
    out = []
    for key, members in sorted(groups.items()):

        def at(s, t, expected=False):
            f = s["base"] if expected else s["fn"]
            if counter and agg != "rate":
                return max(0.0, f(t)) * (t - (time.time() - 30 * 86400))  # a running total
            return max(0.0, f(t)) if counter else f(t)

        vals, lo, hi, anomalies = [], [], [], []
        for k, t in enumerate(ts):
            pts = [at(s, t) for s in members]
            v = {
                "avg": sum(pts) / len(pts),
                "min": min(pts),
                "max": max(pts),
                "sum": sum(pts),
                "rate": sum(pts),
                "last": pts[-1],
            }.get(agg, sum(pts) / len(pts))
            vals.append(round(v, 4))
            if anomaly:
                exp = [at(s, t, True) for s in members]
                e = sum(exp) / len(exp) if agg not in ("sum", "rate") else sum(exp)
                sig = max(1e-9, 3 * max(s["sigma"] for s in members) * (len(members) if agg in ("sum", "rate") else 1))
                lo.append(round(e - sig, 4))
                hi.append(round(e + sig, 4))
                if not (e - sig <= v <= e + sig):
                    anomalies.append(k)
        item = {"label": f"{key}" if group else name, "values": vals}
        if anomaly:
            item["band"] = {"lo": lo, "hi": hi}
            item["anomalies"] = anomalies
        out.append(item)
    unit = matching[0]["unit"]
    if counter and agg == "rate":
        unit = unit + "/s" if unit else "/s"
    return {"t": ts, "series": out, "unit": unit, "step": step}


# =====================================================================
# Rules, alerts, dashboards
# =====================================================================
def make_rules(now):
    def rule(i, name, kind, query, op, th, fs, sev, state, since, value, enabled=True, notify=True):
        return {
            "id": i,
            "name": name,
            "kind": kind,
            "query": query,
            "condition": {"op": op, "threshold": th},
            "for_seconds": fs,
            "severity": sev,
            "enabled": enabled,
            "notify": notify,
            "state": state,
            "state_since": now - since,
            "last_value": value,
            "last_eval": now - 12,
            "created": now - 10 * 86400,
            "updated": now - 86400,
        }

    return [
        rule(
            1,
            "Errors from any host",
            "log_count",
            {"q": "severity:<=err", "window": 300},
            ">",
            20,
            0,
            "warning",
            "firing",
            1900,
            57,
        ),
        rule(2, "fritz.box silent", "absence", {"q": "host:fritz.box"}, ">", 600, 0, "warning", "ok", 86400 * 2, 41),
        rule(
            3,
            "SSH login",
            "log_count",
            {"q": "app:sshd Accepted", "window": 300},
            ">",
            0,
            120,
            "info",
            "pending",
            45,
            1,
        ),
        rule(4, "New log patterns", "pattern_new", {"q": "", "window": 3600}, ">", 0, 0, "info", "firing", 1500, 1),
        rule(
            5,
            "Router CPU > 85 %",
            "metric",
            {"name": "mikrotik_cpu_load", "filter": {"host": "mikrotik"}, "agg": "avg", "window": 300},
            ">",
            85,
            300,
            "critical",
            "firing",
            4200,
            91.3,
        ),
        rule(
            6,
            "WAN throughput anomaly",
            "anomaly",
            {"name": "fritz.wan_bytes_received_rate", "filter": {}},
            ">",
            3,
            600,
            "warning",
            "ok",
            86400,
            0.8,
        ),
        rule(
            7,
            "NAS disk temperature",
            "metric",
            {"name": "node_load1", "filter": {"instance": "nas:9100"}, "agg": "max", "window": 900},
            ">",
            4,
            0,
            "warning",
            "ok",
            86400 * 5,
            1.2,
            enabled=False,
        ),
    ]


def make_alerts(now, rules):
    names = {r["id"]: r for r in rules}
    events = []
    aid = 1

    def ev(rule, ts, state, value, msg):
        nonlocal aid
        r = names[rule]
        events.append(
            {
                "id": aid,
                "rule": rule,
                "rule_name": r["name"],
                "ts": ts,
                "state": state,
                "value": value,
                "message": msg,
                "severity": r["severity"],
            }
        )
        aid += 1

    for d in range(6, 0, -1):
        start = now - d * 86400 + 3600 * (d % 5)
        ev(1, start, "firing", 24 + d, f"{24 + d} error lines in 5 min")
        ev(1, start + 1200 + d * 300, "resolved", 6, "back below 20")
    ev(2, now - 3 * 86400, "firing", 610, "no lines from fritz.box for 10 min")
    ev(2, now - 3 * 86400 + 900, "resolved", 3, "fritz.box is sending again")
    ev(1, now - 1900, "firing", 57, "57 error lines in 5 min")
    ev(4, now - 1500, "firing", 1, "1 new pattern: ipsec: phase1 negotiation failed due to time up <*>[500]<=><*>[500]")
    ev(5, now - 4200, "firing", 91.3, "mikrotik_cpu_load avg 91.3 > 85 for 5 min")
    ev(6, now - 2 * 86400, "firing", 3.4, "fritz.wan_bytes_received_rate |z| 3.4")
    ev(6, now - 2 * 86400 + 1800, "resolved", 1.1, "back inside the band")
    return events


def make_dashboards():
    return [
        {
            "id": 1,
            "name": "Hosts",
            "builtin": True,
            "panels": [
                {
                    "id": "h1",
                    "title": "Log volume by host",
                    "type": "bar",
                    "query": {"source": "fields", "field": "hosts"},
                    "w": 2,
                    "h": 2,
                },
                {
                    "id": "h2",
                    "title": "Errors by host",
                    "type": "bar",
                    "query": {"source": "fields", "field": "hosts", "q": "severity:<=err"},
                    "w": 2,
                    "h": 2,
                },
                {
                    "id": "h3",
                    "title": "Log volume",
                    "type": "timeseries",
                    "query": {"source": "logs", "q": ""},
                    "w": 3,
                    "h": 1,
                },
                {
                    "id": "h4",
                    "title": "Errors",
                    "type": "stat",
                    "query": {"source": "logs", "q": "severity:<=err"},
                    "w": 1,
                    "h": 1,
                },
                {
                    "id": "h5",
                    "title": "Top patterns",
                    "type": "table",
                    "query": {"source": "patterns", "sort": "count"},
                    "w": 4,
                    "h": 2,
                },
            ],
        },
        {
            "id": 2,
            "name": "Network",
            "builtin": True,
            "panels": [
                {
                    "id": "n1",
                    "title": "FRITZ!Box throughput",
                    "type": "timeseries",
                    "query": {
                        "queries": [
                            {"name": "fritz.wan_bytes_received_rate", "agg": "avg"},
                            {"name": "fritz.wan_bytes_sent_rate", "agg": "avg"},
                        ]
                    },
                    "w": 3,
                    "h": 2,
                },
                {
                    "id": "n2",
                    "title": "WAN link",
                    "type": "stat",
                    "query": {"name": "fritz.wan_link_up", "agg": "last"},
                    "w": 1,
                    "h": 1,
                },
                {
                    "id": "n3",
                    "title": "FRITZ!Box uptime",
                    "type": "stat",
                    "query": {"name": "fritz.uptime_seconds", "agg": "last"},
                    "w": 1,
                    "h": 1,
                },
                {
                    "id": "n4",
                    "title": "MikroTik interface traffic",
                    "type": "timeseries",
                    "query": {"name": "mikrotik_interface_rx_bytes_total", "agg": "rate", "group": "interface"},
                    "w": 2,
                    "h": 2,
                },
                {
                    "id": "n5",
                    "title": "Router CPU",
                    "type": "timeseries",
                    "query": {"name": "mikrotik_cpu_load", "agg": "avg", "anomaly": True},
                    "w": 2,
                    "h": 2,
                },
                {
                    "id": "n6",
                    "title": "Router log",
                    "type": "logs",
                    "query": {"source": "logs", "q": "host:mikrotik"},
                    "w": 4,
                    "h": 2,
                },
            ],
        },
    ]


# =====================================================================
# The web app
# =====================================================================
D: Data
STATE = "ok"


def arg_f(request, key, default=None):
    v = request.query.get(key)
    try:
        return float(v) if v not in (None, "") else default
    except ValueError:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": f"{key} must be a number"}), content_type="application/json"
        ) from None


def window(request):
    now = time.time()
    to = arg_f(request, "to", now)
    frm = arg_f(request, "from", to - 3600)
    if to <= frm:
        raise web.HTTPBadRequest(text=json.dumps({"error": "to must be after from"}), content_type="application/json")
    return frm, to


def err(message, status=400):
    return web.json_response({"error": message}, status=status)


def tick():
    D.generate(time.time())


def filtered(request, frm, to):
    try:
        match = parse_query(request.query.get("q", ""))
    except QueryError as e:
        raise web.HTTPBadRequest(text=json.dumps({"error": str(e)}), content_type="application/json") from None
    return [r for r in D.window(frm, to) if match(r)]


@web.middleware
async def guard(request, handler):
    if (
        STATE == "disconnected"
        and request.path.startswith("/api/")
        and request.path not in ("/api/status", "/api/donate")
    ):
        return err("The database is not connected: connection refused", 503)
    if (
        request.method == "POST"
        and request.path.startswith("/api/")
        and request.headers.get("X-Vigil") != "1"
        and request.path != "/api/donate"
    ):
        return err("Missing X-Vigil header", 403)
    return await handler(request)


async def index(request):
    base = request.headers.get("X-Ingress-Path", "").rstrip("/")
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    html = html.replace("__ADDON_VERSION__", VERSION).replace("__BASE__", base)
    return web.Response(text=html, content_type="text/html", headers={"Cache-Control": "no-cache, must-revalidate"})


async def status(_request):
    if STATE == "disconnected":
        return web.json_response(
            {
                "version": VERSION,
                "connected": False,
                "error": "connection refused (a0d7b954-app-chronicle:5432)",
                "database": {"host": "a0d7b954-app-chronicle", "name": "vigil", "user": "vigil"},
                "receivers": {"syslog_udp": 5514, "syslog_tcp": 5514, "otlp": 4318},
                "queue": {"size": 4211, "dropped": 0},
            }
        )
    tick()
    day = D.window(time.time() - 86400, time.time())
    firing = sum(1 for r in D.rules if r["enabled"] and r["state"] == "firing")
    return web.json_response(
        {
            "version": VERSION,
            "connected": True,
            "database": {
                "host": "a0d7b954-app-chronicle",
                "name": "vigil",
                "user": "vigil",
                "timescaledb": "2.17.2",
                "version": "16.6",
            },
            "counts": {"logs_24h": len(day), "series": len(SERIES), "rules": len(D.rules), "firing": firing},
            "receivers": {"syslog_udp": 5514, "syslog_tcp": 5514, "otlp": 4318},
            "queue": {"size": random.randint(0, 80), "dropped": D.dropped},
        }
    )


def sev_group(s):
    return "err" if s <= 3 else "warning" if s == 4 else "info" if s <= 6 else "debug"


def histogram(rows, frm, to, buckets, grouped=False):
    step = max(1.0, (to - frm) / buckets)
    t = [round(frm + k * step, 3) for k in range(buckets)]
    by: dict[str, list] = {}
    for r in rows:
        k = min(buckets - 1, int((r["ts"] - frm) // step))
        name = sev_group(r["severity"]) if grouped else SEVERITIES[r["severity"]]
        by.setdefault(name, [0] * buckets)[k] += 1
    return {"t": t, "by_severity": by, "total": len(rows)}


async def overview(request):
    tick()
    hours = int(arg_f(request, "hours", 24))
    if hours not in (1, 6, 24, 168):
        return err("hours must be 1, 6, 24 or 168")
    to = time.time()
    frm = to - hours * 3600
    rows = D.window(frm, to)
    hosts: dict[str, list] = {}
    for r in rows:
        h = hosts.setdefault(r["host"], [0, 0])
        h[0] += 1
        h[1] += r["severity"] <= 3
    new = [p for p in D.patterns.values() if p["first_seen"] and p["first_seen"] >= frm]
    counts = {}
    for r in rows:
        counts[r["pattern"]] = counts.get(r["pattern"], 0) + 1
    firing_rules = {r["id"] for r in D.rules if r["enabled"] and r["state"] == "firing"}
    latest = {}
    for a in D.alerts:
        if a["rule"] in firing_rules and a["state"] == "firing":
            latest[a["rule"]] = a
    vol = histogram(rows, frm, to, 60, grouped=True)
    return web.json_response(
        {
            "tiles": {
                "logs": len(rows),
                "errors": sum(1 for r in rows if r["severity"] <= 3),
                "warnings": sum(1 for r in rows if r["severity"] == 4),
                "hosts": len(hosts),
                "series": len(SERIES),
                "firing": len(firing_rules),
            },
            "volume": {"t": vol["t"], "by_severity": vol["by_severity"]},
            "top_hosts": [
                {"host": h, "count": c[0], "errors": c[1]} for h, c in sorted(hosts.items(), key=lambda x: -x[1][0])
            ][:10],
            "new_patterns": [
                {
                    "id": p["id"],
                    "template": p["template"],
                    "count": counts.get(p["id"], 0),
                    "first_seen": p["first_seen"],
                }
                for p in new
            ],
            "firing": list(latest.values()),
        }
    )


async def logs(request):
    tick()
    started = time.perf_counter()
    frm, to = window(request)
    before = arg_f(request, "before")
    if before is not None:
        to = min(to, before - 1e-6)
    limit = int(arg_f(request, "limit", 200))
    rows = filtered(request, frm, to)
    rows = rows[::-1]
    return web.json_response(
        {"rows": rows[:limit], "more": len(rows) > limit, "took_ms": round((time.perf_counter() - started) * 1000, 1)}
    )


async def logs_histogram(request):
    tick()
    frm, to = window(request)
    buckets = max(1, min(500, int(arg_f(request, "buckets", 60))))
    return web.json_response(histogram(filtered(request, frm, to), frm, to, buckets))


async def logs_tail(request):
    tick()
    after = arg_f(request, "after", time.time() - 5)
    rows = filtered(request, after + 1e-6, time.time() + 1)
    return web.json_response({"rows": rows[::-1][:200]})


async def logs_fields(request):
    tick()
    frm, to = window(request)
    rows = filtered(request, frm, to)

    def top(key, fn=lambda v: v):
        c: dict = {}
        for r in rows:
            v = r.get(key)
            if v is not None:
                c[fn(v)] = c.get(fn(v), 0) + 1
        return [{"value": k, "count": n} for k, n in sorted(c.items(), key=lambda x: -x[1])][:50]

    return web.json_response(
        {
            "hosts": top("host"),
            "apps": top("app"),
            "sources": top("source"),
            "severities": top("severity", lambda s: SEVERITIES[s]),
        }
    )


async def patterns(request):
    tick()
    frm, to = window(request)
    span = to - frm
    sort = request.query.get("sort", "count")
    q = request.query.get("q", "").lower()
    cur: dict = {}
    spark: dict = {}
    for r in D.window(frm, to):
        cur[r["pattern"]] = cur.get(r["pattern"], 0) + 1
        k = min(23, int((r["ts"] - frm) / span * 24))
        spark.setdefault(r["pattern"], [0] * 24)[k] += 1
    prev: dict = {}
    for r in D.window(frm - span, frm):
        prev[r["pattern"]] = prev.get(r["pattern"], 0) + 1
    out = []
    for pid, n in cur.items():
        p = D.patterns[pid]
        if q and q not in p["template"].lower():
            continue
        out.append({**p, "count": n, "prev_count": prev.get(pid, 0), "spark": spark.get(pid, [0] * 24)})
    if sort == "new":
        out.sort(key=lambda p: -(p["first_seen"] or 0))
    elif sort == "spike":
        out.sort(key=lambda p: -(p["count"] / max(1, p["prev_count"])))
    else:
        out.sort(key=lambda p: -p["count"])
    return web.json_response({"patterns": out[:200]})


async def metric_names(request):
    q = request.query.get("q", "").lower()
    names: dict = {}
    for s in SERIES:
        if q and q not in s["name"].lower():
            continue
        n = names.setdefault(s["name"], {"name": s["name"], "series": 0, "unit": s["unit"], "kind": s["kind"]})
        n["series"] += 1
    return web.json_response({"names": sorted(names.values(), key=lambda n: n["name"])})


async def metric_labels(request):
    name = request.query.get("name", "")
    labels: dict = {}
    for s in SERIES:
        if s["name"] == name:
            for k, v in s["labels"].items():
                labels.setdefault(k, set()).add(v)
    return web.json_response({"labels": {k: sorted(v) for k, v in labels.items()}})


async def metric_query_h(request):
    frm, to = window(request)
    try:
        flt = json.loads(request.query.get("filter") or "{}")
        assert isinstance(flt, dict)
    except (ValueError, AssertionError):
        return err("filter must be a JSON object")
    agg = request.query.get("agg", "avg")
    if agg not in ("avg", "min", "max", "sum", "rate", "last"):
        return err("Unknown aggregation")
    res = metric_query(
        request.query.get("name", ""),
        flt,
        agg,
        request.query.get("group") or "",
        frm,
        to,
        arg_f(request, "step"),
        request.query.get("anomaly") == "1",
    )
    if res is None:
        return web.json_response({"t": [], "series": [], "unit": "", "step": nice_step(to - frm)})
    return web.json_response(res)


async def changes(request):
    tick()
    frm, to = window(request)
    span = to - frm
    cur, prev = D.window(frm, to), D.window(frm - span, frm)
    items = []

    def count(rows, key):
        c: dict = {}
        for r in rows:
            c[key(r)] = c.get(key(r), 0) + 1
        return c

    ch, ph = count(cur, lambda r: r["host"]), count(prev, lambda r: r["host"])
    for h in set(ch) | set(ph):
        a, b = ch.get(h, 0), ph.get(h, 0)
        if a + b < 5:
            continue
        ratio = (a + 1) / (b + 1)
        if ratio > 1.5 or ratio < 0.5:
            items.append(
                {
                    "kind": "host",
                    "title": f"{h}: {a} lines",
                    "detail": f"{'up' if ratio > 1 else 'down'} from {b} in the window before (x{ratio:.1f})",
                    "score": round(abs(math.log(ratio)) * 2, 2),
                    "link": f"#logs/{'host%3A' + h}",
                }
            )
    cs, ps = count(cur, lambda r: SEVERITIES[r["severity"]]), count(prev, lambda r: SEVERITIES[r["severity"]])
    for s in cs:
        a, b = cs.get(s, 0), ps.get(s, 0)
        ratio = (a + 1) / (b + 1)
        if ratio > 2 and a > 5:
            items.append(
                {
                    "kind": "severity",
                    "title": f"{s}: {a} lines",
                    "detail": f"x{ratio:.1f} the {b} before",
                    "score": round(math.log(ratio) * 2.5, 2),
                    "link": f"#logs/severity%3A{s}",
                }
            )
    cp, pp = count(cur, lambda r: r["pattern"]), count(prev, lambda r: r["pattern"])
    for pid, a in cp.items():
        b = pp.get(pid, 0)
        if a >= 5 and (b == 0 or a / b >= 3):
            items.append(
                {
                    "kind": "pattern",
                    "title": D.patterns[pid]["template"][:90],
                    "detail": "new in this window" if b == 0 else f"{a} lines, was {b}",
                    "score": round(4 if b == 0 else math.log(a / b) * 2, 2),
                    "link": f"#logs/pattern%3A{pid}",
                }
            )
    if to > time.time() - 5400 and SERIES:
        items.append(
            {
                "kind": "metric",
                "title": "mikrotik_cpu_load",
                "detail": "mean 24 % → 79 %: +14.2 sigma",
                "score": 14.2,
                "link": {"tab": "metrics", "query": {"name": "mikrotik_cpu_load", "anomaly": 1}},
            }
        )
    items.sort(key=lambda i: -i["score"])
    return web.json_response({"items": items[:20]})


def windows_snippet(host: str) -> str:
    """The real Windows snippet when Vigil's modules load (they need asyncpg), else a short one."""
    try:
        from src.webui import setup_examples

        return setup_examples(514, 4318, True)["windows"].replace("HOME_ASSISTANT_IP", host)
    except ImportError:
        return f"pipeline:\n  inputs:\n    - name: winevtlog\n  outputs:\n    - name: http\n      host: {host}\n      port: 4318"


async def sources(_request):
    tick()
    now = time.time()
    hosts: dict = {}
    for r in D.window(now - 86400, now):
        h = hosts.setdefault(
            (r["source"] if r["source"] != "otlp" else "otlp", r["host"]),
            {"n": 0, "first": r["ts"], "last": r["ts"], "recent": 0},
        )
        h["n"] += 1
        h["last"] = r["ts"]
        h["recent"] += r["ts"] > now - 600
    out = [
        {
            "kind": k,
            "name": h,
            "host": h if k != "fritzbox" else "192.168.178.1",
            "first_seen": v["first"] - 86400 * 6,
            "last_seen": v["last"],
            "received": v["n"] * 7,
            "errors": 0,
            "last_error": None,
            "rate_per_min": round(v["recent"] / 10, 2),
        }
        for (k, h), v in hosts.items()
    ]
    out += (
        []
        if D.empty
        else [
            {
                "kind": "scrape",
                "name": "node-freqle",
                "host": "freqle:9100",
                "first_seen": now - 86400 * 9,
                "last_seen": now - 21,
                "received": 1_204_332,
                "errors": 0,
                "last_error": None,
                "rate_per_min": 412,
            },
            {
                "kind": "scrape",
                "name": "node-nas",
                "host": "nas:9100",
                "first_seen": now - 86400 * 9,
                "last_seen": now - 5400,
                "received": 950_120,
                "errors": 37,
                "last_error": "GET http://nas:9100/metrics: timeout after 10 s",
                "rate_per_min": 0,
            },
            {
                "kind": "fritzbox",
                "name": "fritz.box (TR-064)",
                "host": "fritz.box",
                "first_seen": now - 86400 * 9,
                "last_seen": now - 14,
                "received": 155_090,
                "errors": 2,
                "last_error": "401 Unauthorized <script>x</script>",
                "rate_per_min": 10,
            },
        ]
    )
    host = "homeassistant.local"
    return web.json_response(
        {
            "sources": out,
            "setup": {
                "syslog_port": 514,
                "otlp_port": 4318,
                "token_set": True,
                "examples": {
                    "mikrotik": f"/system logging action add name=vigil target=remote remote={host} remote-port=514 bsd-syslog=yes syslog-facility=local0\n"
                    "/system logging add topics=info action=vigil\n/system logging add topics=warning action=vigil\n"
                    "/system logging add topics=error action=vigil\n/system logging add topics=critical action=vigil",
                    "netgear": f"Server Log Mode: Enable\nServer Address: {host}\nPort: 514\nSeverity: Info",
                    "rsyslog": f"*.* @@{host}:514",
                    "vector": f'[sources.journal]\ntype = "journald"\n\n[sinks.vigil]\ntype = "http"\ninputs = ["journal"]\n'
                    f'uri = "http://{host}:4318/ingest/logs"\nencoding.codec = "json"\nframing.method = "newline_delimited"\n'
                    'request.headers.Authorization = "Bearer <ingest_token>"',
                    "otelcol": f'exporters:\n  otlphttp/vigil:\n    endpoint: http://{host}:4318\n    encoding: json\n    headers:\n      Authorization: "Bearer <ingest_token>"\n'
                    "service:\n  pipelines:\n    logs:\n      exporters: [otlphttp/vigil]\n    metrics:\n      exporters: [otlphttp/vigil]",
                    "node_exporter": "scrape_targets:\n  - name: node-freqle\n    url: http://freqle:9100/metrics\n    interval: 60",
                    "windows": windows_snippet(host),
                },
            },
        }
    )


def rule_value(rule):
    now = time.time()
    q = rule.get("query") or {}
    kind = rule.get("kind")
    if kind in ("log_count", "pattern_new", "absence") and "name" not in q:
        try:
            match = parse_query(q.get("q", ""))
        except QueryError as e:
            raise web.HTTPBadRequest(text=json.dumps({"error": str(e)}), content_type="application/json") from None
        win = float(q.get("window") or 300)
        if kind == "log_count":
            n = sum(1 for r in D.window(now - win, now) if match(r))
            return n, f"{n} matching lines in the last {int(win)} s"
        if kind == "pattern_new":
            n = sum(1 for p in D.patterns.values() if p["first_seen"] and p["first_seen"] >= now - win)
            return n, f"{n} new patterns in the last {int(win)} s"
        last = next((r["ts"] for r in reversed(D.window(now - WEEK, now)) if match(r)), None)
        return (round(now - last, 1) if last else WEEK), "the last matching line" + (
            f" was {int(now - last)} s ago" if last else ": none in 7 days"
        )
    res = metric_query(
        q.get("name", ""), q.get("filter") or {}, q.get("agg", "avg"), "", now - 3600, now, None, kind == "anomaly"
    )
    if not res or not res["series"]:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": f"No series named {q.get('name')!r}"}), content_type="application/json"
        )
    s = res["series"][0]
    if kind == "anomaly":
        lo, hi, v = s["band"]["lo"][-1], s["band"]["hi"][-1], s["values"][-1]
        z = abs(v - (lo + hi) / 2) / max(1e-9, (hi - lo) / 6)
        return round(z, 2), f"last point {v:.2f}, expected {(lo + hi) / 2:.2f}"
    if kind == "absence":
        return 30.0, "the last point was 30 s ago"
    return s["values"][-1], f"{q.get('agg', 'avg')} over the last {int(q.get('window') or 300)} s"


def compare(op, a, b):
    return {">": a > b, ">=": a >= b, "<": a < b, "<=": a <= b, "==": a == b}.get(op, False)


async def rules_get(_request):
    return web.json_response({"rules": D.rules})


async def rules_save(request):
    body = await request.json()
    if not body.get("name") or body.get("kind") not in ("log_count", "metric", "absence", "pattern_new", "anomaly"):
        return err("A rule needs a name and a known kind")
    value, _detail = rule_value(body)
    now = time.time()
    if body.get("id"):
        r = next((r for r in D.rules if r["id"] == body["id"]), None)
        if not r:
            return err("No such rule", 404)
        r.update(
            {
                k: body[k]
                for k in ("name", "kind", "query", "condition", "for_seconds", "severity", "enabled", "notify")
                if k in body
            }
        )
        r["updated"] = now
    else:
        r = {
            **body,
            "id": D.next_rule,
            "state": "ok",
            "state_since": now,
            "last_value": value,
            "last_eval": now,
            "created": now,
            "updated": now,
        }
        D.next_rule += 1
        D.rules.append(r)
    return web.json_response({"rule": r, "id": r["id"]})


async def rules_delete(request):
    body = await request.json()
    D.rules = [r for r in D.rules if r["id"] != body.get("id")]
    return web.json_response({"ok": True})


async def rules_test(request):
    body = await request.json()
    value, detail = rule_value(body)
    c = body.get("condition") or {}
    return web.json_response(
        {"value": value, "would_fire": compare(c.get("op", ">"), value, float(c.get("threshold", 0))), "detail": detail}
    )


async def alerts(request):
    frm, to = window(request)
    return web.json_response({"alerts": [a for a in D.alerts if frm <= a["ts"] <= to]})


async def dashboards_get(_request):
    return web.json_response({"dashboards": D.dashboards})


async def dashboards_save(request):
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name or not isinstance(body.get("panels", []), list):
        return err("A dashboard needs a name and a list of panels")
    if body.get("id"):
        d = next((d for d in D.dashboards if d["id"] == body["id"]), None)
        if not d:
            return err("No such dashboard", 404)
        d.update({"name": name, "panels": body.get("panels", [])})
    else:
        if any(d["name"] == name for d in D.dashboards):
            return err("A dashboard with that name exists")
        d = {"id": D.next_dash, "name": name, "panels": body.get("panels", [])}
        D.next_dash += 1
        D.dashboards.append(d)
    return web.json_response({"dashboard": d, "id": d["id"]})


async def dashboards_delete(request):
    body = await request.json()
    D.dashboards = [d for d in D.dashboards if d["id"] != body.get("id")]
    return web.json_response({"ok": True})


async def searches_get(_request):
    return web.json_response({"searches": D.searches})


async def searches_save(request):
    body = await request.json()
    name = str(body.get("name") or "").strip()
    if not name:
        return err("A saved search needs a name")
    s = next((s for s in D.searches if s["name"] == name), None)
    if s:
        s["query"] = body.get("query", "")
    else:
        s = {"id": D.next_search, "name": name, "query": body.get("query", ""), "created": time.time()}
        D.next_search += 1
        D.searches.append(s)
    return web.json_response({"search": s, "id": s["id"]})


async def searches_delete(request):
    body = await request.json()
    D.searches = [s for s in D.searches if s["id"] != body.get("id")]
    return web.json_response({"ok": True})


async def donate_get(_request):
    return web.json_response({"show": False})


async def donate_post(_request):
    return web.json_response({"ok": True})


def make_app(state: str = "ok") -> web.Application:
    global D, STATE
    STATE = state
    D = Data(empty=state != "ok")
    if state == "ok":
        build_series(D.now)
    app = web.Application(middlewares=[guard])
    r = app.router
    r.add_get("/", index)
    r.add_static("/web/", WEB_DIR)
    r.add_get("/api/status", status)
    r.add_get("/api/overview", overview)
    r.add_get("/api/logs", logs)
    r.add_get("/api/logs/histogram", logs_histogram)
    r.add_get("/api/logs/tail", logs_tail)
    r.add_get("/api/logs/fields", logs_fields)
    r.add_get("/api/patterns", patterns)
    r.add_get("/api/metrics/names", metric_names)
    r.add_get("/api/metrics/labels", metric_labels)
    r.add_get("/api/metrics/query", metric_query_h)
    r.add_get("/api/changes", changes)
    r.add_get("/api/sources", sources)
    r.add_get("/api/rules", rules_get)
    r.add_post("/api/rules/save", rules_save)
    r.add_post("/api/rules/delete", rules_delete)
    r.add_post("/api/rules/test", rules_test)
    r.add_get("/api/alerts", alerts)
    r.add_get("/api/dashboards", dashboards_get)
    r.add_post("/api/dashboards/save", dashboards_save)
    r.add_post("/api/dashboards/delete", dashboards_delete)
    r.add_get("/api/searches", searches_get)
    r.add_post("/api/searches/save", searches_save)
    r.add_post("/api/searches/delete", searches_delete)
    r.add_get("/api/donate", donate_get)
    r.add_post("/api/donate", donate_post)
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=18098)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--state", choices=("ok", "empty", "disconnected"), default="ok")
    args = parser.parse_args()
    web.run_app(make_app(args.state), host=args.host, port=args.port)
