# Local development

The app image is built entirely from the `Dockerfile` in this folder: the pgAdmin sources are cloned
at the tag given by the `PGADMIN_VERSION` build argument (default at the top of the
Dockerfile), the JS bundle and the Python virtualenv are built in separate stages, and the
PostgreSQL client tools come from the Alpine packages.

## Build with docker

In this folder:

```
docker buildx build --platform linux/amd64 --build-arg BUILD_ARCH=amd64 \
    --tag local/app-pgadmin4:dev --load .
```

Use `linux/arm64` and `BUILD_ARCH=aarch64` for the other architecture. The published image
comes only from the Builder workflow below.

## Build with GitHub Actions

The repository's **Builder** workflow builds this app on every push that touches it and
publishes `ghcr.io/cyberjunky/app-pgadmin4:<version>` (multi-arch) whenever the version in
`config.yaml` is not yet on GHCR. To test a branch on a Home Assistant
machine, copy this folder to `/addons`, remove the `image:` line from `config.yaml`, and let
the Supervisor build it locally.

## Upgrading pgAdmin

Bump `PGADMIN_VERSION` in `Dockerfile` and `version` in `config.yaml`, and add a
CHANGELOG entry. Check the release notes for changes in
the supported PostgreSQL versions; the bundled client tools are the Alpine
`postgresql<major>-client` packages listed in the Dockerfile.

## A shell in the running app

From a system SSH session (port 22222) on the Home Assistant host, `docker ps` shows the
app's container (`addon_<repository>_pgadmin4`); then:

```
docker exec -it addon_<repository>_pgadmin4 /bin/sh
```
