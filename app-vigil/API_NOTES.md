# Vigil API notes (backend vs. DESIGN.md)

Where the backend adds to, or settles something DESIGN.md leaves open. Everything else follows
the table in DESIGN.md. Times are epoch seconds (floats) in and out; errors are
`{"error": "..."}` with a 4xx/5xx status (400 bad parameter or search, 404 unknown id, 409 name
taken, 503 database not connected, 504 query too slow).

## Settled choices

- **Severity comparisons are on the syslog number**: `severity:<=err` = `severity:<=3` = err and
  worse (0..3); `severity:>=info` = info and debug. A name is just its number (`warn`, `error`,
  `critical`, `fatal`, `trace` are accepted too, also as `sev:` and `level:`). So DESIGN.md's
  example `severity:>=warning` means warning, notice, info and debug; "warning or worse" is
  `severity:<=warning`. (Same as the front end and tests/web_mock.py.)
- **Default windows** when `from`/`to` are missing: the last hour (logs, histogram, fields,
  metrics, changes), the last 24 h (patterns), the last 7 days (alerts). Spans are limited to
  400 days (changes: 7 days).
- **Clock skew**: a line or point stamped more than 2 s in the future, or older than the
  retention, is stored at the time it arrived; a log line keeps its own time in
  `attrs.original_ts`.
- **Log row `ts`** has microseconds (6 decimals) so `before=<last row's ts>` pages exactly.
- **`/api/metrics/query` `step`** is a minimum: the server uses at least a "nice" step giving
  about 300 points (and at most 2000 buckets). The answer's `step` is the one used; `t[i]` is
  the start of bucket i, aligned to multiples of `step`; `values` has `null` for empty buckets.
  Without `group` there is one series, labelled with the metric name; with `group` one per
  label value (`"(none)"` when a series lacks the label), at most 30.
- **Panel `query` format** (built-in dashboards and anything saved): the Dashboards page's own
  `{source: "logs"|"fields"|"patterns", q, field, sort}`, or a metric query
  `{name, filter, agg, group, anomaly}`, or `{queries: [metric query, ...]}` for overlays. The
  server stores any JSON object here; it only checks `type`, and clamps `w` 1..4 and `h` 1..3.

## Extra fields and parameters

- `GET /api/status`: `database.port`; `queue.max`, `queue.rate_limited` (records over the
  5 000/s per-sender cap; `queue.dropped` already includes them); `writer: {logs, points,
  last_write, last_write_seconds, error}` (totals since start). When not connected,
  `connected: false`, `error` says why and every `counts` value is `null`.
  `receivers.*` is the port number when listening, else `false`.
- `GET /api/logs/histogram`: optional `group=host|app|source` adds
  `groups: {value: [counts per bucket]}` (top 10, the rest as `"(other)"`), for "volume by host".
  `buckets` is exact (1..500, default 60): `t[i] = from + i * (to - from) / buckets`.
- `GET /api/logs/tail`: `{rows, more, took_ms}` like `/api/logs`, newest first, at most 200,
  rows with `ts > after` (default: the last minute).
- `GET /api/logs/fields`: optional `q` (only matching lines are counted) and
  `field=hosts|apps|sources|severities` (only that list). `severities` items are
  `{value: "err", number: 3, count}`.
- `GET /api/patterns`: each pattern also has `new` (first seen in the window) and `spike`
  (>= 20 hits and >= 5x `prev_count`). `prev_count` is the expected count for the window from
  the preceding 24 h (or the preceding window, when longer): `count_in_baseline * span / baseline`.
  `spark` is 24 counts over the window. `q` filters the counted lines. At most 200.
- `GET /api/changes`: `link` is a page hash: `#logs/<url-encoded q>/<from>/<to>` or
  `#metrics/<url-encoded JSON [{name, filter}]>/<from>/<to>`.
- `GET /api/sources`: `setup.host_placeholder` is `"HOME_ASSISTANT_IP"`, the text the snippets
  use for the address of Home Assistant (the server can't know the LAN address; the page may
  replace it, e.g. with `location.hostname`). Snippets never contain the token itself
  (`<ingest_token>`). Extra snippet `examples.json_lines` (a curl line). `syslog_port` /
  `otlp_port` are the host ports from the app's network mapping (514 / 4318 by default).
  `rate_per_min` is the average of the last 5 full minutes; scrape sources count samples.
- `GET /api/rules`: `{rules: [...], templates: [...]}`; each rule has `id, name, kind, query,
  condition, for_seconds, severity, enabled, notify, state, state_since, last_value, last_eval,
  created, updated` and `error` (the last evaluation's error, or null). `templates` are the
  five built-in rule templates as rule JSON (without id).
- `POST /api/rules/save` (with `id` updates): `{ok, id, rule}`. A changed kind, query or
  condition, or disabling, puts the rule back to `ok`. `severity` is `info|warning|critical`.
  `query` per kind: `log_count {q, window}`, `metric {name, filter, agg, window}`,
  `absence {q}` or `{name, filter}`, `pattern_new {window, q?}`, `anomaly {name, filter, agg, step}`
  (`window`/`step` default 300 s). `notify: false` sends only the `vigil_alert` event (no
  persistent notification, no notify action).
- `POST /api/rules/test`: same body (no id needed): `{value, would_fire, detail}`;
  `value` is `null` when there is no data.
- `POST /api/rules/delete`, `/api/dashboards/delete`, `/api/searches/delete`: body `{id}`,
  answer `{ok: true}` (404 when unknown).
- `POST /api/dashboards/save`: `{id?, name, panels}` -> `{ok, id}`; `GET /api/dashboards`
  items have `id, name, panels, builtin, created, updated`.
- `POST /api/searches/save`: `{id?, name, query}` -> `{ok, id}`; saving an existing name
  without an id replaces its query. `GET /api/searches`: `{searches: [{id, name, query, created}]}`.
- `GET /api/alerts`: rows also for deleted rules are gone (alerts cascade with their rule).

## Schema additions

- `vigil.series.created` (timestamptz): housekeeping deletes series without points in the
  metrics retention only when they are older than a day (so a series is never deleted while its
  first points are still queued).

## Ingest port (4318)

- `POST /v1/logs`, `/v1/metrics`: `{}` on success, or OTLP `partialSuccess` with the number
  rejected by the rate cap. Protobuf bodies get 415 (use `encoding: json`). gzip bodies are fine.
- `POST /ingest/logs`: `{accepted, rejected}`.
- `GET /health`: `{ok: true}` (no token needed).
