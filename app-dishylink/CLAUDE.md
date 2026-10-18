# CLAUDE.md — app-dishylink maintenance notes

Home Assistant app (add-on) that packages the upstream project
[DaveyHert/Dishylink](https://github.com/DaveyHert/Dishylink) in its "browser"
form: built web dashboard + always-on history recorder + LAN/cloud proxies.

Policy: run upstream code unchanged. All Home Assistant specifics live in this
folder (Dockerfile, rootfs, config.yaml, AppArmor). Never fork upstream storage,
polling or API code; if something must differ, do it from the outside (env vars,
nginx, s6 scripts) and document why here.

## Layout

| Path | Role |
| --- | --- |
| `config.yaml` | Add-on manifest. `version` is this add-on's version, not upstream's. `image:` points at GHCR; comment it out in a local-build copy. |
| `Dockerfile` | Two stages. Stage 1 (`node:22-bookworm-slim`) clones upstream at `ARG DISHYLINK_VERSION` and builds `dist/`. Stage 2 (HA base image, Alpine) runs it with node + nginx + rsync. |
| `ha-ingress.js` | Injected as the first `<head>` script. Two jobs: (1) rewrites the dashboard's absolute `/api`, `/dishy`, `/cloud` ... requests onto the ingress prefix (the dashboard is built with `--base=./`); (2) makes the dashboard's "system" theme follow Home Assistant's dark mode by answering `matchMedia("(prefers-color-scheme: dark)")` with `hass.themes.darkMode` read from the same-origin parent window (fallbacks: luminance of HA's `--primary-background-color`, then the parent's OS scheme). Upstream theme logic: `src/lib/theme.ts`, `src/hooks/useTheme.ts`, localStorage key `dishylink-theme`. |
| `rootfs/etc/services.d/dishylink/` | s6 service. `run` sets env, resolves host identity, seeds the RAM dir, then execs `node --import tsx docker/browserHost.mts` as user `dishylink` (uid 1001). `finish` flushes history to disk and halts the container on a non-zero exit. |
| `rootfs/etc/services.d/nginx/` | Ingress proxy on 8099 in front of the browser host on 8080. Only 172.30.32.2 (ingress gateway) is allowed. Drops Origin/Referer because upstream only accepts writes from LAN origins. |
| `rootfs/etc/services.d/historian-flush/` | Timer: every `history_flush_minutes` runs `historian-flush`. Disables itself with `s6-svc -O` when the option is 0. |
| `rootfs/usr/bin/historian-flush` | `rsync -a --delete` from `/tmp/historian` (tmpfs) to `/data/historian`. Excludes `*.tmp`, `historian.lock`, `.starlink-cookie`. |
| `rootfs/etc/nginx/nginx.conf` | The ingress proxy config. |
| `apparmor.txt` | Custom profile with two subprofiles (`dishylink` for node, `nginx`). See the AppArmor section. |
| `translations/en.yaml` | Option names and descriptions shown in the UI. Keep in sync with `schema` in config.yaml. |
| `DOCS.md`, `README.md`, `CHANGELOG.md` | User-facing. One CHANGELOG entry per add-on version. |

## How upstream is wired in

- Upstream entry point: `docker/browserHost.mts` with `HISTORIAN_EMBED=1`. It
  serves `BROWSER_DIST` on `BROWSER_HOST_PORT` (8080), embeds the recorder from
  `collector/historian.mts`, and answers `/api/health` (used by the watchdog).
- Env vars set in the run script: `NODE_ENV`, `HISTORIAN_EMBED`,
  `HISTORIAN_DATA_DIR`, `HISTORIAN_PROTOSET`, `BROWSER_DIST`,
  `BROWSER_HOST_PORT`, `STARLINK_COOKIE_FILE`, and optionally `HOST_LAN_IP`,
  `HOST_MAC`, `DISH_URL`, `ROUTER_URL`. The names come from upstream's own
  Dockerfile, `docker/browserHost.mts` and `collector/historian.mts`.
- Runtime node deps are installed by hand in stage 2, not from upstream's
  lockfile: `@bufbuild/protobuf`, `tsx`, `undici`. The versions mirror what
  upstream's Dockerfile installs in its runtime image.
- Directories copied from the build stage: `dist`, `public/dish.protoset`,
  `collector`, `core`, `cloud`, `docker`, `dev/starlinkCloudProxy.ts`. If
  upstream adds a runtime import outside these, the container fails at start
  with a module-not-found error; add the directory to the Dockerfile.
- Host identity: the Supervisor API `/network/interface/default/info` gives
  the host's LAN IP and MAC so Dishylink can find "this device" in the router's
  client list. Needs `hassio_api: true` (the default role is enough).

## Updating to a new upstream release

1. Read upstream's release notes, `Dockerfile`, `package.json` and
   `docker/browserHost.mts` at the new tag. Check: runtime dep versions, new
   env vars, new directories imported at runtime, changed data file names in
   `collector/historian.mts` (search for `join(DATA_DIR,`), changed ports.
2. Bump `ARG DISHYLINK_VERSION` in the Dockerfile. Keep stage 1 identical to
   upstream's build steps except `--base=./` and the `ha-ingress.js` injection.
3. Check that `ha-ingress.js` still covers every absolute path prefix the
   dashboard requests (grep upstream `src/` for `fetch("/` and `EventSource(`).
4. Bump `version` in config.yaml, add a CHANGELOG entry, update DOCS.md and
   `translations/en.yaml` if options changed.
5. Build locally on the HA host (see below) and verify the start log shows
   `Starting Dishylink <tag> browser host on port 8080` and the panel loads.

## Disk-write design (keep it)

Upstream's recorder rewrites `samples.json` (6 h of 5 s telemetry) and
`client-samples.json` (30 min of per-second per-client rates) in full every
minute via temp file + rename, and appends per-minute rows to the `.ndjson`
logs. That is several MB per minute of flash writes. Instead of patching
upstream, `config.yaml` sets `tmpfs: true` (RAM mounted at `/tmp`) and the
recorder's `HISTORIAN_DATA_DIR` is `/tmp/historian` whenever
`history_flush_minutes > 0`. The `historian-flush` service copies it to
`/data/historian` on the interval, and the dishylink `finish` script copies it
once more after node exits (node writes its final snapshots on SIGTERM). At
start the run script seeds `/tmp/historian` from `/data/historian`.
`history_flush_minutes: 0` restores upstream behaviour (direct writes).
The pasted Starlink session always lives at `/data/historian/.starlink-cookie`
through `STARLINK_COOKIE_FILE`, never in RAM.

No database is used or wanted: upstream stores JSON Lines files and the most
standard setup is to keep it that way. Long-term analytics belong in a
separate exporter, not in this add-on.

## AppArmor

- A custom profile grants no capabilities unless listed. The main profile
  needs `chown`, `fowner`, `fsetid`, `setuid`, `setgid`, `dac_override` (chown
  of the data dirs, `s6-setuidgid` dropping to `dishylink`, rsync preserving
  owners). The nginx subprofile needs `chown`, `setuid`, `setgid`,
  `dac_override`. Symptom when one is missing: `Operation not permitted` on
  chown in the log, service restart loop.
- Anything executed from the main profile must match an `ix` rule
  (`/bin/**`, `/usr/bin/**`, `/command/**` ...). That is why the flush helper
  lives in `/usr/bin`, not `/usr/local/bin`.
- Signal peers use `*_app-dishylink` because the Supervisor names the profile
  after the installed slug: `local_app-dishylink` for a local build,
  `<repo-hash>_app-dishylink` when installed from a repository.
- The profile is loaded at install and update time, not on restart. After
  editing `apparmor.txt`, uninstall and reinstall the app, or bump `version`
  and Update.

## Local install and test on the Home Assistant host

- The Samba share `//192.168.1.251/addons` (mapped as drive `Z:`) is `/addons`
  on the HA host. Local apps placed there are built by the Supervisor.
- Copy this folder to `Z:/app-dishylink` and comment out the `image:` line in
  the copy (the GHCR image does not exist yet). Keep the source here unchanged.
- In HA: Settings > Apps > App Store > menu > Check for updates, then install
  from "Local apps". Config changes in the copy are picked up by "Check for
  updates"; Dockerfile and rootfs changes need Rebuild (or reinstall);
  apparmor changes need reinstall.
- SSH to the host (user `ron`, ssh config alias `homeassistant`) needs a key
  that is not set up on this machine, and the HA MCP connector may be down.
  Without either, install and start are clicked in the UI and logs pasted back.
- Files must have LF line endings. The Dockerfile `chmod a+x` restores the
  executable bit that Git on Windows drops.

## Expected healthy start log

```
INFO: Host identity on the Starlink LAN: ip=<host ip> mac=<host mac>
INFO: Recorder history kept in RAM, flushed to /data/historian every 15 minute(s) and at shutdown
INFO: Starting Dishylink v1.2.0 browser host on port 8080
INFO: Flushing recorder history to /data/historian every 15 minute(s)
INFO: Starting ingress proxy on port 8099
```
