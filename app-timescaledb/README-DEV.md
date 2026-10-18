# Local development

## Prerequisites

- Cross-platform builds: `docker run --privileged --rm tonistiigi/binfmt --install all`
- A buildx builder: `docker buildx create --use --name mybuilder && docker buildx inspect mybuilder --bootstrap`

## project.sh

Run `./project.sh <command>` in this folder:

- `build`: build the app for the current architecture (`PLATFORM` in the script), tagged
  with the version from `config.yaml`.
- `build-push`: the same, then push it to the registry.
- `build-dependencies`: build and push the prebuilt dependency images (extensions) that
  the main Dockerfile pulls from `ghcr.io/<owner>/timescaledb/dependency`.
- `build-ha`: use the Home Assistant builder to build all architectures, tagged `latest`.
- `run-hassos`: build, push, then SSH to Home Assistant and pull the image.
- `inspect`: build and run locally with an interactive shell.
- `debug`: build and run locally with the normal start-up.

The registry defaults to `ghcr.io/cyberjunky/timescaledb`; set `REGISTRY` to push
elsewhere. Component versions are the `ARG` lines at the top of `Dockerfile`, which `project.sh` and
the repository's **Build TimescaleDB dependencies** workflow read.

## Release builds

The repository's **Builder** workflow test-builds this app on every push that touches it
and publishes `ghcr.io/cyberjunky/app-timescaledb:<version>` (multi-arch) when a GitHub
release tagged `app-timescaledb-<version>` is published. It pulls the prebuilt extension
images from `ghcr.io/cyberjunky/timescaledb/dependency`, so rebuild those first with the
**Build TimescaleDB dependencies** workflow whenever a component version changes.

## Manual build

```
docker build --platform linux/aarch64 --tag ghcr.io/cyberjunky/timescaledb/aarch64:5.5.0 .
```

The Dockerfile carries the default base image and architecture:

```
ARG BUILD_FROM=ghcr.io/hassio-addons/base/aarch64:20.2.0
ARG BUILD_ARCH=aarch64
```

## Test on a Home Assistant host

From a system SSH session (port 22222):

```
docker image pull ghcr.io/cyberjunky/timescaledb/aarch64:5.5.0
```

Run with the app's data attached and an interactive shell:

```
docker run -it --entrypoint "/bin/sh" -v /mnt/data/supervisor/addons/data/local_timescaledb/:/data:rw ghcr.io/cyberjunky/timescaledb/aarch64:5.5.0
```

Or attach to the running app:

```
docker exec -it addon_local_timescaledb bash
```
