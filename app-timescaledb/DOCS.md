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
postgresql_config: {}
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

### Option: `postgresql_config`

Parameters written to `postgresql.conf` after TimescaleDB's tuning, so they override tuned
values. Parameters the app manages itself (`shared_preload_libraries`, `port`,
`data_directory`) cannot be changed; invalid names or values are logged and skipped.
Changes take effect after a restart.

```yaml
postgresql_config:
  work_mem: "32MB"
  log_min_duration_statement: "1000"
```

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

## Backup and restore

Home Assistant backups do not copy the raw PostgreSQL data directory. Before each backup
the app writes a complete `pg_dumpall` dump (`backup_db.sql`) into its data folder, the
backup stores that dump, and the dump is removed afterwards. When a backup is restored and
no usable data directory exists, the app initialises a fresh cluster and loads the dump
automatically. If that fails, the dump stays at `/data/backup_db.sql` for a manual restore:

```bash
docker exec -it addon_timescaledb_timescaledb su - postgres -c "psql -U postgres -f /data/backup_db.sql -d postgres"
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
