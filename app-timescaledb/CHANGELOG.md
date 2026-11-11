<!-- https://developers.home-assistant.io/docs/apps/presentation#keeping-a-changelog -->
## 5.6.0

**After updating:** if you tried to use `postgresql_config`, move your settings to the new `postgresql_settings` option, one `name=value` entry per setting. `postgresql_config` never kept a value (the Supervisor dropped its keys on save), so nothing was applied before. For example, instead of `postgresql_config: {work_mem: 32MB}`:

```yaml
postgresql_settings:
  - "work_mem=32MB"
  - "log_min_duration_statement=1000"
```

- Fix: 5.5.0 and 5.5.1 did not start at all: the start-up banner and the service scripts lost their executable bit in the move to this repository, so S6 stopped the container right away. The image now makes them executable.
- Fix: a restore from a Home Assistant backup lost TimescaleDB data. In a test with a compressed hypertable and a continuous aggregate, 19,539 of 20,000 rows came back ("could not find hypertable with id 1"). The restore now runs `timescaledb_pre_restore()` before and `timescaledb_post_restore()` after the data of every database, as TimescaleDB prescribes, and all rows come back. Existing `backup_db.sql.gz` and `backup_db.sql` dumps are restored this way too.
- Fix: after a restore, every sequence is moved up to the highest value in its table, so inserts no longer fail with a duplicate key (for example `recorder_runs_pkey`).
- Fix: the backup no longer fills the disk. It needs at least 1 GiB free on the data disk before it starts, stops when the free space drops below that while dumping, and always removes a failed, stopped or interrupted dump.
- Fix: the app could stop right after a start or a restore with `database "homeassistant" already exists`. The checks for existing databases and extensions no longer depend on `grep` in a pipe, and a database that cannot be created only gives a warning.
- Fix: pgAgent removes registrations left behind by an earlier pgAgent before it starts, so a restart cannot fail on a duplicate key in `pga_jobagent`. A failing pgAgent is restarted on its own and never stops PostgreSQL.
- New `postgresql_settings` option replaces `postgresql_config`. PostgreSQL checks the settings before it starts: when it rejects one, all of them are ignored for that start and the log says why, so a typo cannot keep the database from starting.
- The 17 to 18 upgrade reads the checksum setting of the old cluster without a pipe, and the timescaledb-extras helpers are dropped in the right database instead of always in `homeassistant`.

## 5.5.1

- No more "bashio::addon.* is deprecated" warnings in the log.

## 5.5.0

- PostgreSQL 18 with all components updated: TimescaleDB 2.29.2, TimescaleDB Toolkit
  1.26.0, PostGIS 3.6.4, pgvector 0.8.6, pgAgent 4.2.3, system_stats 4.1.
- New: the pg_cron 1.6.7 job scheduler next to pgAgent.
- New `postgres_password` option; new installations use scram-sha-256.
- Compressed backups.
- Fix: "No such file or directory" start-up error after restoring a Home Assistant backup.
- Fix: `pg_hba_config` crash and duplicate rules after a restart.
- Fix: the postgres user could not read the backup file during a restore.
- Start-up banner tolerates a missing Supervisor API connection; bashio 0.19.0.
- Component versions are declared once, in the Dockerfile; 32-bit architectures are no
  longer built.
- Moved into the home-assistant-apps repository. The image is now the multi-arch
  `ghcr.io/cyberjunky/app-timescaledb`.
- Attribution to the original Apache-licensed add-on added in `NOTICE`.
- Build fix: musl is upgraded in the image, so the PostgreSQL development packages install
  on the pinned base image after Alpine updates musl.

## 5.4.2

- Last release before the move. See the Git history for earlier changes.
