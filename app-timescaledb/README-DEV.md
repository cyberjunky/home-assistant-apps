# Local development

## Prerequisites

- Cross-platform builds: `docker run --privileged --rm tonistiigi/binfmt --install all`
- A buildx builder: `docker buildx create --use --name mybuilder && docker buildx inspect mybuilder --bootstrap`

## project.sh

Run `./project.sh <command>` in this folder:

- `build`: build the app for the current architecture (`PLATFORM` in the script) into the
  local Docker as `local/app-timescaledb:<version>`.
- `debug`: build it and run it with the normal start-up.
- `inspect`: run a shell in the image built last.
- `build-dependencies [component version]`: build and push the prebuilt dependency images
  (extensions and tools) that the Dockerfile pulls from `ghcr.io/<owner>/app-timescaledb/dependency`.
  The workflow below does the same on GitHub's runners.

Set `REGISTRY` to push the dependencies elsewhere. Component versions are the `ARG` lines at the
top of `Dockerfile`, which `project.sh` and the repository's **Build TimescaleDB dependencies**
workflow read.

## Release builds

The repository's **Builder** workflow builds this app on every push that touches it and
publishes `ghcr.io/cyberjunky/app-timescaledb:<version>` (multi-arch) whenever the version in
`config.yaml` is not yet on GHCR. It pulls the prebuilt extension
images from `ghcr.io/cyberjunky/app-timescaledb/dependency`, so rebuild those first with the
**Build TimescaleDB dependencies** workflow whenever a component version changes.

## Where PostgreSQL and TimescaleDB come from

Both come from the official TimescaleDB images (`timescale/timescaledb:<release>-pg17` and
`-pg18`, see `TIMESCALEDB_VERSION` in the Dockerfile): a PostgreSQL and a TimescaleDB that
Timescale builds and tests together. TimescaleDB's libraries must not be put on another
PostgreSQL build. On the PostgreSQL of Alpine's packages that went wrong when Alpine moved to
PostgreSQL 18.7 and 17.12: TimescaleDB 2.32.0 loaded, and every background job (compression,
retention, continuous aggregates) failed, until Timescale adapts TimescaleDB to the new
minor release (timescale/timescaledb e89c98f).

PostgreSQL 18 is in `/usr/local`, as in the official image. PostgreSQL 17, only there to
upgrade an existing database, is in `/opt/postgresql17`. `/usr/libexec/postgresql17` and
`/usr/libexec/postgresql18` point to their programs. The other extensions are built on Alpine
against its PostgreSQL packages (the dependency images) and copied into both.

The build ends with `/usr/share/timescaledb/check_image.sh`: for PostgreSQL 17 and 18 it
starts a temporary database, creates every extension, waits for a TimescaleDB background job
to succeed, and lets `pg_upgrade` check the 17 database for 18. The build stops when one of
these fails. Run it by hand in a shell in the app to check a running image.

## A shell in the running app

From a system SSH session (port 22222) on the Home Assistant host, `docker ps` shows the
app's container (`addon_<repository>_timescaledb`); then:

```
docker exec -it addon_<repository>_timescaledb bash
```
