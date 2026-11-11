# Home Assistant App: Chronicle

This app shows the long-term history that the
[Chronicle integration](https://github.com/cyberjunky/home-assistant-chronicle) records in
TimescaleDB. It connects to the same database, reads the tables in the `chronicle` schema and
shows them through ingress, from the sidebar. It only reads: nothing it does can change or
delete the history.

After installing, enter the database on the app's Configuration tab, start it and open
**Chronicle** from the sidebar. When the integration hasn't created its tables in that
database yet, every page says so.

## Options

| Option | Default | |
|---|---|---|
| `host` | `1b41fb55-timescaledb` | The TimescaleDB app from this repository; or a host name or IP address |
| `port` | `5432` | |
| `username` | `postgres` | |
| `password` | | |
| `database` | `homeassistant` | The database the Chronicle integration records into |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

Every connection the app opens is read-only (`default_transaction_read_only`), and every
query stops after 30 seconds.

### A read-only user

The app works with the `postgres` user, but it doesn't need more than reading. A user that can
only read is the safer choice, also for the Query page. In pgAdmin 4 (or `psql`), connected to
the database as `postgres`:

```sql
CREATE ROLE chronicle_reader LOGIN PASSWORD 'choose-a-password';
GRANT pg_read_all_data TO chronicle_reader;
GRANT CONNECT ON DATABASE homeassistant TO chronicle_reader;
```

Then enter `chronicle_reader` and its password on the app's Configuration tab.
`pg_read_all_data` reads every table in every database and can't change anything.

## Pages

### Overview

- **Tiles:** the number of entities and the rows of the last 24 hours, the rows in the history
  (TimescaleDB's estimate, which costs nothing to ask), the size of the history with its
  chunks, the compression ratio (size before and after, of the compressed chunks), the date of
  the oldest history and the size of the whole database.
- **Rows written** per hour, over 24 hours or 7 days.
- **Most active:** the 15 entities that wrote the most rows in the last 24 hours, with the rows
  per hour. A click opens the entity. Busy entities that you don't need are the first to
  exclude in the integration's options.
- **Domains:** entities and rows per domain. A click shows the domain's entities.

The page refreshes every minute while it is open.

### Entities

Every entity in the history, with its last state, when that was, its rows in the last 24
hours and since when it has history. Search on name or entity ID, filter on domain, and sort
on any column. A click opens the entity.

An entity shows its history over **24 hours, 7 days, 30 days, 1 year** or **All**. A range
never starts before the entity's first record: with an hour of history, every range shows that
hour. The link in the address bar opens the same entity and range again.

**Numbers** are drawn as a line. What the chart shows follows the time it spans, not the
range's name:

| Span | Data |
|---|---|
| Up to 2 days | Every value; averaged per few minutes when there are more than 3,000 |
| Up to 3 weeks | Averaged per few minutes (about 1,000 points) from the recorded values |
| Up to 4 months | The integration's hourly averages (`states_hourly`) |
| Longer | The integration's daily averages (`states_daily`, in Home Assistant's time zone) |

Averages are drawn as their mean with the minimum–maximum band around it. When the averages are
switched off in the integration, the app averages the values itself, by the hour or by the day.
Measurements are lines, counters (`total`, `total_increasing`) steps. Where the entity was
*unavailable* or *unknown* the line has a gap. The value an entity has when the chart starts and
still has at the end is drawn to the chart's edges, so a sensor that changes once an hour still
shows a line. The tiles give the minimum, average and maximum of the period, and the number of
values. Below the chart are the values or averages it shows, the newest first.

**Compare** adds up to four more numeric entities to the chart, each in its own colour. Entities
with another unit get a second y axis on the right; a chart has two at most.

**Other states** (on/off, home/not_home, the weather, a thermostat's mode, ...) are drawn as a
timeline: each state lasts until the next change. An update that only changed attributes (a
device tracker's position, an automation's `last_triggered`) is no change: the timeline and the
changes leave it out, and the hint says how many there were. The legend gives the share and the
time of each state in the period, and for on/off the tiles give how long it was on. Below are
the changes, the newest first, with how long each lasted. A long range shows at most the newest
5,000 changes.

**Latest attributes** shows the attributes of the newest state, as the integration stored them.

### Charts

Every chart in the app (Overview, Entities, Storage and Query) works the same way:

- It follows the width of the page, also after turning a phone or opening the sidebar.
- **Zoom:** drag across the chart to zoom in on that stretch of time; **Reset zoom** (or a double
  click) goes back. On an entity, zooming in fetches the data again for that stretch, so a week
  of hourly averages becomes every recorded value when you zoom in on an afternoon.
- **Tooltip:** pointing at the chart shows the time and the values with their unit; for averages
  also the minimum, maximum and the number of values behind them.
- **Legend:** a click hides or shows a line.
- **Toolbar** at the top right:
  - **Copy as image:** the chart as a PNG on the clipboard. When the browser doesn't allow this
    inside Home Assistant, the image is downloaded instead and a message says so.
  - **Download as PNG.**
  - **Copy the data as CSV:** what the chart shows (in the zoomed stretch), with times in UTC. The
    same download fallback applies.
  - **Full screen:** the chart fills the page; Esc closes it.
- Times are in your browser's local time; numbers in your browser's format, with their unit.
- With too little data for a chart, it says so instead of showing an empty frame; a single value
  is drawn as a point.

The charts are drawn with [uPlot](https://github.com/leeoniya/uPlot) (MIT licence), which is
included in the app.

### Storage

- **Tiles:** the size of the history, the chunks and how many are compressed, what compression
  saves, the size of the hourly and daily averages, the number of distinct attribute sets, and
  how long the history is kept.
- **Chunks:** every chunk of the history (a TimescaleDB chunk holds a fixed stretch of time,
  7 days by default), with its size before and after compression. The newest chunks are not
  compressed yet: the integration compresses chunks once they are older than its *Compress
  after* setting.
- **Background jobs:** TimescaleDB's jobs for the history: compression, retention (when a
  period is set) and the refresh of the averages, with their setting, how often they run, the
  last run and its result, and the next.
- **Tables:** the rows (estimates) and size of the tables.

### Query

Read-only SQL against the database, for questions the other pages don't answer.

The editor and the result are on the left; on the right, **Examples** and **History** (below
the editor on a small screen).

- Type a query and press **Run** or Ctrl+Enter.
- **Examples** are ready-made queries, each with what it shows and when it is useful. A click
  puts one in the editor without running it. Where you must fill something in, the query has a
  placeholder such as `<entity_id>`, which the description mentions; the editor selects the
  first one, so you can type over it. The examples:

  | Example | |
  |---|---|
  | Last day of one entity | Every value of `<entity_id>` in the last 24 hours |
  | Daily averages for a year | Mean, minimum and maximum per day, from the daily averages |
  | One month, day by day | Minimum, maximum, mean and number of values per day of the last month |
  | Busiest entities (24 h) | The 20 entities that wrote the most rows |
  | Changes per entity today | Changes per entity since midnight, in Home Assistant's time zone |
  | Last value of every sensor | The newest state of every sensor, with its unit |
  | Entities that went silent | Entities without a change for more than 24 hours |
  | Hours on per day | How long `<entity_id>` was on per day, from its on/off changes |
  | Attribute over time | The value of `<attribute>` of `<entity_id>` at every change |
  | Top attribute keys | The attribute names found most often |
  | Compression of the chunks | Every chunk with its range and its size before and after compression |
  | Table sizes | The size of the history and the averages |

- **History** keeps the last 50 queries you ran, each with when, the start of the query and
  its rows, or *error*. Running the same query again moves it to the top. ☆ makes one a
  favourite: favourites stay at the top and are never pushed out. A click puts a query back in
  the editor, ✕ removes it, and **Clear history** removes all but the favourites. The history is
  kept in your browser, per device.
- The result shows as a table with the number of rows and how long the query took. **CSV**
  downloads it. When the result has a time column and number columns, it is also drawn as a
  chart: a line per number column, or a line per entity when there is an `entity_id` column and
  one number column. **Chart** hides or shows it.
- When a query fails, PostgreSQL's message shows, with the line and column it points at.
- **Cancel** stops a running query, in the database too.

What a query can do:

- It runs in a `READ ONLY` transaction that is always rolled back, so PostgreSQL itself refuses
  anything that writes: `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, a writing `WITH`,
  whoever the user is.
- One statement at a time, starting with `SELECT`, `WITH`, `TABLE`, `VALUES`, `EXPLAIN` or
  `SHOW`. `COPY`, settings and roles, server files, large objects and functions that act on
  other sessions or the server are refused.
- When the app connects as a superuser (such as `postgres`), each query runs as the built-in
  role `pg_read_all_data`, which reads everything but has no superuser powers. A dedicated
  read-only user is better still, see above.
- At most 10,000 rows come back; the page then says *first 10,000 rows*. The table shows the
  first 2,000; the CSV has all of them.
- A query stops after 30 seconds. On a large history, filter on `time` and on one entity, as
  the examples do: that is fast, also on compressed chunks.

The tables, for your own queries:

| Object | |
|---|---|
| `chronicle.history` | A view with everything joined: `time`, `entity_id`, `state`, `value`, `attributes` |
| `chronicle.states` | `time`, `entity` (id), `state`, `value`, `attributes` (id) |
| `chronicle.entities` | `id`, `entity_id`, `domain`, `name`, `unit`, `device_class`, `state_class`, `first_seen` |
| `chronicle.attributes` | `id`, `data` (jsonb) |
| `chronicle.states_hourly`, `chronicle.states_daily` | `bucket`, `entity`, `mean`, `min`, `max`, `last`, `samples` |

A number is stored in `value` with an empty `state`; `history` shows it as text again. `on` and
`off` are stored as text with `value` 1 and 0. The `mean` of the averages is per recorded value,
not per unit of time: for on/off, use the changes, as the *Hours on per day* example does.

### Info

The database connection (without the password), the PostgreSQL and TimescaleDB versions, what
the Chronicle integration set up (layout version, averages, their time zone, an LTSS import),
the app version and links.

## Requirements

- The Chronicle integration, recording into a TimescaleDB database
- A user that can read that database (`postgres`, or the read-only user above)

## Development

```bash
docker run -d --name chronicle-test-db -p 55432:5432 -e POSTGRES_PASSWORD=test timescale/timescaledb:latest-pg18
pip install aiohttp asyncpg pytest pytest-asyncio ruff
pytest                       # the database tests are skipped when the database isn't there
./lint.sh
CHRONICLE_DATA_DIR=/tmp/chronicle CHRONICLE_DB_HOST=127.0.0.1 CHRONICLE_DB_PORT=55432 \
    CHRONICLE_DB_PASSWORD=test CHRONICLE_DB_NAME=homeassistant python -m src
```

The dashboard is then at http://localhost:8099.
