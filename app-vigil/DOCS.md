# Home Assistant App: Vigil

Vigil collects the logs and metrics of your home network and servers, keeps them in a database
of its own on the [Chronicle](../app-chronicle/) app, and shows them in Home Assistant: search,
charts, dashboards and alerts, plus log patterns, anomaly bands and "What changed?". This page
explains how to set it up, what each page does, and how it keeps the data safe.

## Setting up

1. **A database in Chronicle.** Install the Chronicle app (0.3.0 or later), open it from the
   sidebar and go to **Maintenance → App databases → New app database…**. Name it `vigil` and
   **Create**. Chronicle shows the connection once: keep the password.
2. **Vigil.** Install **Vigil** from this repository. On its **Configuration** tab, enter that
   password as `database_password`, and start the app. It finds Chronicle by itself, creates its
   tables on the first start, and then shows *connected* in the status bar at the bottom.
3. **Sources.** Open Vigil from the sidebar and go to **Sources**: it has the setup for each
   kind of sender, with Home Assistant's address filled in. Logs show up within seconds of the
   first line arriving.

If the status bar says the database can't be reached: check that Chronicle runs, that the
password is the one it showed (*New password…* in Chronicle gives a new one; enter that), and
that the database and login are both named as in the options.

## Options

| Option | Default | |
|---|---|---|
| `database_password` | | The password Chronicle showed for Vigil's database |
| `database_host` | (found) | Chronicle's host name; empty = Vigil finds the Chronicle app itself |
| `database_port` | 5432 | |
| `database_name`, `database_user` | `vigil` | The names you gave the app database in Chronicle |
| `ingest_token` | | The token for OpenTelemetry and JSON on port 4318 (see *Ingest port*) |
| `scrape_targets` | | Prometheus exporters: `name`, `url` of its `/metrics`, `interval` in seconds (10-3600, default 60) |
| `fritzbox_host`, `fritzbox_user`, `fritzbox_password` | | The FRITZ!Box to collect from; empty = off |
| `notify_service` | | Where alerts go besides a Home Assistant notification, such as `notify.mobile_app_phone` |
| `logs_retention_days` | 14 | How long logs are kept (1-365) |
| `metrics_retention_days` | 90 | How long metrics are kept (1-1825) |
| `log_level` | `info` | The app's own logging |

### Network

| Port | For |
|---|---|
| **514** UDP and TCP | Syslog from routers, switches and servers |
| **4318** TCP | OpenTelemetry over HTTP (JSON) and JSON lines |

Change a port on the app's **Network** settings, or clear it to switch that receiver off. The
web UI has no port: it is only reachable through Home Assistant.

## Sources

The **Sources** page lists every sender Vigil has heard from, with when it last sent, how much
per minute, and errors (a scrape target that doesn't answer shows its error there). Below it is
copy-paste setup for each kind; the address of Home Assistant is filled in.

- **MikroTik (RouterOS):** a remote logging action and the topics to send, pasted in a terminal
  (Winbox → New Terminal, or SSH). RouterOS' topics (`firewall,info`) are kept: the first one is
  the line's *app*, all of them are in the line's attributes. For the router's metrics (CPU,
  interfaces), run the [MKTXP](https://github.com/akpw/mktxp) exporter somewhere and add it as a
  scrape target; the built-in Network dashboard and the *Router CPU* rule template use its metric
  names (`mktxp_system_cpu_load`, `mktxp_interface_rx_byte_total`).
- **Netgear and other switches:** remote logging (syslog) to Home Assistant's address, port
  514, UDP, severity *Informational*.
- **Linux servers (rsyslog):** one line in `/etc/rsyslog.d/90-vigil.conf`:
  `*.* @@HOME_ASSISTANT_IP:514` (TCP; one `@` for UDP), then `systemctl restart rsyslog`. This
  covers sshd, sudo, the kernel and everything else in the system log.
- **Servers with Vector or the OpenTelemetry Collector:** journald, files (such as a web
  server's JSON access log) and host metrics, sent to port 4318. Vector: an `http` sink to
  `/ingest/logs` with newline-delimited JSON. OpenTelemetry Collector: an `otlphttp` exporter with
  `encoding: json` (Vigil doesn't take protobuf).
- **Prometheus exporters** (node_exporter, MKTXP, ..): add them to `scrape_targets`; Vigil
  fetches them at the interval. It also records `up` (1 or 0) and `scrape_duration_seconds` per
  target, so a rule can alert when an exporter stops answering.
- **FRITZ!Box:** set `fritzbox_host`, `fritzbox_user` and `fritzbox_password` (a FRITZ!Box user
  with the *FRITZ!Box settings* permission). Every 30 seconds: `fritz.wan_bytes_received_rate`,
  `fritz.wan_bytes_sent_rate`, `fritz.wan_link_up`, `fritz.uptime_seconds` and the line's maximum
  bit rates; every 5 minutes the new lines of its event log, as logs of host `fritz.box`.
- **Anything else:** `POST /ingest/logs` on port 4318 with one JSON object per line (or a JSON
  array): `ts` or `timestamp`, `host`, `app`, `severity` or `level`, `message` or `msg`; every
  other field becomes an attribute.

### Ingest port

Port 4318 takes data only with the token from `ingest_token`
(`Authorization: Bearer <token>`), or, while no token is set, only from private addresses (your
own network). Each sender can send at most 5,000 records a second; bodies are at most 5 MB
(gzip is fine). Syslog has no token: keep port 514 to your own network.

### Times

A syslog line in the classic format (RFC 3164, MikroTik) carries no time zone; Vigil reads it in
Home Assistant's time zone, which is how devices send it. RFC 5424, OpenTelemetry and JSON carry
their own. A line stamped more than 2 seconds in the future, or older than the retention, is
stored at the time it arrived, with its own time kept as the attribute `original_ts`.

## Pages

A time range at the top (15 min to 7 days, or *Custom…*) is shared by every page; dragging
across any time chart zooms in, and the browser's Back goes back out. The address in the
browser keeps the page, the search and the range, so a link opens the same view.

### Overview

Log lines, errors, warnings, hosts, metric series and rules firing; the log volume by severity
over time; the busiest hosts with their share of errors; the newest log patterns; the rules
firing now. **What changed?** (top right, on every page) explains the time range.

### Logs

The search, the matching lines by severity over time, and the lines, newest first (*Load older*
for more). Click a line for all its fields and attributes, *Lines around this one* and *Copy as
JSON*; click a host, app or pattern to filter on it (Alt-click to leave it out). **Live** follows
new lines every 2 seconds and pauses while you scroll. **Save** keeps a search; **Create alert**
turns it into a rule.

The search:

| You type | Finds |
|---|---|
| `failed password` | lines with both words |
| `"connection refused"` | the exact phrase |
| `-dhcp` | lines without the word |
| `host:mikrotik`, `app:sshd`, `source:syslog` | that host, app or source (`syslog`, `otlp`, `json`, `fritzbox`, `vigil`) |
| `host:sw-*` | a name starting with `sw-` |
| `severity:err` or `severity:3` | that severity |
| `severity:<=warning` | warning and worse (emerg 0, alert 1, crit 2, err 3, warning 4, notice 5, info 6, debug 7: a lower number is worse) |
| `pattern:12` | the lines of a pattern |
| `attrs.topics:firewall` | an attribute's value |

Everything is combined with AND. `sev:` and `level:` work like `severity:`, and `warn`,
`error`, `critical` and `fatal` like the syslog names.

### Patterns

Log lines grouped into templates: the parts that change from line to line (numbers, addresses,
names in quotes, ..) are shown as `<*>`. For the time range: how many lines, against how many
were expected from the 24 hours before; **NEW** for a template first seen in the range, **SPIKE**
for at least 20 lines and 5 times the expected number. A new kind of line, such as an attack
that starts or a device that fails in a new way, stands out here before anything else. Click a
pattern for its lines.

### Metrics

Every metric by name, with how many series, its unit and kind. Pick one, filter its labels,
choose the aggregation (average, minimum, maximum, sum, last, or *rate* for counters) and
optionally a label to group by. **Anomaly band** draws the range expected from the same hour on
the days before (with a week of data; before that, from the hour before) and marks the points
outside it. *Add query* puts more metrics on the same chart. **Add to dashboard** and **Alert on
this** take the chart along.

### What changed?

For the time range, compared with the same length just before it: patterns that are new or grew,
hosts that started, stopped or send much more or less, severities that went up, and metric
series whose average moved most. Ranked by how unusual the change is; each one links to the
lines or the chart.

### Dashboards

Panels on a grid of four columns: a chart of metrics, a number, bars (lines by host, app or
severity), a table of patterns, or a list of log lines for a search. **Edit** adds, changes,
moves, resizes and removes panels; **New** starts a dashboard. Two are built in: **Hosts** (log
volume and errors by host, the top patterns) and **Network** (FRITZ!Box throughput and line,
MikroTik interfaces and CPU, when those are collected).

### Alerts

Rules, with their state: *ok*, *pending* (the condition holds, waiting for *For at least*),
*firing*. Every 30 seconds each rule is evaluated:

| Kind | Value |
|---|---|
| Log search | the number of lines matching a search in the last window |
| Metric threshold | an aggregate of a metric over the last window |
| No data | seconds since a search last matched, or a metric last had a point |
| New log patterns | patterns first seen in the last window (optionally only for a search) |
| Anomaly | how far the latest value of a metric is outside its usual range (in standard deviations) |

With a condition (`>`, `>=`, `<`, `<=`, `==` a threshold). **Test** evaluates a rule once on the
data now, before saving. Templates: errors from any host, a host gone silent, an SSH login, new
log patterns, router CPU.

When a rule fires, and when it resolves: a line in the alert history, a Home Assistant event
`vigil_alert` (with the rule, state, value, severity and message, for your automations), a
persistent notification (removed again when it resolves) and, when set, the `notify_service`.
A rule with *Notify* off only sends the event.

### Sources and Info

**Sources**: see *Sources* above. **Info**: the database connection, the receivers, the
version, and what Vigil has stored.

## Storage

Vigil owns the `vigil` database in Chronicle and keeps everything in its schema `vigil`. With
TimescaleDB (always there in a Chronicle app database), logs and metrics are compressed after 2
days and removed after the retention from the options; without it, Vigil removes old rows every
hour. Chronicle's **Databases** page shows its size, and Chronicle's backups include it.

Received data is written in batches every second. While the database can't be reached, up to
50,000 records wait in memory and are written once it is back; beyond that, new records are
dropped and counted (the status bar shows *dropped*).

## Limits

- **Syslog over UDP** is lost when a sender sends faster than Vigil reads, for a few thousand
  lines in one burst. Use TCP where the sender has it (rsyslog: `@@`), or Vector.
- **OpenTelemetry** only in JSON encoding (`encoding: json`), not protobuf.
- **Metric names** in the Network dashboard and the router template are MKTXP's; with another
  exporter, change the panels or the rule.

## Security

- **The web UI** is reachable only through Home Assistant (ingress, administrators); direct
  connections to its port are refused, and changes must come from the page itself.
- **The ingest port** takes data only with the token, or without one only from private
  addresses; a limit per sender and a maximum size per request keep a flood from filling the
  database.
- **The database login** can only use Vigil's own database; it is no superuser and can't create
  roles or databases (Chronicle makes it so).
- **Passwords** (database, FRITZ!Box) are in the app's options only: the app runs as its own user
  with a private copy, and never logs or shows them.
- **Log lines are data:** everything is shown as text, never as HTML, so a line with markup in
  it can't run in your browser.

See [SECURITY.md](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-vigil/SECURITY.md)
for reporting a problem and the details.

## Development

`./lint.sh` checks the code (`./lint.sh fix` formats it). The tests: `pytest`; the database
tests run against a TimescaleDB when `VIGIL_TEST_DB` points at one:

```bash
docker run -d --rm --name vigil-test-db -p 127.0.0.1:55433:5432 -e POSTGRES_PASSWORD=test \
    timescale/timescaledb:2.32.0-pg18
VIGIL_TEST_DB=postgresql://postgres:test@127.0.0.1:55433/postgres pytest
```

`python3 tests/web_mock.py --port 18098` serves the web UI with made-up data, without a database.
DESIGN.md has the design, API_NOTES.md the API in detail.
