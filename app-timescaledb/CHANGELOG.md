<!-- https://developers.home-assistant.io/docs/apps/presentation#keeping-a-changelog -->
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

## 5.4.2

- Last release before the move. See the Git history for earlier changes.
