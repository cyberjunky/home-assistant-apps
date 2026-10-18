# https://developers.home-assistant.io/docs/apps/configuration#app-dockerfile
#
# Packages Dishylink (https://github.com/DaveyHert/Dishylink, MIT) in its
# "browser" form: the built web dashboard plus the always-on history recorder
# and the LAN/cloud proxies, exactly what upstream's own Dockerfile ships.
# Stage 1 builds the dashboard from a pinned upstream tag; stage 2 runs it on
# the Home Assistant base image behind an nginx ingress proxy.
ARG BUILD_FROM=ghcr.io/home-assistant/base:3.23
ARG DISHYLINK_VERSION=v1.2.0

# ---------------------------------------------------------------------------
# Stage 1: build the Dishylink web dashboard
# Same Debian/Node image Dishylink's own Dockerfile builds with, so the
# monorepo lockfile resolves exactly as upstream resolves it.
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS build
ARG DISHYLINK_VERSION

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${DISHYLINK_VERSION}" \
        https://github.com/DaveyHert/Dishylink.git /src
WORKDIR /src

# --ignore-scripts skips the Electron/WXT postinstall downloads, as upstream does.
RUN npm ci --ignore-scripts --no-audit --no-fund

# Relative base: under Home Assistant ingress the dashboard is served below
# /api/hassio_ingress/<token>/ rather than at /. Upstream's desktop build uses
# the same relative base, so this is a supported configuration of the bundle.
RUN npx tsc -b && npx vite build --base=./

# The ingress shim rewrites the dashboard's absolute /api, /dishy, /cloud ...
# requests onto the ingress prefix. It must run before the module bundle, so
# it is injected as the first element of <head>.
COPY ha-ingress.js dist/ha-ingress.js
RUN sed -i 's|<head>|<head><script src="./ha-ingress.js"></script>|' dist/index.html \
    && grep -q 'ha-ingress.js' dist/index.html

# ---------------------------------------------------------------------------
# Stage 2: runtime on the Home Assistant base image
# ---------------------------------------------------------------------------
FROM ${BUILD_FROM}
ARG DISHYLINK_VERSION
ENV DISHYLINK_VERSION=${DISHYLINK_VERSION}

RUN apk add --no-cache nodejs npm nginx rsync \
    && addgroup -S -g 1001 dishylink \
    && adduser -S -u 1001 -G dishylink -h /app -H dishylink \
    && mkdir -p /app /run/nginx

WORKDIR /app

# Runtime only needs the recorder's protobuf decoder, tsx to load the .mts
# tree, and undici for the cloud handler's resilient fetch (the same three
# packages, at the same versions, as upstream's image). npm is removed again
# afterwards; node stays.
RUN printf '%s\n' '{"name":"dishylink-browser","private":true,"type":"module"}' > package.json \
    && npm install --omit=dev --ignore-scripts --no-audit --no-fund \
        @bufbuild/protobuf@2.6.0 tsx@4.23.1 undici@7.29.0 \
    && npm cache clean --force \
    && apk del npm \
    && chown -R dishylink:dishylink /app

COPY --from=build --chown=dishylink:dishylink /src/dist ./dist
COPY --from=build --chown=dishylink:dishylink /src/public/dish.protoset ./public/dish.protoset
COPY --from=build --chown=dishylink:dishylink /src/collector ./collector
COPY --from=build --chown=dishylink:dishylink /src/core ./core
COPY --from=build --chown=dishylink:dishylink /src/cloud ./cloud
COPY --from=build --chown=dishylink:dishylink /src/docker ./docker
COPY --from=build --chown=dishylink:dishylink /src/dev/starlinkCloudProxy.ts ./dev/starlinkCloudProxy.ts
COPY --from=build /src/LICENSE ./LICENSE.dishylink

# Copy root filesystem
COPY rootfs /
# Git on Windows does not record the executable bit reliably; set it here.
RUN chmod a+x /etc/services.d/*/run /etc/services.d/*/finish /usr/bin/historian-flush

LABEL \
    org.opencontainers.image.title="Home Assistant App: Dishylink" \
    org.opencontainers.image.description="Starlink dish and router dashboard with recorded history, powered by Dishylink." \
    org.opencontainers.image.source="https://github.com/cyberjunky/home-assistant-apps" \
    org.opencontainers.image.licenses="MIT"
