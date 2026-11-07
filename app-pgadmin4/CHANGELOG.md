<!-- https://developers.home-assistant.io/docs/apps/presentation#keeping-a-changelog -->
## 4.1.1

- Fix: the TimescaleDB app was not registered. pgAdmin now asks the Supervisor which
  TimescaleDB app is installed, whatever repository it came from, registers it for the
  pgAdmin user that actually logs in, and says in the log why it could not (not installed,
  not started).
- No more "bashio::addon.* is deprecated" warnings in the log.

## 4.1.0

- pgAdmin 9.17 with the PostgreSQL 17 and 18 client tools.
- New `servers` option: database servers are registered in pgAdmin automatically.
  The TimescaleDB app from this repository is registered without configuration.
- Fix: start-up crash loop on Supervisor 2026.08 (bashio 0.19.0).
- Moved into the home-assistant-apps repository. The image is now the multi-arch
  `ghcr.io/cyberjunky/app-pgadmin4`; 32-bit architectures are no longer built.
- Attribution to the original Apache-licensed add-on added in `NOTICE`.

## 4.0.0

- Last release before the move. See the Git history for earlier changes.
