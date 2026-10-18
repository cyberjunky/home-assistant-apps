# Local development

The app image is built entirely from the `Dockerfile` in this folder: the pgAdmin sources are cloned
at the tag given by the `PGADMIN_VERSION` build argument (default at the top of the
Dockerfile), the JS bundle and the Python virtualenv are built in separate stages, and the
PostgreSQL client tools come from the Alpine packages.

## Build with docker

In this folder run:

```
./build.sh
```

which builds and pushes `ghcr.io/cyberjunky/pgadmin4/<arch>:<version>` for the architectures
in `config.yaml`, tagged with the version from `config.yaml`. Set `REGISTRY` to push elsewhere, or `--version` to use another tag:

```
REGISTRY=ghcr.io/<your-user>/pgadmin4 ./build.sh --version 4.1.0-test
```

A single architecture can also be built directly:

```
docker buildx build --platform linux/amd64 \
    --build-arg BUILD_ARCH=amd64 \
    --tag ghcr.io/cyberjunky/pgadmin4/amd64:4.1.0 .
```

## Build with GitHub Actions

The repository's **Builder** workflow test-builds this app on every push that touches it
and publishes `ghcr.io/cyberjunky/app-pgadmin4:<version>` (multi-arch) when a GitHub
release tagged `app-pgadmin4-<version>` is published. To test a branch on a Home Assistant
machine, copy this folder to `/addons`, remove the `image:` line from `config.yaml`, and let
the Supervisor build it locally.

## Upgrading pgAdmin

Bump `PGADMIN_VERSION` in `Dockerfile` and `version` in `config.yaml`, and add a
CHANGELOG entry. Check the release notes for changes in
the supported PostgreSQL versions; the bundled client tools are the Alpine
`postgresql<major>-client` packages listed in the Dockerfile.

## Run the app with an interactive shell

From a system SSH session (port 22222) on the Home Assistant host:

```
docker run -it --entrypoint "/bin/sh" -v /mnt/data/supervisor/addons/data/local_pgadmin4/:/data:rw ghcr.io/cyberjunky/pgadmin4/aarch64:4.1.0
```
