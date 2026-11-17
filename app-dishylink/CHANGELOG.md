<!-- https://developers.home-assistant.io/docs/apps/presentation#keeping-a-changelog -->
## 1.0.5

- No longer marked experimental.
- Dutch translation of the options. The port's description shows on the Configuration tab (its label never did).
- Services in the current s6-overlay layout; AppArmor allows that folder instead of the old ones.
- Docs and log messages say "app" instead of "add-on", as Home Assistant does since 2026.2.
- The README's links work inside Home Assistant.

## 1.0.4

- Health check moved from the obsolete `watchdog` option to a Docker
  `HEALTHCHECK` on the recorder's `/api/health`.
- Config cleanup: removed keys that only restated defaults (startup, boot,
  ingress_port), as required by the Home Assistant app linter.
- Images are now built and published to GHCR by GitHub Actions.

## 1.0.3

- The dashboard follows Home Assistant's dark/light mode when opened through
  ingress. Its theme toggle still works: "System" now means "same as Home
  Assistant". Dashboards that never had a theme chosen start in that mode.

## 1.0.2

- Fix: AppArmor denied Node access to `/etc/ssl/openssl.cnf`, so the browser
  host exited with an OpenSSL configuration error.

## 1.0.1

- Fix: the AppArmor profile lacked the capabilities for `chown` and for
  dropping privileges, so both services restarted in a loop with "Operation
  not permitted".
- Recorder works in RAM and is flushed to disk on an interval (default 15
  minutes) and at shutdown, to limit flash wear. New option
  `history_flush_minutes`; 0 writes straight to disk.

## 1.0.0

- Initial release, packaging Dishylink v1.2.0 (browser dashboard, history
  recorder and LAN/cloud proxies).
- Served through Home Assistant ingress; optional direct port 8080.
- Host LAN identity detected from the Supervisor, with manual overrides.
- Optional dish and router endpoint overrides.
- Recordings persist in `/data/historian` and are included in backups.
- Custom AppArmor profile.
