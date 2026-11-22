# Vigil - design (v0.1)

Vigil is a Home Assistant app that collects **logs and metrics** from the home network and
servers (syslog from MikroTik/Netgear/servers, OpenTelemetry/JSON from Vector or the OTel
Collector, Prometheus exporters, the FRITZ!Box over TR-064), stores them in **its own database on
the Chronicle app** (PostgreSQL + TimescaleDB), and turns them into searchable events, charts,
dashboards and alerts - with "smart" views SigNoz doesn't have out of the box: **log patterns**
(new / spiking message templates), **anomaly bands** on metrics, and **"what changed?"**
correlation for any time window.

Same design and code layout as the Chronicle app (`../app-chronicle`): Python 3 + aiohttp +
asyncpg backend in `src/`, a single-page UI in `src/web/` (plain scripts sharing one global
scope, uPlot charts, IBM Plex fonts, dark by default and following Home Assistant's theme),
served only through Home Assistant ingress. Read Chronicle's `src/webui.py`, `src/config.py`,
`src/__main__.py`, `src/web/index.html`, `core.js`, `charts.js`, `app.css` before writing code,
and follow them.

## Processes and ports (one Python process, asyncio)

| Part | Listens | Notes |
|---|---|---|
| Web UI + JSON API | 8099 (ingress only) | `X-Ingress-Path`; only 172.30.32.2 may connect when `SUPERVISOR_TOKEN` is set; POSTs need header `X-Vigil: 1` |
| Syslog receiver | 5514/udp, 5514/tcp (mapped to 514 on the host) | RFC 3164 and RFC 5424, octet-counted and newline-framed TCP |
| OTLP/HTTP + JSON ingest | 4318/tcp | `POST /v1/logs`, `POST /v1/metrics` (OTLP **JSON** encoding), `POST /ingest/logs` (NDJSON lines). `Authorization: Bearer <ingest_token>` required when `ingest_token` is set; refuse all when unset except from private networks (RFC1918, ULA) |
| Prometheus scraper | - | every target in `scrape_targets` at its `interval` (default 60 s) |
| FRITZ!Box poller | - | every 30 s when `fritzbox.host` is set |
| Rule engine | - | every 30 s |
| Pattern miner / housekeeping | - | in the write path + every 5 min |

Writes are **batched**: receivers put records on an `asyncio.Queue` (bounded, 50 000; when full,
drop and count `dropped`), a writer flushes every 1 s or 2 000 records with `COPY`
(`copy_records_to_table`). Never one INSERT per log line.

## Options (`/data/options.json`, see config.yaml)

`database_host` (empty = find the Chronicle app through the Supervisor API: `GET
http://supervisor/addons`, the installed add-on whose slug ends in `app-chronicle`, then `GET
/addons/<slug>/info` -> `hostname`), `database_port`, `database_name` (default `vigil`),
`database_user` (default `vigil`), `database_password`, `ingest_token`, `scrape_targets`
(`[{name, url, interval}]`), `fritzbox: {host, user, password}`, `notify_service` (e.g.
`notify.mobile_app_phone`; empty = persistent notification only), `logs_retention_days` (14),
`metrics_retention_days` (90), `log_level`. Development: `VIGIL_*` environment variables override
(`VIGIL_DB_HOST`, `VIGIL_DB_PORT`, `VIGIL_DB_NAME`, `VIGIL_DB_USER`, `VIGIL_DB_PASSWORD`,
`VIGIL_DATA_DIR`, `VIGIL_PORT`, `VIGIL_INGEST_PORT`, `VIGIL_SYSLOG_PORT`, `VIGIL_OPTIONS`).

The database and its login are made in **Chronicle → Maintenance → App databases** (Chronicle
0.3.0): a database owned by a login of its own, with TimescaleDB in it. Vigil owns everything in
it and creates/migrates its schema on start (table `vigil.meta`, key `schema_version`).

## Database (schema `vigil`)

TimescaleDB when `SELECT extname FROM pg_extension` has it: hypertables, compression after 2 days,
retention policies from the options. Without it: plain tables, and housekeeping deletes rows older
than the retention every hour. All times `timestamptz`, UTC.

```sql
vigil.meta(key text primary key, value text)
vigil.sources(id serial pk, kind text, name text, host text, first_seen, last_seen,
              received bigint, errors bigint, last_error text, unique(kind, name))
vigil.logs(ts timestamptz not null, received timestamptz not null default now(),
           source text not null,     -- 'syslog' | 'otlp' | 'json' | 'fritzbox' | 'vigil'
           host text, app text,      -- syslog HOSTNAME / APP-NAME, OTLP resource host.name / service.name
           severity smallint not null,  -- syslog 0 emerg .. 7 debug (OTLP SeverityNumber mapped)
           facility smallint, message text not null, attrs jsonb not null default '{}',
           pattern bigint,           -- vigil.patterns.id
           search tsvector generated always as (to_tsvector('simple', coalesce(host,'')||' '||coalesce(app,'')||' '||message)) stored)
  -- hypertable on ts (1 day chunks); indexes: (ts desc), (host, ts desc), (severity, ts desc), (pattern, ts desc), gin(search)
vigil.patterns(id bigserial pk, template text not null unique, first_seen, last_seen,
               count bigint, sample text, host text, app text)
vigil.series(id serial pk, name text not null, labels jsonb not null, unit text, kind text,
             unique(name, labels))
vigil.points(ts timestamptz not null, series int not null, value double precision not null)
  -- hypertable on ts; index (series, ts desc)
vigil.rules(id serial pk, name text, kind text, query jsonb, condition jsonb, for_seconds int,
            severity text, enabled bool, notify bool, state text, state_since timestamptz,
            last_value double precision, last_eval timestamptz, created, updated)
vigil.alerts(id bigserial pk, rule int references vigil.rules on delete cascade, ts timestamptz,
             state text,            -- 'firing' | 'resolved'
             value double precision, message text)
vigil.dashboards(id serial pk, name text unique, panels jsonb, created, updated)
vigil.saved_searches(id serial pk, name text unique, query text, created)
```

Severity numbers: 0 emerg, 1 alert, 2 crit, 3 err, 4 warning, 5 notice, 6 info, 7 debug. OTLP
SeverityNumber 1-4 trace/5-8 debug -> 7, 9-12 info -> 6, 13-16 warn -> 4, 17-20 error -> 3,
21-24 fatal -> 2.

## Smart features

- **Log patterns** (Drain-like, in `src/patterns.py`): tokenise the message, mask variable tokens
  (numbers, hex, IPv4/IPv6, MAC, UUID, e-mail, quoted strings, paths with digits, key=value values)
  as `<*>`, then merge templates of the same length and first tokens that differ in <= 30 % of
  tokens. Cache template -> id in memory (bounded LRU 20 000), upsert counts in batches.
  "New" = first_seen in the window; "spiking" = count in the window vs the same window length in
  the preceding 24 h, ratio >= 5 with >= 20 hits.
- **Anomaly band** for a metric series (`src/anomaly.py`): per time bucket, mean and standard
  deviation of the same bucket over the previous 7 days (hour-of-day seasonality when there is a
  week of data, else a rolling 1 h window); band = mean +/- 3 sigma; points outside are anomalies.
- **What changed?** (`GET /api/changes?from=&to=`): compare the window with the window of the same
  length just before it: per host and per severity log counts, top patterns that grew/appeared,
  metric series whose mean moved most in sigma; ranked list with a one-line explanation each.

## Query language for logs (`src/query.py`)

Free text terms (AND), `"exact phrase"`, `-term` (NOT), `field:value` for `host`, `app`, `source`,
`severity` (name or number, compared on the syslog number: `severity:<=warning` is warning and worse, `severity:<=3` err and worse), `pattern:<id>`, `attrs.<key>:value`,
`*` wildcard at the end of a value (`host:sw-*`). Parsed into a parameterised SQL WHERE
(never string-concatenated values). Text terms use `search @@ plainto_tsquery('simple', $n)` plus
`message ILIKE` for terms with characters tsvector drops.

## Web API (JSON, under the ingress base path)

Times are epoch seconds (float) in and out. Errors: `{"error": "..."}` with 4xx/5xx.

| Method & path | Input | Output |
|---|---|---|
| GET `/api/status` | - | `{version, connected, database:{host,name,user,timescaledb,version}, error?, counts:{logs_24h, series, rules, firing}, receivers:{syslog_udp, syslog_tcp, otlp: bool/port}, queue:{size, dropped}}` |
| GET `/api/overview?hours=24` | 1, 6, 24, 168 | `{tiles:{logs, errors, warnings, hosts, series, firing}, volume:{t:[..], by_severity:{err:[..], warning:[..], info:[..], debug:[..]}}, top_hosts:[{host, count, errors}], new_patterns:[{id, template, count, first_seen}], firing:[alert rows]}` |
| GET `/api/logs?q=&from=&to=&limit=200&before=` | query string, window, page by `before` (ts) | `{rows:[{ts, host, app, source, severity, facility, message, attrs, pattern}], more: bool, took_ms}` |
| GET `/api/logs/histogram?q=&from=&to=&buckets=60` | | `{t:[..], by_severity:{...}, total}` |
| GET `/api/logs/tail?q=&after=` | ts | rows newer than `after` (max 200), for 2 s polling |
| GET `/api/logs/fields?from=&to=` | | `{hosts:[{value,count}], apps:[..], sources:[..], severities:[..]}` (top 50 each) |
| GET `/api/patterns?from=&to=&sort=count|new|spike&q=` | | `{patterns:[{id, template, count, prev_count, first_seen, last_seen, sample, host, app, spark:[..24 counts]}]}` |
| GET `/api/metrics/names?q=` | | `{names:[{name, series, unit, kind}]}` |
| GET `/api/metrics/labels?name=` | | `{labels:{key:[values..]}}` |
| GET `/api/metrics/query?name=&filter=&agg=avg|min|max|sum|rate|last&group=&from=&to=&step=&anomaly=0|1` | `filter` = JSON object of label equality, `group` = label key | `{t:[..], series:[{label, values:[..], band?:{lo:[..],hi:[..]}, anomalies?:[idx..]}], unit, step}` |
| GET `/api/changes?from=&to=` | | `{items:[{kind:'host'|'severity'|'pattern'|'metric', title, detail, score, link}]}` |
| GET `/api/sources` | | `{sources:[{kind, name, host, first_seen, last_seen, received, errors, last_error, rate_per_min}], setup:{syslog_port, otlp_port, token_set, examples:{rsyslog, vector, otelcol, mikrotik, netgear, node_exporter}}}` |
| GET `/api/rules` / POST `/api/rules/save` / POST `/api/rules/delete` / POST `/api/rules/test` | rule JSON | rules with state; `test` evaluates once without saving: `{value, would_fire, detail}` |
| GET `/api/alerts?from=&to=` | | `{alerts:[{id, rule, rule_name, ts, state, value, message, severity}]}` |
| GET `/api/dashboards` / POST `/api/dashboards/save` / POST `/api/dashboards/delete` | | dashboards with panels |
| GET `/api/searches` / POST `/api/searches/save` / POST `/api/searches/delete` | | saved log searches |
| GET `/api/donate`... | | Chronicle's donate prompt (copy `src/donate.py` as is) |

### Rules

`kind`: `log_count` (query = `{q}`; value = matching logs in the last `window` s),
`metric` (query = `{name, filter, agg, window}`; value = aggregate), `absence` (query = log `{q}` or
metric `{name, filter}`; value = seconds since the last data), `pattern_new` (value = number of new
patterns in the window, optional `{q}` to restrict), `anomaly` (metric query; value = |z| of the last
point). `condition = {op: '>'|'>='|'<'|'<='|'==', threshold}`. States: `ok` -> `pending` (condition
true, waiting `for_seconds`) -> `firing` -> `ok` (resolved). On firing/resolved: a row in
`vigil.alerts`, an HA event `vigil_alert` (`{rule, name, state, value, severity, message}`), a
persistent notification (id `vigil_rule_<id>`, dismissed on resolve), and `notify_service` if set.
Built-in rule templates the UI offers: "Errors from any host > 20 in 5 min", "Host silent for
10 min", "SSH login (`app:sshd Accepted`)", "New log patterns", "Router CPU > 85 %".

### Dashboards

`panels = [{id, title, type: 'timeseries'|'stat'|'logs'|'bar'|'table', query: {...as the APIs
above...}, w: 1..4, h: 1..3}]` on a 4-column grid. Two built-in dashboards are created on first
start: **Hosts** (log volume by host, errors by host, top patterns) and **Network** (FRITZ!Box
up/down throughput, WAN state, MikroTik interface traffic when present).

## Collectors

- **Syslog** (`src/syslog.py`): RFC 5424 (`<PRI>1 TS HOST APP PROCID MSGID [SD] MSG`), RFC 3164
  (`<PRI>Mmm dd hh:mm:ss HOST TAG[pid]: MSG`, also without HOST), MikroTik (`topics,topics msg`
  -> app = first topic), messages without PRI (severity 5). Host falls back to the sender IP.
  Remember each sender in `vigil.sources` (kind `syslog`, name = host).
- **OTLP JSON** (`src/otlp.py`): logs (`resourceLogs[].scopeLogs[].logRecords[]`, body string or
  kvlist, attributes -> attrs, `host.name`/`service.name` from resource), metrics (gauge, sum ->
  points; histograms -> `_count`, `_sum` and quantile series). Labels = attributes + resource
  `host.name`/`service.name`.
- **JSON lines** (`POST /ingest/logs`): `{"ts"|"timestamp", "host", "app", "severity"|"level",
  "message"|"msg", ...rest -> attrs}` one object per line; also accepts a JSON array.
- **Prometheus scraper** (`src/scrape.py`): text exposition format 0.0.4 (comments, `# TYPE`,
  labels with escapes, `+Inf`, NaN skipped), counter/gauge/untyped -> points, summary/histogram
  -> `_sum`, `_count` (+ quantiles; buckets skipped). Label `job` = target name, `instance` = host:port.
  Source row kind `scrape`.
- **FRITZ!Box** (`src/fritzbox.py`, `fritzconnection` in a thread): every 30 s
  `fritz.wan_bytes_received_rate`, `fritz.wan_bytes_sent_rate`, `fritz.wan_link_up`,
  `fritz.uptime_seconds`, `fritz.max_bitrate_down/up`; every 5 min the device log
  (`DeviceInfo:GetDeviceLog`) -> new lines as logs with source `fritzbox`, host `fritz.box`.
- **Self metrics**: `vigil.queue_size`, `vigil.dropped_total`, `vigil.write_seconds` every 30 s.

## UI pages (tabs, like Chronicle)

Overview · Logs · Patterns · Metrics · Dashboards · Alerts · Sources · Info. A global time range
picker (15 min, 1 h, 6 h, 24 h, 7 d, custom; zoom on any time chart narrows it) shared by the pages,
in the URL hash (`#logs/<q>/<from>/<to>`). Logs: query bar with field suggestions, histogram
(stacked by severity, drag to zoom), table (time, host, app, severity pill, message with the
pattern link), row expands to all fields/attrs, "live" toggle (2 s tail), "save search", "create
alert from this search". Patterns: sortable list with sparkline, new/spiking badges, click -> Logs
filtered `pattern:<id>`. Metrics: metric picker with search, label filters, aggregation, group-by,
anomaly band toggle, multiple queries overlaid, "add to dashboard", "alert on this". Dashboards:
grid, edit mode (add/edit/remove/resize panels), built-ins. Alerts: rules table with live state,
rule editor with **Test**, alert history timeline. Sources: every receiver/target with last seen,
rate, errors, and copy-paste setup snippets (MikroTik `/system logging action add ...`, Netgear
syslog, rsyslog `*.* @@host:514`, Vector config, OTel Collector exporter, node_exporter as a
scrape target, the FRITZ!Box options). Info: connection, version, about, donate (as Chronicle).
"What changed?" is a button on Overview, Logs and Metrics: it opens a panel for the current window.

## Security

Ingress only for the UI (Chronicle's `_ingress_only` and `_same_page` middlewares, header
`X-Vigil`). Ingest port: token or private source addresses only; body size cap 5 MB; per-source
rate cap 5 000 records/s (excess dropped and counted). All SQL parameterised; identifiers from a
fixed allowlist. Passwords from options are never logged or returned by the API. Log messages are
escaped in the UI (`escapeHtml`), never rendered as HTML.

## Tests (`tests/`, pytest, `asyncio_mode = auto`)

Pure-Python tests that need no database: syslog parsing (3164, 5424, MikroTik, no-PRI), OTLP
JSON parsing, Prometheus text parsing, query-language -> SQL, pattern masking/merging, anomaly
band maths, rule state machine. DB tests (skipped unless `VIGIL_TEST_DB` is set, a TimescaleDB):
schema create/migrate, write + search round trip, histogram, metrics query with rate.
