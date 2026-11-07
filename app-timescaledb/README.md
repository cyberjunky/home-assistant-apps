[![Version][version-shield]][changelog]
[![GitHub Activity][commits-shield]][commits]
[![License][license-shield]](LICENSE)
![Project Maintenance][maintenance-shield]
![Supports aarch64 Architecture][aarch64-shield]
![Supports amd64 Architecture][amd64-shield]

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

<img src="https://raw.githubusercontent.com/cyberjunky/home-assistant-apps/main/app-timescaledb/icon.png" alt="TimescaleDB" width="128" align="right">

# TimescaleDB App for Home Assistant

A Home Assistant app that runs [PostgreSQL](https://www.postgresql.org/) with the
[TimescaleDB](https://www.timescale.com/) time-series extension, so you can keep your Home
Assistant recorder data and your long-term measurements in one SQL database and query them
with tools such as Grafana.

## Supported Features

- **PostgreSQL with TimescaleDB**: hypertables, compression, continuous aggregates and
  retention policies on plain SQL
- **Extensions included**: TimescaleDB Toolkit, PostGIS, pgvector, pg_cron, pgAgent and
  system_stats
- **Declarative configuration**: PostgreSQL parameters and `pg_hba.conf` rules from the
  app options, no shell commands needed
- **Safe backups**: Home Assistant backups contain a consistent SQL dump that is restored
  automatically
- **Resource limits**: memory and CPU budget for TimescaleDB's tuning
- **Extensible**: install extra Alpine packages and run commands at start-up

## Installation

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fcyberjunky%2Fhome-assistant-apps)

Alternatively:

1. Go to **Settings → Apps → App Store**
2. Open the menu (⋮) and choose **Repositories**
3. Add `https://github.com/cyberjunky/home-assistant-apps`
4. Install **TimescaleDB** and start it
5. Check the log: the first start initialises the database and can take a while

## Usage

Map port `5432` in the app's network section to reach the database from your network. If
another service already uses it, pick a different host port.

> [!WARNING]
> The default user is `postgres` with password `homeassistant`. Change it right after the
> first start, for example with the pgAdmin 4 app or `psql`:
>
> ```sql
> ALTER USER postgres WITH PASSWORD 'a-strong-password';
> ```

The default `pg_hba.conf` allows local peer logins and password logins from the network.
Tighten it with the `pg_hba_config` option described below.

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

| Option | Default | Description |
|--------|---------|-------------|
| `databases` | `[homeassistant]` | Databases to create on start-up if they do not exist. |
| `timescale_enabled` | `[homeassistant]` | Databases that get the TimescaleDB extension enabled. |
| `timescaledb.telemetry` | `basic` | TimescaleDB telemetry: `off` or `basic`. |
| `timescaledb.maxmemory` | `512MB` | Memory budget used by TimescaleDB's tuning. |
| `timescaledb.maxcpus` | `4` | CPU budget used by TimescaleDB's tuning. |
| `max_connections` | `50` | Maximum number of client connections (at least 20). |
| `postgres_password` | unset | Sets the password of the `postgres` user at start-up. |
| `system_packages` | `[]` | Extra Alpine packages to install at start-up. |
| `init_commands` | `[]` | Shell commands to run at start-up. |
| `retry_upgrade` | `false` | Retry a failed PostgreSQL major-version upgrade on the next start. |
| `postgresql_config` | `{}` | PostgreSQL parameters, see below. |
| `pg_hba_config` | `[]` | Extra authentication rules, see below. |

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

> [!CAUTION]
> A wrong `pg_hba.conf` can lock you out. Keep at least one working rule before adding
> restrictions.

## Backup and Restore

Home Assistant backups do not copy the raw PostgreSQL data directory. Before each backup
the app writes a complete `pg_dumpall` dump (`backup_db.sql`) into its data folder, the
backup stores that dump, and the dump is removed afterwards. When a backup is restored and
no usable data directory exists, the app initialises a fresh cluster and loads the dump
automatically. If that fails, the dump stays at `/data/backup_db.sql` for a manual restore:

```bash
docker exec -it addon_timescaledb_timescaledb su - postgres -c "psql -U postgres -f /data/backup_db.sql -d postgres"
```

## 💖 Support This Project

If you find this app useful, please consider supporting its continued development and
maintenance:

### 🌟 Ways to Support

- **⭐ Star this repository** - Help others discover the project
- **💰 Financial Support** - Contribute to development and hosting costs
- **🐛 Report Issues** - Help improve stability and compatibility
- **📖 Spread the Word** - Share with other Home Assistant users

### 💳 Financial Support Options

[![Donate via PayPal](https://img.shields.io/badge/Donate-PayPal-blue.svg?style=for-the-badge&logo=paypal)](https://www.paypal.me/cyberjunkynl/)
[![Sponsor on GitHub](https://img.shields.io/badge/Sponsor-GitHub-red.svg?style=for-the-badge&logo=github)](https://github.com/sponsors/cyberjunky)

Every contribution, no matter the size, makes a difference and is greatly appreciated! 🙏

## License

This project is licensed under the Apache License 2.0 - see the [LICENSE](LICENSE) file for
details. It is derived from an Apache-licensed project by Hans van Essen; attribution is in
the [NOTICE](NOTICE) file. PostgreSQL, TimescaleDB and the bundled extensions keep their own
licenses.

---

[version-shield]: https://img.shields.io/badge/dynamic/yaml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fcyberjunky%2Fhome-assistant-apps%2Fmain%2Fapp-timescaledb%2Fconfig.yaml&query=%24.version&label=version&style=for-the-badge
[changelog]: https://github.com/cyberjunky/home-assistant-apps/blob/main/app-timescaledb/CHANGELOG.md
[commits-shield]: https://img.shields.io/github/commit-activity/y/cyberjunky/home-assistant-apps.svg?style=for-the-badge
[commits]: https://github.com/cyberjunky/home-assistant-apps/commits/main
[license-shield]: https://img.shields.io/badge/license-Apache%202.0-blue.svg?style=for-the-badge
[maintenance-shield]: https://img.shields.io/badge/maintainer-cyberjunky-blue.svg?style=for-the-badge
[aarch64-shield]: https://img.shields.io/badge/aarch64-yes-green.svg?style=for-the-badge
[amd64-shield]: https://img.shields.io/badge/amd64-yes-green.svg?style=for-the-badge
