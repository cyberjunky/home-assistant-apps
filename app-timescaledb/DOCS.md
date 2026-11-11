# TimescaleDB

PostgreSQL with the TimescaleDB time-series extension, so you can keep your Home Assistant
recorder data and your long-term measurements in one SQL database and query them with
tools such as Grafana. Included extensions: TimescaleDB Toolkit, PostGIS, pgvector,
pg_cron, pgAgent and system_stats.

## Installation

1. Install the app and start it. The first start initialises the database and can take a
   while; watch the log.
2. Map port `5432` in the app's network section to reach the database from your network.
   If another service already uses it, pick a different host port.

The default user is `postgres` with password `homeassistant`. Change it right after the
first start, either with the `postgres_password` option or with:

```sql
ALTER USER postgres WITH PASSWORD 'a-strong-password';
```

The default `pg_hba.conf` allows local peer logins and password logins from the network.
Tighten it with the `pg_hba_config` option.

## Configuration

Example configuration:

```yaml
databases:
  - homeassistant
timescale_enabled:
  - homeassistant
timescaledb:
  telemetry: basic
  maxmemory: 512MB
  maxcpus: 4
max_connections: 50
system_packages: []
init_commands: []
retry_upgrade: false
postgresql_settings: []
pg_hba_config: []
```

### Option: `databases`

Databases to create on start-up if they do not exist. Default: `homeassistant`.

### Option: `timescale_enabled`

Databases that get the TimescaleDB extension enabled. Default: `homeassistant`.

### Option: `timescaledb`

- `telemetry`: TimescaleDB telemetry, `off` or `basic`.
- `maxmemory`: memory budget used by TimescaleDB's tuning, for example `512MB`.
- `maxcpus`: CPU budget used by TimescaleDB's tuning.

### Option: `max_connections`

Maximum number of client connections, at least 20. Default: `50`.

### Option: `postgres_password`

Sets the password of the `postgres` user at start-up.

### Option: `system_packages`

Extra Alpine packages to install at start-up.

### Option: `init_commands`

Shell commands to run at start-up.

### Option: `retry_upgrade`

Retry a failed PostgreSQL major-version upgrade on the next start.

### Option: `postgresql_settings`

PostgreSQL settings as a list of `name=value` entries. They are written to
`postgresql.user.conf`, which is included at the end of `postgresql.conf`, so they win over
the app's own settings and TimescaleDB's tuning. The file is rewritten on every start:
removing an entry also removes the setting. Extension settings (`timescaledb.*`) work too;
quote values that contain spaces. Settings the app manages itself
(`shared_preload_libraries`, `port`, `data_directory`, `hba_file`, `ident_file`,
`unix_socket_directories`, `config_file` and the `include` directives) and malformed entries
are skipped with a warning. Before PostgreSQL starts, it checks the settings: when it rejects
one (an unknown name or a wrong value), all of them are ignored for that start and the error
is in the log, so a typo cannot keep the database from starting. Changes take effect after a
restart.

```yaml
postgresql_settings:
  - "work_mem=32MB"
  - "log_min_duration_statement=1000"
  - "log_line_prefix='%m [%p] '"
  - "timescaledb.max_background_workers=12"
```

### Option: `postgresql_config`

Replaced by `postgresql_settings` in 5.6.0. It could never hold a value (the Supervisor drops
keys of a dictionary option that the schema does not list by name), is ignored, and will be
removed in a later version.

### Option: `pg_hba_config`

Rules appended to the default `pg_hba.conf`. Rules are evaluated in order; the first match
wins. Invalid rules are logged and skipped. Changes take effect after a restart.

```yaml
pg_hba_config:
  - type: host            # local, host, hostssl or hostnossl
    database: all
    user: all
    address: 192.168.1.0/24
    method: scram-sha-256 # md5, scram-sha-256, trust, reject, peer, cert, ...
    options: ""           # optional, e.g. clientcert=verify-full
```

A wrong `pg_hba.conf` can lock you out. Keep at least one working rule before adding
restrictions.

## Moving from the Expaso add-on

This app is derived from the Expaso TimescaleDB add-on and uses the same data layout, so it
takes over an existing installation in place: databases, users, passwords and settings stay,
and a PostgreSQL 17 cluster is upgraded to 18 on the first start. The two add-ons have
different hostnames, so the switch is a copy of the data folder plus a hostname change in
the clients.

1. Make a Home Assistant backup of the Expaso add-on (`77b2833f_timescaledb`), then **stop**
   it. Its data must not change while it is copied.
2. Install this app from the store and give it the **same options** as the old one
   (`databases`, `timescale_enabled`, `timescaledb`, `max_connections`, ...). Do not start it
   yet. Leave the host port unmapped until the old add-on is gone.
3. Copy the old data folder into the new one. The Terminal add-on with *Protection mode*
   off reaches the host only through Docker, so first find where the old add-on's `/data`
   lives on the host (the container is `app_77b2833f_timescaledb`, or `addon_...` on older
   Supervisors; `docker ps -a` lists it):

   ```bash
   docker inspect app_77b2833f_timescaledb --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' | grep '/data$'
   ```

   The new app's folder is the sibling named `1b41fb55_timescaledb` in the same parent
   directory (it exists once the app is installed). Then, with those two paths:

   ```bash
   docker run --rm \
     -v <old path>:/old:ro \
     -v <new path>:/new \
     alpine sh -c "cp -a /old/. /new/ && rm -f /new/options.json && ls -la /new /new/postgres | head -20"
   ```

   `/new/postgres/PG_VERSION` must exist afterwards. The copy needs as much free disk space
   as the old folder; the upgrade itself links the files and needs little extra.
4. Start this app and watch its log: "Upgrading PostgreSql from 17 to 18" ends with
   "PostgreSQL upgraded succesfully!". If it fails, the log says why; the old add-on's data
   is untouched, so starting the old add-on again is the way back.
5. Point the clients at the new hostname: `77b2833f-timescaledb` becomes
   `1b41fb55-timescaledb` in the Home Assistant recorder's `db_url`, in the DSMR Reader,
   TeslaMate, LTSS or Grafana configuration, and so on. Clients that use the host's IP
   address keep working once the port mapping is moved to this app. pgAdmin 4 registers
   the new server by itself on its next start.
6. Once everything runs against the new app, uninstall the Expaso add-on.

## Backup and restore

Home Assistant backups do not copy the raw PostgreSQL data directory. Before each backup
the app writes a complete, gzip compressed `pg_dumpall` dump (`backup_db.sql.gz`) into its
data folder, the backup stores that dump, and the dump is removed afterwards. The dump
always leaves at least 1 GiB free on the data disk: when the disk gets fuller, the dump is
stopped and removed, the log says so, and that backup fails instead of filling the disk.

When a backup is restored and no usable data directory exists, the app initialises a fresh
cluster and loads the dump automatically, the way TimescaleDB prescribes for a logical dump
(`timescaledb_pre_restore()` before and `timescaledb_post_restore()` after the data of each
database), and moves every sequence up to its data so new rows do not hit duplicate keys.
If that fails, the dump stays at `/data/backup_db.sql.gz` for a manual restore (the
container is named `app_<slug>`, `addon_<slug>` on older Supervisors; see `docker ps`).
Stop the clients first (the dump drops and recreates the databases), and load it with the
TimescaleDB steps added, never with a plain `psql -f`, which loses the rows of compressed
chunks:

```bash
docker exec -it app_1b41fb55_timescaledb bash -c \
  'source /usr/share/timescaledb/restore_from_backup.sh && gzip -dc /data/backup_db.sql.gz | addTimescaleRestoreSteps | psql -X -U postgres -d postgres'
```

## Troubleshooting

- **The app does not start and the log mentions the port**: another service already uses
  the host port you mapped. Choose a different one.
- **A PostgreSQL major upgrade failed**: fix the cause shown in the log, set
  `retry_upgrade: true` and restart.
- **Locked out after changing `pg_hba_config`**: remove the rules, restart, and add them
  back one at a time.

## Support

Open an issue in the repository that hosts this app.
