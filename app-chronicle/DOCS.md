# Home Assistant App: Chronicle

This app is the database of the [Chronicle integration](https://github.com/cyberjunky/home-assistant-chronicle):
PostgreSQL with TimescaleDB, made for it, and the place to look into it. Opened from the
sidebar it shows what the integration records (the history of every entity as a chart, the
storage and its compression), and like a small pgAdmin it browses every database and table,
runs SQL, shows what the server is doing, and does the upkeep, the backups and restores.

## Setting up

1. Install **Chronicle** from this repository and start it. It creates its database on the
   first start; that takes a few seconds.
2. Home Assistant then offers the Chronicle integration: **Settings → Devices & services →
   Discovered → Chronicle → Add**. The connection is filled in; confirm it. (Install the
   integration from HACS first if it isn't there yet; Home Assistant offers it after its next
   restart.)
3. Open **Chronicle** from the sidebar. The Chronicle pages fill as the integration records.

Setting up the integration by hand, or moving one that records elsewhere: the app's **Info**
page shows the host, user, password and database to enter (the integration's *Reconfigure*
moves an existing one).

### Moving from the TimescaleDB app

When the Chronicle integration records into the TimescaleDB app now:

1. Install and start this app (step 1 above), but don't add the discovered integration.
2. **Backups → Copy a database… → From the TimescaleDB app**, enter the TimescaleDB app's
   `postgres` password, and **Check**. It shows what is there: the Chronicle history, and other
   tables (such as an old `ltss` table) you can tick to leave out.
3. **Copy**. The TimescaleDB app is only read. The copy goes into a new database here; its log
   is on the Maintenance page.
4. When it is done, choose **Use for Chronicle**: the copy becomes the database the integration
   records into here (the empty one the app made is kept under another name, to remove later).
5. Point the integration at this app: **Settings → Devices & services → Chronicle → ⋮ →
   Reconfigure**, with the connection from the app's Info page. The integration continues where
   it was; what it recorded into the TimescaleDB app between the copy and this step is not in
   the copy, so do the steps one after another.
6. Once you are happy, stop the TimescaleDB app (and remove it when nothing else uses it).

The copy creates TimescaleDB at the release of the other server first (TimescaleDB restores
only into the same release) and updates it afterwards; this app can copy from TimescaleDB 2.31.0
(the TimescaleDB app 5.6.0) and newer.

## Options

| Option | Default | |
|---|---|---|
| `max_memory` | `512MB` | The memory PostgreSQL is tuned for (`timescaledb-tune`), such as `512MB` or `2GB`. More makes large queries and charts faster; leave room for Home Assistant and the other apps |
| `log_level` | `info` | `debug`, `info`, `warning` or `error` |

### Network

PostgreSQL is reachable on Home Assistant's internal network only; the integration connects
there. To reach it from another machine (Grafana on a laptop, ..), enter a port for
`5432/tcp` on the app's **Network** settings, and use a read-only login (Maintenance page).
The app's own users can't connect through that port: `postgres` only from inside the app, the
integration's `chronicle` only from Home Assistant's internal network.

## Pages

The first three pages are about the Chronicle history; the next five about the databases.

### Overview

- **Recording:** what the Chronicle integration in Home Assistant says about its writing, from
  its own sensors: *Recording*, *Not connected* (to the database) or *Not writing* (nothing
  written for 5 minutes), when it last wrote and how long that took, the states waiting in its
  queue, and the states it dropped because the queue was full. Not shown when the app can't
  read those sensors, for example outside Home Assistant.
- **Tiles:** the number of entities and the rows of the last 24 hours, the rows in the history
  (TimescaleDB's estimate, which costs nothing to ask), the size of the history with its
  chunks, the compression ratio (size before and after, of the compressed chunks), the date of
  the oldest history and the size of the whole database.
- **Rows written** per hour, over 24 hours or 7 days, with the total, the rows per hour, the
  busiest hour and the hour in progress (which the chart leaves out until it is complete).
- **Most active:** the 15 entities that wrote the most rows in the last 24 hours, with the rows
  per hour. A click opens the entity. Busy entities that you don't need are the first to
  exclude in the integration's options.
- **Domains:** entities and rows per domain. A click shows the domain's entities.

The page refreshes while it is open: the recording line every 15 seconds, the rest every minute.

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

Every chart in the app (Overview, Entities, Storage, Databases, SQL and Activity) works the same way:

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

### Databases

On the left every database as a tree: its schemas, and in them the tables, hypertables, views,
continuous aggregates and functions, with their sizes. **Find a table** searches every database;
**System** also shows the schemas of PostgreSQL and TimescaleDB themselves.

**A database** shows its dashboard:

- **Tiles:** size, connections, cache hits (the share of reads served from memory), the
  transactions and the rows written since the statistics were reset, and for TimescaleDB what
  compression saves and whether its background jobs run fine.
- **Transactions** and **Rows written** per minute. The app samples them every minute while it
  runs (in memory: the charts start again when the app restarts).
- **Largest tables:** a hypertable with all its chunks, a continuous aggregate with its data.
- **Table health:** live and dead rows, the last (auto)vacuum, and sequential against index
  scans: many sequential scans on a large table can mean a missing index. **VACUUM** per table.
- **Open in SQL**, **Back up**, **VACUUM ANALYZE** the whole database, and for another database
  **Use for Chronicle** (it becomes the one the integration records into; the current one is
  kept under another name) and **Remove**.

**A table** (or view) shows:

- **Data:** its rows, 100 at a time, newest first when it has a time column; a click on a
  column sorts on it.
- **Columns** with their types, defaults and comments; **Indexes** and constraints with their
  size and use; **Statistics:** sizes, rows inserted, updated and deleted, scans and the last
  vacuum and analyze; **Definition** of a view; **TimescaleDB:** chunks, time range,
  compression, how it is partitioned and its background jobs.
- **Query in SQL** opens a `SELECT` of it on the SQL page. **VACUUM**, **ANALYZE** and
  **REINDEX**; for a hypertable **Compress now** (chunks older than a number of days, without
  waiting for the policy) and **Remove old history** (removes the chunks older than a number of
  days: that history is gone; type the table's name to confirm); for a continuous aggregate
  **Refresh** (recalculates it from the history).

These run as tasks; the Maintenance page shows them and their log.

### SQL

SQL against any database, like pgAdmin's query tool. The editor is on the left; on the right
**Examples** and **History** (below the editor on a small screen).

- Pick the **database**, type and press **Run** or Ctrl+Enter. With text selected, only that
  runs. A script can hold many statements; they run one after the other and stop at the first
  error, which shows PostgreSQL's message and selects the place in the editor.
- **Auto-commit** on (the default): every statement is saved when it ran. Off: the statements
  run in a transaction that stays open, also between runs; **Commit** saves it, **Roll back**
  undoes it. `BEGIN` in the script does the same with auto-commit on. While a transaction is
  open the database can't be changed, and *transaction open* shows next to the options.
- Statements that change something (`INSERT`, `UPDATE`, `DELETE`, `CREATE`, `DROP`, `ALTER`,
  `TRUNCATE`, `VACUUM`, `CALL`, ..) are listed first and run after you confirm; *Don't ask again*
  stops asking until the page is closed. This guards against typos: a `SELECT` that calls a
  function that writes is not recognised.
- A statement stops after the time picked next to the database (30 seconds to an hour).
  **Cancel** stops it in the database.
- The result shows as a table: at most 10,000 rows come back per statement (2,000 in the
  table, all of them in the **CSV**). A script with several results gets a tab per result;
  messages (`INSERT 0 3`, notices) show below the editor. When a result has a time column and
  number columns, it is also drawn as a chart: a line per number column, or a line per entity
  when there is an `entity_id` column and one number column. **Chart** hides or shows it.
- The connection is the app's own role for the web UI: it reads and changes every table and
  creates databases, but it is no superuser (no server files, no programs, no other roles'
  sessions). Closing the page ends its connection, and an open transaction is rolled back; so
  does 15 minutes without use.

**Examples** are ready-made queries about the Chronicle history, each with what it shows and
when it is useful. A click puts one in the editor; one about an entity first asks which, with
the entities to pick from. The examples:

| Example | |
|---|---|
| Last day of one entity | Every value of an entity in the last 24 hours |
| Last hour, per minute | The average of an entity per minute over the last hour |
| Hourly averages for a week | Mean, minimum and maximum per hour, from the hourly averages |
| Daily averages for a year | Mean, minimum and maximum per day, from the daily averages |
| One month, day by day | Minimum, maximum, mean and number of values per day of the last month |
| Use per day of a meter | What an energy, gas or water total counted per day |
| Busiest entities (24 h) | The 20 entities that wrote the most rows |
| Rows per domain (24 h) | Rows and entities per domain |
| Unavailable this week | How often each entity became unavailable or unknown |
| Changes per entity today | Changes per entity since midnight, in Home Assistant's time zone |
| Last value of every sensor | The newest state of every sensor, with its unit |
| Entities that went silent | Entities without a change for more than 24 hours |
| Hours on per day | How long a switch, light or binary sensor was on per day, from its changes |
| Attribute over time | The value of one attribute of an entity at every change |
| Top attribute keys | The attribute names found most often |
| Compression of the chunks | Every chunk with its range and its size before and after compression |
| Table sizes | The size of the history and the averages |

The averages examples need some history: a day's averages exist from that day on, so on a new
installation *Daily averages for a year* returns a row or two.

**History** keeps the last 50 scripts you ran, with the database, when, and the result or
*error*. Running the same one again moves it to the top. ☆ makes one a favourite: favourites
stay at the top and are never pushed out. A click puts it back in the editor (with its
database), ✕ removes it, and **Clear history** removes all but the favourites. The history is
kept in your browser, per device.

The Chronicle tables, for your own queries:

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

### Activity

What the server is doing, refreshed every 10 seconds while the page is open:

- **Tiles:** PostgreSQL and how long it runs, connections against the maximum, the longest
  running query, sessions waiting on a lock, checkpoints and the WAL written.
- **Transactions** and **Connections** per minute, of all databases (sampled while the app runs).
- **Sessions:** every session running a query, with its database, role, program, for how long
  and its query; **idle and background ones too** shows the rest. A session waiting on another
  says which. **Stop** stops its query, **Disconnect** closes the session (the program, such as
  the integration, connects again). The superuser's own sessions can't be stopped from here.
- **Slowest statements:** the statements that took the most time in total, with their calls,
  mean time, rows and cache hits (from `pg_stat_statements`).
- **Settings:** the memory, connection and worker settings PostgreSQL runs with.

### Maintenance

- **Upkeep of a database:** VACUUM ANALYZE, ANALYZE or REINDEX of a whole database.
  PostgreSQL's autovacuum normally keeps up by itself; run these after removing a lot of
  history, or when the Databases page shows many dead rows. REINDEX makes writing to each table
  wait while its indexes are rebuilt.
- **Read-only logins** for Grafana and other tools: a login that reads every table of the
  databases you pick, also tables the Chronicle integration adds later. The app makes the
  password and shows it once. **Remove** takes the login away again.
- **Tasks:** the last 30 tasks (maintenance, backups, restores, copies), newest first, with how
  long they took and their log; a running task updates by itself. One task runs at a time.

### Backups

- **Back up a database:** a dump (`pg_dump`, custom format) of one database in
  `/share/chronicle`, which Samba and the File editor reach too. **Upload a dump…** puts a dump
  file from your computer there.
- **Backup files:** each with its database, size, date and TimescaleDB release. **Download**,
  **Restore…** into a new database (nothing that exists is changed; *Use for Chronicle* on the
  Databases page makes it the integration's), and **Remove**.
- **Copy from another server:** see *Moving from the TimescaleDB app* above; any PostgreSQL
  server and database works. **Check** connects first and shows the versions, the size,
  whether it holds Chronicle history and its other tables, which you can tick to leave out.

A dump can hold any SQL, and a restore runs it with full rights over the database server. The
app records the SHA-256 of every dump it makes and checks it before restoring; a file it didn't
make (*not made here*), or one that changed since, is only restored when you tick that you
trust it.

`/share/chronicle` is also open to other apps that map the share folder, and to Samba users:
they can read the dumps there, which hold your whole history.

### Info

The database (PostgreSQL and TimescaleDB versions), the connection for the integration (the
password on request), what the Chronicle integration set up (layout version, averages and
their time zone), the app version and links.

## Backups of the app

Home Assistant's backups of this app hold every database: just before the backup, the app
writes a dump of the whole server (`pg_dumpall`, compressed) into its folder, and the backup
takes that instead of the database files. Copying the files of a running database gives a copy
that may not start; a dump is always consistent. The dump is removed again after the backup.
While it is written, at least 1 GB stays free on the disk; when that isn't possible the backup
goes on without the database, and the log says so.

After restoring such a backup, the app starts with an empty database folder and loads the dump
into a new database server. That takes longer than a normal start, more so for a large history;
the app's log says when it starts and when it is done.

## Updates

- **TimescaleDB** is updated in every database on the first start after an app update.
- **PostgreSQL** major versions (18 to 19, ..): an app version with a new major version carries
  the old one as well and upgrades the database on its first start (`pg_upgrade`, which takes
  seconds to minutes). When anything fails, the old database is put back and the app stops with
  a message; an interrupted upgrade is put back on the next start. The app never starts a
  database of a newer major version than its own.

## Security

The database holds when you are home and what you do; how the app protects it:

- **Roles:** `postgres` (the superuser) only for the app's own scripts, inside the app; the
  integration's `chronicle` only from Home Assistant's internal network, with a password the app
  generates; the web UI's `chronicle_ui` without a password, only from inside the app, and no
  superuser; read-only logins with their own password.
- **The web UI** is only reachable through Home Assistant (ingress, administrators only);
  direct connections to its port are refused. Requests that change something must come from
  the page itself.
- **Superuser tasks** (backups, restores, copies) run in a separate process that only takes
  these tasks, with checked names, and only from the web UI.
- **Passwords** are stored in the app's folder, readable by the app only, and never logged.
- **Restores** check that a dump is the app's own (or that you trust it), and keep the owners
  from the dump, so nothing in it runs with more rights than it had.

See [SECURITY.md](https://github.com/cyberjunky/home-assistant-apps/blob/main/app-chronicle/SECURITY.md)
for the details.

## Requirements

- The [Chronicle integration](https://github.com/cyberjunky/home-assistant-chronicle) 0.2.0 or
  later (it is offered by Home Assistant when this app runs), on Home Assistant 2026.9 or later
- About 250 MB for the app, plus the history (compressed, typically a tenth of its raw size)

## Development

```bash
docker run -d --name chronicle-test-db -p 55432:5432 -e POSTGRES_PASSWORD=test timescale/timescaledb:2.32.0-pg18
pip install aiohttp asyncpg pytest pytest-asyncio pytest-aiohttp ruff
pytest                       # the database tests are skipped when the database isn't there
./lint.sh
tests/upgrade/run.sh         # the PostgreSQL upgrade, with Docker (builds a test image)
CHRONICLE_DATA_DIR=/tmp/chronicle CHRONICLE_DB_HOST=127.0.0.1 CHRONICLE_DB_PORT=55432 \
    CHRONICLE_DB_USER=postgres CHRONICLE_DB_PASSWORD=test CHRONICLE_DB_NAME=homeassistant python -m src
```

The dashboard is then at http://localhost:8099 (the Backups page needs the app's helper and
works in the app only).
