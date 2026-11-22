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
| `fritzbox` (*FRITZ!Box (TR-064)*): `host`, `user`, `password` | | The FRITZ!Box to collect from; an empty address = off |
| `notify_service` | | Where alerts go besides a Home Assistant notification: any action that takes a title and a message, such as `notify.mobile_app_phone` or `telegram_bot.send_message` |
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
copy-paste setup for each kind, with Home Assistant's address filled in. This section has the
full steps. Everywhere below, `HOME_ASSISTANT_IP` is your Home Assistant's address on your
network (such as `192.168.1.10`), and `<ingest_token>` the `ingest_token` from Vigil's options.

After setting up a sender, check **Sources**: it appears within a minute. If it doesn't, the
sender can't reach Home Assistant on that port (a firewall, a wrong address, or the port cleared
in Vigil's Network settings).

### MikroTik (RouterOS 7)

**Logs.** In a terminal (Winbox → *New Terminal*, or SSH):

```
/system logging action add name=vigil target=remote remote=HOME_ASSISTANT_IP remote-port=514 \
    remote-log-format=syslog syslog-facility=local0
/system logging add action=vigil topics=info
/system logging add action=vigil topics=warning
/system logging add action=vigil topics=error
/system logging add action=vigil topics=critical
```

In Winbox instead: **System → Logging → Actions → +**: Name `vigil`, Type *remote*, Remote
Address, Remote Port `514`, Remote Log Format *syslog* (on RouterOS before 7.18: tick *BSD
Syslog*). Then **Rules → +** for each topic (info, warning, error, critical) with Action
`vigil`. Vigil shows RouterOS' first topic as the line's *app* (`firewall`, `dhcp`, `system`,
`interface`), so `app:dhcp` finds the DHCP lines.

**Firewall lines.** RouterOS only logs firewall rules that have *log* on. For the drop rules
you want to see: `/ip firewall filter set [find comment="drop all not coming from LAN"] log=yes
log-prefix="drop"` (or Winbox: open the rule → *Action* → tick *Log*). Logging every accepted
connection is a lot of lines: log the drops.

**Metrics** (CPU, memory, interfaces, Wi-Fi clients) come from the
[MKTXP](https://github.com/akpw/mktxp) exporter, on any machine that can reach the router:

1. On the router, a read-only user for it, and the API only from that machine:
   ```
   /user group add name=mktxp policy=api,read
   /user add name=mktxp group=mktxp password=<a password>
   /ip service set api address=<machine running MKTXP>/32 disabled=no
   ```
2. Run MKTXP (Docker: `ghcr.io/akpw/mktxp`), with the router's address, the user and password
   in `mktxp.conf`. It serves its metrics on port 49090.
3. In Vigil's options, a scrape target: `name: mikrotik`, `url: http://<that machine>:49090/metrics`,
   `interval: 30`.

The built-in Network dashboard and the *Router CPU* rule template use MKTXP's names
(`mktxp_system_cpu_load`, `mktxp_interface_rx_byte_total`).

### Netgear and other switches

Managed Netgear switches (Smart Managed Plus/Pro, M4250, ..) send syslog; the menu differs per
model and firmware:

- **Smart Managed Pro** (GS7xx, GS108T, ..): **System → Logs → Server Log**: Admin Mode *Enable*,
  then **Add**: Host Address `HOME_ASSISTANT_IP`, Port `514`, Severity Filter *Info*.
- **Insight/M4250 and newer**: **System → Logs → Remote Log** (or *Syslog*), the same values.
- **Netgear routers** (Nighthawk, Orbi): most consumer models can't send syslog; their logs can
  only be e-mailed.

Other brands are the same idea: look for *Remote syslog*, *Syslog server* or *Log server*, UDP,
port 514, level *Informational*. Give each switch a clear system name (it becomes the *host*),
such as `sw-office`, so `host:sw-*` finds them all.

### FRITZ!Box

Vigil asks the FRITZ!Box itself (TR-064); nothing changes on the box except a user:

1. **Home Network → Network → Network Settings**: tick **Allow access for applications**
   (Heimnetz → Netzwerk → Netzwerkeinstellungen → *Zugriff für Anwendungen zulassen*).
2. **System → FRITZ!Box Users → Add User**: a user name such as `vigil`, a password, and only the
   right **FRITZ!Box Settings**; no access from the internet.
3. In Vigil's options, the section **FRITZ!Box (TR-064)**: the box's address (such as
   `192.168.178.1`), that user and its password. Restart Vigil.

Every 30 seconds: `fritz.wan_bytes_received_rate`, `fritz.wan_bytes_sent_rate` (bytes per
second), `fritz.wan_link_up` (1 or 0), `fritz.uptime_seconds`, `fritz.connection_uptime_seconds`
and the line's maximum (`fritz.max_bitrate_down/up`, bits per second). Every 5 minutes the new
lines of the box's event log, as logs of host `fritz.box` (source `fritzbox`).

### Linux servers: rsyslog

rsyslog is on most Debian/Ubuntu servers (and Raspberry Pi OS, Proxmox VE with
`apt install rsyslog`). It forwards everything in the system log: sshd, sudo, the kernel, cron
and every systemd service.

1. Create `/etc/rsyslog.d/90-vigil.conf`:
   ```
   # Everything to Vigil over TCP, in RFC 5424 (with the year and time zone),
   # queued on the server while Vigil can't be reached
   *.* action(type="omfwd" target="HOME_ASSISTANT_IP" port="514" protocol="tcp"
              template="RSYSLOG_SyslogProtocol23Format"
              queue.type="linkedList" queue.size="10000" action.resumeRetryCount="-1")
   ```
2. `sudo systemctl restart rsyslog`
3. Test: `logger -t vigil-test "hello Vigil"`, then search `app:vigil-test` in Vigil.

TCP and the queue mean no lines are lost when Vigil restarts. Only some facilities:
`auth,authpriv.* action(...)` instead of `*.*`.

### Linux servers: syslog-ng

In `/etc/syslog-ng/conf.d/vigil.conf`:

```
destination d_vigil { syslog("HOME_ASSISTANT_IP" transport("tcp") port(514)); };
log { source(s_src); destination(d_vigil); };
```

then `sudo systemctl restart syslog-ng`. (`syslog()` sends RFC 5424 with octet counting, which
Vigil reads.) `s_src` is the system source in Debian's syslog-ng; check `syslog-ng.conf` for
yours.

### Vector (logs from journald, Docker and files; host metrics)

[Vector](https://vector.dev) is one small program that collects from journald, Docker containers
and log files, and sends them to Vigil as JSON lines. Install it from its repository
(`bash -c "$(curl -L https://setup.vector.dev)"` then `sudo apt install vector`), and put this in
`/etc/vector/vector.yaml`:

```yaml
sources:
  journald:
    type: journald                       # sshd, sudo, kernel, every systemd service
  docker:
    type: docker_logs                    # every container (the vector user needs the docker group)
  host:
    type: host_metrics                   # CPU, memory, disks, network
    scrape_interval_secs: 30

transforms:
  to_vigil:
    type: remap
    inputs: [journald, docker]
    source: |
      app_name = .SYSLOG_IDENTIFIER || .container_name || ._SYSTEMD_UNIT || "vector"
      level = to_int(.PRIORITY) ?? 6
      if .stream == "stderr" { level = 4 }     # a container's error output
      . = {"timestamp": .timestamp, "host": .host, "app": app_name,
           "severity": level, "message": .message}

sinks:
  vigil:
    type: http
    inputs: [to_vigil]
    uri: http://HOME_ASSISTANT_IP:4318/ingest/logs
    encoding: { codec: json }
    framing: { method: newline_delimited }
    request:
      headers: { Authorization: "Bearer <ingest_token>" }   # leave out when no token is set
  metrics:
    type: prometheus_exporter            # Vigil scrapes this, see below
    inputs: [host]
    address: 0.0.0.0:9598
```

`sudo systemctl restart vector`, and `sudo usermod -aG docker vector` for the Docker logs. For
the host metrics, add a scrape target in Vigil's options: `url: http://<server>:9598/metrics`.
A log file: a `file` source (`include: [/var/log/myapp/*.log]`) added to the transform's inputs;
a JSON log file (a web server's access log): add `codec: json` to it, and its fields become
attributes you can search with `attrs.<field>:value`.

### OpenTelemetry Collector

The [OpenTelemetry Collector](https://opentelemetry.io/docs/collector/) (the *contrib*
distribution, `otelcol-contrib`) sends logs and metrics to Vigil over OTLP. Vigil takes **JSON**
encoding only. In its `config.yaml`:

```yaml
receivers:
  journald: {}
  filelog:
    include: [/var/log/myapp/*.log]
  hostmetrics:
    collection_interval: 30s
    scrapers: { cpu: {}, memory: {}, load: {}, filesystem: {}, network: {} }

processors:
  batch: {}
  resourcedetection:
    detectors: [system]                  # host.name, which Vigil shows as the host

exporters:
  otlphttp/vigil:
    endpoint: http://HOME_ASSISTANT_IP:4318
    encoding: json
    headers: { Authorization: "Bearer <ingest_token>" }

service:
  pipelines:
    logs:    { receivers: [journald, filelog], processors: [resourcedetection, batch], exporters: [otlphttp/vigil] }
    metrics: { receivers: [hostmetrics],       processors: [resourcedetection, batch], exporters: [otlphttp/vigil] }
```

Any program with OpenTelemetry built in can send straight to Vigil the same way: endpoint
`http://HOME_ASSISTANT_IP:4318` with the JSON encoding (environment variables
`OTEL_EXPORTER_OTLP_ENDPOINT=http://HOME_ASSISTANT_IP:4318` and
`OTEL_EXPORTER_OTLP_PROTOCOL=http/json`).

### Prometheus exporters (node_exporter and others)

Vigil fetches exporters itself; add each one to `scrape_targets` in Vigil's options:

```yaml
scrape_targets:
  - name: nas
    url: http://192.168.1.20:9100/metrics
    interval: 60
```

- **node_exporter** (a Linux server's CPU, memory, disks, network): `sudo apt install
  prometheus-node-exporter` on Debian/Ubuntu; it listens on port 9100. Allow only Home
  Assistant to reach it (`sudo ufw allow from HOME_ASSISTANT_IP to any port 9100`).
- Anything with a Prometheus `/metrics` page works the same: MKTXP (MikroTik), Proxmox
  (`prometheus-pve-exporter`), a UPS (`nut_exporter`), Docker (cAdvisor), Caddy (its admin
  `/metrics`), Pi-hole, ..

For each target Vigil also records `up` (1 when it answered, 0 when not) and
`scrape_duration_seconds`, labelled with `job` (the name) and `instance`: a rule *metric
`up` < 1* tells you when a server or exporter is down.

### Anything else: JSON lines

`POST http://HOME_ASSISTANT_IP:4318/ingest/logs` with one JSON object per line (or a JSON
array). Vigil reads `ts`, `timestamp`, `time` or `@timestamp` (ISO 8601 or epoch seconds),
`host` or `hostname`, `app`, `service` or `appname`, `severity`, `level` or `lvl` (a syslog
name or number, `warn`, `error`, ..), and `message`, `msg` or `log`; every other field becomes
an attribute. From a script:

```
curl -X POST http://HOME_ASSISTANT_IP:4318/ingest/logs \
    -H 'Authorization: Bearer <ingest_token>' \
    --data-binary '{"host": "nas", "app": "backup", "level": "error", "message": "Backup failed", "job": "nightly"}'
```

The answer says how many lines were accepted and rejected.

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
