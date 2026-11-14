# Changelog

## 0.2.1

**After updating:** nothing to do.

- **Copying from another server works:** *Check* in *Copy a database…* stopped with "Cannot connect: [Errno 13] Permission denied" whatever the host and password, because the web UI looked for PostgreSQL client certificates in a folder it may not open.

## 0.2.0

**After updating:** the app is now the database itself. It no longer connects to the TimescaleDB app: its connection options are gone. To keep your history, move it over once: **Backups → Copy a database… → From the TimescaleDB app**, then **Use for Chronicle**, and point the Chronicle integration (0.2.0 or later) at this app with *Reconfigure* (the Info page has the connection). See *Moving from the TimescaleDB app* in the documentation. The donate card may show once more: its state moved.

- **The database, in the app:** PostgreSQL 18 with TimescaleDB 2.32, set up for the Chronicle integration with its own role and generated password. Home Assistant finds it by itself and offers the integration with the connection filled in. The option *Memory* tunes PostgreSQL. About 250 MB, against several GB for the TimescaleDB app.
- **Databases:** every database as a tree; a dashboard per database (size, cache hits, transactions and rows written per minute, the largest tables, table health) and per table its rows, columns, indexes, statistics and TimescaleDB details, with VACUUM, ANALYZE, REINDEX, *Compress now*, *Refresh* and *Remove old history*.
- **SQL** (was *Query*): scripts against any database, which can change data now: auto-commit, or a transaction to commit or roll back; a confirmation before statements that change something; a tab per result and the messages; a timeout to pick. The examples ask for the entity instead of a placeholder to type over, and five are new (the last hour per minute, hourly averages for a week, use per day of a meter, rows per domain, unavailable this week).
- **Activity:** the sessions and their queries (stop or disconnect one), sessions waiting on a lock, the slowest statements, transactions and connections per minute, the main settings.
- **Maintenance:** upkeep of a whole database, read-only logins for Grafana and other tools, and every task with its log.
- **Backups:** dumps of a database in `/share/chronicle` (download, upload, restore as a new database, remove), and copying a database from another server, such as the TimescaleDB app, into a new one here. Home Assistant's backups of the app hold every database, as a dump.
- **Updates:** TimescaleDB is updated in every database on the first start after an app update; a later PostgreSQL major version upgrades the database (`pg_upgrade`), and puts the old one back when anything fails.
- **Storage:** no error any more when a TimescaleDB job hasn't run yet (as right after a restore).
- **Security:** see SECURITY.md: the superuser only inside the app, the web UI without superuser powers, restores and copies in a separate process, requests that change something only from the page itself.

## 0.1.6

**After updating:** nothing to do.

- **The top bar fits a phone:** the app's name and buttons on top with the theme button last, the menu full width below. When the menu doesn't fit it scrolls sideways instead of losing a tab; on a laptop it gives way the same way and the clock goes first (below 860 px).

## 0.1.5

**After updating:** nothing to do.

- **Overview: is it recording?** A line at the top shows what the Chronicle integration in Home Assistant says about its writing: *Recording* (or *Not connected*, or *Not writing* when nothing was written for 5 minutes), when it last wrote, how long that took, the queue and the states it had to drop. It reads the integration's own sensors and is left out when the app can't reach them.
- **Rows written** is a low area chart with a summary above it: the rows in the period, the rows per hour, the busiest hour and the hour in progress. That hour isn't drawn any more, so the chart doesn't end in a dip, and the first hour of the period is counted whole.

## 0.1.4

**After updating:** nothing to do.

- **Overview:** *Compression* shows "none yet" at the size of the other tiles, aligned with them, and a detail line that fits ("chunks compress with age").

## 0.1.3

**After updating:** update the Chronicle integration to 0.1.5 or later for *Exclude*; *Delete history* works with any version.

- **Exclude and delete, from the page:** an entity has *Exclude…* (stop recording it, optionally deleting its history) and *Delete history…*; with a domain picked on Entities, *Exclude domain…* does the same for the whole domain. They run the Chronicle integration's actions in Home Assistant, so the exclusions land in the integration's options, where you can undo them. The app itself still only reads the database.

## 0.1.2

**After updating:** nothing to do.

- **Entities:** a *Clear filter* button next to the domain list, shown while a search or domain filter is set.
- **Overview:** *Oldest history* shows a short date (11 Nov, or Nov 2025 for an earlier year) that fits the tile; the full date is in its tooltip. *Compression* says "none yet" until TimescaleDB compresses its first chunk, which it only does once a chunk is older than the compression policy.

## 0.1.1

**After updating:** nothing to do.

- **Charts with a short history:** a range no longer starts before the entity's first record. With an hour of history, every range shows that hour with every value, instead of a sliver at the edge of 24 hours or a few averages at the edge of 30 days. What a chart shows now follows the time it spans: every value up to two days, then averages per few minutes, hourly and daily.
- **New charts** (uPlot), on every page:
  - they follow the page's width, also for a chart drawn while its page was hidden
  - drag to zoom; on an entity, zooming fetches the data again at a finer level, down to every value
  - a tooltip with the time, the values and their unit
  - a legend that hides and shows a line
  - a toolbar: copy as image, download as PNG, copy the data as CSV, full screen
- Counters are drawn as steps, measurements as lines with their minimum–maximum band; *unavailable* and *unknown* are gaps instead of being joined over.
- **Compare:** add up to four more entities to an entity's chart, with a second y axis for another unit.
- State timelines leave out updates that only changed attributes (a tracker's position, an automation's `last_triggered`): the changes are real changes, and the hint counts the attribute updates.
- A chart with too little data says so; a single value is drawn as a point.
- The CSV link of an entity is now the toolbar's **Copy the data as CSV**, for every chart.

## 0.1.0

**After updating:** nothing to do. This is the first version.

- The dashboard for the Chronicle integration's history in TimescaleDB:
  - **Overview:** entities, rows, sizes and compression, rows written per hour, the most active entities and domains
  - **Entities:** every recorded entity, and the history of each over 24 hours to everything, as a chart with its minimum–maximum band or as a state timeline, with CSV
  - **Storage:** chunks and their compression, TimescaleDB's background jobs, table sizes
  - **Query:** read-only SQL with twelve described examples and a history of the last 50 queries with favourites, side by side with the editor; a chart of the result and CSV. Queries run in a read-only transaction that is rolled back, for at most 30 seconds and 10,000 rows
  - **Info:** the connection and what the integration set up
- Only reads: every connection is read-only.
