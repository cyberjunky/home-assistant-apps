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

## A shell in the running app

From a system SSH session (port 22222) on the Home Assistant host, `docker ps` shows the
app's container (`addon_<repository>_timescaledb`); then:

```
docker exec -it addon_<repository>_timescaledb bash
```
